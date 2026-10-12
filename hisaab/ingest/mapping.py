"""Find the table in a raw grid and work out what each column means.

The hard parts, and how they are handled:
  * junk above / below the table      -> the header row is the row whose cells match the most known header words
  * different words for the same thing -> a small rule table (Narration / Particulars / Transaction Remarks -> description)
  * how debit vs credit is encoded     -> 5 modes: split columns | signed amount | amount + Type column | "120.00(Dr)" suffix |
                                         unsigned (direction must be inferred from the balance or the wording)
  * repeated headers, footers, wrapped description lines are handled while extracting rows (importer.py)

The result is a `Mapping` the user can see and override in the UI - we never hide a guess.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .parsing import is_null, looks_like_date, parse_amount

# (field, regex on the normalised header).  ORDER MATTERS: first match wins.
_HEADER_RULES: list[tuple[str, re.Pattern]] = [(f, re.compile(p)) for f, p in [
    ("value_date", r"\bvalue (date|dt)\b"),
    ("time", r"^(txn |transaction |tran )?time$"),
    ("date", r"^(txn |transaction |tran |trans |posting |booking )?(date|dt)$|\b(txn|transaction|tran|posting|booking) date\b"),
    ("balance", r"\b(closing |running |available )?bal(ance)?\b"),
    ("status", r"\bstatus\b"),
    ("ref", r"\b(ref|reference|utr|rrn|chq|cheque|instrument)\b|\b(transaction|txn) id\b"),
    ("debit", r"\b(withdraw\w*|debit\w*|dr|paid out|money out|outflow|spent)\b"),
    ("credit", r"\b(deposit\w*|credit\w*|cr|paid in|money in|inflow|received)\b"),
    ("type", r"^(txn |transaction )?type$|\bdr ?cr\b|\bcr ?dr\b|\bdebit credit\b"),
    ("amount", r"\b(amount|amt)\b"),
    ("desc", r"\b(description|narration|particulars|details|remarks?|memo|narrative|merchant|payee)\b"),
]]
_IGNORE = re.compile(r"^(s ?no|sr ?no|srl ?no|serial|sl ?no|sol|branch|your account|account|account no)\b")

_SUCCESS = {"success", "successful", "completed", "complete", "done", "settled", "approved", "paid", ""}


def norm_header(h: object) -> str:
    s = "" if h is None else str(h)
    s = re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
    return s


def classify_header(h: object) -> str | None:
    n = norm_header(h)
    if not n or _IGNORE.search(n):
        return None
    hits = [f for f, rx in _HEADER_RULES if rx.search(n)]
    if not hits:
        return None
    if "debit" in hits and "credit" in hits:        # "Withdrawal (Dr)/Deposit (Cr)" = ONE column holding both
        return "amount"
    return hits[0]


@dataclass
class Mapping:
    header_row: int
    cols: dict[str, list[int]] = field(default_factory=dict)
    amount_mode: str = "split"          # split | signed | type | suffix | unsigned
    headers: list[str] = field(default_factory=list)
    month_first: bool = False
    invert: bool = False                # flip debit/credit if the file uses the opposite sign convention
    confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)
    sheet: str = ""

    def to_dict(self) -> dict:
        return {"header_row": self.header_row, "cols": self.cols, "amount_mode": self.amount_mode, "headers": self.headers,
                "month_first": self.month_first, "invert": self.invert, "confidence": round(self.confidence, 2),
                "warnings": self.warnings, "sheet": self.sheet}

    @classmethod
    def from_dict(cls, d: dict) -> "Mapping":
        return cls(header_row=int(d["header_row"]), cols={k: [int(i) for i in v] for k, v in d["cols"].items()},
                   amount_mode=d.get("amount_mode", "split"), headers=list(d.get("headers", [])),
                   month_first=bool(d.get("month_first", False)), invert=bool(d.get("invert", False)),
                   confidence=float(d.get("confidence", 0.0)), warnings=list(d.get("warnings", [])), sheet=d.get("sheet", ""))


def _col(grid: list[list], idx: int, start: int, limit: int = 400) -> list:
    out = []
    for r in grid[start:start + limit]:
        out.append(r[idx] if idx < len(r) else None)
    return out


def find_header(grid: list[list]) -> tuple[int, dict[str, list[int]], float] | None:
    best = None
    for i, row in enumerate(grid[:80]):
        fields: dict[str, list[int]] = {}
        for j, cell in enumerate(row):
            f = classify_header(cell)
            if f:
                fields.setdefault(f, []).append(j)
        names = set(fields)
        has_money = bool(names & {"amount", "debit", "credit"})
        score = len(names) + (2 if "date" in names else 0) + (2 if has_money else 0) + (1 if "desc" in names else 0)
        if "date" in names and has_money and len(names) >= 3:
            if best is None or score > best[2]:
                best = (i, fields, score)
    return best


def _rate(vals: list, pred) -> float:
    vals = [v for v in vals if not is_null(v)]
    if not vals:
        return 0.0
    return sum(1 for v in vals if pred(v)) / len(vals)


def _is_num(v) -> bool:
    return parse_amount(v)[0] is not None


def infer_mapping(grid: list[list], sheet: str = "") -> Mapping | None:
    """Return a Mapping or None if no transaction table is recognisable."""
    found = find_header(grid)
    if not found:
        return _infer_headerless(grid, sheet)
    hr, fields, score = found
    headers = [("" if c is None else str(c).strip()) for c in grid[hr]]
    m = Mapping(header_row=hr, headers=headers, sheet=sheet)
    # dates: prefer a real 'date' column; if several, the first
    cols: dict[str, list[int]] = {k: list(v) for k, v in fields.items()}
    if "value_date" in cols and "date" not in cols:
        cols["date"] = cols.pop("value_date")
    cols.pop("value_date", None)
    data_start = hr + 1
    # validate the date column by content
    date_idx = cols["date"][0]
    d_rate = _rate(_col(grid, date_idx, data_start), looks_like_date)
    if d_rate < 0.6:
        # maybe the date column is another one
        for j in range(len(headers)):
            if _rate(_col(grid, j, data_start), looks_like_date) >= 0.8:
                cols["date"] = [j]
                m.warnings.append(f"Header said column {date_idx + 1} is the date but its values are not dates; using column {j + 1}.")
                break
        else:
            m.warnings.append("The date column has many unreadable dates.")
    if "desc" not in cols:
        # longest text column that is not numeric / date
        best_j, best_len = None, 0.0
        for j in range(len(headers)):
            if any(j in v for v in cols.values()):
                continue
            vals = [str(v) for v in _col(grid, j, data_start) if not is_null(v)]
            if not vals or _rate(vals, _is_num) > 0.5:
                continue
            avg = sum(len(v) for v in vals) / len(vals)
            if avg > best_len:
                best_j, best_len = j, avg
        if best_j is not None:
            cols["desc"] = [best_j]
            m.warnings.append(f"No description header found; using column {best_j + 1}.")
    # amount mode
    if "debit" in cols and "credit" in cols:
        m.amount_mode = "split"
    elif "amount" in cols and "type" in cols:
        m.amount_mode = "type"
    elif "amount" in cols:
        vals = _col(grid, cols["amount"][0], data_start)
        signs = [parse_amount(v)[1] for v in vals if not is_null(v)]
        if any(s in ("dr", "cr") for s in signs):
            m.amount_mode = "suffix"
        elif any(s == "-" for s in signs):
            m.amount_mode = "signed"
        else:
            m.amount_mode = "unsigned"
    elif "debit" in cols or "credit" in cols:
        m.amount_mode = "split"
        m.warnings.append("Only one of Debit/Credit columns found.")
    m.cols = cols
    if "date" not in cols or not (set(cols) & {"amount", "debit", "credit"}):
        return None
    # day-first unless the data proves otherwise
    m.month_first = False
    from .parsing import infer_month_first
    m.month_first = infer_month_first(_col(grid, cols["date"][0], data_start))
    if m.amount_mode == "unsigned" and "balance" not in cols:
        m.warnings.append("The amount column has no sign / Dr-Cr marker and there is no balance column; direction will be guessed from the wording.")
    m.confidence = min(1.0, 0.35 + 0.1 * len(cols) + 0.2 * d_rate)
    return m


def _infer_headerless(grid: list[list], sheet: str) -> Mapping | None:
    """No header row: guess from content (date column, numeric columns, longest text column)."""
    rows = [r for r in grid[:300] if any(not is_null(c) for c in r)]
    if len(rows) < 5:
        return None
    ncol = max(len(r) for r in rows)
    date_cols = [j for j in range(ncol) if _rate([r[j] if j < len(r) else None for r in rows], looks_like_date) >= 0.8]
    if not date_cols:
        return None
    num_cols = [j for j in range(ncol) if j not in date_cols and _rate([r[j] if j < len(r) else None for r in rows], _is_num) >= 0.5]
    text_cols = [j for j in range(ncol) if j not in date_cols and j not in num_cols]
    if not num_cols or not text_cols:
        return None
    desc = max(text_cols, key=lambda j: sum(len(str(r[j])) for r in rows if j < len(r) and r[j] is not None))
    m = Mapping(header_row=-1, sheet=sheet, headers=[f"col{j + 1}" for j in range(ncol)])
    m.cols = {"date": [date_cols[0]], "desc": [desc]}
    if len(num_cols) >= 3:
        m.cols.update({"debit": [num_cols[0]], "credit": [num_cols[1]], "balance": [num_cols[2]]})
        m.amount_mode = "split"
    else:
        m.cols["amount"] = [num_cols[0]]
        signs = [parse_amount(r[num_cols[0]])[1] for r in rows if num_cols[0] < len(r)]
        m.amount_mode = "signed" if any(s == "-" for s in signs) else "unsigned"
    m.warnings.append("No header row was found; columns were guessed from their content. Please check the mapping.")
    m.confidence = 0.4
    return m
