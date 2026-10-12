"""Flask app: a thin JSON API over the Engine + the single-page UI.  All real work happens in hisaab.engine / hisaab.core.

SECURITY MODEL (the app handles bank statements, so even though it is "just localhost" we do the basics properly):
  * binds to 127.0.0.1 only (run.py) - never reachable from the network
  * Host-header check          -> DNS-rebinding defence (a malicious website cannot make your browser talk to us under its own name)
  * every state-changing request must carry the custom header  X-Hisaab  AND, if an Origin is sent, it must be our own origin
                                -> CSRF defence (another site cannot forge such a request: custom headers trigger a CORS preflight
                                   that we never approve)
  * strict Content-Security-Policy (no inline script, no third-party anything), nosniff, no-referrer, no-store for API data
  * uploads: size limit (config.MAX_UPLOAD_MB), never written to disk, parsed in memory; filenames only used as labels
  * all SQL is parameterised; all text from statements is inserted into the page with textContent (never innerHTML) - see static/js/ui.js
  * errors: JSON message only, stack traces go to the server log, not to the browser
  * "Delete all my data" really deletes the database file and the models
  * profiles: each one is a separate data folder (hisaab/profiles.py); the browser's choice is a cookie holding only the
    profile id (HttpOnly, SameSite=Strict) and an unknown id falls back to the first profile
"""
from __future__ import annotations

import datetime as _dt
import logging
import traceback
from pathlib import Path

import numpy as np
import pandas as pd
from flask import Flask, Response, abort, g, jsonify, render_template, request
from flask.json.provider import DefaultJSONProvider
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge
from werkzeug.utils import secure_filename

from .. import config, taxonomy
from ..engine import Engine
from ..ingest.readers import ReadError
from ..nlq import llm
from ..profiles import Profiles

log = logging.getLogger("hisaab")
PROFILE_COOKIE = "hisaab_profile"
MAX_FILES = 20                       # statements per multi-file import
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"} | config.EXTRA_HOSTS   # extra names only for a hosted demo (config.py)
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; font-src 'self'; "
       "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


def _clean(o):
    """Browsers reject NaN/Infinity (they are not JSON). Turn them into null, recursively."""
    if isinstance(o, float):
        return None if (o != o or o in (float("inf"), float("-inf"))) else o
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, np.floating):
        return _clean(float(o))
    return o


class _Provider(DefaultJSONProvider):
    def dumps(self, obj, **kwargs):
        return super().dumps(_clean(obj), **kwargs)

    @staticmethod
    def default(o):
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.floating):
            return None if np.isnan(o) else float(o)
        if isinstance(o, np.bool_):
            return bool(o)
        if isinstance(o, (pd.Timestamp, _dt.datetime, _dt.date)):
            return o.isoformat()
        return DefaultJSONProvider.default(o)


class _CurrentEngine:
    """Stands in for 'the engine of the profile this request is using', so every route can keep saying eng.something()."""
    def __init__(self, profiles: Profiles) -> None:
        self._profiles = profiles

    def __getattr__(self, name):
        return getattr(self._profiles.engine(g.profile["id"]), name)


def create_app(engine: Engine | None = None, profiles: Profiles | None = None) -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.json = _Provider(app)
    app.json.sort_keys = False                      # keep dict order: series are ranked by spend and the UI relies on that order
    app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_MB * 1024 * 1024
    # a fixed engine (tests) means one implicit profile; otherwise every request uses the profile chosen by its cookie
    profs = None if engine is not None else (profiles or Profiles())
    eng = engine if engine is not None else _CurrentEngine(profs)
    app.config["ENGINE"] = eng
    app.config["PROFILES"] = profs

    # ---- guards -------------------------------------------------------------------------------------------------------
    @app.before_request
    def _guard():
        host = (request.host or "").rsplit(":", 1)[0] if not request.host.startswith("[") else request.host.split("]")[0] + "]"
        if host not in ALLOWED_HOSTS:
            abort(403)
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            if request.headers.get(config.CUSTOM_HEADER) != "1":
                return jsonify(error="Missing X-Hisaab header (blocked as a possible cross-site request)."), 403
            origin = request.headers.get("Origin")
            if origin and origin.split("://", 1)[-1] != request.host:
                return jsonify(error="Cross-origin request blocked."), 403
        g.profile = profs.resolve(request.cookies.get(PROFILE_COOKIE)) if profs else {"id": "default", "name": "Me"}

    @app.after_request
    def _headers(resp: Response):
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Frame-Options"] = "DENY"
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        else:
            resp.headers.setdefault("Cache-Control", "no-cache")
        return resp

    @app.errorhandler(Exception)
    def _err(e):
        if isinstance(e, RequestEntityTooLarge):
            return jsonify(error=f"The upload is larger than {config.MAX_UPLOAD_MB} MB (all files together). Import fewer files at a time."), 413
        if isinstance(e, HTTPException):
            return jsonify(error=e.description), e.code
        if isinstance(e, (ValueError, ReadError)):
            return jsonify(error=str(e)), 400
        log.error("Unhandled error on %s %s\n%s", request.method, request.path, traceback.format_exc())
        return jsonify(error="Something went wrong on our side. The details are in the server log."), 500

    # ---- pages ------------------------------------------------------------------------------------------------------------
    @app.get("/")
    def index():
        return render_template("index.html")

    # ---- API: data in -------------------------------------------------------------------------------------------------------
    def _file():
        f = request.files.get("file")
        if not f or not f.filename:
            raise ValueError("Choose a statement file (.csv, .xlsx or .xls).")
        return f.read(), secure_filename(f.filename) or "statement"

    def _mapping():
        import json
        raw = request.form.get("mapping")
        return json.loads(raw) if raw else None

    def _need_profiles() -> Profiles:
        if profs is None:
            abort(404)
        return profs

    def _select(resp: Response, pid: str) -> Response:
        resp.set_cookie(PROFILE_COOKIE, pid, max_age=365 * 24 * 3600, httponly=True, samesite="Strict", secure=request.is_secure)
        return resp

    # ---- API: profiles --------------------------------------------------------------------------------------------------------
    @app.get("/api/profiles")
    def profiles_list():
        return jsonify(active=g.profile["id"], profiles=_need_profiles().all() if profs else [g.profile])

    @app.post("/api/profiles")
    def profile_create():
        p = _need_profiles().create(_json())
        return _select(jsonify(p), p["id"])

    @app.post("/api/profiles/<pid>")
    def profile_update(pid: str):
        return jsonify(_need_profiles().update(pid, _json()))

    @app.post("/api/profiles/<pid>/select")
    def profile_select(pid: str):
        p = _need_profiles().get(pid)
        if p is None:
            raise ValueError("That profile does not exist any more.")
        return _select(jsonify(p), pid)

    @app.post("/api/profiles/<pid>/delete")
    def profile_delete(pid: str):
        if _json().get("confirm") != "DELETE":
            raise ValueError('Send {"confirm": "DELETE"} to delete a profile and all its data.')
        _need_profiles().delete(pid)
        resp = jsonify(ok=True)
        return _select(resp, profs.all()[0]["id"]) if pid == g.profile["id"] else resp

    @app.get("/api/status")
    def status():
        return jsonify(eng.status())

    @app.post("/api/demo/load")
    def demo_load():
        return jsonify(eng.load_demo())

    @app.post("/api/import/preview")
    def import_preview():
        data, name = _file()
        return jsonify(eng.preview(data, name, _mapping()))

    @app.post("/api/import/commit")
    def import_commit():
        data, name = _file()
        return jsonify(eng.commit(data, name, _mapping()))

    @app.post("/api/import/commit-many")
    def import_commit_many():
        files = [f for f in request.files.getlist("file") if f and f.filename]
        if not files:
            raise ValueError("Choose one or more statement files (.csv, .xlsx or .xls).")
        if len(files) > MAX_FILES:
            raise ValueError(f"Import at most {MAX_FILES} files at a time.")
        return jsonify(eng.commit_many([(f.read(), secure_filename(f.filename) or "statement", None) for f in files]))

    @app.post("/api/reset")
    def reset():
        if (request.get_json(silent=True) or {}).get("confirm") != "DELETE":
            raise ValueError('Send {"confirm": "DELETE"} to wipe everything.')
        eng.reset()
        return jsonify(ok=True)

    # ---- API: views -----------------------------------------------------------------------------------------------------------
    @app.get("/api/taxonomy")
    def tax():
        return jsonify(categories=[{"name": c.name, "group": c.group, "blurb": c.blurb, "examples": c.examples} for c in taxonomy.CATEGORIES],
                       kinds=taxonomy.KIND_LABEL, uncategorised=taxonomy.UNCATEGORISED,
                       allowed_debit=taxonomy.ALLOWED_FOR_DEBIT, allowed_credit=taxonomy.ALLOWED_FOR_CREDIT)

    @app.get("/api/dashboard")
    def dashboard():
        return jsonify(eng.dashboard(request.args.get("period")))

    @app.get("/api/transactions")
    def transactions():
        return jsonify(eng.transactions_page(request.args.to_dict()))

    @app.get("/api/transactions/<int:tid>")
    def txn(tid: int):
        return jsonify(eng.txn_detail(tid))

    @app.get("/api/merchants")
    def merchants():
        return jsonify(eng.merchants_page())

    @app.get("/api/merchants/<int:mid>")
    def merchant(mid: int):
        return jsonify(eng.merchant_detail(mid))

    @app.get("/api/patterns")
    def patterns():
        a = eng.analysis()
        return jsonify(recurring=a["recurring"]["recurring"], habits=a["recurring"]["habits"], anomalies=a["anomalies"])

    @app.get("/api/review")
    def review():
        return jsonify(eng.review_queue(int(request.args.get("limit", 30))))

    @app.get("/api/learning")
    def learning():
        return jsonify(eng.learning_log())

    # ---- API: teaching ----------------------------------------------------------------------------------------------------------
    def _json() -> dict:
        d = request.get_json(silent=True)
        if not isinstance(d, dict):
            raise ValueError("Expected a JSON body.")
        return d

    @app.post("/api/transactions/<int:tid>/category")
    def txn_category(tid: int):
        d = _json()
        return jsonify(eng.set_category(str(d.get("category", "")), txn_id=tid, scope="transaction" if d.get("scope") == "transaction" else "merchant"))

    @app.post("/api/merchants/<int:mid>/category")
    def merchant_category(mid: int):
        return jsonify(eng.set_category(str(_json().get("category", "")), merchant_id=mid, scope="merchant"))

    @app.post("/api/merchants/<int:mid>/kind")
    def merchant_kind(mid: int):
        return jsonify(eng.set_kind(mid, str(_json().get("kind", ""))))

    @app.post("/api/merchants/<int:mid>/rename")
    def merchant_rename(mid: int):
        return jsonify(eng.rename_merchant(mid, str(_json().get("name", ""))))

    @app.post("/api/merchants/merge")
    def merchant_merge():
        d = _json()
        return jsonify(eng.merge_merchants(int(d.get("keep")), int(d.get("drop"))))

    @app.post("/api/forget")
    def forget():
        d = _json()
        res = eng.forget(str(d.get("what", "")), merchant_id=d.get("merchant_id"), direction=d.get("direction"), txn_id=d.get("txn_id"))
        return jsonify(res)

    @app.post("/api/learn/retrain")
    def retrain():
        return jsonify(eng.retrain())

    # ---- API: ask / playground -----------------------------------------------------------------------------------------------------
    @app.post("/api/ask")
    def ask():
        return jsonify(eng.ask(str(_json().get("question", ""))))

    @app.post("/api/playground")
    def playground():
        d = _json()
        try:
            amount = float(d.get("amount") or 100)
        except (TypeError, ValueError):
            raise ValueError("Amount must be a number.")
        direction = "credit" if d.get("direction") == "credit" else "debit"
        return jsonify(eng.playground(str(d.get("narration", "")), amount, direction, d.get("time")))

    @app.get("/api/llm/status")
    def llm_status():
        return jsonify(llm.status())

    # ---- API: files out / labels in --------------------------------------------------------------------------------------------------
    @app.get("/api/export/transactions.csv")
    def export_csv():
        return Response(eng.export_transactions_csv(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=hisaab_transactions.csv"})

    @app.get("/api/export/labels.xlsx")
    def export_labels():
        assisted = request.args.get("assisted") in ("1", "true")
        return Response(eng.labels_template(assisted), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f"attachment; filename=hisaab_labels_{'assisted' if assisted else 'blind'}.xlsx"})

    @app.post("/api/labels/import")
    def labels_import():
        data, name = _file()
        return jsonify(eng.import_labels(data, name))

    return app
