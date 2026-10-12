"""Turn a statement file into clean rows:  bytes -> grid -> mapping -> [RawRow].

Nothing is written to the database here; the caller can show a preview (detected mapping, sample rows, warnings) and
let the user correct the mapping before committing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time

from . import mapping as mp
from .parsing import is_null, parse_amount, parse_datetime, parse_time
from .readers import ReadError, read_grids

_DEBIT_WORDS = ("debit", "withdraw", "dr", "paid", "sent", "spent", "out")
_CREDIT_WORDS = ("credit", "deposit", "cr", "received", "in")


@dataclass
class RawRow:
    src_row: int
    ts: datetime
    has_time: bool
    desc: str
    amount: float
    direction: str                      # debit | credit
    balance: float | None = None
    ref: str | None = None
    raw: dict = field(default_factory=dict)
    guessed_direction: bool = False

    @property
    def date(self) -> date:
        return self.ts.date()


@dataclass
class Report:
    sheet: str = ""
    rows_in_table: int = 0
    accepted: int = 0
    blank: int = 0
    repeated_header: int = 0
    continuation_merged: int = 0
    failed_status: int = 0
    no_date_or_footer: int = 0
    bad_amount: int = 0
    direction_guessed: int = 0
    balance_checked: int = 0
    balance_matched: int = 0
    newest_first: bool = False
    date_min: str = ""
    date_max: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


@dataclass
class Preview:
    mapping: mp.Mapping
    rows: list[RawRow]
    report: Report


def _cell(row: list, i: int | None):
    if i is None or i >= len(row):
        return None
    return row[i]


def _ref_str(v) -> str | None:
    if is_null(v):
        return None
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    s = str(v).strip()
    if re.search(r"[eE]\+\d+$", s):         # Excel destroyed a long reference into 6.15E+11 - unrecoverable
        return None
    return s or None


def _is_repeat_header(row: list, headers: list[str]) -> bool:
    norm = [mp.norm_header(c) for c in row]
    h = [mp.norm_header(c) for c in headers]
    same = sum(1 for a, b in zip(norm, h) if a and a == b)
    return same >= max(2, int(0.6 * sum(1 for x in h if x)))


def extract(grid: list[list], m: mp.Mapping) -> tuple[list[RawRow], Report]:
    rep = Report(sheet=m.sheet)
    rows: list[RawRow] = []
    cols = m.cols
    di, ti = cols["date"][0], (cols.get("time") or [None])[0]
    desc_idx = cols.get("desc", [])
    dbi, cri = (cols.get("debit") or [None])[0], (cols.get("credit") or [None])[0]
    ami, tyi = (cols.get("amount") or [None])[0], (cols.get("type") or [None])[0]
    bai, sti = (cols.get("balance") or [None])[0], (cols.get("status") or [None])[0]
    ref_idx = cols.get("ref", [])
    start = m.header_row + 1
    for ridx in range(start, len(grid)):
        row = grid[ridx]
        if all(is_null(c) for c in row):
            rep.blank += 1
            continue
        rep.rows_in_table += 1
        if m.header_row >= 0 and _is_repeat_header(row, m.headers):
            rep.repeated_header += 1
            continue
        dt, has_time = parse_datetime(_cell(row, di), m.month_first)
        texts = [str(_cell(row, i)).strip() for i in desc_idx if not is_null(_cell(row, i))]
        if dt is None or not (1990 <= dt.year <= 2100):
            # wrapped description line (no date, no amount) -> glue to the previous transaction; else footer/junk
            money_here = any(parse_amount(_cell(row, i))[0] is not None for i in (dbi, cri, ami) if i is not None)
            if texts and rows and not money_here and not any(parse_amount(_cell(row, i))[0] for i in [bai] if i is not None):
                rows[-1].desc = (rows[-1].desc + " " + " ".join(texts)).strip()
                rep.continuation_merged += 1
            else:
                rep.no_date_or_footer += 1
            continue
        if ti is not None:
            t = parse_time(_cell(row, ti))
            if t is not None:
                dt = datetime.combine(dt.date(), t)
                has_time = True
        if sti is not None:
            st = str(_cell(row, sti) or "").strip().lower()
            if st not in mp._SUCCESS:
                rep.failed_status += 1
                continue
        amount, direction, guessed = None, None, False
        if m.amount_mode == "split":
            d, _ = parse_amount(_cell(row, dbi))
            c, _ = parse_amount(_cell(row, cri))
            if d and not c:
                amount, direction = d, "debit"
            elif c and not d:
                amount, direction = c, "credit"
            elif d and c:
                amount, direction = (d, "debit") if d >= c else (c, "credit")
        elif m.amount_mode == "signed":
            v, sign = parse_amount(_cell(row, ami))
            if v:
                amount, direction = v, ("debit" if sign == "-" else "credit")
        elif m.amount_mode == "type":
            v, sign = parse_amount(_cell(row, ami))
            ty = str(_cell(row, tyi) or "").strip().lower()
            if v:
                amount = v
                if sign == "-" or any(ty.startswith(w) or w in ty for w in ("debit", "withdraw", "paid", "sent")) or ty in ("dr", "d"):
                    direction = "debit"
                elif any(w in ty for w in ("credit", "deposit", "received")) or ty in ("cr", "c"):
                    direction = "credit"
                else:
                    direction = "debit" if sign == "-" else None
        elif m.amount_mode == "suffix":
            v, sign = parse_amount(_cell(row, ami))
            if v:
                amount = v
                direction = {"dr": "debit", "-": "debit", "cr": "credit", "+": "credit"}.get(sign or "", None)
        else:                                                       # unsigned
            v, sign = parse_amount(_cell(row, ami))
            if v:
                amount = v
                direction = "debit" if sign == "-" else None
        if not amount or amount <= 0:
            rep.bad_amount += 1
            continue
        if m.invert and direction:
            direction = "credit" if direction == "debit" else "debit"
        bal = parse_amount(_cell(row, bai))[0] if bai is not None else None
        ref = next((r for r in (_ref_str(_cell(row, i)) for i in ref_idx) if r), None)
        desc = " ; ".join(dict.fromkeys(texts))
        raw = {(m.headers[j] if j < len(m.headers) and m.headers[j] else f"col{j + 1}"): str(c)[:200]
               for j, c in enumerate(row) if not is_null(c)}
        rows.append(RawRow(src_row=len(rows), ts=dt, has_time=has_time, desc=desc, amount=round(float(amount), 2),
                           direction=direction or "", balance=bal, ref=ref, raw=raw))
    rep.accepted = len(rows)
    return rows, rep


def _order_and_infer(rows: list[RawRow], rep: Report) -> list[RawRow]:
    """Put rows in chronological order (files may be newest-first), then fill in unknown directions."""
    if len(rows) >= 2:
        dec = sum(1 for a, b in zip(rows, rows[1:]) if b.ts.date() < a.ts.date())
        inc = sum(1 for a, b in zip(rows, rows[1:]) if b.ts.date() > a.ts.date())
        if dec > inc:
            rep.newest_first = True
            rows = rows[::-1]
    rows = sorted(rows, key=lambda r: r.ts)                      # stable: keeps the file order within one timestamp
    # unknown directions -> balance delta, else wording
    prev_bal: float | None = None
    from ..core.upi_parse import parse as parse_desc
    for r in rows:
        if not r.direction:
            if r.balance is not None and prev_bal is not None and abs(abs(r.balance - prev_bal) - r.amount) < 0.011:
                r.direction = "credit" if r.balance > prev_bal else "debit"
            else:
                hint = parse_desc(r.desc).dir_hint
                r.direction = hint or "debit"
                r.guessed_direction = True
                rep.direction_guessed += 1
        if r.balance is not None:
            if prev_bal is not None:
                rep.balance_checked += 1
                exp = prev_bal + (r.amount if r.direction == "credit" else -r.amount)
                if abs(exp - r.balance) < 0.011:
                    rep.balance_matched += 1
            prev_bal = r.balance
    return rows


def read_statement(data: bytes, filename: str, mapping_override: dict | None = None) -> Preview:
    grids = read_grids(data, filename)
    best: tuple[float, mp.Mapping, list[list]] | None = None
    if mapping_override:
        m = mp.Mapping.from_dict(mapping_override)
        for name, g in grids:
            if not m.sheet or m.sheet == name:
                m.sheet = name
                best = (1.0, m, g)
                break
    else:
        for name, g in grids:
            m = mp.infer_mapping(g, name)
            if m is None:
                continue
            score = m.confidence * (1 + min(len(g), 5000) / 5000)
            if best is None or score > best[0]:
                best = (score, m, g)
    if best is None:
        raise ReadError("I could not find a transaction table in this file. It needs at least a date column and an amount "
                        "(or debit/credit) column. Try the CSV/Excel download from your bank's net-banking site.")
    _, m, g = best
    rows, rep = extract(g, m)
    if not rows:
        raise ReadError("The table was found but no valid transaction rows could be read from it. Check the column mapping.")
    rows = _order_and_infer(rows, rep)
    rep.date_min, rep.date_max = rows[0].ts.date().isoformat(), rows[-1].ts.date().isoformat()
    if rep.balance_checked >= 10 and rep.balance_matched / rep.balance_checked < 0.8:
        rep.warnings.append("The running balance does not reconcile with the amounts for many rows - double-check which column is "
                            "debit and which is credit (use 'swap debit/credit' if they are reversed).")
    if rep.direction_guessed:
        rep.warnings.append(f"{rep.direction_guessed} rows had no clear debit/credit marker; direction was inferred from wording/balance.")
    if rep.failed_status:
        rep.warnings.append(f"{rep.failed_status} failed/pending rows were ignored (no money moved).")
    rep.warnings.extend(m.warnings)
    return Preview(mapping=m, rows=rows, report=rep)
