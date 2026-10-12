"""Turn a transaction (or a synthetic seed example) into the three text fields the category model reads.

  name_text    the cleaned merchant name                      -> TF-IDF over CHARACTER n-grams (2-5)
  words_text   name words + the payer's typed remark + words from the UPI address  -> TF-IDF over WORDS (1-2)
  ctx_text     "mode=UPI handle=ybl kind=merchant amt=a50_100 hr=morning ..."  -> binary tokens (context)

Why character n-grams for the name?  Local shop names are misspelt, truncated and abbreviated: "KIRNA", "KIRANA ST",
"KIRANA STORE" share most 3-grams ("kir", "ira", "ran", "ana") even though they are different strings, so the model
generalises to spellings it has never seen - something a plain dictionary lookup cannot do.

Why a separate "context" field?  For names that say nothing ("RAJU", "NEW CORNER") the amount and time of day still carry a
little signal (Rs 15 at 8 am is tea; Rs 600 at 9 pm is a restaurant or delivery).  It gets a small weight so it only
decides when the words are silent.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from ..textutil import na, tokens_of, vpa_parts
from .normalize import clean_payee

AMT_BUCKETS = [(20, "a0_20"), (50, "a20_50"), (100, "a50_100"), (250, "a100_250"), (500, "a250_500"),
               (1000, "a500_1k"), (3000, "a1k_3k")]


def amt_bucket(x: float) -> str:
    for hi, name in AMT_BUCKETS:
        if x < hi:
            return name
    return "a3k_plus"


def hour_bucket(h) -> str:
    if h is None or (isinstance(h, float) and np.isnan(h)):
        return "hr_unk"
    h = int(h)
    return "hr_night" if h < 5 else "hr_morning" if h < 11 else "hr_noon" if h < 15 else "hr_eve" if h < 19 else "hr_late"


def vpa_shape(v: str | None) -> str:
    v = na(v)
    if not v:
        return "vs_none"
    local = vpa_parts(v)[0].lower()
    if re.fullmatch(r"(?:91)?[6-9]\d{9}", local):
        return "vs_phone"
    if local.startswith("paytmqr") or re.fullmatch(r"q\d{7,}", local) or local.startswith("bharatpe"):
        return "vs_qr"
    return "vs_named"


def vpa_words(v: str | None) -> list[str]:
    v = na(v)
    if not v:
        return []
    local = vpa_parts(v)[0]
    return [p.upper() for p in re.split(r"[._\-\d]+", local) if len(p) >= 3 and p.lower() not in ("paytmqr", "gpay")]


def ctx_tokens(mode: str | None, marker: str | None, handle: str | None, kind: str | None, amount: float, hour, vpa: str | None,
               weekday: int | None, direction: str | None) -> str:
    toks = [f"mode_{mode or 'NA'}", f"amt_{amt_bucket(float(amount))}", hour_bucket(hour), vpa_shape(vpa)]
    if marker:
        toks.append(f"mk_{marker}")
    if handle:
        toks.append(f"hd_{handle.lower()}")
    if kind:
        toks.append(f"kind_{kind}")
    if weekday is not None and not (isinstance(weekday, float) and np.isnan(weekday)):
        toks.append("wk_end" if int(weekday) >= 5 else "wk_day")
    if direction:
        toks.append(f"dir_{direction}")
    return " ".join(toks)


def row_fields(name: str, remarks: str, vpa: str | None, mode, marker, handle, kind, amount, hour, weekday, direction) -> dict:
    name, remarks, vpa, mode, marker, handle, kind = (na(x) for x in (name, remarks, vpa, mode, marker, handle, kind))
    c = clean_payee(name or "")
    words = list(c.tokens) + tokens_of(remarks or "") + vpa_words(vpa)
    return {
        "name_text": c.text or "",
        "words_text": " ".join(words),
        "ctx_text": ctx_tokens(mode, marker, handle, kind, amount, hour, vpa, weekday, direction),
    }


def frame_from_stage1(df: pd.DataFrame, canonical: dict[int, str], kinds: dict[int, str],
                      placeholders: set[int] | None = None) -> pd.DataFrame:
    """Feature frame (one row per transaction) from a Stage1 DataFrame; name = the entity's canonical name
    (empty for placeholder entities - a stand-in name like 'QR merchant' is not evidence about what the shop sells)."""
    placeholders = placeholders or set()
    recs = []
    for r in df.itertuples(index=False):
        mid = int(r.merchant_id)
        recs.append(row_fields("" if mid in placeholders else canonical[mid], na(r.remarks) or "", na(r.vpa), na(r.mode), na(r.marker), na(r.handle), kinds.get(mid),
                               r.amount, r.hour, r.weekday, r.direction))
    return pd.DataFrame(recs, index=df.index)
