"""Profiles: several people (or "me" and "demo") in one Hisaab, each with completely separate data.

Every profile is its own data folder with its own database and models, so nothing - transactions, merchants, learned rules -
can leak from one profile to another. The first profile ("default") uses the original data folder, so data from before
profiles existed simply becomes the default profile. Others live in data/profiles/<id>/.

The list of profiles and their details (name, monthly budget, income ...) is one small JSON file: data/profiles.json.
Which profile a browser is using is remembered in a cookie (see web/app.py), not here.
"""
from __future__ import annotations

import json
import re
import secrets
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .engine import Engine

DEFAULT_ID = "default"
MAX_PROFILES = 12
_ID_RE = re.compile(r"[a-z0-9]{1,16}")

# Details a profile can carry. type = how the value is checked; every one is optional.
FIELDS = {
    "name": str,             # shown in the sidebar
    "monthly_budget": float, # spending limit per month - the dashboard shows how much of it is used
    "monthly_income": float, # usual money in per month (pocket money, stipend, salary)
    "income_day": int,       # day of the month the money usually arrives (1-31)
    "goal": str,             # free text: what the person is saving for
}
_LIMITS = {"name": 40, "goal": 120}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_details(d: dict, partial: bool = True) -> dict:
    """Validate user input for the FIELDS above. Empty values clear a field. Raises ValueError with a readable message."""
    out: dict = {}
    for key, typ in FIELDS.items():
        if key not in d:
            continue
        v = d[key]
        if v is None or (isinstance(v, str) and not v.strip()):
            if key == "name":
                raise ValueError("A profile needs a name.")
            out[key] = None
            continue
        if typ is str:
            out[key] = " ".join(str(v).split())[:_LIMITS[key]]
        else:
            try:
                num = typ(float(v))
            except (TypeError, ValueError):
                raise ValueError(f"{key.replace('_', ' ').capitalize()} must be a number.")
            if num < 0:
                raise ValueError(f"{key.replace('_', ' ').capitalize()} cannot be negative.")
            if key == "income_day" and not 1 <= num <= 31:
                raise ValueError("Income day must be between 1 and 31.")
            out[key] = num
    if not partial and not out.get("name"):
        raise ValueError("A profile needs a name.")
    return out


class Profiles:
    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else config.DATA_DIR
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "profiles.json"
        self._lock = threading.RLock()
        self._engines: dict[str, Engine] = {}
        with self._lock:
            if not self._read():
                self._write([{"id": DEFAULT_ID, "name": "Me", "created_at": _now()}])

    # ---- storage ---------------------------------------------------------------------------------------------------------
    def _read(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return [p for p in data.get("profiles", []) if isinstance(p, dict) and _ID_RE.fullmatch(str(p.get("id", "")))]
        except (OSError, ValueError):
            return []

    def _write(self, profiles: list[dict]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"profiles": profiles}, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)          # atomic: a crash never leaves half a file

    def data_dir(self, pid: str) -> Path:
        return self.root if pid == DEFAULT_ID else self.root / "profiles" / pid

    # ---- queries ----------------------------------------------------------------------------------------------------------
    def all(self) -> list[dict]:
        with self._lock:
            return self._read()

    def get(self, pid: str | None) -> dict | None:
        return next((p for p in self.all() if p["id"] == pid), None)

    def resolve(self, pid: str | None) -> dict:
        """The profile a request asked for, or the first one if it no longer exists."""
        return self.get(pid) or self.all()[0]

    def engine(self, pid: str) -> Engine:
        with self._lock:
            eng = self._engines.get(pid)
            if eng is None:
                eng = self._engines[pid] = Engine(self.data_dir(pid))
            return eng

    # ---- changes ----------------------------------------------------------------------------------------------------------
    def create(self, details: dict) -> dict:
        d = clean_details(details, partial=False)
        with self._lock:
            profiles = self._read()
            if len(profiles) >= MAX_PROFILES:
                raise ValueError(f"You can have at most {MAX_PROFILES} profiles. Delete one first.")
            pid = secrets.token_hex(4)
            p = {"id": pid, **{k: v for k, v in d.items() if v is not None}, "created_at": _now()}
            profiles.append(p)
            self._write(profiles)
            return p

    def update(self, pid: str, details: dict) -> dict:
        d = clean_details(details)
        with self._lock:
            profiles = self._read()
            p = next((x for x in profiles if x["id"] == pid), None)
            if p is None:
                raise ValueError("That profile does not exist any more.")
            for k, v in d.items():
                if v is None:
                    p.pop(k, None)
                else:
                    p[k] = v
            self._write(profiles)
            return p

    def delete(self, pid: str) -> None:
        """Remove a profile and ALL its data. The default profile cannot be deleted (use 'Delete all my data' to empty it)."""
        if pid == DEFAULT_ID:
            raise ValueError("The first profile cannot be deleted. Use Import → Delete all my data to empty it.")
        with self._lock:
            profiles = self._read()
            if not any(x["id"] == pid for x in profiles):
                raise ValueError("That profile does not exist any more.")
            self._write([x for x in profiles if x["id"] != pid])
            self._engines.pop(pid, None)
            d = self.data_dir(pid)
            if d.exists():
                shutil.rmtree(d)
