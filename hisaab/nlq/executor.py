"""Execute a QueryPlan against the transactions and write the answer.

ALL numbers come from here (pandas over the stored transactions) - never from a language model.  Every answer carries
  * the plan ("How I understood your question")  * a table/chart with the breakdown  * the actual transactions used (evidence)
so the user can check any figure.  Answer text is assembled from templates for the same reason: nothing is free-form.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field

import pandas as pd

from .. import taxonomy
from ..core import analytics as A
from ..textutil import fmt_inr
from . import timeparse
from .parser import QueryPlan

EXAMPLES = ["How much did I spend on groceries last month?", "Which local shops do I visit most?", "Where did my spending increase?",
            "Show my recurring expenses.", "Any unusual transactions?", "How much did I send to friends this month?",
            "Top 5 merchants by spend", "What was my biggest expense in September?"]


@dataclass
class Env:
    df: pd.DataFrame
    recurring: dict = field(default_factory=lambda: {"recurring": [], "habits": []})
    anomalies: list = field(default_factory=list)


def when(p) -> str:
    """'in September 2026' / 'across all your data (…)' / 'over the last 30 days' / 'during last week (…)' - reads naturally in a sentence."""
    if p is None:
        return ""
    lab = p.label
    first = lab.split(" ")[0]
    if first in calendar.month_name[1:] or first.isdigit():
        return f"in {lab}"
    if lab.startswith(("all time", "all your data")):
        return f"across {lab.replace('all time', 'all your data')}"
    if lab.startswith(("last ", "past ")) and lab.split(" ")[1].isdigit():
        return f"over the {lab}"
    if lab.startswith(("yesterday", "today")):
        return f"on {lab}"
    if lab.startswith(("since ",)):
        return lab
    return f"during {lab}"


def change_phrase(now: float, before: float, label: str) -> str | None:
    if not before:
        return None
    pct = (now - before) / before * 100
    if abs(pct) < 1:
        return f"About the same as {label} ({_inr(before)})."
    return f"That is {abs(pct):.0f}% {'higher' if pct > 0 else 'lower'} than {label} ({_inr(before)})."


def _inr(x: float) -> str:
    return fmt_inr(x, 0 if abs(x) >= 100 or float(x).is_integer() else 2)


def _pct(a: float, b: float) -> str | None:
    return f"{(a - b) / b * 100:+.0f}%" if b else None


def _filter(df: pd.DataFrame, plan: QueryPlan) -> pd.DataFrame:
    d = A.in_period(df, plan.period)
    if plan.direction == "credit":
        d = d[(d["direction"] == "credit") & ~d["category"].isin(taxonomy.NOT_SPEND)]
    else:
        d = d[A.spend_mask(d)]
    if plan.category:
        d = d[d["category"] == plan.category]
    if plan.merchant_id is not None:
        d = d[d["merchant_id"] == plan.merchant_id]
    if plan.kind:
        d = d[d["kind"] == plan.kind]
    if plan.local_only:
        d = d[d["is_local"] & (d["kind"] == "merchant")]
    if plan.weekend == "weekend":
        d = d[d["weekday"] >= 5]
    elif plan.weekend == "weekday":
        d = d[d["weekday"] < 5]
    return d


def _scope_words(plan: QueryPlan) -> str:
    bits = []
    if plan.merchant:
        bits.append(plan.merchant)
    if plan.category:
        bits.append(plan.category)
    if plan.kind == "person":
        bits.append("people (P2P)")
    if plan.local_only:
        bits.append("local shops")
    if plan.weekend:
        bits.append("weekends" if plan.weekend == "weekend" else "weekdays")
    return " · ".join(bits)


def _interpretation(plan: QueryPlan) -> list[str]:
    label = {"total_spend": "Total", "top_merchants": "Top merchants", "top_categories": "Category breakdown", "compare": "Compare periods",
             "recurring": "Recurring payments", "anomalies": "Unusual payments", "list_transactions": "List", "largest": "Largest payments",
             "count": "Count", "average": "Average", "income": "Money received", "net": "Net (in − out)", "summary": "Summary",
             "needs_review": "Needs review", "help": "Help"}.get(plan.intent, plan.intent)
    parts = [f"What: {label} ({'money received' if plan.direction == 'credit' else 'money spent'})"]
    if plan.period:
        parts.append(f"When: {plan.period.label}")
    sc = _scope_words(plan)
    if sc:
        parts.append(f"Filter: {sc}")
    return parts + [f"Note: {n}" for n in plan.notes if not n.startswith(("merchant =", "category ="))]


def _txns(d: pd.DataFrame, n: int = 12, by: str = "amount") -> list[dict]:
    if d.empty:
        return []
    d = d.sort_values(by, ascending=False) if by == "amount" else d.sort_values("ts", ascending=False)
    return A.txn_records(d.head(n))


def _follow(*qs: str) -> list[str]:
    return list(qs)


def run(plan: QueryPlan, env: Env) -> dict:
    df = env.df
    base = {"ok": True, "intent": plan.intent, "question": plan.question, "plan": plan.to_dict(), "interpretation": _interpretation(plan),
            "table": None, "chart": None, "txns": [], "followups": [], "value": None}
    if plan.clarify:
        base.update(headline="Which one do you mean?", text=plan.clarify["question"], clarify=plan.clarify)
        return base
    if df.empty:
        base.update(ok=False, headline="No data yet", text="Import a statement (or load the demo data) first, then ask me about it.")
        return base
    fn = {"total_spend": _total, "top_merchants": _top_merchants, "top_categories": _top_categories, "compare": _compare, "recurring": _recurring,
          "anomalies": _anomalies, "list_transactions": _list, "largest": _largest, "count": _count, "average": _average, "income": _income,
          "net": _net, "summary": _summary, "needs_review": _needs_review, "help": _help}.get(plan.intent)
    if fn is None:
        return _unknown(plan, base)
    out = fn(plan, env, base)
    return out


# ---- intents -------------------------------------------------------------------------------------------------------------
def _total(plan: QueryPlan, env: Env, base: dict) -> dict:
    d = _filter(env.df, plan)
    p = plan.period
    cred = plan.direction == "credit"
    if d.empty:
        msg = f"I found no {'credits' if cred else 'payments'}{(' for ' + _scope_words(plan)) if _scope_words(plan) else ''} {when(p) if p else 'in your data'}."
        if plan.merchant_id is not None:
            allm = env.df[(env.df["merchant_id"] == plan.merchant_id) & (env.df["direction"] == ("credit" if cred else "debit"))]
            if len(allm):
                last = allm.sort_values("ts").iloc[-1]
                msg += (f" Overall you have {len(allm)} such payment{'s' if len(allm) != 1 else ''} to {plan.merchant} between "
                        f"{allm['date'].min():%d %b} and {allm['date'].max():%d %b %Y}; the latest was {_inr(last['amount'])} on {last['date']:%d %b %Y}.")
        base.update(headline="Nothing found", value=0.0, text=msg, followups=_follow("Show my top categories", "Which merchants do I pay most?"))
        return base
    total, n = float(d["amount"].sum()), len(d)
    verb = "received" if cred else "spent"
    if plan.merchant:
        verb = "received from" if cred else "paid"
        what = f" {plan.merchant}" if not cred else f" {plan.merchant}"
        head = f"{_inr(total)} {'from' if cred else 'to'} {plan.merchant}"
    elif plan.category:
        head = f"{_inr(total)} on {plan.category}" if not cred else f"{_inr(total)} from {plan.category}"
    elif plan.kind == "person":
        head = f"{_inr(total)} {'from' if cred else 'to'} people"
    else:
        head = f"{_inr(total)}"
    wk = {"weekend": " on weekends", "weekday": " on weekdays"}.get(plan.weekend or "", "")
    if plan.merchant and not cred:
        text = f"You paid {plan.merchant} {_inr(total)}"
    elif plan.merchant and cred:
        text = f"You received {_inr(total)} from {plan.merchant}"
    elif plan.category and not cred:
        text = f"You spent {_inr(total)} on {plan.category}"
    elif plan.kind == "person" and not cred:
        text = f"You sent {_inr(total)} to people"
    elif plan.kind == "person" and cred:
        text = f"You received {_inr(total)} from people"
    else:
        text = f"You {verb} {_inr(total)}"
    if plan.local_only:
        text += " at local shops"
    text += wk
    text += f" {when(p)}" if p else ""
    text += f" — {n} {'credit' if cred else 'payment'}{'s' if n != 1 else ''}, about {_inr(total / n)} each."
    # share of total + comparison with the previous period
    extra = []
    if p and not cred:
        allp = A.in_period(env.df, p)
        alltot = float(allp[A.spend_mask(allp)]["amount"].sum())
        if (plan.category or plan.merchant or plan.kind or plan.local_only or plan.weekend) and alltot:
            extra.append(f"That is {total / alltot * 100:.0f}% of everything you spent {when(p)} ({_inr(alltot)}).")
    if p and p.days <= 62:
        prev = timeparse.previous_period(p)
        if prev.end >= env.df["date"].min().date():
            pp = plan.__class__(**{**plan.__dict__})
            pp.period = prev
            dp = _filter(env.df, pp)
            if len(dp):
                ph = change_phrase(total, float(dp["amount"].sum()), prev.label)
                if ph:
                    extra.append(ph)
    text = (text + " " + " ".join(extra)).strip()
    base.update(headline=head, value=round(total, 2), text=text, txns=_txns(d))
    # breakdown
    if plan.merchant_id is None:
        grp = d.groupby("merchant").agg(amount=("amount", "sum"), n=("id", "size")).sort_values("amount", ascending=False).head(6)
        if plan.category is None and plan.kind is None and not plan.local_only:
            grp = d.groupby("category").agg(amount=("amount", "sum"), n=("id", "size")).sort_values("amount", ascending=False).head(8)
            base["table"] = {"columns": ["Category", "Amount", "Payments"], "rows": [[i, _inr(r.amount), int(r.n)] for i, r in grp.iterrows()]}
        else:
            base["table"] = {"columns": ["Merchant", "Amount", "Payments"], "rows": [[i, _inr(r.amount), int(r.n)] for i, r in grp.iterrows()]}
        base["chart"] = {"type": "bars", "title": "Breakdown", "labels": list(grp.index), "values": [round(float(v), 2) for v in grp["amount"]]}
    base["followups"] = _follow("Where did my spending increase?", "Which local shops do I visit most?", "Show my recurring expenses.")
    return base


def _top_merchants(plan: QueryPlan, env: Env, base: dict) -> dict:
    by = "visits" if plan.order_by == "visits" else "amount"
    rows = A.merchant_ranking(env.df, plan.period, by=by, local_only=plan.local_only, kind=plan.kind, category=plan.category,
                              limit=plan.limit, direction="credit" if plan.direction == "credit" else "debit")
    p = plan.period
    what = "local shops" if plan.local_only else "merchants"
    if not rows:
        base.update(headline="Nothing found", text=f"I found no {what} {when(p) if p else 'in your data'}.", followups=_follow("Show my top categories"))
        return base
    lead = "visited most often" if by == "visits" else "where you spent the most"
    lines = [f"{i + 1}. {r['merchant']} — {r['days']} day{'s' if r['days'] != 1 else ''} ({r['visits']} payment{'s' if r['visits'] != 1 else ''}), {_inr(r['amount'])}"
             for i, r in enumerate(rows)]
    text = f"Your {what} {lead} {when(p)}: " + "; ".join(lines[:5]) + "."
    base.update(headline=f"{rows[0]['merchant']} comes first", text=text,
                table={"columns": ["#", "Merchant", "Days visited", "Payments", "Total", "Avg", "Category"],
                       "rows": [[i + 1, r["merchant"], r["days"], r["visits"], _inr(r["amount"]), _inr(r["avg"]), r["category"]] for i, r in enumerate(rows)]},
                chart={"type": "bars", "title": "Days visited" if by == "visits" else "Total spent", "labels": [r["merchant"] for r in rows],
                       "values": [r["days"] if by == "visits" else r["amount"] for r in rows]},
                followups=_follow("Which shops do I spend the most at?", "Show my recurring expenses.", "Where did my spending increase?"))
    return base


def _top_categories(plan: QueryPlan, env: Env, base: dict) -> dict:
    rows = A.category_breakdown(env.df, plan.period, "credit" if plan.direction == "credit" else "debit")
    if not rows:
        base.update(headline="Nothing found", text="No spending found for that period.")
        return base
    p = plan.period
    top = rows[:plan.limit]
    text = f"{when(p).capitalize()} your money went mostly to: " + "; ".join(f"{r['category']} {_inr(r['amount'])} ({r['share'] * 100:.0f}%)" for r in top) + "."
    base.update(headline=f"{rows[0]['category']} is your biggest category", text=text,
                table={"columns": ["Category", "Amount", "Share", "Payments"], "rows": [[r["category"], _inr(r["amount"]), f"{r['share'] * 100:.0f}%", r["count"]] for r in rows]},
                chart={"type": "bars", "title": "Spending by category", "labels": [r["category"] for r in rows], "values": [r["amount"] for r in rows]},
                followups=_follow("Which shops do I spend the most at?", "Where did my spending increase?", "How much did I spend on groceries last month?"))
    return base


def _compare(plan: QueryPlan, env: Env, base: dict) -> dict:
    a, b = plan.period, plan.compare_to
    r = A.compare(env.df, a, b, plan.group, limit=6)
    if r["recent_total"] == 0 and r["base_total"] == 0:
        base.update(headline="Nothing to compare", text=f"I found no spending in {a.label} or {b.label}.")
        return base
    delta, pct = r["delta"], r["pct"]
    direction = "up" if delta > 0 else "down"
    head = f"Spending {direction} {abs(pct):.0f}%" if pct is not None else f"Spending {direction} {_inr(abs(delta))}"
    text = f"Your spending went {direction} by {_inr(abs(delta))}" + (f" ({pct:+.0f}%)" if pct is not None else "") + f": {_inr(r['recent_total'])} in {a.label} vs {_inr(r['base_total'])} in {b.label}."
    if a.days != b.days:
        text += f" (Note: {a.label} has {a.days} days, {b.label} has {b.days} days.)"
    first, second = ("increases", "decreases") if plan.trend == "increase" else ("decreases", "increases")
    def fmt(row):
        pc = f", {row['pct']:+.0f}%" if row["pct"] is not None else " (new)"
        return f"{row['name']} {'+' if row['delta'] > 0 else '−'}{_inr(abs(row['delta']))} ({_inr(row['base'])} → {_inr(row['recent'])}{pc})"
    if r[first]:
        text += f" Biggest {first[:-1] if first.endswith('s') else first}s: " + "; ".join(fmt(x) for x in r[first][:3]) + "."
    if r[second]:
        text += f" Biggest {second[:-1] if second.endswith('s') else second}s: " + "; ".join(fmt(x) for x in r[second][:2]) + "."
    rows = [[x["name"], _inr(x["base"]), _inr(x["recent"]), ("+" if x["delta"] > 0 else "−") + _inr(abs(x["delta"])), f"{x['pct']:+.0f}%" if x["pct"] is not None else "new"]
            for x in (r["increases"] + r["decreases"])]
    movers = r["increases"][:3] + r["decreases"][:3]
    base.update(headline=head, value=delta, text=text,
                table={"columns": [r["group"].title(), b.label, a.label, "Change", "%"], "rows": rows},
                chart={"type": "bars", "title": "Change vs " + b.label, "labels": [x["name"] for x in movers], "values": [x["delta"] for x in movers]},
                followups=_follow("Which merchants increased the most?", "Show my recurring expenses.", "Any unusual transactions?"))
    if r[first]:
        top_name = r[first][0]["name"]
        d = A.in_period(env.df, a)
        d = d[A.spend_mask(d) & (d["category" if r["group"] == "category" else "merchant"] == top_name)]
        base["txns"] = _txns(d)
    return base


def _recurring(plan: QueryPlan, env: Env, base: dict) -> dict:
    rec = [r for r in env.recurring["recurring"] if r["direction"] == ("credit" if plan.direction == "credit" else "debit")]
    if plan.merchant_id is not None:
        rec = [r for r in rec if r["merchant_id"] == plan.merchant_id]
    if plan.category:
        rec = [r for r in rec if r["category"] == plan.category]
    habits = env.recurring["habits"]
    if not rec:
        base.update(headline="No recurring payments found", text="I did not find payments that repeat on a regular schedule (I need at least 3 similar payments).")
        return base
    total = sum(r["monthly_cost"] for r in rec)
    text = f"You have {len(rec)} recurring {'payments' if plan.direction == 'debit' else 'credits'}, about {_inr(total)} per month: " + \
           "; ".join(f"{r['merchant']} {_inr(r['last_amount'])}/{r['period'].replace('ly', '') if r['period'] != 'monthly' else 'month'}" for r in rec[:6]) + "."
    over = [r for r in rec if r["status"] == "overdue"]
    if over:
        text += " Possibly stopped or missed: " + ", ".join(f"{r['merchant']} (expected {r['next_expected']})" for r in over) + "."
    pcs = [r for r in rec if r["price_change"]]
    if pcs:
        text += " Price change: " + ", ".join(f"{r['merchant']} {_inr(r['price_change']['from'])} → {_inr(r['price_change']['to'])}" for r in pcs) + "."
    if habits and plan.direction == "debit":
        text += f" Not subscriptions but worth knowing — daily habits: " + ", ".join(f"{h['merchant']} ≈ {_inr(h['monthly_cost'])}/month" for h in habits[:3]) + "."
    base.update(headline=f"{len(rec)} recurring · ≈ {_inr(total)}/month", value=round(total, 2), text=text,
                table={"columns": ["Merchant", "Repeats", "Amount", "Next expected", "Status", "≈ per month"],
                       "rows": [[r["merchant"], r["period"], _inr(r["last_amount"]), r["next_expected"], r["status"] + (f" ({r['days_overdue']}d)" if r["status"] == "overdue" else ""),
                                 _inr(r["monthly_cost"])] for r in rec]},
                followups=_follow("Any unusual transactions?", "Where did my spending increase?"))
    return base


def _anomalies(plan: QueryPlan, env: Env, base: dict) -> dict:
    items = env.anomalies
    if plan.period and plan.period.start != env.df["date"].min().date():
        items = [a for a in items if plan.period.start.isoformat() <= a["date"] <= plan.period.end.isoformat()]
    if plan.merchant_id is not None:
        items = [a for a in items if a["merchant_id"] == plan.merchant_id]
    if not items:
        base.update(headline="Nothing unusual", text="I found nothing unusual in that period.")
        return base
    top = items[:plan.limit]
    text = f"{len(items)} payment{'s' if len(items) != 1 else ''} look unusual. " + " ".join(
        f"[{a['severity']}] {a['date']} {a['merchant']} {_inr(a['amount'])}: {a['reasons'][0]['text']}" for a in top[:3])
    base.update(headline=f"{len(items)} unusual payment{'s' if len(items) != 1 else ''}", text=text,
                table={"columns": ["Date", "Merchant", "Amount", "Severity", "Why"], "rows": [[a["date"], a["merchant"], _inr(a["amount"]), a["severity"], a["reasons"][0]["text"]] for a in items[:plan.limit + 5]]},
                followups=_follow("Show my recurring expenses.", "Where did my spending increase?"))
    ids = [a["txn_id"] for a in items[:8]]
    base["txns"] = A.txn_records(env.df[env.df["id"].isin(ids)])
    return base


def _list(plan: QueryPlan, env: Env, base: dict) -> dict:
    d = _filter(env.df, plan)
    if d.empty:
        base.update(headline="Nothing found", text="No matching transactions.")
        return base
    d = d.sort_values("ts", ascending=False)
    base.update(headline=f"{len(d)} transactions · {_inr(d['amount'].sum())}", value=round(float(d["amount"].sum()), 2),
                text=f"Showing the latest {min(len(d), 15)} of {len(d)} matching transactions (total {_inr(d['amount'].sum())}).", txns=A.txn_records(d.head(15)))
    return base


def _largest(plan: QueryPlan, env: Env, base: dict) -> dict:
    d = _filter(env.df, plan)
    if d.empty:
        base.update(headline="Nothing found", text="No matching payments.")
        return base
    top = d.sort_values("amount", ascending=False).head(plan.limit)
    recs = A.txn_records(top)
    base.update(headline=f"Biggest: {_inr(top['amount'].iloc[0])} at {top['merchant'].iloc[0]}", value=float(top["amount"].iloc[0]),
                text=f"Your biggest {'credit' if plan.direction == 'credit' else 'payment'}{' ' + when(plan.period) if plan.period else ''} was {_inr(top['amount'].iloc[0])} "
                     f"to {top['merchant'].iloc[0]} on {top['date'].iloc[0]:%d %b %Y}. Next: " + "; ".join(f"{_inr(r.amount)} at {r.merchant}" for r in top.iloc[1:4].itertuples()) + ".",
                txns=recs)
    return base


def _count(plan: QueryPlan, env: Env, base: dict) -> dict:
    d = _filter(env.df, plan)
    n, days = len(d), int(d["date"].nunique())
    what = plan.merchant or plan.category or ("people" if plan.kind == "person" else "payments")
    base.update(headline=f"{n} payment{'s' if n != 1 else ''}", value=n,
                text=f"You made {n} payment{'s' if n != 1 else ''} to {what}{' ' + when(plan.period) if plan.period else ''}, on {days} different day{'s' if days != 1 else ''} (total {_inr(d['amount'].sum())}).",
                txns=_txns(d, by="ts"))
    return base


def _average(plan: QueryPlan, env: Env, base: dict) -> dict:
    d = _filter(env.df, plan)
    if d.empty:
        base.update(headline="Nothing found", text="No matching payments.")
        return base
    avg, days = float(d["amount"].mean()), max(plan.period.days if plan.period else 1, 1)
    what = plan.merchant or plan.category or "payment"
    base.update(headline=f"Average {_inr(avg)}", value=round(avg, 2),
                text=f"Your average {what}{'' if (plan.merchant or plan.category) is None else ' payment'} is {_inr(avg)} across {len(d)} payments; typical (median) {_inr(float(d['amount'].median()))}. "
                     f"That is about {_inr(d['amount'].sum() / days)} per day.", txns=_txns(d))
    return base


def _income(plan: QueryPlan, env: Env, base: dict) -> dict:
    plan.direction = "credit"
    d = _filter(env.df, plan)
    r = A.in_period(env.df, plan.period)
    refunds = float(r[(r["direction"] == "credit") & (r["category"] == "Refund & Cashback")]["amount"].sum())
    if d.empty:
        base.update(headline="Nothing received", text=f"I found no money received {when(plan.period) if plan.period else 'in your data'}.")
        return base
    src = d.groupby("merchant")["amount"].sum().sort_values(ascending=False).head(5)
    total = float(d["amount"].sum())
    base.update(headline=f"{_inr(total)} received", value=round(total, 2),
                text=f"You received {_inr(total)} {when(plan.period)}, mostly from " + ", ".join(f"{k} ({_inr(v)})" for k, v in src.items()) + (f". Refunds/cashback: {_inr(refunds)}." if refunds else "."),
                table={"columns": ["From", "Amount"], "rows": [[k, _inr(v)] for k, v in src.items()]}, txns=_txns(d))
    return base


def _net(plan: QueryPlan, env: Env, base: dict) -> dict:
    o = A.overview(env.df, plan.period)
    n = o["net"]
    base.update(headline=f"Net {'+' if n >= 0 else '−'}{_inr(abs(n))}", value=n,
                text=f"{when(plan.period).capitalize()} you received {_inr(o['income'])}" + (f" (+{_inr(o['refunds'])} refunds)" if o["refunds"] else "") + f" and spent {_inr(o['spend'])}, so {'you kept' if n >= 0 else 'you overspent by'} {_inr(abs(n))}.")
    return base


def _summary(plan: QueryPlan, env: Env, base: dict) -> dict:
    o = A.overview(env.df, plan.period)
    top = o.get("top_category")
    text = f"{plan.period.label}: spent {_inr(o['spend'])} across {o['n_payments']} payments (about {_inr(o['avg_per_day'])}/day), received {_inr(o['income'])}."
    if top:
        text += f" Biggest category: {top['name']} ({_inr(top['amount'])}, {top['share'] * 100:.0f}%)."
    if o["unreviewed_spend"]:
        text += f" {_inr(o['unreviewed_spend'])} is not yet categorised."
    base.update(headline=f"Spent {_inr(o['spend'])}", value=o["spend"], text=text,
                table={"columns": ["Category", "Amount", "Share"], "rows": [[r["category"], _inr(r["amount"]), f"{r['share'] * 100:.0f}%"] for r in A.category_breakdown(env.df, plan.period)[:8]]})
    return base


def _needs_review(plan: QueryPlan, env: Env, base: dict) -> dict:
    d = env.df
    sp = d[A.spend_mask(d) & d["cat_status"].isin(["unknown", "suggested"])]
    m = sp.groupby("merchant")["amount"].sum().sort_values(ascending=False)
    base.update(headline=f"{len(m)} merchants need a look", text=f"{len(m)} merchants ({_inr(sp['amount'].sum())}) have a guessed or missing category. Open the Review page to confirm them — the biggest few cover most of the money.",
                table={"columns": ["Merchant", "Amount"], "rows": [[k, _inr(v)] for k, v in m.head(8).items()]})
    return base


def _help(plan: QueryPlan, env: Env, base: dict) -> dict:
    base.update(headline="Ask me about your spending", text="I can total spending (by category, shop, person or period), rank shops and categories, compare months, list recurring and unusual payments. Try one of these:",
                followups=EXAMPLES)
    return base


def _unknown(plan: QueryPlan, base: dict) -> dict:
    if plan.refusal:
        base.update(ok=False, headline="I can only read your statements", text=plan.refusal + " Try a question about your spending instead:", followups=EXAMPLES)
        return base
    base.update(ok=False, headline="I did not understand that",
                text="I could not turn that into a question about your transactions, so I will not guess a number. Try wording like one of these:",
                followups=EXAMPLES)
    return base
