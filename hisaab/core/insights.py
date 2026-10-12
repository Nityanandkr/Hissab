"""Plain-language insights for the dashboard: the few things worth knowing, each with the evidence behind it.

Every insight is computed from the same analytics functions the chat uses, so a number on the dashboard and the same number in an
answer can never disagree.  Tone: 'warn' = needs attention, 'good' = good news, 'info' = FYI.
"""
from __future__ import annotations

import calendar

import pandas as pd

from ..textutil import fmt_inr
from . import analytics as A


def _inr(x: float) -> str:
    return fmt_inr(x, 0 if abs(x) >= 100 or float(x).is_integer() else 2)


def build(df: pd.DataFrame, recurring: dict, anomalies: list, review: dict, limit: int = 8) -> list[dict]:
    if df.empty:
        return []
    ref = A.ref_date(df)
    out: list[dict] = []
    last_day = calendar.monthrange(ref.year, ref.month)[1]
    partial = ref.day < last_day
    # ---- trend ------------------------------------------------------------------------------------------------------------
    if partial and ref.day >= 5:
        cur = A.month_period(ref.year, ref.month, ref.day)
        y, m = A.shift_month(ref.year, ref.month, -1)
        prev = A.month_period(y, m, ref.day)
    else:
        y, m = (ref.year, ref.month) if not partial else A.shift_month(ref.year, ref.month, -1)
        cur = A.month_period(y, m)
        py, pm = A.shift_month(y, m, -1)
        prev = A.month_period(py, pm)
    if prev.start >= df["date"].min().date().replace(day=1) or True:
        comp = A.compare(df, cur, prev, "category", limit=3)
        if comp["base_total"] > 0:
            pct = comp["pct"]
            tone = "warn" if (pct or 0) > 15 else "good" if (pct or 0) < -10 else "info"
            ups = comp["increases"][:2]
            why = (" Mostly " + " and ".join(f"{u['name']} ({'+' if u['delta'] > 0 else '−'}{_inr(abs(u['delta']))})" for u in ups) + ".") if ups and (pct or 0) > 0 else ""
            out.append({"id": "trend", "tone": tone, "title": f"Spending {'up' if comp['delta'] > 0 else 'down'} {abs(pct):.0f}% vs {prev.label}" if pct is not None else "Spending trend",
                        "text": f"{cur.label}: {_inr(comp['recent_total'])} vs {_inr(comp['base_total'])} in {prev.label}.{why}",
                        "link": {"page": "ask", "q": "Where did my spending increase?"}})
    # ---- review -----------------------------------------------------------------------------------------------------------
    if review.get("n_merchants", 0):
        out.append({"id": "review", "tone": "warn", "title": f"{review['n_merchants']} merchants need a quick look",
                    "text": f"{_inr(review['money'])} of your spending is not confidently categorised. Labelling just the top {min(5, review['n_merchants'])} would settle about {review['top5_share'] * 100:.0f}% of it — and the app learns from each answer.",
                    "link": {"page": "review"}})
    # ---- recurring / subscriptions -----------------------------------------------------------------------------------------
    rec = [r for r in recurring["recurring"] if r["direction"] == "debit"]
    if rec:
        total = sum(r["monthly_cost"] for r in rec)
        over = [r for r in rec if r["status"] == "overdue"]
        pcs = [r for r in rec if r["price_change"]]
        txt = f"{len(rec)} payments repeat on a schedule, about {_inr(total)} a month."
        if over:
            txt += " " + ", ".join(r["merchant"] for r in over[:2]) + f" {'was' if len(over) == 1 else 'were'} expected but did not arrive — cancelled, or missed?"
        if pcs:
            txt += " " + "; ".join(f"{r['merchant']} went {_inr(r['price_change']['from'])} → {_inr(r['price_change']['to'])}" for r in pcs[:2]) + "."
        out.append({"id": "recurring", "tone": "warn" if (over or pcs) else "info", "title": "Your recurring payments", "text": txt,
                    "link": {"page": "patterns"}})
    # ---- habits ---------------------------------------------------------------------------------------------------------
    hab = recurring["habits"]
    if hab:
        top = hab[0]
        out.append({"id": "habit", "tone": "info", "title": f"Small spends add up: {top['merchant']}",
                    "text": f"{top['visits']} visits (about {top['per_week']:.0f} a week) — roughly {_inr(top['monthly_cost'])} a month. " +
                            (f"Across your top {min(3, len(hab))} habits that is {_inr(sum(h['monthly_cost'] for h in hab[:3]))} a month." if len(hab) > 1 else ""),
                    "link": {"page": "patterns"}})
    # ---- unusual -----------------------------------------------------------------------------------------------------------
    if anomalies:
        a = anomalies[0]
        out.append({"id": "anomaly", "tone": "warn", "title": f"{len(anomalies)} unusual payment{'s' if len(anomalies) != 1 else ''}",
                    "text": f"Most notable: {a['date']} · {a['merchant']} · {_inr(a['amount'])} — {a['reasons'][0]['text']}", "link": {"page": "patterns"}})
    # ---- local shops ---------------------------------------------------------------------------------------------------------
    win = A.Period(max(ref - pd.Timedelta(days=89), df["date"].min().date()), ref, "last 90 days")
    shops = A.merchant_ranking(df, win, by="visits", local_only=True, limit=3)
    if shops:
        s = shops[0]
        out.append({"id": "local", "tone": "info", "title": f"Your most-visited local shop: {s['merchant']}",
                    "text": f"{s['days']} days in the last 90 ({_inr(s['amount'])} in total). Next: " + ", ".join(f"{x['merchant']} ({x['days']})" for x in shops[1:3]) + "." if len(shops) > 1 else f"{s['days']} days in the last 90 ({_inr(s['amount'])} in total).",
                    "link": {"page": "ask", "q": "Which local shops do I visit most?"}})
    # ---- p2p share --------------------------------------------------------------------------------------------------------------
    full = A.Period(df["date"].min().date(), ref, "all")
    o = A.overview(df, full)
    if o["spend"] and o["p2p_spend"] / o["spend"] >= 0.2:
        out.append({"id": "p2p", "tone": "info", "title": f"{o['p2p_spend'] / o['spend'] * 100:.0f}% of your spending goes to people",
                    "text": f"{_inr(o['p2p_spend'])} was sent to individuals (friends, family, personal accounts) and {_inr(o['shop_spend'])} to shops and companies. "
                            "Some of the 'people' may really be shops that take money on a personal QR — you can tell the app on the Review page.",
                    "link": {"page": "review"}})
    order = {"warn": 0, "good": 1, "info": 2}
    out.sort(key=lambda i: order[i["tone"]])
    return out[:limit]
