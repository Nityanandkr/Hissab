"""Cell-level parsers for statement files: amounts, dates, times.  Pure functions, never raise.

Statements are written for humans: "1,23,456.00" (Indian digit grouping), "₹120", "- Rs.120.00", "120.00(Dr)",
"(450.00)", "12 Oct 2026", "05/06/26", Excel serial numbers ... All of that is normalised here.  Dates are DAY-FIRST
(India) unless the data itself proves otherwise.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from datetime import date, datetime, time, timedelta

_NULLS = {"", "nan", "none", "null", "-", "--", "na", "n/a", "nil", "—"}
_CURRENCY = re.compile(r"(?i)(?:rs\.?|inr|₹|\$|usd|rupees?)")
_UNI_MINUS = str.maketrans({"−": "-", "–": "-", "—": "-"})
_DRCR_SUFFIX = re.compile(r"(?i)\(?\s*\b(dr|cr|debit|credit|d|c)\b\.?\s*\)?\s*$")
_DRCR_PREFIX = re.compile(r"(?i)^\s*\(?\s*\b(dr|cr|debit|credit)\b\.?\s*\)?\s*[:\-]?\s*")


def is_null(v: object) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    return isinstance(v, str) and v.strip().lower() in _NULLS


def parse_amount(v: object) -> tuple[float | None, str | None]:
    """-> (absolute amount, sign) with sign in {'-', '+', 'dr', 'cr', None}.  None amount = not a number."""
    if is_null(v):
        return None, None
    if isinstance(v, bool):
        return None, None
    if isinstance(v, (int, float)):
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None, None
        return abs(f), ("-" if f < 0 else None)
    s = str(v).translate(_UNI_MINUS).strip()
    sign: str | None = None
    m = _DRCR_SUFFIX.search(s)
    if m and any(ch.isdigit() for ch in s[:m.start()]):
        sign = {"d": "dr", "dr": "dr", "debit": "dr", "c": "cr", "cr": "cr", "credit": "cr"}[m.group(1).lower()]
        s = s[:m.start()]
    else:
        m = _DRCR_PREFIX.match(s)
        if m and any(ch.isdigit() for ch in s[m.end():]):
            sign = {"dr": "dr", "debit": "dr", "cr": "cr", "credit": "cr"}[m.group(1).lower()]
            s = s[m.end():]
    s = _CURRENCY.sub("", s).strip()
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1]
    s = s.strip()
    if s.startswith("-"):
        neg, s = True, s[1:]
    elif s.startswith("+"):
        sign = sign or "+"
        s = s[1:]
    s = s.replace(",", "").replace(" ", "")
    if s.endswith("-"):                      # "120.00-" accounting style
        neg, s = True, s[:-1]
    if not re.fullmatch(r"\d+(?:\.\d+)?|\.\d+", s):
        return None, None
    val = float(s)
    if neg:
        sign = "-"
    return val, sign


# --------------------------------------------------------------------------------------------------------------------
_DATE_FORMATS = [
    "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y", "%d.%m.%y",
    "%d %b %Y", "%d-%b-%Y", "%d-%b-%y", "%d %b %y", "%d %B %Y", "%d-%B-%Y", "%b %d, %Y", "%B %d, %Y", "%b %d %Y",
    "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%d%b%Y", "%d-%m-%Y",
]
_DT_SUFFIXES = ["", " %H:%M:%S", " %H:%M", " %I:%M %p", " %I:%M:%S %p", ", %I:%M %p", ", %H:%M", "T%H:%M:%S", " %I:%M%p"]
_MONTHFIRST = ["%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y"]


def _strptime_any(s: str, formats: list[str]) -> tuple[datetime | None, str | None]:
    for f in formats:
        for suf in _DT_SUFFIXES:
            try:
                return datetime.strptime(s, f + suf), f + suf
            except ValueError:
                continue
    return None, None


def excel_serial_to_datetime(x: float) -> datetime | None:
    if 20000 <= x <= 80000:                       # 1954..2119 - plausible statement dates
        return datetime(1899, 12, 30) + timedelta(days=x)
    return None


def parse_datetime(v: object, month_first: bool = False) -> tuple[datetime | None, bool]:
    """-> (datetime, has_time). has_time is False when only a date was present."""
    if is_null(v):
        return None, False
    if isinstance(v, datetime):
        return v, (v.hour or v.minute or v.second) != 0
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day), False
    if hasattr(v, "to_pydatetime"):                   # pandas Timestamp
        d = v.to_pydatetime()
        return d, (d.hour or d.minute or d.second) != 0
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        d = excel_serial_to_datetime(float(v))
        if d is None:
            return None, False
        has = abs(float(v) - int(float(v))) > 1e-6
        return (d if has else d.replace(hour=0, minute=0, second=0, microsecond=0)), has
    s = re.sub(r"\s+", " ", str(v)).strip()
    s = re.sub(r"(?i)\b(am|pm)\b", lambda m: m.group(1).upper(), s)
    d, fmt = _strptime_any(s, _MONTHFIRST + _DATE_FORMATS if month_first else _DATE_FORMATS + _MONTHFIRST)
    if d is None:
        return None, False
    has = fmt is not None and any(t in fmt for t in ("%H", "%I"))
    return d, has


def infer_month_first(values: list[object]) -> bool:
    """True if the column proves month-first (some value has a second field > 12 and none has a first field > 12)."""
    first_gt12 = second_gt12 = False
    for v in values:
        if not isinstance(v, str):
            continue
        m = re.match(r"^\s*(\d{1,2})[/\-.](\d{1,2})[/\-.]\d{2,4}", v)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            first_gt12 |= a > 12
            second_gt12 |= b > 12
    return second_gt12 and not first_gt12


def parse_time(v: object) -> time | None:
    if is_null(v):
        return None
    if isinstance(v, datetime):
        return v.time()
    if isinstance(v, time):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        x = float(v) % 1.0
        secs = int(round(x * 86400))
        return time(secs // 3600 % 24, secs % 3600 // 60, secs % 60)
    s = re.sub(r"\s+", " ", str(v)).strip()
    s = re.sub(r"(?i)\b(am|pm)\b", lambda m: m.group(1).upper(), s)
    for f in ("%H:%M:%S", "%H:%M", "%I:%M %p", "%I:%M:%S %p", "%I:%M%p", "%H.%M"):
        try:
            return datetime.strptime(s, f).time()
        except ValueError:
            continue
    return None


def looks_like_date(v: object) -> bool:
    d, _ = parse_datetime(v)
    return d is not None and 1990 <= d.year <= 2100


def most_common_nonnull(values: list[object], n: int = 3) -> list[object]:
    return [v for v, _ in Counter(v for v in values if not is_null(v)).most_common(n)]
