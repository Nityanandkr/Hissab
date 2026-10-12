"""Recurring payments: subscriptions, bills, rent/mess, allowance - and habits (the daily chai).

HOW IT WORKS (per merchant and direction):
  1. collapse to distinct days, compute the gaps between consecutive payments
  2. MONTHLY test (most important): look at calendar months. Does it appear in >= 75% of the months between its first and last
     payment, always around the same day of the month (+-4 days)?  Extra one-off payments in a month are tolerated, because
     we anchor on the payment nearest the usual day.  (Gap arithmetic alone fails here: 31 Jan -> 28 Feb is 28 days, 28 Feb -> 31 Mar
     is 31 days - both are "monthly".)
  3. WEEKLY / FORTNIGHTLY / QUARTERLY / YEARLY: score = share of gaps close to k x period (a skipped week still counts, partly)
  4. amount behaviour: fixed (CV<2%), variable (bill-like, CV<35%) or irregular (lower confidence)
  5. next expected date; OVERDUE if nothing arrived a while after it (maybe cancelled, maybe forgotten); PRICE CHANGE if the
     latest amount is >=10% away from the previous ones (Spotify 59 -> 89).
HABITS are different: many payments, short gaps (the chai-wallah). They are reported separately with their monthly cost,
because "small daily spends" are the classic leak and are not subscriptions.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .. import config, taxonomy

PERIODS = [("weekly", 7, 1.5), ("fortnightly", 14, 2.5), ("quarterly", 91, 6.0), ("yearly", 365, 10.0)]
SKIP_CATEGORIES = {"Self Transfer"}


def _next_month(y: int, m: int) -> tuple[int, int]:
    return (y + 1, 1) if m == 12 else (y, m + 1)


def _monthly(dates: list[date], amounts: list[float], largest: bool = False) -> dict | None:
    """Calendar-month test.  `largest=True` anchors on the biggest payment of each month (income with extra top-ups)."""
    months = sorted({(d.year, d.month) for d in dates})
    if len(months) < 3:
        return None
    first, last = months[0], months[-1]
    span = (last[0] * 12 + last[1]) - (first[0] * 12 + first[1]) + 1
    coverage = len(months) / span
    if not largest and len(dates) / len(months) > 1.6:         # 4+ payments a month is weekly/habit, not monthly
        return None
    anchors, anchor_amts = [], []
    if largest:
        for (y, m) in months:
            cand = [(a, d) for d, a in zip(dates, amounts) if d.year == y and d.month == m]
            a, d = max(cand)
            anchors.append(d)
            anchor_amts.append(a)
        big = float(np.median(anchor_amts))
        keep = [i for i, a in enumerate(anchor_amts) if a >= 0.5 * big]
        anchors, anchor_amts = [anchors[i] for i in keep], [anchor_amts[i] for i in keep]
        if len(anchors) / span < 0.75:
            return None
        med_dom = int(np.median([d.day for d in anchors]))
    else:
        med_dom = int(np.median([d.day for d in dates]))
        for (y, m) in months:
            cand = [(abs(d.day - med_dom), d, a) for d, a in zip(dates, amounts) if d.year == y and d.month == m]
            _, d, a = min(cand, key=lambda t: t[0])
            anchors.append(d)
            anchor_amts.append(a)
    spread = max(abs(d.day - med_dom) for d in anchors)
    if coverage < 0.75 or spread > 5:
        return None
    score = 0.6 * coverage + 0.4 * (1 - min(spread, 5) / 5)
    return {"label": "monthly", "days": 30.4, "score": score, "anchors": anchors, "anchor_amts": anchor_amts, "dom": med_dom, "spread": spread}


def _gap_period(dates: list[date], label: str, period: int, tol: float) -> dict | None:
    if len(dates) < 3:
        return None
    gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
    ok = 0.0
    for g in gaps:
        k = max(1, round(g / period))
        if k <= 3 and abs(g - k * period) <= tol:
            ok += 1.0 if k == 1 else 0.6
    score = ok / len(gaps)
    if score < 0.7:
        return None
    return {"label": label, "days": float(period), "score": score}


def detect(df: pd.DataFrame, today: date | None = None) -> dict:
    """Returns {"recurring": [...], "habits": [...]} - both JSON-ready."""
    if df.empty:
        return {"recurring": [], "habits": []}
    today = today or df["date"].max().date()
    rec, habits = [], []
    d0 = df[~df["category"].isin(SKIP_CATEGORIES) & df["kind"].ne("self")]
    for (mid, direction), g in d0.groupby(["merchant_id", "direction"]):
        g = g.sort_values("ts")
        days = g.groupby("date").agg(amount=("amount", "sum")).reset_index()
        dates = [d.date() for d in days["date"]]
        amounts = days["amount"].astype(float).tolist()
        n = len(dates)
        if n < 2:
            continue
        name, category = g["merchant"].iat[0], g["category"].mode().iat[0]
        kind = g["kind"].iat[0]
        # ---- habit?  (frequent small spends) -----------------------------------------------------------------------
        span_days = max((dates[-1] - dates[0]).days, 1)
        if direction == "debit" and n >= 8 and span_days / max(n - 1, 1) <= 4.5 and float(np.median(amounts)) < 250:
            per_month = n / span_days * 30.4
            habits.append({"merchant_id": int(mid), "merchant": name, "category": category, "visits": n, "per_week": round(n / span_days * 7, 1),
                           "avg_amount": round(float(np.mean(amounts)), 2), "monthly_cost": round(per_month * float(np.mean(amounts)), 2),
                           "first": dates[0].isoformat(), "last": dates[-1].isoformat(), "is_local": bool(g["is_local"].iat[0])})
            continue
        if n < config.RECUR_MIN_OCCURRENCES:
            continue
        best = _monthly(dates, amounts)
        if best is None and direction == "credit":
            best = _monthly(dates, amounts, largest=True)
        if best is None:
            m_gap = _gap_period(dates, "monthly", 30, 3.0)               # short series: only two month-ends so far
            if m_gap and n >= 3 and span_days <= 75:
                m_gap["dom"] = int(np.median([d.day for d in dates]))
                m_gap["spread"] = 3
                best = m_gap
        if best is None:
            for label, period, tol in PERIODS:
                cand = _gap_period(dates, label, period, tol)
                if cand and (best is None or cand["score"] > best["score"]):
                    best = cand
        if best is None:
            continue
        use_amts = best.get("anchor_amts", amounts)
        mean_a = float(np.mean(use_amts))
        cv = float(np.std(use_amts) / mean_a) if mean_a else 1.0
        amount_type = "fixed" if cv < 0.02 else "variable" if cv < 0.5 else "irregular"
        conf = 0.55 * best["score"] + 0.25 * {"fixed": 1.0, "variable": 0.7, "irregular": 0.15}[amount_type] + 0.2 * min(n / 6, 1.0)
        if conf < 0.55 or amount_type == "irregular" or (amount_type == "variable" and n < 4):
            continue
        last = dates[-1]
        if best["label"] == "monthly":
            ny, nm = _next_month(last.year, last.month)
            nxt = date(ny, nm, min(best["dom"], calendar.monthrange(ny, nm)[1]))
            grace = max(3, int(best.get("spread", 3)) + 3)
        else:
            nxt = last + timedelta(days=int(best["days"]))
            grace = int(max(3, best["days"] * 0.25))
        status = "overdue" if today > nxt + timedelta(days=grace) else "active"
        if status == "overdue" and kind == "person" and amount_type != "fixed":
            status = "active"            # a friend who paid you / was paid roughly monthly is not a subscription that "lapsed"
        price_change = None
        if len(use_amts) >= 3:
            ua = list(use_amts)
            # the most recent change point: first index i such that everything from i on is ~ the last amount, and ua[i-1] differs >= 10%
            i = len(ua) - 1
            while i > 0 and abs(ua[i - 1] - ua[-1]) / ua[-1] <= 0.02:
                i -= 1
            before = ua[:i]
            if i >= 1 and len(before) >= 2 and np.std(before) / np.mean(before) < 0.03 and abs(ua[-1] - np.mean(before)) / np.mean(before) >= 0.10:
                ids = g.groupby("date")["id"].first().tolist()
                anchor_ids = ids if "anchors" not in best else [ids[dates.index(d)] for d in best["anchors"]]
                price_change = {"from": round(float(np.mean(before)), 2), "to": round(float(ua[-1]), 2),
                                "pct": round((ua[-1] - float(np.mean(before))) / float(np.mean(before)) * 100, 1),
                                "txn_id": int(anchor_ids[i]) if i < len(anchor_ids) else None}
        last_amt = float(amounts[-1])
        per_month = last_amt * (30.4 / best["days"])
        rec.append({
            "merchant_id": int(mid), "merchant": name, "category": category, "direction": direction, "kind": kind, "period": best["label"],
            "period_days": round(best["days"], 1), "occurrences": n, "avg_amount": round(mean_a, 2), "last_amount": round(last_amt, 2),
            "amount_type": amount_type, "first": dates[0].isoformat(), "last": last.isoformat(), "next_expected": nxt.isoformat(),
            "status": status, "days_overdue": (today - nxt).days if status == "overdue" else 0, "price_change": price_change,
            "confidence": round(conf, 2), "monthly_cost": round(per_month, 2), "is_local": bool(g["is_local"].iat[0]),
            "txn_ids": [int(x) for x in g["id"].tolist()],
        })
    rec.sort(key=lambda r: (r["direction"] != "debit", -r["monthly_cost"]))
    habits.sort(key=lambda h: -h["monthly_cost"])
    return {"recurring": rec, "habits": habits}
