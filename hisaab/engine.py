"""The Engine: one object that owns the database and the models and exposes everything the web layer needs.

Flow of an import:
   file bytes -> importer (grid, mapping, rows) -> de-duplicate -> stage1 (parse + merchant resolution) -> INSERT
   -> recompute()  (merchant kind + category for ALL transactions, because new data can change what we believe)

Flow of a correction:
   user says "Ramesh Kirana is Groceries" -> memory rule (instant, certain) -> recompute() -> model marked stale
   -> retrain() (debounced by the UI) -> recompute() -> report which OTHER merchants changed.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from . import config, seeds, store, taxonomy
from .core import categorize as cat_mod
from .core import features, p2p, pipeline, seed_data
from .core.model import CategoryModel, effective_params
from .core.upi_parse import parse as parse_narration
from .ingest import importer
from .ingest.readers import ReadError
from .textutil import mask_vpa, na, norm_ws
from .views import ViewsMixin

_NOW = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")


def row_key(r: importer.RawRow, occurrence: int) -> str:
    """Identity of a transaction for de-duplication.  A 12-digit UPI reference is globally unique (and also appears in app
    exports), so rows that carry one are keyed by (reference, amount, direction) - re-importing the same statement, or
    importing the bank statement AND the app export of the same payments, never double counts.  Rows without a
    reference are keyed by their content plus an occurrence counter (two genuine identical rows in one file are kept)."""
    rrn = None
    if r.ref and re.fullmatch(r"\d{12}", r.ref):
        rrn = r.ref
    else:
        rrn = parse_narration(r.desc).rrn
        if rrn and not re.fullmatch(r"\d{12}", rrn):
            rrn = None
    if rrn:
        base = f"rrn|{rrn}|{r.amount:.2f}|{r.direction}"
    else:
        base = f"row|{r.ts.date().isoformat()}|{r.ts.strftime('%H%M%S') if r.has_time else ''}|{r.amount:.2f}|{r.direction}|{norm_ws(r.desc).lower()}|{occurrence}"
    return hashlib.sha1(base.encode()).hexdigest()


class Engine(ViewsMixin):
    def __init__(self, data_dir: Path | str | None = None) -> None:
        if data_dir is not None:
            d = Path(data_dir)
            self.data_dir, self.db_path, self.models_dir = d, d / "hisaab.db", d / "models"
        else:
            self.data_dir, self.db_path, self.models_dir = config.DATA_DIR, config.DB_PATH, config.MODELS_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._cat_model: CategoryModel | None = None
        self._kind_model: p2p.KindModel | None = None
        self._version = 0
        self._df_cache: tuple[int, pd.DataFrame] | None = None
        c = self.conn()
        store.init(c)
        c.close()

    # ---- plumbing -------------------------------------------------------------------------------------------------------
    def conn(self):
        return store.connect(self.db_path)

    def _bump(self) -> None:
        self._version += 1
        self._df_cache = None

    def reset(self) -> None:
        """Delete ALL user data (transactions, merchants, rules, models). Used by 'Delete all my data'."""
        with self._lock:
            for p in (self.db_path, Path(str(self.db_path) + "-wal"), Path(str(self.db_path) + "-shm")):
                if p.exists():
                    p.unlink()
            for p in self.models_dir.glob("*"):
                p.unlink()
            self._cat_model = self._kind_model = None
            self._bump()
            c = self.conn()
            store.init(c)
            c.close()

    # ---- models ---------------------------------------------------------------------------------------------------------
    def kind_seed_arrays(self) -> tuple[np.ndarray, np.ndarray]:
        for path in (config.SEED_DIR / "kind_seed.npz", self.models_dir / "kind_seed.npz"):
            if path.exists():
                z = np.load(path)
                return z["X"], z["y"]
        m = p2p.train_seed_arrays()
        np.savez(self.models_dir / "kind_seed.npz", X=m[0], y=m[1])
        return m

    def kind_model(self) -> p2p.KindModel:
        if self._kind_model is None:
            X, y = self.kind_seed_arrays()
            self._kind_model = p2p.KindModel().fit(X, y)
        return self._kind_model

    def cat_model(self) -> CategoryModel:
        if self._cat_model is None:
            path = self.models_dir / "category_current.joblib"
            if path.exists():
                try:
                    self._cat_model = joblib.load(path)
                    if getattr(self._cat_model, "sig", None) != effective_params():           # saved by another version / other settings
                        self._cat_model = None
                except Exception:
                    self._cat_model = None
            if self._cat_model is None:
                self._cat_model = CategoryModel().fit(seed_data.build())
                joblib.dump(self._cat_model, self.models_dir / "category_current.joblib")
                self._mark_dirty_if_taught()
        return self._cat_model

    def _mark_dirty_if_taught(self) -> None:
        """A seed-only model was just rebuilt: if the user has already taught the app things, say so (the UI offers 'Re-learn now')."""
        if not self.db_path.exists():
            return
        c = self.conn()
        try:
            taught = c.execute("SELECT (SELECT COUNT(*) FROM rules) + (SELECT COUNT(*) FROM merchants WHERE kind_source='user')").fetchone()[0]
            if taught:
                store.set_setting(c, "model_dirty", True)
                c.commit()
        except Exception:
            pass
        finally:
            c.close()

    # ---- reading --------------------------------------------------------------------------------------------------------
    def df(self) -> pd.DataFrame:
        """All transactions joined with their merchant, as a DataFrame (cached until the next write)."""
        if self._df_cache and self._df_cache[0] == self._version:
            return self._df_cache[1]
        c = self.conn()
        try:
            df = pd.read_sql_query(
                "SELECT t.*, m.canonical AS merchant, m.kind AS kind, m.kind_conf AS kind_conf, m.kind_source AS kind_source, "
                "m.p_person AS p_person, m.is_brand AS is_brand, m.is_local AS is_local, m.placeholder AS placeholder "
                "FROM transactions t LEFT JOIN merchants m ON m.id = t.merchant_id ORDER BY t.ts, t.id", c)
        finally:
            c.close()
        if not df.empty:
            df["ts"] = pd.to_datetime(df["ts"])
            df["date"] = pd.to_datetime(df["date"])
            df["has_time"] = df["has_time"].astype(bool)
            df["hour"] = np.where(df["has_time"], df["ts"].dt.hour, np.nan)
            df["weekday"] = df["ts"].dt.weekday
            df["month"] = df["ts"].dt.strftime("%Y-%m")
            df["flags"] = df["flags"].map(lambda s: json.loads(s) if s else [])
            df["category"] = df["category"].fillna(taxonomy.UNCATEGORISED)          # defensive: never hand out NULLs to the UI
            df["cat_status"] = df["cat_status"].fillna("unknown")
            df["cat_source"] = df["cat_source"].fillna("none")
            df["kind"] = df["kind"].fillna("merchant")
            df["kind_source"] = df["kind_source"].fillna("model")
            df["merchant"] = df["merchant"].fillna("Unknown payee")
            for col in ("cat_conf", "kind_conf", "p_person"):
                df[col] = df[col].fillna(0.0)
            for col in ("is_brand", "is_local", "placeholder", "locked"):
                df[col] = df[col].fillna(0).astype(bool)
        self._df_cache = (self._version, df)
        return df

    def status(self) -> dict:
        c = self.conn()
        try:
            n = c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
            rng = c.execute("SELECT MIN(date), MAX(date) FROM transactions").fetchone()
            merchants = c.execute("SELECT COUNT(*) FROM merchants WHERE id IN (SELECT DISTINCT merchant_id FROM transactions)").fetchone()[0]
            rules = c.execute("SELECT COUNT(*) FROM rules").fetchone()[0]
            user_kinds = c.execute("SELECT COUNT(*) FROM merchants WHERE kind_source='user'").fetchone()[0]
            corr = c.execute("SELECT COUNT(*) FROM corrections").fetchone()[0]
            imports = c.execute("SELECT COUNT(*) FROM imports").fetchone()[0]
            stale = store.get_setting(c, "model_dirty", False)
            last_run = c.execute("SELECT * FROM model_runs ORDER BY id DESC LIMIT 1").fetchone()
        finally:
            c.close()
        cm = self._cat_model
        return {"transactions": n, "date_min": rng[0], "date_max": rng[1], "merchants": merchants, "rules": rules,
                "user_kinds": user_kinds, "corrections": corr, "imports": imports, "model_stale": bool(stale),
                "model": {"loaded": cm is not None, "n_train": getattr(cm, "n_train", None), "n_user": getattr(cm, "n_user", 0),
                          "classes": getattr(cm, "classes_", []), "last_run": dict(last_run) if last_run else None}}

    # ---- importing ------------------------------------------------------------------------------------------------------
    def preview(self, data: bytes, filename: str, mapping: dict | None = None) -> dict:
        pv = importer.read_statement(data, filename, mapping)
        keyed = self._keyed(pv.rows)
        c = self.conn()
        try:
            existing = self._existing_keys(c, [k for k, _ in keyed])
        finally:
            c.close()
        sample = []
        for r in pv.rows[:8]:
            sample.append({"date": r.ts.date().isoformat(), "time": r.ts.strftime("%H:%M") if r.has_time else "",
                           "description": mask_vpa_in_text(r.desc)[:140], "amount": r.amount, "direction": r.direction})
        return {"mapping": pv.mapping.to_dict(), "report": pv.report.to_dict(), "sample": sample,
                "n_rows": len(pv.rows), "n_duplicates": sum(1 for k, _ in keyed if k in existing)}

    @staticmethod
    def _keyed(rows: list[importer.RawRow]) -> list[tuple[str, importer.RawRow]]:
        seen: dict[str, int] = {}
        out = []
        for r in rows:
            probe = row_key(r, 0)
            k_idx = seen.get(probe, 0)
            seen[probe] = k_idx + 1
            out.append((row_key(r, k_idx) if k_idx else probe, r))
        return out

    @staticmethod
    def _existing_keys(c, keys: list[str]) -> set[str]:
        found: set[str] = set()
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            q = ",".join("?" * len(chunk))
            found.update(r[0] for r in c.execute(f"SELECT row_key FROM transactions WHERE row_key IN ({q})", chunk))
        return found

    def commit(self, data: bytes, filename: str, mapping: dict | None = None) -> dict:
        res = self.commit_many([(data, filename, mapping)])
        if res["files"][0].get("error"):
            raise ValueError(res["files"][0]["error"])
        return {**res["files"][0], "status": res["status"], "changes": res["changes"]}

    def commit_many(self, files: list[tuple[bytes, str, dict | None]]) -> dict:
        """Import several statements in ONE database transaction and classify once at the end (classifying is the slow part).
        A file that cannot be read is reported and skipped; the others are still imported. Rows repeated across the files
        (overlapping statements, or a bank statement plus the app export) are stored once."""
        with self._lock:
            results = []
            c = self.conn()
            try:
                for data, filename, mapping in files:
                    try:
                        pv = importer.read_statement(data, filename, mapping)
                    except (ValueError, ReadError) as e:
                        results.append({"filename": Path(filename).name, "error": str(e), "new": 0, "duplicates": 0})
                        continue
                    results.append({"filename": Path(filename).name, **self._insert(c, pv, filename)})
                changes = self._recompute(c)          # classify in the SAME transaction: no reader ever sees unclassified rows
                c.commit()
            finally:
                c.close()
            self._bump()
            return {"files": results, "new": sum(r["new"] for r in results), "duplicates": sum(r["duplicates"] for r in results),
                    "status": self.status(), "changes": changes}

    def _insert(self, c, pv, filename: str) -> dict:
        keyed = self._keyed(pv.rows)
        existing = self._existing_keys(c, [k for k, _ in keyed])
        new_pairs = [(k, r) for k, r in keyed if k not in existing]
        # an app export of an already-imported bank row adds the missing time-of-day
        upgraded = 0
        for k, r in keyed:
            if k in existing and r.has_time:
                cur = c.execute("UPDATE transactions SET ts=?, has_time=1 WHERE row_key=? AND has_time=0",
                                (r.ts.isoformat(sep=" "), k))
                upgraded += cur.rowcount
        cur = c.execute("INSERT INTO imports(filename, imported_at, n_rows, n_new, n_dupes, date_min, date_max, mapping_json, report_json) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (Path(filename).name[:120], _NOW(), len(pv.rows), len(new_pairs), len(pv.rows) - len(new_pairs),
                         pv.report.date_min, pv.report.date_max, json.dumps(pv.mapping.to_dict()), json.dumps(pv.report.to_dict())))
        import_id = cur.lastrowid
        if new_pairs:
            reg = store.load_registry(c)
            st = pipeline.stage1([r for _, r in new_pairs], reg)
            key_by_src = {r.src_row: k for k, r in new_pairs}
            raw_by_src = {r.src_row: r.raw for _, r in new_pairs}
            rows = []
            for rec in st.df.itertuples(index=False):
                rows.append((key_by_src[rec.src_row], import_id, int(rec.src_row), rec.ts.isoformat(sep=" "), int(rec.has_time),
                             rec.ts.date().isoformat(), rec.desc, float(rec.amount), rec.direction, na(rec.balance), na(rec.ref),
                             na(rec.rrn), rec.mode, na(rec.marker), na(rec.vpa), na(rec.handle), na(rec.app), na(rec.phone),
                             rec.payee_raw, rec.payee_source, float(rec.name_score), rec.remarks, json.dumps(rec.flags),
                             float(rec.parse_conf), na(rec.special), rec.match_method, float(rec.match_conf), int(rec.merchant_id),
                             json.dumps(raw_by_src.get(rec.src_row, {}))))
            store.save_registry(c, reg)
            c.executemany(
                "INSERT INTO transactions(row_key, import_id, src_row, ts, has_time, date, description, amount, direction, balance, ref, rrn, "
                "mode, marker, vpa, handle, app, phone, payee_raw, payee_source, name_score, remarks, flags, parse_conf, special, "
                "match_method, match_conf, merchant_id, raw_json) VALUES (" + ",".join("?" * 29) + ")", rows)
        return {"import_id": import_id, "rows_in_file": len(pv.rows), "new": len(new_pairs), "duplicates": len(pv.rows) - len(new_pairs),
                "time_added_to_existing": upgraded, "report": pv.report.to_dict(), "mapping": pv.mapping.to_dict()}

    def load_demo(self) -> dict:
        from .synth import demo_world, layouts
        w = demo_world()
        df, _truth, lay = layouts.render(w, "demo")
        buf = io.StringIO()
        df.to_csv(buf, index=False)
        return self.commit(buf.getvalue().encode("utf-8"), "demo_statement.csv")

    # ---- derive: kind + category for everything --------------------------------------------------------------------------
    def _load_state(self, c):
        merchants = {r["id"]: dict(r) for r in c.execute("SELECT * FROM merchants")}
        rules = {(r["merchant_id"], r["direction"]): r["category"] for r in c.execute("SELECT * FROM rules")}
        return merchants, rules

    def _stage_df(self, c) -> pd.DataFrame:
        """Stage1-shaped DataFrame from the stored transactions (no re-parsing)."""
        df = pd.read_sql_query("SELECT * FROM transactions ORDER BY ts, id", c)
        if df.empty:
            return df
        df["ts"] = pd.to_datetime(df["ts"])
        df["has_time"] = df["has_time"].astype(bool)
        df["hour"] = np.where(df["has_time"], df["ts"].dt.hour, np.nan)
        df["weekday"] = df["ts"].dt.weekday
        df["flags"] = df["flags"].map(lambda s: json.loads(s) if s else [])
        return df

    def recompute(self) -> dict:
        """Recompute kind + category for every merchant and write them back. Returns what changed."""
        with self._lock:
            c = self.conn()
            try:
                res = self._recompute(c)
                c.commit()
            finally:
                c.close()
            self._bump()
            return res

    def _recompute(self, c) -> dict:
        """The actual work, on the CALLER's connection and WITHOUT committing - so an import can insert and classify in ONE transaction
        (readers never see rows that are stored but not yet classified)."""
        merchants, rules = self._load_state(c)
        df = self._stage_df(c)
        if df.empty:
            return {"changed": [], "n_changed_rows": 0}
        before = {(r["merchant_id"], r["direction"]): r["category"] for r in c.execute(
            "SELECT merchant_id, direction, MAX(category) AS category FROM transactions GROUP BY merchant_id, direction")}
        canon = {mid: m["canonical"] for mid, m in merchants.items()}
        is_brand = {mid: bool(m["is_brand"]) for mid, m in merchants.items()}
        brand_cat = {mid: m["brand_category"] for mid, m in merchants.items()}
        placeholders = {mid for mid, m in merchants.items() if m["placeholder"]}
        user_kinds = {mid: m["kind"] for mid, m in merchants.items() if m["kind_source"] == "user" and m["kind"]}
        kinds = p2p.classify(df, canon, is_brand, self.kind_model(), user_kinds, placeholders)
        frame = features.frame_from_stage1(df, canon, {m: k.kind for m, k in kinds.items()}, placeholders)
        cats = cat_mod.categorize(df, frame, canon, kinds, is_brand, brand_cat, rules, self.cat_model())
        for mid, k in kinds.items():
            c.execute("UPDATE merchants SET kind=?, kind_conf=?, kind_source=?, p_person=?, is_local=? WHERE id=?",
                      (k.kind, round(k.confidence, 4), k.source, round(k.p_person, 4), int(k.kind == "merchant" and not is_brand.get(mid, False)), mid))
        for (mid, direction), r in cats.items():
            c.execute("UPDATE transactions SET category=?, cat_conf=?, cat_source=?, cat_status=? WHERE merchant_id=? AND direction=? AND locked=0",
                      (r.category, round(r.conf, 4), r.source, r.status, mid, direction))
        changed = []
        for (mid, direction), r in cats.items():
            old = before.get((mid, direction))
            if old is not None and old != r.category:
                n = int(((df["merchant_id"] == mid) & (df["direction"] == direction)).sum())
                changed.append({"merchant_id": mid, "merchant": canon[mid], "direction": direction, "from": old, "to": r.category, "rows": n, "source": r.source})
        return {"changed": changed, "n_changed_rows": sum(x["rows"] for x in changed)}

    # ---- learning ---------------------------------------------------------------------------------------------------------
    def _log(self, c, what, txn_id=None, merchant_id=None, old=None, new=None, scope=None, note=None) -> None:
        c.execute("INSERT INTO corrections(at, what, txn_id, merchant_id, old_value, new_value, scope, note) VALUES(?,?,?,?,?,?,?,?)",
                  (_NOW(), what, txn_id, merchant_id, old, new, scope, note))

    def set_category(self, category: str, txn_id: int | None = None, merchant_id: int | None = None, scope: str = "merchant") -> dict:
        """scope='merchant': remember it for this merchant+direction (and every payment, now and future).
           scope='transaction': only this one payment (locked)."""
        if category not in taxonomy.NAMES:
            raise ValueError(f"Unknown category: {category}")
        with self._lock:
            c = self.conn()
            try:
                if txn_id is not None:
                    t = c.execute("SELECT id, merchant_id, direction, category FROM transactions WHERE id=?", (txn_id,)).fetchone()
                    if not t:
                        raise ValueError("No such transaction")
                    merchant_id, direction, old = t["merchant_id"], t["direction"], t["category"]
                else:
                    t = c.execute("SELECT direction, MAX(category) AS category FROM transactions WHERE merchant_id=? "
                                  "GROUP BY direction ORDER BY SUM(amount) DESC LIMIT 1", (merchant_id,)).fetchone()
                    if not t:
                        raise ValueError("No such merchant")
                    direction, old = t["direction"], t["category"]
                allowed = taxonomy.ALLOWED_FOR_DEBIT if direction == "debit" else taxonomy.ALLOWED_FOR_CREDIT
                if category not in allowed:
                    raise ValueError(f"'{category}' is not a valid category for a {'payment' if direction == 'debit' else 'credit'}.")
                if scope == "transaction":
                    c.execute("UPDATE transactions SET category=?, cat_conf=1.0, cat_source='user', cat_status='confirmed', locked=1 WHERE id=?",
                              (category, txn_id))
                else:
                    c.execute("INSERT INTO rules(merchant_id, direction, category, created_at) VALUES(?,?,?,?) "
                              "ON CONFLICT(merchant_id, direction) DO UPDATE SET category=excluded.category, created_at=excluded.created_at",
                              (merchant_id, direction, category, _NOW()))
                    c.execute("UPDATE transactions SET category=?, cat_conf=1.0, cat_source='user', cat_status='confirmed' "
                              "WHERE merchant_id=? AND direction=? AND locked=0", (category, merchant_id, direction))
                self._log(c, "category", txn_id, merchant_id, old, category, scope)
                store.set_setting(c, "model_dirty", True)
                c.commit()
            finally:
                c.close()
            self._bump()
            return {"ok": True, "merchant_id": merchant_id, "direction": direction, "category": category, "scope": scope, "model_stale": True}

    def set_kind(self, merchant_id: int, kind: str) -> dict:
        if kind not in taxonomy.KINDS:
            raise ValueError("kind must be one of " + ", ".join(taxonomy.KINDS))
        with self._lock:
            c = self.conn()
            try:
                m = c.execute("SELECT kind FROM merchants WHERE id=?", (merchant_id,)).fetchone()
                if not m:
                    raise ValueError("No such merchant")
                c.execute("UPDATE merchants SET kind=?, kind_source='user', kind_conf=1.0 WHERE id=?", (kind, merchant_id))
                # a kind change invalidates the old default categories of this merchant (e.g. Transfers (P2P) -> shop)
                c.execute("DELETE FROM rules WHERE merchant_id=? AND category IN ('Transfers (P2P)','Received from Friends')", (merchant_id,))
                self._log(c, "kind", None, merchant_id, m["kind"], kind, "merchant")
                store.set_setting(c, "model_dirty", True)
                c.commit()
            finally:
                c.close()
            self._bump()
            return self.recompute()

    def forget(self, what: str, merchant_id: int | None = None, direction: str | None = None, txn_id: int | None = None) -> dict:
        """UNDO a lesson. what = 'category' (a merchant rule), 'kind' (a shop/person answer) or 'payment' (a single-payment label).
        The thing goes back to being decided automatically; the model is marked stale so the next re-learning drops it too."""
        with self._lock:
            c = self.conn()
            try:
                if what == "category":
                    if merchant_id is None or direction not in ("debit", "credit"):
                        raise ValueError("merchant_id and direction are required")
                    row = c.execute("SELECT category FROM rules WHERE merchant_id=? AND direction=?", (merchant_id, direction)).fetchone()
                    if not row:
                        raise ValueError("No such rule")
                    c.execute("DELETE FROM rules WHERE merchant_id=? AND direction=?", (merchant_id, direction))
                    self._log(c, "forget", None, merchant_id, row["category"], None, "merchant", direction)
                elif what == "kind":
                    c.execute("UPDATE merchants SET kind_source='auto' WHERE id=? AND kind_source='user'", (merchant_id,))
                    self._log(c, "forget", None, merchant_id, "kind", None, "merchant")
                elif what == "payment":
                    c.execute("UPDATE transactions SET locked=0 WHERE id=?", (txn_id,))
                    self._log(c, "forget", txn_id, None, "payment label", None, "transaction")
                else:
                    raise ValueError("what must be category, kind or payment")
                store.set_setting(c, "model_dirty", True)
                c.commit()
            finally:
                c.close()
            self._bump()
            return self.recompute()

    def rename_merchant(self, merchant_id: int, name: str) -> dict:
        name = norm_ws(name)[:80]
        if not name:
            raise ValueError("Name cannot be empty")
        with self._lock:
            c = self.conn()
            try:
                m = c.execute("SELECT canonical FROM merchants WHERE id=?", (merchant_id,)).fetchone()
                if not m:
                    raise ValueError("No such merchant")
                c.execute("UPDATE merchants SET canonical=?, user_named=1 WHERE id=?", (name, merchant_id))
                self._log(c, "rename", None, merchant_id, m["canonical"], name, "merchant")
                c.commit()
            finally:
                c.close()
            self._bump()
            return {"ok": True}

    def merge_merchants(self, keep_id: int, drop_id: int) -> dict:
        if keep_id == drop_id:
            raise ValueError("Pick two different merchants")
        with self._lock:
            c = self.conn()
            try:
                reg = store.load_registry(c)
                if keep_id not in reg.entities or drop_id not in reg.entities:
                    raise ValueError("No such merchant")
                reg.merge(keep_id, drop_id)
                c.execute("UPDATE transactions SET merchant_id=? WHERE merchant_id=?", (keep_id, drop_id))
                c.execute("UPDATE merchant_keys SET merchant_id=? WHERE merchant_id=?", (keep_id, drop_id))
                c.execute("INSERT OR IGNORE INTO rules(merchant_id, direction, category, created_at) "
                          "SELECT ?, direction, category, created_at FROM rules WHERE merchant_id=?", (keep_id, drop_id))
                c.execute("DELETE FROM rules WHERE merchant_id=?", (drop_id,))
                c.execute("DELETE FROM merchant_names WHERE merchant_id=?", (drop_id,))
                c.execute("DELETE FROM merchants WHERE id=?", (drop_id,))
                store.save_registry(c, reg)
                for name, n in reg.entities[keep_id].names.items():
                    c.execute("INSERT INTO merchant_names(merchant_id,name,n) VALUES(?,?,?) ON CONFLICT(merchant_id,name) DO UPDATE SET n=excluded.n",
                              (keep_id, name, n))
                self._log(c, "merge", None, keep_id, str(drop_id), str(keep_id), "merchant")
                c.commit()
            finally:
                c.close()
            self._bump()
            return self.recompute()

    def _user_training_frames(self, c) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
        """Training rows contributed by the user: category labels (per merchant, capped) and kind labels."""
        merchants, rules = self._load_state(c)
        df = self._stage_df(c)
        empty = pd.DataFrame(columns=["name_text", "words_text", "ctx_text", "label", "weight", "src"])
        if df.empty:
            return empty, np.zeros((0, len(p2p.FEATURE_NAMES))), np.zeros(0)
        canon = {mid: m["canonical"] for mid, m in merchants.items()}
        placeholders = {mid for mid, m in merchants.items() if m["placeholder"]}
        kinds_now = {mid: (m["kind"] or "merchant") for mid, m in merchants.items()}
        frame = features.frame_from_stage1(df, canon, kinds_now, placeholders)
        rows = []
        for (mid, direction), cat in rules.items():
            if direction != "debit" or mid not in merchants or merchants[mid]["kind"] == "person":
                continue
            idx = df.index[(df["merchant_id"] == mid) & (df["direction"] == "debit")]
            if len(idx) == 0:
                continue
            sub = frame.loc[idx].drop_duplicates()
            sub = sub.head(8).copy()
            sub["label"], sub["weight"], sub["src"] = cat, config.USER_SAMPLE_WEIGHT, "user"
            rows.append(sub)
        # transaction-level corrections (locked rows)
        locked = df[(df["locked"] == 1) & (df["direction"] == "debit")]
        if len(locked):
            sub = frame.loc[locked.index].copy()
            sub["label"] = pd.read_sql_query("SELECT id, category FROM transactions WHERE locked=1", c).set_index("id").loc[locked["id"], "category"].to_numpy()
            sub["weight"], sub["src"] = config.USER_SAMPLE_WEIGHT / 2, "user"
            rows.append(sub)
        user_frame = pd.concat(rows) if rows else empty
        user_frame = user_frame[user_frame["label"].isin(cat_mod.MODEL_CLASSES_FOR_SHOPS + ["Others"])]
        # kind labels
        Xk, yk = [], []
        for mid, m in merchants.items():
            if m["kind_source"] == "user" and m["kind"] in ("merchant", "person"):
                g = df[df["merchant_id"] == mid]
                if len(g):
                    Xk.append(p2p.entity_features(g, "" if m["placeholder"] else m["canonical"]))
                    yk.append(1 if m["kind"] == "person" else 0)
        return user_frame, (np.array(Xk) if Xk else np.zeros((0, len(p2p.FEATURE_NAMES)))), np.array(yk)

    def retrain(self) -> dict:
        """Refit both models on seed knowledge + everything the user has taught us, re-apply, and report what changed."""
        with self._lock:
            t0 = time.time()
            c = self.conn()
            try:
                user_frame, Xk, yk = self._user_training_frames(c)
            finally:
                c.close()
            seed = seed_data.build()
            frame = pd.concat([seed, user_frame[seed.columns]], ignore_index=True) if len(user_frame) else seed
            self._cat_model = CategoryModel().fit(frame, n_user=len(user_frame))
            joblib.dump(self._cat_model, self.models_dir / "category_current.joblib")
            Xs, ys = self.kind_seed_arrays()
            if len(yk):
                w = np.concatenate([np.ones(len(ys)), np.full(len(yk), config.USER_SAMPLE_WEIGHT)])
                self._kind_model = p2p.KindModel().fit(np.vstack([Xs, Xk]), np.concatenate([ys, yk]), w)
            else:
                self._kind_model = p2p.KindModel().fit(Xs, ys)
            res = self.recompute()
            c = self.conn()
            try:
                store.set_setting(c, "model_dirty", False)
                c.execute("INSERT INTO model_runs(at, n_train, n_user, seconds, note, changed_json) VALUES(?,?,?,?,?,?)",
                          (_NOW(), len(frame), len(user_frame), round(time.time() - t0, 2), "retrain", json.dumps(res["changed"][:50])))
                c.commit()
            finally:
                c.close()
            res.update({"seconds": round(time.time() - t0, 2), "n_train": len(frame), "n_user": len(user_frame)})
            return res


def mask_vpa_in_text(s: str) -> str:
    """Hide phone numbers inside UPI addresses in any free text we send to the browser."""
    return re.sub(r"[A-Za-z0-9._\-]{1,63}@[A-Za-z][A-Za-z0-9]{1,23}", lambda m: mask_vpa(m.group(0)), s or "")
