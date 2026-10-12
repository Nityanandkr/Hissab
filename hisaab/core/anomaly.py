"""Unusual transactions - every flag comes with a plain-language reason a person can check.

Method (robust statistics + rules, not a black-box model):
  * Amounts are heavy-tailed (a few big purchases among many small ones), so the mean/std z-score is useless - one big
    purchase inflates the std and hides itself.  We use the MEDIAN and the MAD (median absolute deviation), which ignore
    outliers:   robust_z = 0.6745 * (x - median) / MAD     (0.6745 makes it comparable to a normal z-score)
    ...computed on log(amount): spending is multiplicative, and on raw rupees a broad-spend merchant (a friend: Rs 20 - Rs 650) was
    flagged again and again.
  * Reasons implemented:
      amount_vs_merchant   much larger than your other payments to the same merchant
      amount_vs_category   much larger than usual for the category (log scale)
      new_merchant_large   first-ever payment to a merchant AND bigger than 95% of your payments
      new_payee_large      big transfer to a person you have never paid before
      duplicate            same merchant and same amount within a few minutes (or the same day if there is no time)
      odd_hour             a sizeable payment between midnight and 5 am (only if the data has times and you rarely do that)
      price_change         a subscription got more expensive (from recurring.py)
  * several reasons on one payment => higher severity.
Cold start: rules that need history (merchant/category baselines) stay silent until there is enough data, instead of guessing.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config
from ..textutil import fmt_inr
from . import analytics


def _robust_z_log(log_x: float, others_log: np.ndarray) -> tuple[float, float]:
    """Robust z-score on the LOG of the amount (median / MAD of the logs); returns (z, typical amount in rupees).
    Why log: spending is right-skewed and multiplicative.  On raw rupees a friend you pay Rs 20 - Rs 650 looks "unusual" at Rs 600 every
    time; on the log scale that merchant's spread is wide, so it is not flagged - while Rs 600 at a Rs 60 tea stall stays a loud outlier."""
    med = float(np.median(others_log))
    mad = max(float(np.median(np.abs(others_log - med))), 0.25)          # floor = about 37% natural wobble, even for fixed-price shops
    return 0.6745 * (log_x - med) / mad, float(np.exp(med))


def detect(df: pd.DataFrame, recurring: list[dict] | None = None) -> list[dict]:
    if df.empty:
        return []
    deb = df[analytics.spend_mask(df)].copy()
    if deb.empty:
        return []
    found: dict[int, dict] = {}

    def add(row, code: str, title: str, reason: str, weight: float) -> None:
        a = found.setdefault(int(row["id"]), {"txn_id": int(row["id"]), "merchant_id": int(row["merchant_id"]), "merchant": row["merchant"],
                                              "date": row["date"].date().isoformat(), "time": row["ts"].strftime("%H:%M") if row["has_time"] else "",
                                              "amount": round(float(row["amount"]), 2), "category": row["category"], "reasons": [], "codes": [],
                                              "score": 0.0})
        if code not in a["codes"]:
            a["codes"].append(code)
            a["reasons"].append({"code": code, "title": title, "text": reason})
            a["score"] += weight

    p95 = float(np.percentile(deb["amount"], 95))
    p90_p2p = float(np.percentile(deb.loc[deb["kind"] == "person", "amount"], 90)) if (deb["kind"] == "person").sum() >= 5 else 2000.0

    # 1) vs the merchant's own history (log scale: amounts are multiplicative, a Rs 600 meal is "3x" not "+Rs 500") --------------------
    for mid, g in deb.groupby("merchant_id"):
        if len(g) < 9 or g["kind"].iat[0] == "cash":          # need a real baseline; ATM amounts are chosen, not "priced"
            continue
        logs = np.log(g["amount"].to_numpy(dtype=float))
        for pos, (idx, row) in enumerate(g.iterrows()):
            z, med = _robust_z_log(float(logs[pos]), np.delete(logs, pos))
            if z >= config.ANOMALY_Z and row["amount"] >= max(1.5 * med, 300):
                add(row, "amount_vs_merchant", "Much more than usual here",
                    f"{fmt_inr(row['amount'])} at {row['merchant']} - your usual payment there is about {fmt_inr(med)} "
                    f"({row['amount'] / med:.1f}× higher).", 1.0 + min(z, 10) / 10)

    # 2) vs the category (log scale) --------------------------------------------------------------------------------------
    fixed_subs = {r["merchant_id"] for r in (recurring or []) if r["direction"] == "debit" and r["amount_type"] == "fixed"}
    for cat, g in deb[~deb["merchant_id"].isin(fixed_subs)].groupby("category"):
        if len(g) < 15 or cat in ("Transfers (P2P)", "Cash Withdrawal", "Needs review", "Rent & Hostel"):
            continue
        logs = np.log(g["amount"].to_numpy())
        for idx, row in g.iterrows():
            if row["amount"] < 500:
                continue
            others = np.delete(logs, g.index.get_loc(idx))
            med = float(np.median(others))
            mad = max(float(np.median(np.abs(others - med))), 0.15)
            z = 0.6745 * (np.log(row["amount"]) - med) / mad
            if z >= config.ANOMALY_Z:
                add(row, "amount_vs_category", f"Unusually large for {cat}",
                    f"{fmt_inr(row['amount'])} is far above the typical {cat} payment (about {fmt_inr(np.exp(med))}).", 0.9)

    # 3) first time + big --------------------------------------------------------------------------------------------------
    counts = deb.groupby("merchant_id")["id"].size()
    for _, row in deb.iterrows():
        n = int(counts[row["merchant_id"]])
        if n == 1 and row["kind"] == "merchant" and row["amount"] >= max(p95, 2000):
            add(row, "new_merchant_large", "First time at this merchant, and a big amount",
                f"First payment to {row['merchant']}, and {fmt_inr(row['amount'])} is larger than 95% of your payments.", 1.2)
        if n <= 2 and row["kind"] == "person" and row["amount"] >= max(p90_p2p, 2000):
            add(row, "new_payee_large", "Large transfer to a new person",
                f"{fmt_inr(row['amount'])} sent to {row['merchant']}, a person you have rarely paid before.", 1.2)

    # 4) duplicates ---------------------------------------------------------------------------------------------------------
    for mid, g in deb.groupby("merchant_id"):
        if len(g) < 2:
            continue
        g = g.sort_values("ts")
        for (_, a), (_, b) in zip(g.iloc[:-1].iterrows(), g.iloc[1:].iterrows()):
            if abs(a["amount"] - b["amount"]) > 0.01 or a["amount"] < 100:
                continue
            if a["has_time"] and b["has_time"]:
                mins = (b["ts"] - a["ts"]).total_seconds() / 60
                if mins <= config.DUPLICATE_WINDOW_MIN:
                    add(b, "duplicate", "Possible duplicate charge",
                        f"{fmt_inr(b['amount'])} at {b['merchant']} twice within {max(1, round(mins))} minute(s).", 1.4)
            elif a["date"] == b["date"] and a["amount"] >= 200:
                add(b, "duplicate", "Possible duplicate charge (same day)",
                    f"{fmt_inr(b['amount'])} at {b['merchant']} twice on the same day (no time in the statement to be sure).", 0.8)

    # 5) odd hours ---------------------------------------------------------------------------------------------------------
    timed = deb[deb["has_time"]]
    if len(timed) >= 50:
        night_share = float((timed["hour"] < 5).mean())
        if night_share < 0.03:
            for _, row in timed[(timed["hour"] < 5) & (timed["amount"] >= 500)].iterrows():
                add(row, "odd_hour", "Payment in the middle of the night",
                    f"{fmt_inr(row['amount'])} paid at {row['ts'].strftime('%H:%M')} - you rarely pay at this hour.", 0.8)

    # 6) price changes of subscriptions ----------------------------------------------------------------------------------
    byid = deb.set_index("id")
    for r in recurring or []:
        pc = r.get("price_change")
        if pc and r["direction"] == "debit" and pc.get("txn_id"):
            tid = pc["txn_id"]
            if tid in byid.index:
                row = byid.loc[tid].to_dict()
                row["id"] = tid
                add(pd.Series(row), "price_change", f"{r['merchant']} got more expensive" if pc["to"] > pc["from"] else f"{r['merchant']} got cheaper",
                    f"{r['merchant']} now costs {fmt_inr(pc['to'])} (was {fmt_inr(pc['from'])}, {pc['pct']:+.0f}%).", 1.0)

    out = list(found.values())
    for a in out:
        a["severity"] = "high" if (len(a["codes"]) >= 2 or a["score"] >= 1.8) else "medium" if a["score"] >= 1.0 else "low"
        a["score"] = round(a["score"], 2)
        a["title"] = a["reasons"][0]["title"] if len(a["reasons"]) == 1 else f"{len(a['reasons'])} things look unusual"
    out.sort(key=lambda a: (-{"high": 2, "medium": 1, "low": 0}[a["severity"]], -a["score"], a["date"]))
    return out
