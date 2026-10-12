"""Spending analytics: pure functions over the transactions DataFrame (columns documented in Engine.df()).

Definitions used everywhere (so every screen and every chat answer agrees):
  * SPEND   = debits whose category is not neutral (wallet top-ups / own-account moves are not spending)
  * INCOME  = credits that are not neutral and not refunds
  * REFUNDS = credits categorised "Refund & Cashback" (shown separately; they are not netted against spend)
  * "Needs review" money is still counted in total spend - just not in a specific category - so totals are never understated.
  * Months are calendar months; "today" is the date of the LATEST transaction in the data (statements are historical files).
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .. import taxonomy
from ..textutil import mask_vpa


@dataclass(frozen=True)
class Period:
    start: date
    end: date               # inclusive
    label: str

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def to_dict(self) -> dict:
        return {"start": self.start.isoformat(), "end": self.end.isoformat(), "label": self.label, "days": self.days}


def month_period(y: int, m: int, to_day: int | None = None) -> Period:
    last = calendar.monthrange(y, m)[1]
    end = date(y, m, min(to_day or last, last))
    label = f"{calendar.month_name[m]} {y}" + (f" (1–{end.day})" if to_day and end.day < last else "")
    return Period(date(y, m, 1), end, label)


def shift_month(y: int, m: int, delta: int) -> tuple[int, int]:
    k = y * 12 + (m - 1) + delta
    return k // 12, k % 12 + 1


def ref_date(df: pd.DataFrame) -> date:
    return df["date"].max().date()


def spend_mask(df: pd.DataFrame) -> pd.Series:
    return (df["direction"] == "debit") & ~df["category"].isin(taxonomy.NOT_SPEND)


def income_mask(df: pd.DataFrame) -> pd.Series:
    return (df["direction"] == "credit") & ~df["category"].isin(taxonomy.NOT_SPEND) & (df["category"] != "Refund & Cashback")


def in_period(df: pd.DataFrame, p: Period | None) -> pd.DataFrame:
    if p is None or df.empty:
        return df
    return df[(df["date"] >= pd.Timestamp(p.start)) & (df["date"] <= pd.Timestamp(p.end))]


def _f(x) -> float:
    return round(float(x), 2)


# ---- headline numbers ---------------------------------------------------------------------------------------------------
def overview(df: pd.DataFrame, p: Period, prev: Period | None = None) -> dict:
    d = in_period(df, p)
    sp, inc = d[spend_mask(d)], d[income_mask(d)]
    refunds = d[(d["direction"] == "credit") & (d["category"] == "Refund & Cashback")]
    out = {
        "period": p.to_dict(), "spend": _f(sp["amount"].sum()), "income": _f(inc["amount"].sum()), "refunds": _f(refunds["amount"].sum()),
        "n_payments": int(len(sp)), "avg_per_day": _f(sp["amount"].sum() / max(p.days, 1)),
        "avg_payment": _f(sp["amount"].mean()) if len(sp) else 0.0,
        "p2p_spend": _f(sp.loc[sp["kind"] == "person", "amount"].sum()),
        "shop_spend": _f(sp.loc[sp["kind"] == "merchant", "amount"].sum()),
        "cash_spend": _f(sp.loc[sp["kind"] == "cash", "amount"].sum()),
        "unreviewed_spend": _f(sp.loc[sp["category"] == taxonomy.UNCATEGORISED, "amount"].sum()),
        "unreviewed_share": _f(sp.loc[sp["category"] == taxonomy.UNCATEGORISED, "amount"].sum() / sp["amount"].sum()) if len(sp) and sp["amount"].sum() else 0.0,
    }
    unsure = sp[sp["cat_status"].isin(["unknown", "suggested"])]["amount"].sum()
    out["uncertain_spend"] = _f(unsure)                       # unknown + best-guess categories: what a human should confirm
    out["uncertain_share"] = _f(unsure / sp["amount"].sum()) if len(sp) and sp["amount"].sum() else 0.0
    out["net"] = _f(out["income"] + out["refunds"] - out["spend"])
    if len(sp):
        big = sp.loc[sp["amount"].idxmax()]
        out["biggest"] = {"id": int(big["id"]), "amount": _f(big["amount"]), "merchant": big["merchant"], "date": big["date"].date().isoformat()}
        top = sp.groupby("category")["amount"].sum().sort_values(ascending=False)
        out["top_category"] = {"name": top.index[0], "amount": _f(top.iloc[0]), "share": _f(top.iloc[0] / top.sum())}
    if prev is not None:
        pv = in_period(df, prev)
        psp = pv[spend_mask(pv)]["amount"].sum()
        out["prev"] = {"period": prev.to_dict(), "spend": _f(psp), "delta": _f(out["spend"] - psp),
                       "pct": _f((out["spend"] - psp) / psp * 100) if psp else None}
    return out


def category_breakdown(df: pd.DataFrame, p: Period | None, direction: str = "debit") -> list[dict]:
    d = in_period(df, p)
    d = d[spend_mask(d)] if direction == "debit" else d[income_mask(d)]
    if d.empty:
        return []
    g = d.groupby("category")["amount"].agg(["sum", "count", "mean"]).sort_values("sum", ascending=False)
    tot = g["sum"].sum()
    return [{"category": c, "amount": _f(r["sum"]), "count": int(r["count"]), "avg": _f(r["mean"]), "share": _f(r["sum"] / tot)}
            for c, r in g.iterrows()]


def monthly_matrix(df: pd.DataFrame) -> dict:
    sp = df[spend_mask(df)]
    inc = df[income_mask(df)]
    months = sorted(set(df["month"]))
    cats = list(sp.groupby("category")["amount"].sum().sort_values(ascending=False).index)
    piv = sp.pivot_table(index="month", columns="category", values="amount", aggfunc="sum", fill_value=0.0).reindex(months, fill_value=0.0)
    last_month = months[-1] if months else None
    # is the last month partial?  (its last day is not the last day of the month)
    partial = False
    if last_month:
        y, m = map(int, last_month.split("-"))
        partial = ref_date(df) < date(y, m, calendar.monthrange(y, m)[1])
    return {
        "months": months, "partial_last": partial,
        "series": {c: [_f(piv[c].get(mo, 0.0)) if c in piv else 0.0 for mo in months] for c in cats},
        "spend": [_f(sp.loc[sp["month"] == mo, "amount"].sum()) for mo in months],
        "income": [_f(inc.loc[inc["month"] == mo, "amount"].sum()) for mo in months],
    }


def daily_series(df: pd.DataFrame, p: Period) -> list[dict]:
    d = in_period(df, p)
    d = d[spend_mask(d)]
    s = d.groupby("date")["amount"].sum()
    out, cur = [], p.start
    while cur <= p.end:
        out.append({"date": cur.isoformat(), "amount": _f(s.get(pd.Timestamp(cur), 0.0))})
        cur += timedelta(days=1)
    return out


# ---- merchants -----------------------------------------------------------------------------------------------------------
def merchant_ranking(df: pd.DataFrame, p: Period | None, by: str = "amount", local_only: bool = False, kind: str | None = None,
                     category: str | None = None, limit: int = 10, direction: str = "debit") -> list[dict]:
    d = in_period(df, p)
    d = d[spend_mask(d)] if direction == "debit" else d[d["direction"] == "credit"]
    if local_only:
        d = d[d["is_local"] & (d["kind"] == "merchant")]
    if kind:
        d = d[d["kind"] == kind]
    if category:
        d = d[d["category"] == category]
    if d.empty:
        return []
    g = d.groupby("merchant_id").agg(merchant=("merchant", "first"), amount=("amount", "sum"), visits=("id", "size"),
                                      days=("date", "nunique"), last=("date", "max"), category=("category", lambda s: s.mode().iat[0]),
                                      kind=("kind", "first"), is_local=("is_local", "first"), avg=("amount", "mean"))
    g = g.sort_values(["days", "amount"] if by == "visits" else ["amount", "days"], ascending=False).head(limit)
    return [{"merchant_id": int(i), "merchant": r["merchant"], "amount": _f(r["amount"]), "visits": int(r["visits"]), "days": int(r["days"]),
             "avg": _f(r["avg"]), "last": r["last"].date().isoformat(), "category": r["category"], "kind": r["kind"], "is_local": bool(r["is_local"])}
            for i, r in g.iterrows()]


def compare(df: pd.DataFrame, a: Period, b: Period, group: str = "category", limit: int = 8) -> dict:
    """b = the earlier/base period, a = the recent one.  Returns the biggest INCREASES and DECREASES with their share of the change."""
    da, db = in_period(df, a), in_period(df, b)
    da, db = da[spend_mask(da)], db[spend_mask(db)]
    key = "category" if group == "category" else "merchant"
    sa, sb = da.groupby(key)["amount"].sum(), db.groupby(key)["amount"].sum()
    names = sorted(set(sa.index) | set(sb.index))
    rows = []
    for n in names:
        x, y = float(sa.get(n, 0.0)), float(sb.get(n, 0.0))
        rows.append({"name": n, "recent": _f(x), "base": _f(y), "delta": _f(x - y), "pct": _f((x - y) / y * 100) if y else None})
    total_delta = float(sa.sum() - sb.sum())
    for r in rows:
        r["share_of_change"] = _f(r["delta"] / total_delta) if total_delta else None
    inc = sorted([r for r in rows if r["delta"] > 0], key=lambda r: -r["delta"])[:limit]
    dec = sorted([r for r in rows if r["delta"] < 0], key=lambda r: r["delta"])[:limit]
    return {"recent": a.to_dict(), "base": b.to_dict(), "recent_total": _f(sa.sum()), "base_total": _f(sb.sum()), "delta": _f(total_delta),
            "pct": _f(total_delta / sb.sum() * 100) if sb.sum() else None, "increases": inc, "decreases": dec, "group": group}


def weekday_hour(df: pd.DataFrame, p: Period | None = None) -> dict:
    d = in_period(df, p)
    d = d[spend_mask(d)]
    wd = d.groupby("weekday")["amount"].agg(["sum", "count"]).reindex(range(7), fill_value=0)
    timed = d[d["has_time"]]
    hr = timed.groupby("hour")["amount"].agg(["sum", "count"]).reindex(range(24), fill_value=0) if len(timed) else None
    return {"weekday": [{"day": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][i], "amount": _f(r["sum"]), "count": int(r["count"])}
                        for i, r in wd.iterrows()],
            "hour": [{"hour": int(i), "amount": _f(r["sum"]), "count": int(r["count"])} for i, r in hr.iterrows()] if hr is not None else None,
            "time_coverage": _f(d["has_time"].mean()) if len(d) else 0.0}


def projection(df: pd.DataFrame, today: date) -> dict | None:
    """Naive month-end projection: spend so far / days elapsed x days in month.  Clearly an estimate."""
    y, m = today.year, today.month
    days_in = calendar.monthrange(y, m)[1]
    if today.day >= days_in:
        return None
    p = month_period(y, m, today.day)
    spent = in_period(df, p)
    spent = spent[spend_mask(spent)]["amount"].sum()
    py, pm = shift_month(y, m, -1)
    last = in_period(df, month_period(py, pm))
    last_total = last[spend_mask(last)]["amount"].sum()
    return {"month": f"{calendar.month_name[m]} {y}", "spent_so_far": _f(spent), "days_elapsed": today.day, "days_in_month": days_in,
            "projected": _f(spent / today.day * days_in), "last_month": _f(last_total)}


# ---- transactions as records for the UI ---------------------------------------------------------------------------------------
def txn_records(d: pd.DataFrame, limit: int | None = None) -> list[dict]:
    out = []
    for r in d.itertuples(index=False):
        out.append({
            "id": int(r.id), "date": r.date.date().isoformat(), "time": r.ts.strftime("%H:%M") if r.has_time else "",
            "amount": _f(r.amount), "direction": r.direction, "merchant_id": int(r.merchant_id), "merchant": r.merchant,
            "kind": r.kind, "category": r.category, "cat_status": r.cat_status, "cat_conf": _f(r.cat_conf) if r.cat_conf == r.cat_conf else None,
            "cat_source": r.cat_source, "locked": bool(r.locked), "is_local": bool(r.is_local), "is_brand": bool(r.is_brand),
            "description": _mask_text(r.description), "mode": r.mode,
        })
        if limit and len(out) >= limit:
            break
    return out


def _mask_text(s: str | None) -> str:
    import re
    return re.sub(r"[A-Za-z0-9._\-]{1,63}@[A-Za-z][A-Za-z0-9]{1,23}", lambda m: mask_vpa(m.group(0)), s or "")
