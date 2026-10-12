"""The review queue = ACTIVE LEARNING: ask the user about the merchants where an answer helps the most.

Why not ask about everything?  A month of statements has ~100 merchants; labelling them all is the "do it manually in Excel"
chore this product exists to remove.  But merchants differ enormously in how much their label matters:
  * priority = (how much money) x (how unsure we are)   -> a ₹16,000 mess payee we are unsure about beats a ₹20 tea we are unsure about
  * labelling the top 5-10 items usually settles most of the uncertain money (shown as a cumulative share)
Two kinds of questions:
  category   "what does this merchant sell?"           (model unsure or only guessing)
  kind       "is this a shop or a person?"             (model unsure, OR a 'person' that behaves like a service: pays regularly,
                                                        one-way, same amount - a mess contractor, a landlord, an auto driver)
Nothing here changes data; it only ranks questions.  Answers go through Engine.set_category / set_kind.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import taxonomy
from .. import config
from . import analytics as A
from .p2p import service_like


def build(df: pd.DataFrame, limit: int = 30) -> dict:
    """Return {"items": [...], "n_merchants": N, "money": ₹, "top5_share": 0..1}  (items sorted by priority)."""
    if df.empty:
        return {"items": [], "n_merchants": 0, "money": 0.0, "top5_share": 0.0}
    items = []
    for mid, g in df.groupby("merchant_id"):
        first = g.iloc[0]
        kind, kind_src = first["kind"], first["kind_source"]
        if kind in ("cash", "bank", "self"):
            continue
        deb = g[A.spend_mask(g)]
        money = float(deb["amount"].sum())
        if money <= 0 and not (g["direction"] == "credit").any():
            continue
        questions, why = [], []
        # category question (spending side)
        if len(deb):
            st = deb["cat_status"].mode().iat[0]
            conf = float(deb["cat_conf"].mean())
            if st in ("unknown", "suggested") and kind == "merchant":
                questions.append("category")
                why.append("not sure what it sells" if st == "unknown" else f"best guess {deb['category'].mode().iat[0]} (confidence {conf:.0%})")
        # kind question
        if kind_src != "user":
            kc = float(first["kind_conf"]) if first["kind_conf"] == first["kind_conf"] else 1.0
            sl = service_like(g) if kind == "person" else None
            if sl:
                questions.append("kind")
                why.append("looks like a person but " + sl + (f" (the model is only {kc:.0%} sure it is a person)" if kc < config.KIND_CONF_AUTO else ""))
            elif kc < config.KIND_CONF_AUTO:
                questions.append("kind")
                why.append(f"shop-or-person is a close call ({kc:.0%})")
        if not questions:
            continue
        conf_eff = min([float(deb["cat_conf"].mean()) if len(deb) and "category" in questions else 1.0,
                        float(first["kind_conf"]) if "kind" in questions and first["kind_conf"] == first["kind_conf"] else 1.0])
        priority = max(money, 1.0) * (1.0 - conf_eff + 0.15)
        items.append({"merchant_id": int(mid), "merchant": first["merchant"], "kind": kind, "kind_conf": round(float(first["kind_conf"]), 2) if first["kind_conf"] == first["kind_conf"] else None,
                      "category": deb["category"].mode().iat[0] if len(deb) else None, "cat_conf": round(float(deb["cat_conf"].mean()), 2) if len(deb) else None,
                      "n": int(len(g)), "total": round(money, 2), "avg": round(float(deb["amount"].mean()), 2) if len(deb) else 0.0,
                      "first": g["date"].min().date().isoformat(), "last": g["date"].max().date().isoformat(), "questions": questions,
                      "why": "; ".join(why), "priority": round(priority, 1), "is_local": bool(first["is_local"]),
                      "samples": [s for s in g.sort_values("ts", ascending=False)["description"].head(2)]})
    items.sort(key=lambda x: -x["priority"])
    money = sum(i["total"] for i in items)
    top5 = sum(i["total"] for i in items[:5])
    return {"items": items[:limit], "n_merchants": len(items), "money": round(money, 2), "top5_share": round(top5 / money, 3) if money else 0.0}
