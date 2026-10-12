"""Small pure text helpers shared by the parser, normaliser, feature builder and the NL layer."""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

_WS = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^A-Z0-9]+")
_APOS = re.compile(r"[’‘'`´]")


def na(x):
    """pandas stores a missing string as NaN (a float!). Return None for NaN/None/pd.NA so `if x:` and `"@" in x` are safe."""
    if x is None:
        return None
    try:
        if x != x:                      # NaN
            return None
    except Exception:                   # pd.NA raises on comparison
        return None
    return x


def norm_ws(s: str | None) -> str:
    """Collapse all whitespace runs to one space."""
    return _WS.sub(" ", s or "").strip()


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def tokens_of(s: str | None) -> list[str]:
    """UPPERCASE alphanumeric tokens.  "Domino's Pizza & Co." -> [DOMINO, S, PIZZA, AND, CO]."""
    s = strip_accents(s or "")
    s = _APOS.sub(" ", s).upper().replace("&", " AND ")
    return [t for t in _NON_ALNUM.split(s) if t]


def day_numbers(ts) -> "np.ndarray":
    """Whole days since 1970-01-01 for a datetime Series, whatever its resolution.  (pandas 3 stores datetimes as microseconds or seconds,
    so `astype("int64") // 86_400_000_000_000` silently gives garbage - it made the 'regular gaps' feature constant zero until it was caught.)"""
    import numpy as np
    import pandas as pd
    return np.asarray((pd.to_datetime(ts).dt.normalize() - pd.Timestamp("1970-01-01")) // pd.Timedelta(days=1), dtype=np.int64)


def seq_ratio(a: str, b: str) -> float:
    """Character-level similarity in [0, 1] (Ratcliff/Obershelp, from the standard library)."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def title_name(tokens: list[str]) -> str:
    """Display form: ['RAMESH','KIRANA','STORE'] -> 'Ramesh Kirana Store'; 'AND' -> '&'; initials stay upper."""
    out = []
    for t in tokens:
        if t == "AND":
            out.append("&")
        elif len(t) == 1 or (len(t) <= 3 and not any(v in t for v in "AEIOU")) or any(ch.isdigit() for ch in t):
            out.append(t)                       # K S TRADERS -> K S Traders ; MSEDCL stays upper below
        else:
            out.append(t.capitalize())
    return " ".join(out)


# ---- vpa helpers -------------------------------------------------------------------------------------------------
def vpa_parts(vpa: str | None) -> tuple[str, str]:
    vpa = na(vpa)
    if not vpa or "@" not in vpa:
        return (vpa or ""), ""
    local, _, handle = vpa.partition("@")
    return local, handle


_LONG_DIGITS = re.compile(r"\d{6,}")


def mask_vpa(vpa: str | None) -> str:
    """9876543210@ybl -> 98XXXXXX10@ybl  (phone numbers are personal data - never show them in full)."""
    vpa = na(vpa)
    if not vpa:
        return ""
    local, handle = vpa_parts(vpa)

    def _m(m: re.Match) -> str:
        d = m.group(0)
        return d[:2] + "X" * (len(d) - 4) + d[-2:]

    local = _LONG_DIGITS.sub(_m, local)
    return f"{local}@{handle}" if handle else local


def mask_digits(s: str | None) -> str:
    return _LONG_DIGITS.sub(lambda m: m.group(0)[:2] + "X" * (len(m.group(0)) - 4) + m.group(0)[-2:], s or "")


# ---- money -------------------------------------------------------------------------------------------------------
def fmt_inr(x: float | int | None, decimals: int = 0, symbol: bool = True) -> str:
    """Indian digit grouping: 1234567.5 -> ₹12,34,568 (decimals=0)."""
    if x is None:
        return "-"
    neg = x < 0
    s = f"{abs(x):.{decimals}f}"
    whole, _, frac = s.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts: list[str] = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    out = whole + (("." + frac) if frac else "")
    return ("-" if neg else "") + ("₹" if symbol else "") + out
