"""Read-side of the Engine: everything the web UI shows, assembled from the stored data.  Kept separate from engine.py (which
changes state) so it is obvious which methods can never modify the database."""
from __future__ import annotations

import calendar
import copy
import io
import json
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from . import config, store, taxonomy
from .core import analytics as A
from .core import anomaly, categorize as cat_mod, features, insights, p2p, pipeline, recurring, review
from .core.upi_parse import parse as parse_narration
from .ingest.importer import RawRow
from .nlq import executor, llm, parser
from .textutil import fmt_inr, mask_vpa, na, tokens_of, vpa_parts


def _py(x):
    """numpy/pandas scalars -> plain Python (JSON-safe)."""
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating,)):
        return None if np.isnan(x) else float(x)
    if isinstance(x, (pd.Timestamp, datetime)):
        return x.isoformat()
    if isinstance(x, float) and x != x:
        return None
    return x


class ViewsMixin:
    # ---- shared analysis (cached per data version) ---------------------------------------------------------------------------
    def analysis(self) -> dict:
        c = getattr(self, "_analysis_cache", None)
        if c and c[0] == self._version:
            return c[1]
        df = self.df()
        if df.empty:
            res = {"recurring": {"recurring": [], "habits": []}, "anomalies": [], "review": {"items": [], "n_merchants": 0, "money": 0, "top5_share": 0}}
        else:
            rec = recurring.detect(df)
            res = {"recurring": rec, "anomalies": anomaly.detect(df, rec["recurring"]), "review": review.build(df)}
        self._analysis_cache = (self._version, res)
        return res

    # ---- dashboard ---------------------------------------------------------------------------------------------------------------
    def _period(self, df: pd.DataFrame, key: str | None) -> tuple[A.Period, A.Period | None, list[dict]]:
        ref = A.ref_date(df)
        months = sorted(set(df["month"]))
        opts = []
        for m in reversed(months):
            y, mo = map(int, m.split("-"))
            partial = (y, mo) == (ref.year, ref.month) and ref.day < calendar.monthrange(y, mo)[1]
            opts.append({"key": f"m:{m}", "label": f"{calendar.month_name[mo]} {y}" + (" (so far)" if partial else "")})
        opts += [{"key": "d:30", "label": "Last 30 days"}, {"key": "all", "label": "All time"}]
        key = key or opts[0]["key"]
        dmin = df["date"].min().date()
        if key == "all":
            return A.Period(dmin, ref, "All time"), None, opts
        if key.startswith("d:"):
            n = int(key[2:])
            p = A.Period(max(ref - timedelta(days=n - 1), dmin), ref, f"Last {n} days")
            return p, A.Period(p.start - timedelta(days=n), p.start - timedelta(days=1), f"previous {n} days"), opts
        y, mo = map(int, key[2:].split("-"))
        partial = (y, mo) == (ref.year, ref.month) and ref.day < calendar.monthrange(y, mo)[1]
        p = A.month_period(y, mo, ref.day if partial else None)
        py, pm = A.shift_month(y, mo, -1)
        prev = A.month_period(py, pm, p.end.day if partial else None)
        return p, prev, opts

    @staticmethod
    def _pace(df: pd.DataFrame, p: A.Period, prev: A.Period | None) -> dict | None:
        """Cumulative spending day by day: this period vs the previous one, on ONE axis (never dual-axis)."""
        if prev is None:
            return None

        def cum(period: A.Period) -> list[float]:
            d = A.in_period(df, period)
            s = d[A.spend_mask(d)].groupby(d["date"].dt.date)["amount"].sum()
            out, acc, cur = [], 0.0, period.start
            while cur <= period.end:
                acc += float(s.get(cur, 0.0))
                out.append(round(acc, 2))
                cur += timedelta(days=1)
            return out

        return {"recent": cum(p), "base": cum(prev), "recent_label": p.label.replace(" (so far)", ""), "base_label": prev.label}

    def dashboard(self, key: str | None = None) -> dict:
        df = self.df()
        if df.empty:
            return {"empty": True, "status": self.status()}
        p, prev, opts = self._period(df, key)
        an = self.analysis()
        ref = A.ref_date(df)
        ov = A.overview(df, p, prev)
        return {
            "empty": False, "ref": ref.isoformat(), "range": {"min": df["date"].min().date().isoformat(), "max": ref.isoformat()},
            "periods": opts, "period": p.to_dict(), "overview": ov, "categories": A.category_breakdown(df, p),
            "monthly": A.monthly_matrix(df), "daily": A.daily_series(df, p),
            "top_merchants": A.merchant_ranking(df, p, "amount", limit=8),
            "local_shops": A.merchant_ranking(df, p, "visits", local_only=True, limit=6),
            "weekday_hour": A.weekday_hour(df, p), "pace": self._pace(df, p, prev),
            "projection": A.projection(df, ref) if p.end == ref and ref.day < calendar.monthrange(ref.year, ref.month)[1] else None,
            "insights": insights.build(df, an["recurring"], an["anomalies"], an["review"]),
            "status": self.status(),
        }

    # ---- transactions --------------------------------------------------------------------------------------------------------------
    def transactions_page(self, q: dict) -> dict:
        df = self.df()
        if df.empty:
            return {"total": 0, "rows": [], "page": 1, "pages": 1, "sum_debit": 0, "sum_credit": 0}
        d = df
        if q.get("q"):
            s = str(q["q"]).lower()
            d = d[d["merchant"].str.lower().str.contains(s, regex=False) | d["description"].fillna("").str.lower().str.contains(s, regex=False)
                  | d["category"].fillna("").str.lower().str.contains(s, regex=False)]
        for col in ("category", "kind", "direction", "cat_status"):
            if q.get(col):
                d = d[d[col] == q[col]]
        if q.get("merchant_id"):
            d = d[d["merchant_id"] == int(q["merchant_id"])]
        if q.get("needs_review") in ("1", 1, True, "true"):
            d = d[d["cat_status"].isin(["unknown", "suggested"]) & (d["direction"] == "debit")]
        if q.get("start"):
            d = d[d["date"] >= pd.Timestamp(q["start"])]
        if q.get("end"):
            d = d[d["date"] <= pd.Timestamp(q["end"])]
        if q.get("min"):
            d = d[d["amount"] >= float(q["min"])]
        if q.get("max"):
            d = d[d["amount"] <= float(q["max"])]
        sort = q.get("sort") or "date"
        asc = (q.get("order") == "asc")
        key = {"date": "ts", "amount": "amount", "merchant": "merchant", "category": "category"}.get(sort, "ts")
        d = d.sort_values(key, ascending=asc, kind="stable")
        per = max(1, min(int(q.get("per_page") or 50), 200))
        total = len(d)
        pages = max(1, (total + per - 1) // per)
        page = max(1, min(int(q.get("page") or 1), pages))
        part = d.iloc[(page - 1) * per: page * per]
        return {"total": total, "page": page, "pages": pages, "per_page": per, "rows": A.txn_records(part),
                "sum_debit": round(float(d.loc[d["direction"] == "debit", "amount"].sum()), 2),
                "sum_credit": round(float(d.loc[d["direction"] == "credit", "amount"].sum()), 2)}

    # ---- merchants ---------------------------------------------------------------------------------------------------------------------
    def merchants_page(self) -> list[dict]:
        df = self.df()
        if df.empty:
            return []
        out = []
        for mid, g in df.groupby("merchant_id"):
            f = g.iloc[0]
            deb, cre = g[g["direction"] == "debit"], g[g["direction"] == "credit"]
            main = deb if deb["amount"].sum() >= cre["amount"].sum() else cre
            out.append({"id": int(mid), "name": f["merchant"], "kind": f["kind"], "kind_conf": _py(f["kind_conf"]), "kind_source": f["kind_source"],
                        "is_local": bool(f["is_local"]), "is_brand": bool(f["is_brand"]), "category": main["category"].mode().iat[0],
                        "cat_status": main["cat_status"].mode().iat[0], "cat_source": main["cat_source"].mode().iat[0], "n": int(len(g)),
                        "spend": round(float(deb["amount"].sum()), 2), "received": round(float(cre["amount"].sum()), 2), "days": int(g["date"].nunique()),
                        "avg": round(float(deb["amount"].mean()), 2) if len(deb) else 0.0, "first": g["date"].min().date().isoformat(),
                        "last": g["date"].max().date().isoformat(), "placeholder": bool(f["placeholder"])})
        out.sort(key=lambda m: -(m["spend"] + m["received"]))
        return out

    def merchant_detail(self, mid: int) -> dict:
        df = self.df()
        g = df[df["merchant_id"] == mid]
        if g.empty:
            raise ValueError("No such merchant")
        f = g.iloc[0]
        c = self.conn()
        try:
            names = [(r["name"], r["n"]) for r in c.execute("SELECT name, n FROM merchant_names WHERE merchant_id=? ORDER BY n DESC", (mid,))]
            keys = [r["key"][4:] for r in c.execute("SELECT key FROM merchant_keys WHERE merchant_id=? AND key LIKE 'vpa:%'", (mid,))]
            rules = [dict(r) for r in c.execute("SELECT direction, category FROM rules WHERE merchant_id=?", (mid,))]
        finally:
            c.close()
        sp = g[A.spend_mask(g)]
        monthly = sp.groupby("month")["amount"].sum()
        rec = [r for r in self.analysis()["recurring"]["recurring"] if r["merchant_id"] == mid]
        return {"id": int(mid), "name": f["merchant"], "kind": f["kind"], "kind_conf": _py(f["kind_conf"]), "kind_source": f["kind_source"],
                "p_person": _py(f["p_person"]), "is_local": bool(f["is_local"]), "is_brand": bool(f["is_brand"]),
                "names_seen": [{"name": n.title(), "count": k} for n, k in names], "upi_addresses": [mask_vpa(k) for k in keys],
                "rules": rules, "n": int(len(g)), "spend": round(float(sp["amount"].sum()), 2),
                "received": round(float(g.loc[g["direction"] == "credit", "amount"].sum()), 2),
                "monthly": [{"month": m, "amount": round(float(v), 2)} for m, v in monthly.items()],
                "categories": [{"category": k, "count": int(v)} for k, v in g["category"].value_counts().items()],
                "recurring": rec, "txns": A.txn_records(g.sort_values("ts", ascending=False), 40)}

    # ---- review queue with suggestions ----------------------------------------------------------------------------------------------
    def review_queue(self, limit: int = 30) -> dict:
        df = self.df()
        base = self.analysis()["review"]
        if df.empty:
            return base
        items = base["items"][:limit]
        mids = [i["merchant_id"] for i in items]
        pred = self._predict_for(mids)
        for it in items:
            pr = pred.get(it["merchant_id"], {})
            it["top3"] = pr.get("top3", [])
            it["top3_shop"] = pr.get("top3_shop", [])
            it["kind_reasons"] = pr.get("kind_reasons", [])
            it["p_person"] = pr.get("p_person")
            it["samples"] = [_mask_free(s) for s in it["samples"]]
        return {**base, "items": items}

    def _state_for_predict(self, c):
        merchants, rules = self._load_state(c)
        canon = {mid: m["canonical"] for mid, m in merchants.items()}
        is_brand = {mid: bool(m["is_brand"]) for mid, m in merchants.items()}
        brand_cat = {mid: m["brand_category"] for mid, m in merchants.items()}
        placeholders = {mid for mid, m in merchants.items() if m["placeholder"]}
        user_kinds = {mid: m["kind"] for mid, m in merchants.items() if m["kind_source"] == "user" and m["kind"]}
        return merchants, rules, canon, is_brand, brand_cat, placeholders, user_kinds

    def _predict_for(self, mids: list[int]) -> dict[int, dict]:
        c = self.conn()
        try:
            merchants, rules, canon, is_brand, brand_cat, placeholders, user_kinds = self._state_for_predict(c)
            df = self._stage_df(c)
        finally:
            c.close()
        df = df[df["merchant_id"].isin(mids)]
        if df.empty:
            return {}
        kinds = p2p.classify(df, canon, is_brand, self.kind_model(), user_kinds, placeholders)
        frame = features.frame_from_stage1(df, canon, {m: k.kind for m, k in kinds.items()}, placeholders)
        cats = cat_mod.categorize(df, frame, canon, kinds, is_brand, brand_cat, {}, self.cat_model())   # ignore memory: show what the MODEL thinks
        # what would the category be IF the user says "it's a shop"?  (so Review can continue with a second question in the same card)
        as_shop = {mid: p2p.KindResult("merchant", 0.0, 0.5, "assumed", ["assumed to be a shop"]) for mid in mids}
        frame_s = features.frame_from_stage1(df, canon, {m: "merchant" for m in mids}, placeholders)
        cats_s = cat_mod.categorize(df, frame_s, canon, as_shop, {m: False for m in mids}, {m: None for m in mids}, {}, self.cat_model())
        out = {}
        for mid in mids:
            cr = cats.get((mid, "debit"))
            cs = cats_s.get((mid, "debit"))
            kr = kinds.get(mid)
            out[mid] = {"top3": [[n, round(p, 3)] for n, p in (cr.top3 if cr else [])], "kind_reasons": kr.reasons if kr else [],
                        "p_person": round(kr.p_person, 3) if kr else None,
                        "top3_shop": [[n, round(p, 3)] for n, p in (cs.top3 if cs else [])]}
        return out

    # ---- explain a transaction -----------------------------------------------------------------------------------------------------
    def txn_detail(self, tid: int) -> dict:
        df = self.df()
        row = df[df["id"] == tid]
        if row.empty:
            raise ValueError("No such transaction")
        r = row.iloc[0]
        mid = int(r["merchant_id"])
        c = self.conn()
        try:
            merchants, rules, canon, is_brand, brand_cat, placeholders, user_kinds = self._state_for_predict(c)
            sdf = self._stage_df(c)
            names = [(x["name"], x["n"]) for x in c.execute("SELECT name, n FROM merchant_names WHERE merchant_id=? ORDER BY n DESC", (mid,))]
        finally:
            c.close()
        p = parse_narration(r["description"])
        g = sdf[sdf["merchant_id"] == mid]
        kinds = p2p.classify(g, canon, is_brand, self.kind_model(), user_kinds, placeholders)
        kr = kinds[mid]
        frame = features.frame_from_stage1(g, canon, {mid: kr.kind}, placeholders)
        cats = cat_mod.categorize(g, frame, canon, kinds, is_brand, brand_cat, rules, self.cat_model())
        cr = cats.get((mid, r["direction"]))
        stages = []
        stages.append({"key": "read", "title": "1 · Read the narration", "summary": f"UPI payment{' to a merchant' if p.marker == 'P2M' else ''}" if p.mode == "UPI" else f"{p.mode} line",
                       "notes": p.to_dict()["notes"], "data": {k: v for k, v in p.to_dict().items() if k in ("mode", "marker", "dir_hint", "rrn", "vpa", "app", "ifsc", "bank_hint", "payee_raw", "remarks", "template", "confidence", "flags")}})
        stages.append({"key": "merchant", "title": "2 · Find the merchant", "summary": f"{r['merchant']}",
                       "notes": [self._match_note(r), f"Names seen for this merchant: " + ", ".join(f"{n.title()} ×{k}" for n, k in names[:5])] if names else [self._match_note(r)],
                       "data": {"match_method": r["match_method"], "match_confidence": _py(r["match_conf"]), "is_brand": bool(r["is_brand"])}})
        stages.append({"key": "kind", "title": "3 · Shop or person?", "summary": f"{taxonomy.KIND_LABEL.get(kr.kind, kr.kind)} ({kr.source}, {kr.confidence:.0%} sure)",
                       "notes": kr.reasons or ["No strong signal either way."],
                       "data": {"p_person": round(kr.p_person, 3), "source": kr.source, "contributions": [[n, round(c, 2)] for n, c in kr.contributions]}})
        cat_notes = list(cr.evidence) if cr else []
        data = {"source": cr.source if cr else None, "status": cr.status if cr else None, "confidence": round(cr.conf, 3) if cr else None,
                "top3": [[n, round(pp, 3)] for n, pp in (cr.top3 if cr else [])]}
        if cr and cr.source == "model":
            ex = self.cat_model().explain(frame.iloc[[0]])
            data["words"] = [[w, round(v, 2)] for w, v in ex.words]
            data["context"] = [[w, round(v, 2)] for w, v in ex.context]
            data["similar"] = [[n.title(), lab, round(s, 2)] for n, lab, s in ex.neighbours]
            if ex.words:
                cat_notes.append("Words that pushed it here: " + ", ".join(w for w, _ in ex.words) + ".")
            if ex.neighbours:
                cat_notes.append("Similar names the model knows: " + "; ".join(f"{n.title()} → {lab}" for n, lab, _ in ex.neighbours[:3]) + ".")
        if r["locked"]:
            cat_notes.insert(0, "You set this single transaction's category by hand.")
        stages.append({"key": "category", "title": "4 · Category", "summary": f"{r['category']} · {r['cat_status']}" + (f" ({r['cat_conf']:.0%})" if r["cat_conf"] == r["cat_conf"] else ""),
                       "notes": cat_notes, "data": data})
        an = [a for a in self.analysis()["anomalies"] if a["txn_id"] == tid]
        rec = [x for x in self.analysis()["recurring"]["recurring"] if tid in x["txn_ids"]]
        pat = []
        for a in an:
            pat += [f"Unusual: {x['text']}" for x in a["reasons"]]
        for x in rec:
            pat.append(f"Part of a {x['period']} pattern ({x['occurrences']} payments, next expected {x['next_expected']}).")
        stages.append({"key": "patterns", "title": "5 · Patterns", "summary": "Looks unusual" if an else ("Recurring" if rec else "Nothing special"),
                       "notes": pat or ["Not part of a recurring series and not unusual."], "data": {}})
        return {"txn": A.txn_records(row)[0], "stages": stages}

    @staticmethod
    def _match_note(r) -> str:
        m = r["match_method"]
        return {"brand": "Matched a known brand (including its legal/company name).", "vpa": "Same UPI address as earlier payments.",
                "name": "Same name as earlier payments.", "fuzzy": "A spelling/truncation variant of a merchant seen before.",
                "new": "First time this merchant appeared."}.get(m, m or "")

    # ---- playground: run ANY narration through the pipeline, store nothing --------------------------------------------------------
    def playground(self, narration: str, amount: float = 100.0, direction: str = "debit", time_hhmm: str | None = None) -> dict:
        narration = (narration or "")[:400]
        c = self.conn()
        try:
            reg = copy.deepcopy(store.load_registry(c))
            merchants, rules, canon, is_brand, brand_cat, placeholders, user_kinds = self._state_for_predict(c)
            sdf = self._stage_df(c)
        finally:
            c.close()
        ts = datetime.combine(date.today(), datetime.min.time())
        has_time = False
        if time_hhmm and ":" in time_hhmm:
            try:
                hh, mm = map(int, time_hhmm.split(":")[:2])
                ts, has_time = ts.replace(hour=hh, minute=mm), True
            except ValueError:
                pass
        if not sdf.empty:
            ts = max(ts, sdf["ts"].max().to_pydatetime() + timedelta(seconds=1))
        row = RawRow(src_row=0, ts=ts, has_time=has_time, desc=narration, amount=float(amount), direction=direction)
        st = pipeline.stage1([row], reg)
        d1 = st.df
        mid = int(d1["merchant_id"].iat[0])
        ent = st.registry.entities[mid]
        canon = {**canon, mid: ent.canonical}
        is_brand = {**is_brand, mid: ent.is_brand}
        brand_cat = {**brand_cat, mid: ent.brand_category}
        placeholders = set(placeholders) | ({mid} if ent.placeholder else set())
        hist = sdf[sdf["merchant_id"] == mid] if not sdf.empty else sdf
        g = pd.concat([hist, d1], ignore_index=True) if len(hist) else d1.copy()
        if "locked" not in g:
            g["locked"] = 0
        kinds = p2p.classify(g, canon, is_brand, self.kind_model(), user_kinds, placeholders)
        kr = kinds[mid]
        frame = features.frame_from_stage1(g, canon, {mid: kr.kind}, placeholders)
        cats = cat_mod.categorize(g, frame, canon, kinds, is_brand, brand_cat, rules, self.cat_model())
        cr = cats.get((mid, direction))
        p = parse_narration(narration)
        out = {"parsed": p.to_dict(),
               "merchant": {"name": ent.canonical, "method": d1["match_method"].iat[0], "confidence": round(float(d1["match_conf"].iat[0]), 2),
                            "is_brand": ent.is_brand, "existing": mid in merchants, "history_rows": int(len(hist))},
               "kind": {"kind": kr.kind, "label": taxonomy.KIND_LABEL.get(kr.kind), "confidence": round(kr.confidence, 3), "source": kr.source,
                        "p_person": round(kr.p_person, 3), "reasons": kr.reasons},
               "category": {"category": cr.category if cr else None, "status": cr.status if cr else None, "confidence": round(cr.conf, 3) if cr else None,
                            "source": cr.source if cr else None, "top3": [[n, round(pp, 3)] for n, pp in (cr.top3 if cr else [])], "evidence": cr.evidence if cr else []}}
        if cr and cr.source == "model":
            ex = self.cat_model().explain(frame.iloc[[len(g) - 1]])
            out["category"]["words"] = [[w, round(v, 2)] for w, v in ex.words]
            out["category"]["similar"] = [[n.title(), lab, round(s, 2)] for n, lab, s in ex.neighbours]
        return out

    # ---- ask ------------------------------------------------------------------------------------------------------------------------------
    def ask(self, question: str) -> dict:
        df = self.df()
        if df.empty:
            return {"ok": False, "headline": "No data yet", "text": "Import a statement (or load the demo data) first, then ask me about it.",
                    "interpretation": [], "followups": executor.EXAMPLES, "question": question, "table": None, "chart": None, "txns": []}
        if len(question) > 300:
            return {"ok": False, "headline": "That question is too long", "text": "Please keep a question under 300 characters.",
                    "interpretation": [], "followups": executor.EXAMPLES, "question": question[:80] + "…", "table": None, "chart": None, "txns": []}
        ctx = parser.Context.from_df(df)
        plan = parser.parse(question, ctx)
        if plan.confidence < 0.5 and llm.enabled():
            plan = llm.refine(question, ctx, plan) or plan
        an = self.analysis()
        return executor.run(plan, executor.Env(df, an["recurring"], an["anomalies"]))

    # ---- labelling template (label one month in Excel, then re-import it) ----------------------------------------------------------
    def labels_template(self, assisted: bool = False) -> bytes:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        from openpyxl.worksheet.datavalidation import DataValidation
        df = self.df()
        wb = Workbook()
        ws = wb.active
        ws.title = "Label me"
        cols = ["id", "date", "time", "description", "amount", "direction"]
        if assisted:
            cols += ["auto_merchant", "auto_kind", "auto_category", "auto_confidence"]
        cols += ["my_category", "my_kind", "my_merchant_name", "notes"]
        ws.append(cols)
        for cell in ws[1]:
            cell.font = Font(bold=True)
        for r in df.sort_values("ts").itertuples(index=False):
            row = [int(r.id), r.date.date().isoformat(), r.ts.strftime("%H:%M") if r.has_time else "", r.description, float(r.amount), r.direction]
            if assisted:
                row += [r.merchant, r.kind, r.category, round(float(r.cat_conf), 2) if r.cat_conf == r.cat_conf else None]
            row += [None, None, None, None]
            ws.append(row)
        n = ws.max_row
        col = {name: i + 1 for i, name in enumerate(cols)}
        from openpyxl.utils import get_column_letter
        dv1 = DataValidation(type="list", formula1='"' + ",".join(taxonomy.NAMES) + '"', allow_blank=True)
        dv2 = DataValidation(type="list", formula1='"merchant,person,cash,bank,self"', allow_blank=True)
        ws.add_data_validation(dv1)
        ws.add_data_validation(dv2)
        dv1.add(f"{get_column_letter(col['my_category'])}2:{get_column_letter(col['my_category'])}{n}")
        dv2.add(f"{get_column_letter(col['my_kind'])}2:{get_column_letter(col['my_kind'])}{n}")
        for name, w in {"description": 70, "my_category": 30, "my_merchant_name": 26, "auto_merchant": 26, "auto_category": 28}.items():
            if name in col:
                ws.column_dimensions[get_column_letter(col[name])].width = w
        ws.freeze_panes = "A2"
        info = wb.create_sheet("How to use")
        for line in ["1. Fill my_category for the rows you know (pick from the drop-down). my_kind (merchant/person/cash/bank/self) is optional.",
                     "2. my_merchant_name is optional: type a friendly name for a shop and every row of that shop gets it.",
                     "3. Save, then in Hisaab: Model → Import labels. The app learns from your answers and shows how often it already agreed with you.",
                     "TIP: for an honest accuracy number, use the BLIND template (without auto_* columns) so you are not influenced by the app's guesses.",
                     "Categories: " + ", ".join(taxonomy.NAMES)]:
            info.append([line])
        info.column_dimensions["A"].width = 140
        bio = io.BytesIO()
        wb.save(bio)
        return bio.getvalue()

    def import_labels(self, data: bytes, filename: str) -> dict:
        from .ingest.readers import read_grids
        grids = read_grids(data, filename)
        grid = max(grids, key=lambda g: len(g[1]))[1]
        hdr_i = next((i for i, r in enumerate(grid[:10]) if r and "id" in [str(x).strip().lower() for x in r]), None)
        if hdr_i is None:
            raise ValueError("I could not find the 'id' column. Please use the template exported from Hisaab.")
        hdr = [str(x).strip().lower() if x is not None else "" for x in grid[hdr_i]]
        ix = {h: i for i, h in enumerate(hdr)}
        if "my_category" not in ix and "my_kind" not in ix:
            raise ValueError("The file has no my_category / my_kind columns.")
        df = self.df()
        byid = df.set_index("id")
        labelled = []
        for r in grid[hdr_i + 1:]:
            try:
                tid = int(float(r[ix["id"]]))
            except (TypeError, ValueError, IndexError):
                continue
            if tid not in byid.index:
                continue
            cat = str(r[ix["my_category"]]).strip() if "my_category" in ix and ix["my_category"] < len(r) and r[ix["my_category"]] not in (None, "") else None
            kind = str(r[ix["my_kind"]]).strip().lower() if "my_kind" in ix and ix["my_kind"] < len(r) and r[ix["my_kind"]] not in (None, "") else None
            nm = str(r[ix["my_merchant_name"]]).strip() if "my_merchant_name" in ix and ix["my_merchant_name"] < len(r) and r[ix["my_merchant_name"]] not in (None, "") else None
            if cat and cat not in taxonomy.NAMES:
                continue
            if cat or kind or nm:
                labelled.append((tid, cat, kind, nm))
        if not labelled:
            raise ValueError("No labelled rows found - fill the my_category column first.")
        # ---- honest evaluation BEFORE learning: how often did the app already agree with the user?
        ev_rows = [(byid.loc[t, "category"], cat, byid.loc[t, "cat_status"]) for t, cat, _k, _n in labelled if cat]
        agree = sum(1 for a, b, _s in ev_rows if a == b)
        by_status = {}
        for a, b, s in ev_rows:
            d = by_status.setdefault(s, [0, 0])
            d[0] += 1
            d[1] += int(a == b)
        evaluation = {"labelled_rows": len(ev_rows), "agreed": agree, "accuracy": round(agree / len(ev_rows), 3) if ev_rows else None,
                      "by_status": {s: {"rows": v[0], "accuracy": round(v[1] / v[0], 3)} for s, v in by_status.items()}}
        # ---- apply: merchant-level rule when all of a merchant's labels agree, else per-transaction locks
        per_m: dict[int, list] = {}
        for tid, cat, kind, nm in labelled:
            per_m.setdefault(int(byid.loc[tid, "merchant_id"]), []).append((tid, cat, kind, nm))
        n_rules = n_tx = n_kind = n_names = 0
        for mid, items in per_m.items():
            kinds = [k for _t, _c, k, _n in items if k]
            if kinds and kinds[0] in taxonomy.KINDS and len(set(kinds)) == 1:
                self.set_kind(mid, kinds[0])
                n_kind += 1
            names = [n for _t, _c, _k, n in items if n]
            if names:
                self.rename_merchant(mid, names[0])
                n_names += 1
            cats = [(t, c) for t, c, _k, _n in items if c]
            if not cats:
                continue
            total_rows = int((df["merchant_id"] == mid).sum())
            if len({c for _t, c in cats}) == 1 and (len(cats) >= max(1, int(0.6 * total_rows)) or len(cats) >= 3):
                try:
                    self.set_category(cats[0][1], merchant_id=mid, scope="merchant")
                    n_rules += 1
                    continue
                except ValueError:
                    pass
            for t, c in cats:
                try:
                    self.set_category(c, txn_id=t, scope="transaction")
                    n_tx += 1
                except ValueError:
                    pass
        res = self.retrain()
        return {"evaluation": evaluation, "applied": {"merchant_rules": n_rules, "transaction_labels": n_tx, "kinds": n_kind, "renames": n_names},
                "retrain": {k: res[k] for k in ("seconds", "n_train", "n_user", "n_changed_rows")}, "changed": res["changed"][:15]}

    def export_transactions_csv(self) -> bytes:
        df = self.df()
        cols = ["id", "date", "time", "merchant", "kind", "category", "cat_status", "direction", "amount", "description"]
        out = pd.DataFrame({"id": df["id"], "date": df["date"].dt.date, "time": df["ts"].dt.strftime("%H:%M").where(df["has_time"], ""),
                            "merchant": df["merchant"], "kind": df["kind"], "category": df["category"], "cat_status": df["cat_status"],
                            "direction": df["direction"], "amount": df["amount"], "description": df["description"].map(_mask_free)})
        return out[cols].to_csv(index=False).encode("utf-8")

    def learning_log(self, limit: int = 100) -> dict:
        c = self.conn()
        try:
            corr = [dict(r) for r in c.execute("SELECT c.*, m.canonical AS merchant FROM corrections c LEFT JOIN merchants m ON m.id=c.merchant_id ORDER BY c.id DESC LIMIT ?", (limit,))]
            runs = [dict(r) for r in c.execute("SELECT * FROM model_runs ORDER BY id DESC LIMIT 20")]
            rules = [dict(r) for r in c.execute("SELECT r.*, m.canonical AS merchant FROM rules r LEFT JOIN merchants m ON m.id=r.merchant_id ORDER BY r.created_at DESC")]
            kinds = [dict(r) for r in c.execute("SELECT id AS merchant_id, canonical AS merchant, kind FROM merchants WHERE kind_source='user' ORDER BY canonical")]
            locked = [dict(r) for r in c.execute("SELECT t.id, t.date, t.amount, t.category, m.canonical AS merchant FROM transactions t JOIN merchants m ON m.id=t.merchant_id WHERE t.locked=1 ORDER BY t.date DESC")]
        finally:
            c.close()
        for r in runs:
            r["changed"] = json.loads(r.pop("changed_json") or "[]")
        return {"corrections": corr, "runs": runs, "rules": rules, "kinds": kinds, "locked": locked, "status": self.status()}


def _mask_free(s: str | None) -> str:
    import re
    return re.sub(r"[A-Za-z0-9._\-]{1,63}@[A-Za-z][A-Za-z0-9]{1,23}", lambda m: mask_vpa(m.group(0)), s or "")
