"""The enrichment pipeline: raw rows -> parsed, merchant-resolved, kind-classified, categorised transactions.

This file is the "spine": it calls the specialised modules in order and keeps the DataFrame schema they share.

    stage 1  parse + resolve    narration -> fields -> merchant entity   (upi_parse, normalize)
    stage 2  kind               shop / person / cash / bank / self        (p2p)
    stage 3  category           cascade: rules > memory > brand > model > lexicon   (categorize)

Stage 1 is stateful (the merchant registry grows), stages 2-3 are recomputed from the data whenever the user teaches
us something, because a correction can change what we believe about *other* merchants too.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..ingest.importer import RawRow
from .normalize import Registry
from .upi_parse import parse

# lines without a counterparty get a fixed label so that all ATM withdrawals become ONE entity, etc.
SPECIAL_LABEL = {"atm": "ATM CASH WITHDRAWAL", "wallet_topup": "WALLET TOP UP", "interest": "BANK INTEREST",
                 "bank_charge": "BANK CHARGES"}
NAME_TRUST = 2.2


@dataclass
class Stage1:
    df: pd.DataFrame
    registry: Registry


def stage1(rows: list[RawRow], registry: Registry | None = None) -> Stage1:
    """Parse every narration and resolve it to a merchant entity (chronological order matters for the registry)."""
    reg = registry or Registry()
    recs: list[dict] = []
    for r in sorted(rows, key=lambda x: x.ts):
        p = parse(r.desc)
        payee, trusted, special = p.payee_raw, (p.payee_source == "text" and p.name_score >= NAME_TRUST), None
        if not p.vpa and not p.rrn:
            special = next((SPECIAL_LABEL[f] for f in p.flags if f in SPECIAL_LABEL), None)
            if special:
                payee, trusted = special, True
        res = reg.resolve(payee, p.vpa, name_trusted=trusted)
        rrn = p.rrn if (p.rrn and len(p.rrn) == 12) else (r.ref if (r.ref and re.fullmatch(r"\d{12}", r.ref)) else p.rrn)
        recs.append({
            "src_row": r.src_row, "ts": r.ts, "has_time": r.has_time, "desc": r.desc, "amount": r.amount,
            "direction": r.direction, "balance": r.balance, "ref": r.ref, "rrn": rrn, "mode": p.mode, "marker": p.marker,
            "dir_hint": p.dir_hint, "vpa": p.vpa, "handle": p.handle, "app": p.app, "phone": p.phone,
            "payee_raw": p.payee_raw, "payee_source": p.payee_source, "name_score": p.name_score, "remarks": p.remarks,
            "flags": p.flags, "parse_conf": p.confidence, "special": special,
            "merchant_id": res.entity.id, "match_method": res.method, "match_conf": res.confidence,
        })
    df = pd.DataFrame(recs)
    if df.empty:
        return Stage1(df, reg)
    df["date"] = df["ts"].dt.normalize()
    df["hour"] = np.where(df["has_time"], df["ts"].dt.hour, np.nan)
    df["weekday"] = df["ts"].dt.weekday
    df["merchant"] = df["merchant_id"].map(lambda i: reg.entities[i].canonical)
    df["is_brand"] = df["merchant_id"].map(lambda i: reg.entities[i].is_brand)
    df["brand_category"] = df["merchant_id"].map(lambda i: reg.entities[i].brand_category)
    return Stage1(df, reg)
