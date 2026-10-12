"""SQLite persistence: schema + the few low-level helpers the engine needs.

Why SQLite?  One file, zero setup, ACID transactions, ships inside Python, and a single user's data (thousands of rows)
is far below its limits.  "Local-first": the file lives on the user's machine; deleting it deletes everything.
Plain SQL with parameters (never string-formatting user data into SQL).
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path

from .core.normalize import Entity, Registry

SCHEMA = """
CREATE TABLE IF NOT EXISTS imports(
  id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT, imported_at TEXT, n_rows INTEGER, n_new INTEGER, n_dupes INTEGER,
  date_min TEXT, date_max TEXT, mapping_json TEXT, report_json TEXT);

CREATE TABLE IF NOT EXISTS merchants(
  id INTEGER PRIMARY KEY, canonical TEXT NOT NULL, is_brand INTEGER DEFAULT 0, brand_key TEXT, brand_category TEXT,
  placeholder INTEGER DEFAULT 0, user_named INTEGER DEFAULT 0,
  kind TEXT, kind_conf REAL, kind_source TEXT, p_person REAL, is_local INTEGER DEFAULT 0);

CREATE TABLE IF NOT EXISTS merchant_keys(
  key TEXT PRIMARY KEY, merchant_id INTEGER NOT NULL REFERENCES merchants(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS merchant_names(
  merchant_id INTEGER NOT NULL REFERENCES merchants(id) ON DELETE CASCADE, name TEXT NOT NULL, n INTEGER NOT NULL,
  PRIMARY KEY (merchant_id, name));

CREATE TABLE IF NOT EXISTS transactions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, row_key TEXT UNIQUE NOT NULL, import_id INTEGER, src_row INTEGER,
  ts TEXT NOT NULL, has_time INTEGER DEFAULT 0, date TEXT NOT NULL,
  description TEXT, amount REAL NOT NULL, direction TEXT NOT NULL, balance REAL, ref TEXT, rrn TEXT,
  mode TEXT, marker TEXT, vpa TEXT, handle TEXT, app TEXT, phone TEXT, payee_raw TEXT, payee_source TEXT, name_score REAL,
  remarks TEXT, flags TEXT, parse_conf REAL, special TEXT, match_method TEXT, match_conf REAL,
  merchant_id INTEGER REFERENCES merchants(id),
  category TEXT, cat_conf REAL, cat_source TEXT, cat_status TEXT, locked INTEGER DEFAULT 0, raw_json TEXT);
CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_tx_merchant ON transactions(merchant_id);

CREATE TABLE IF NOT EXISTS rules(
  merchant_id INTEGER NOT NULL, direction TEXT NOT NULL, category TEXT NOT NULL, created_at TEXT,
  PRIMARY KEY (merchant_id, direction));
CREATE TABLE IF NOT EXISTS corrections(
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, what TEXT, txn_id INTEGER, merchant_id INTEGER, old_value TEXT,
  new_value TEXT, scope TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS model_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, n_train INTEGER, n_user INTEGER, seconds REAL, note TEXT, changed_json TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
"""


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def get_setting(conn, key: str, default=None):
    r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(r["value"]) if r else default


def set_setting(conn, key: str, value) -> None:
    conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, json.dumps(value)))


# ---- registry <-> tables ------------------------------------------------------------------------------------------------
def load_registry(conn: sqlite3.Connection) -> Registry:
    reg = Registry()
    for r in conn.execute("SELECT * FROM merchants"):
        e = Entity(id=r["id"], canonical=r["canonical"], is_brand=bool(r["is_brand"]), brand_key=r["brand_key"],
                   brand_category=r["brand_category"], user_named=bool(r["user_named"]), placeholder=bool(r["placeholder"]))
        reg.entities[e.id] = e
    for r in conn.execute("SELECT merchant_id, name, n FROM merchant_names"):
        if r["merchant_id"] in reg.entities:
            reg.entities[r["merchant_id"]].names[r["name"]] = r["n"]
    for r in conn.execute("SELECT key, merchant_id FROM merchant_keys"):
        if r["merchant_id"] in reg.entities:
            reg.keys[r["key"]] = r["merchant_id"]
            if r["key"].startswith("vpa:"):
                reg.entities[r["merchant_id"]].vpas.add(r["key"][4:])
    reg._next = (max(reg.entities) + 1) if reg.entities else 1
    reg.dirty.clear()
    return reg


def save_registry(conn: sqlite3.Connection, reg: Registry) -> None:
    for eid in sorted(reg.dirty):
        e = reg.entities.get(eid)
        if e is None:
            continue
        conn.execute(
            "INSERT INTO merchants(id, canonical, is_brand, brand_key, brand_category, placeholder, user_named) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET canonical=excluded.canonical, user_named=excluded.user_named",
            (e.id, e.canonical, int(e.is_brand), e.brand_key, e.brand_category, int(e.placeholder), int(e.user_named)))
        for name, n in e.names.items():
            conn.execute("INSERT INTO merchant_names(merchant_id, name, n) VALUES(?,?,?) "
                         "ON CONFLICT(merchant_id, name) DO UPDATE SET n=excluded.n", (e.id, name, n))
    keyrows = [(k, v) for k, v in reg.keys.items() if v in reg.dirty]
    conn.executemany("INSERT INTO merchant_keys(key, merchant_id) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET merchant_id=excluded.merchant_id",
                     keyrows)
    reg.dirty.clear()
