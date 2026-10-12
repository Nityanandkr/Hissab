"""Natural-language question  ->  QueryPlan (a small, explicit, inspectable description of what to compute).

THE DESIGN IDEA: the language layer only *understands*; a separate deterministic executor *computes*.
  * An LLM can be fluent but it can also invent numbers - so it is never asked to add up transactions.
  * The plan is shown to the user ("I understood: spend · Groceries · September 2026"), so a misunderstanding is visible.
  * The rules parser below is free, instant, works offline, and is fully testable.  An optional LLM
    (nlq/llm.py) can fill the gaps for questions the rules do not cover; it emits the SAME QueryPlan JSON and is validated by the
    same code, and it never sees transactions - only the question and the vocabulary.
Hinglish is supported for the common words ("pichle mahine kitna kharcha kiya groceries pe").
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from .. import seeds, taxonomy
from ..core.analytics import Period, month_period, shift_month
from ..core.normalize import osa_distance
from ..textutil import tokens_of
from . import timeparse

INTENTS = ["total_spend", "top_merchants", "top_categories", "compare", "recurring", "anomalies", "list_transactions",
           "largest", "count", "average", "income", "net", "summary", "needs_review", "help", "unknown"]

HINGLISH = [
    ("pichle mahine", "last month"), ("pichhle mahine", "last month"), ("pichle hafte", "last week"), ("is mahine", "this month"),
    ("is hafte", "this week"), ("is saal", "this year"), ("pichle saal", "last year"), ("kitna kharcha", "how much spent"),
    ("kitne paise", "how much"), ("kitna paisa", "how much"), ("kharcha kiya", "spent"), ("sabse zyada", "most"), ("sabse jyada", "most"),
    ("sabse kam", "least"), ("kahan kahan", "where"), ("kis par", "on which"), ("kitna", "how much"), ("kharcha", "spent"),
    ("kharch", "spent"), ("kharche", "spent"), ("dukaan", "shop"), ("dukan", "shop"), ("dikhao", "show"), ("batao", "show"),
    ("dikha", "show"), ("kahan", "where"), ("aaj", "today"), ("kal", "yesterday"), ("paise", "money"), ("paisa", "money"),
    ("mera", "my"), ("meri", "my"), ("mere", "my"), ("mujhe", "me"), ("pe ", "on "), ("par ", "on "), ("mein ", "in "),
    ("aamdani", "income"), ("kamai", "income"), ("badha", "increased"), ("badhe", "increased"), ("ghata", "decreased"),
]

CATEGORY_WORDS: dict[str, list[str]] = {
    "Groceries": ["groceries", "grocery", "kirana", "provision", "provisions", "vegetables", "veggies", "fruits", "milk", "dairy", "sabzi",
                  "supermarket", "ration"],
    "Food & Dining": ["food", "dining", "eating out", "restaurants", "restaurant", "cafe", "chai", "tea", "coffee", "snacks", "canteen",
                      "mess", "meals", "food delivery", "takeaway", "khana", "eating", "dinner", "lunch", "breakfast"],
    "Travel & Transport": ["travel", "transport", "commute", "commuting", "cab", "cabs", "taxi", "autos", "auto", "rickshaw", "fuel", "petrol",
                           "diesel", "train", "trains", "bus", "flight", "flights", "metro", "trip", "trips", "yatra", "ride", "rides"],
    "Shopping": ["shopping", "clothes", "clothing", "fashion", "electronics", "gadgets", "online shopping", "shoes"],
    "Bills & Recharge": ["bills", "bill", "recharge", "recharges", "electricity", "utilities", "broadband", "wifi", "dth", "mobile recharge",
                         "prepaid", "phone bill"],
    "Subscriptions & Entertainment": ["subscriptions", "subscription", "entertainment", "movies", "movie", "ott", "streaming", "games",
                                      "gaming", "music", "fun"],
    "Health & Medical": ["health", "medical", "medicine", "medicines", "pharmacy", "doctor", "hospital", "clinic", "dawai", "healthcare"],
    "Education": ["education", "books", "stationery", "xerox", "photocopy", "printing", "course", "courses", "tuition", "exam", "study"],
    "Personal Care": ["personal care", "salon", "haircut", "grooming", "laundry", "gym", "fitness", "beauty", "barber"],
    "Rent & Hostel": ["rent", "hostel", "pg", "accommodation", "housing"],
    "Cash Withdrawal": ["cash", "atm", "withdrawals", "withdrawal"],
    "Finance & Fees": ["fees", "bank charges", "charges", "emi", "insurance", "investments", "loan"],
    "Transfers (P2P)": ["transfers", "transfer"],
    "Others": ["others", "misc", "miscellaneous"],
    "Family & Allowance": ["allowance", "pocket money", "parents"],
    "Salary & Stipend": ["salary", "stipend", "scholarship"],
    "Refund & Cashback": ["refund", "refunds", "cashback"],
    "Interest Earned": ["interest"],
}
_CAT_INDEX = sorted(((w, c) for c, ws in CATEGORY_WORDS.items() for w in ws), key=lambda x: -len(x[0]))
for _c in taxonomy.NAMES:
    _CAT_INDEX.append((_c.lower(), _c))
_CAT_INDEX.sort(key=lambda x: -len(x[0]))

STOP = {"HOW", "MUCH", "MANY", "DID", "DO", "DOES", "I", "ME", "MY", "SPEND", "SPENT", "SPENDING", "ON", "IN", "AT", "THE", "A", "AN", "OF",
        "LAST", "THIS", "MONTH", "WEEK", "YEAR", "TODAY", "YESTERDAY", "TOTAL", "SHOW", "LIST", "WHICH", "WHAT", "WHERE", "WHEN", "WHO",
        "MOST", "TOP", "TO", "FOR", "FROM", "AND", "OR", "IS", "ARE", "WAS", "WERE", "HAVE", "HAD", "WITH", "PAID", "PAY", "SENT", "SEND",
        "RECEIVED", "RECEIVE", "GOT", "GET", "MONEY", "PAYMENTS", "PAYMENT", "TRANSACTIONS", "EXPENSES", "EXPENSE", "BY", "THAN", "THAT",
        "THESE", "THOSE", "ALL", "ANY", "OVER", "ABOUT", "NEXT", "MORE", "LESS", "INCREASE", "INCREASED", "RECURRING", "SUBSCRIPTIONS",
        "UNUSUAL", "BIGGEST", "LARGEST", "AVERAGE", "COUNT", "TIMES", "OFTEN", "VISIT", "VISITED", "SHOPS", "SHOP", "STORES", "LOCAL"}


@dataclass
class QueryPlan:
    intent: str = "unknown"
    question: str = ""
    direction: str = "debit"                 # debit | credit
    period: Period | None = None
    compare_to: Period | None = None
    category: str | None = None
    merchant_id: int | None = None
    merchant: str | None = None
    kind: str | None = None                  # merchant | person
    local_only: bool = False
    order_by: str = "amount"                 # amount | visits
    limit: int = 5
    weekend: str | None = None               # 'weekend' | 'weekday'
    group: str = "category"                  # compare grouping
    trend: str = "increase"                  # compare: increase | decrease
    notes: list[str] = field(default_factory=list)
    confidence: float = 0.0
    clarify: dict | None = None              # {"question": "...", "options": [{"id":.., "name":..}]}
    refusal: str | None = None               # set when the request is an ACTION (send money ...) this read-only app will not perform
    ignored: list[str] = field(default_factory=list)      # content words of the question that influenced nothing (shown to the user)
    source: str = "rules"

    def to_dict(self) -> dict:
        return {"intent": self.intent, "direction": self.direction, "period": self.period.to_dict() if self.period else None,
                "compare_to": self.compare_to.to_dict() if self.compare_to else None, "category": self.category,
                "merchant": self.merchant, "merchant_id": self.merchant_id, "kind": self.kind, "local_only": self.local_only,
                "order_by": self.order_by, "limit": self.limit, "weekend": self.weekend, "group": self.group, "trend": self.trend,
                "refusal": self.refusal, "ignored": self.ignored, "notes": self.notes, "confidence": round(self.confidence, 2), "source": self.source}


@dataclass
class Context:
    ref: date
    data_min: date
    data_max: date
    merchants: list[dict]                     # {id, name, kind, is_local, is_brand}

    @classmethod
    def from_df(cls, df: pd.DataFrame) -> "Context":
        m = df.drop_duplicates("merchant_id")[["merchant_id", "merchant", "kind", "is_local", "is_brand"]]
        merchants = [{"id": int(r.merchant_id), "name": r.merchant, "kind": r.kind, "is_local": bool(r.is_local), "is_brand": bool(r.is_brand)}
                     for r in m.itertuples(index=False)]
        return cls(df["date"].max().date(), df["date"].min().date(), df["date"].max().date(), merchants)


# ---- helpers -----------------------------------------------------------------------------------------------------------
def normalise(q: str) -> str:
    s = " " + re.sub(r"\s+", " ", q.lower().replace("’", "'")).strip() + " "
    s = re.sub(r"[?!.,;:]+ ", " ", s)
    for a, b in HINGLISH:
        s = s.replace(f" {a}", f" {b}") if a.endswith(" ") else re.sub(rf"\b{re.escape(a)}\b", b, s)
    return re.sub(r"\s+", " ", s)


_TYPE_WORDS = {w for g in seeds.lexicon()["categories"].values() for w in g["strong"] for w in tokens_of(w)}


def _core_tokens(name: str) -> list[str]:
    toks = tokens_of(name)
    drop = seeds.generic_words() | _TYPE_WORDS | STOP
    core = [t for t in toks if t not in drop and not t.isdigit() and len(t) >= 3]
    return core


def match_merchants(q_norm: str, ctx: Context) -> list[tuple[float, dict]]:
    qt = tokens_of(q_norm)
    qset = set(qt)
    phrase = " " + " ".join(qt) + " "
    out: list[tuple[float, dict]] = []
    for m in ctx.merchants:
        name_toks = tokens_of(m["name"])
        if not name_toks:
            continue
        if f" {' '.join(name_toks)} " in phrase and len(" ".join(name_toks)) >= 4:
            out.append((1.0, m))
            continue
        core = _core_tokens(m["name"])
        if not core:
            continue
        hits = 0
        for t in core:
            if t in qset or any(len(t) >= 5 and len(u) >= 4 and osa_distance(t, u) <= 1 for u in qset):
                hits += 1
        if hits == len(core):
            out.append((0.85 + 0.03 * min(len(core), 3), m))
    if not out:
        # second chance: ONE distinctive word of a longer name ("rahul" for "Rahul Verma", "ramesh" for "Ramesh Jha").  If several merchants share the
        # word, parse() sees a tie and asks "which one do you mean?" instead of guessing.
        for m in ctx.merchants:
            core = _core_tokens(m["name"])
            if any(len(t) >= 4 and t in qset for t in core):
                out.append((0.7, m))
    out.sort(key=lambda x: -x[0])
    return out


def _find_category(q_norm: str) -> str | None:
    for w, c in _CAT_INDEX:
        if re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", q_norm):
            return c
    return None


def _limit(q_norm: str, default: int) -> int:
    m = re.search(r"\btop (\d+)\b|\b(\d+) (?:most|biggest|largest|top)\b|\bfirst (\d+)\b", q_norm)
    if m:
        return max(1, min(int(next(g for g in m.groups() if g)), 50))
    m = re.search(r"\b(?:top|first|biggest|largest) (one|two|three|four|five|six|seven|eight|nine|ten)\b", q_norm)
    if m:
        return timeparse.NUMWORDS[m[1]]
    return default


# ---- intent rules (ordered: the first that fires wins) ---------------------------------------------------------------------
_RULES: list[tuple[str, re.Pattern]] = [(i, re.compile(p)) for i, p in [
    ("help", r"\b(help|what can you do|how do i use|examples?)\b"),
    ("needs_review", r"\b(needs? review|uncategori[sz]ed|not categori[sz]ed|unclassified|not classified|unlabell?ed|unknown merchants?|unsure|waiting for (?:my )?review|(?:pending|for|to) review|review queue|yet to be (?:label+ed|categori[sz]ed|classified))\b"),
    ("recurring", r"\b(recurring|repeat(?:ed|ing)? payments?|every month|monthly payments?|auto ?pay|standing instructions?|regular payments?|regular expenses?|fixed expenses?|fixed (?:monthly )?(?:costs?|charges?|commitments?)|monthly (?:costs?|charges?|commitments?|outgoings?))\b|^(?!.*\b(?:how much|total|spent|spend|spending)\b).*\bsubscriptions?\b"),
    ("compare", r"\b(increas\w*|went up|gone up|go(?:es)? up|going up|grew|growth|rise|rose|risen|jump\w*|decreas\w*|dropped|drop|went down|gone down|go(?:es)? down|going down|came down|come down|fell|fall(?:s|en|ing)?|reduced|compared?(?: to| with)?|versus|vs\.?|difference|change[ds]?|why is my spending|where did my spending|what changed)\b|\b(?:more|less|higher|lower|fewer)\b(?: [a-z']+){0,6} than (?!\d|rs\b|₹)"),
    ("anomalies", r"\b(unusual|suspicious|anomal\w*|strange|weird|fraud\w*|duplicate\w*|double[- ]charged|overcharg\w*|unexpected|odd|fishy|irregular)\b|\b(?:charged|paid|debited|billed|deducted)(?: me)? (?:twice|double|two times)\b|\btwice\b[^?]*\b(?:charged|paid|debited|deducted)\b|\bdouble (?:payment|charge|debit)\w*"),
    ("summary", r"\b(summary|overview|snapshot|how am i doing|report|insights?|highlights?)\b"),
    ("net", r"\b(saved|savings?|left over|leftover|remaining|net|balance|surplus|deficit|how much (?:did i|have i) (?:save|keep)|(?:what|how much)(?:'s| is| was)? left|left (?:after|with|in)|money left)\b"),
    ("income", r"\b(income|received|receive|earn(?:ed|ings?)?|credited|came in|money in|pocket money|allowance|salary|stipend)\b|\b(?:sent|gave|transferred|credited|deposited)\b[^?]*\bme\b|\bdid\b[^?]*\b(?:send|give|pay|transfer)\b[^?]*\bme\b"),
    ("top_categories", r"\b(?:top|biggest|largest|main|major|highest|best)\b[^?]*\bcategor\w*|\b(which|what) categor\w*|\bcategor(?:y|ies) (?:breakdown|wise)\b|\b(spend|spent|spending) (?:the )?most on\b|\bwhere (?:is|does|did) my money (?:go|going|went)\b|\bbreak ?down\b|\bwhere do i spend\b(?!.*\b(shop|store))|\bmost\b.*\bwhere\b.*\bspen[dt]\b|\bwhere\b.*\bspen[dt]\b.*\bmost\b(?!.*\b(shop|store|merchant))"),
    ("top_merchants", r"\b(which|what|top|favou?rite|frequent\w*|regular)\b.*\b(shops?|stores?|merchants?|places?|vendors?|restaurants?|cafes?|sellers?|outlets?|apps?|payees?)\b|\b(?:which|what|who)\b[^?]*\b(?:person|people|friends?|relatives?|roommates?)\b[^?]*\b(?:most|often|more)\b|\bwho\b[^?]*\b(?:most|often)\b|\bwhere do i (?:spend|shop|eat|go|pay)\b.*\bmost\b|\b(?:most|often) (?:visited|used)\b|\bvisit\w* most\b|\bwhere (?:do|did) i (?:spend|pay) the most\b|\bmost (?:visited|frequent)\b"),
    ("largest", r"\b(biggest|largest|highest|most expensive|costliest|top)\b.*\b(expenses?|payments?|transactions?|purchases?|spends?|bills?)\b"),
    ("count", r"\bcount of\b|\bno\.? of (?:times|transactions|payments|orders|visits|purchases)\b|\bhow many (?:\w+ )?(?:times|transactions|payments|orders|visits|purchases)\b|\bnumber of (?:\w+ )?(?:times|transactions|payments|orders|visits|purchases)\b|\bhow often\b"),
    ("average", r"\b(average|avg|typical|usual(?:ly)?|mean)\b"),
    ("list_transactions", r"\b(show|list|display|give|find|see|view)\b.*\b(transactions?|payments?|purchases?|orders?|spendings?|expenses?|history)\b|\ball (?:my )?(?:transactions|payments)\b"),
    ("total_spend", r"\b(how much|total|spent|spend|spending|cost|expense|expenses|paid|pay|sent|send|gave|give)\b"),
]]


# an imperative that would MOVE MONEY ("transfer 500 to rahul", "pay rent") is not a question about the past: refuse instead of answering something else
_ACTION = re.compile(r"^ (?:please |pls |can you |could you )?(?:(?:transfer|send|pay|wire|remit|lend|invest|block|cancel|buy|sell|book|order)\b|recharge (?:my|the|a|for|\d|rs|₹))")


def parse(question: str, ctx: Context) -> QueryPlan:
    plan = QueryPlan(question=question.strip())
    q = normalise(question)
    if not q.strip():
        plan.intent = "help"
        plan.confidence = 1.0
        return plan

    if _ACTION.search(q):
        plan.intent = "unknown"
        plan.refusal = "I can only read and explain the statements you imported. I cannot send money, pay or change anything at your bank."
        plan.confidence = 0.0
        return plan

    # 1) merchants first (their name words must not be mistaken for categories / intents)
    cands = match_merchants(q, ctx)
    q_wo_merchant = q
    if cands:
        top = cands[0][0]
        best = [m for s, m in cands if s >= top - 0.02]
        if len(best) == 1 or len({m["name"].lower() for m in best}) == 1:
            plan.merchant_id, plan.merchant = best[0]["id"], best[0]["name"]
            plan.notes.append(f"merchant = {best[0]['name']}")
            for t in tokens_of(best[0]["name"]):
                q_wo_merchant = re.sub(rf"\b{t.lower()}\b", " ", q_wo_merchant)
            q_wo_merchant = re.sub(r"\s+", " ", q_wo_merchant)
        else:
            plan.clarify = {"question": "Which one do you mean?", "options": [{"id": m["id"], "name": m["name"]} for m in best[:6]]}
    # 2) intent
    intent, matched = None, ""
    for name, rx in _RULES:
        hit = rx.search(q_wo_merchant)
        if hit:
            intent, matched = name, hit.group(0)
            break
    plan.intent = intent or "unknown"
    # 3) direction, kind, local, ordering, limit, weekend
    if plan.intent in ("income",) or re.search(r"\b(received|receive|earned|credited|came in|got from)\b|\b(?:sent|gave|transferred)\b[^?]*\bme\b", q):
        plan.direction = "credit"
    if re.search(r"\b(friends?|people|persons?|p2p|person to person|relatives?|roommates?)\b", q):
        plan.kind = "person"
    if re.search(r"\blocal\b|\bnearby\b|\bneighbou?rhood\b|\bsmall (?:shops?|stores?|vendors?)\b|\bkirana\b|\bstreet\b", q):
        plan.local_only = True
        if plan.intent in ("unknown",):
            plan.intent = "top_merchants"
    if re.search(r"\b(visit\w*|often|frequent\w*|times|go to|goes? to)\b", q) or "most" in q and re.search(r"\b(visit|often|frequent)", q):
        plan.order_by = "visits"
    if re.search(r"\bweekends?\b", q):
        plan.weekend = "weekend"
    elif re.search(r"\bweekdays?\b", q):
        plan.weekend = "weekday"
    plan.limit = _limit(q, 10 if plan.intent in ("list_transactions", "anomalies") else 5)
    if plan.intent == "compare":
        plan.trend = "decrease" if re.search(r"\b(decreas\w*|dropp?ed?|drop|went down|gone down|go(?:es)? down|going down|come down|came down|reduced|less|lower|fell|fall(?:s|ing|en)?)\b", q) else "increase"
        plan.group = "merchant" if re.search(r"\b(shops?|merchants?|stores?|places?)\b", q) else "category"
    # 4) category (after removing merchant words)
    cat = _find_category(q_wo_merchant)
    if cat and plan.intent not in ("top_categories",):
        plan.category = cat
        if taxonomy.group_of(cat) == "income":
            plan.direction = "credit"
        plan.notes.append(f"category = {cat}")
    elif cat and plan.intent == "top_categories":
        plan.category = None
    # 5) time
    _set_period(plan, q, ctx)
    # 6) post-fix: a bare merchant/category question is a spend question
    if plan.intent == "unknown" and (plan.merchant_id or plan.category):
        plan.intent = "total_spend"
    if plan.intent == "total_spend" and plan.order_by == "visits" and not (plan.merchant_id or plan.category):
        plan.intent = "top_merchants"
    plan.confidence = _confidence(plan, intent is not None)
    if plan.intent not in ("unknown", "help"):
        plan.ignored = _unused_words(q, plan, matched, cands)
        if plan.ignored:
            # honesty: say which words did not influence the answer, and be less sure (the optional LLM, if on, is asked below 0.5)
            plan.notes.append("I did not use these words: " + ", ".join(plan.ignored))
            plan.confidence = max(0.2, plan.confidence - 0.15 * min(len(plan.ignored), 3))
    return plan


_FILLER = set("""please pls kindly tell give show me can could would you i my mine want need know see view display get find let lemme us also just only
again now so then there here hey hi hello thanks thank ok okay is are was were be been am do does did have has had will shall may might should of the a an
and or to in on at for from by with about over into than that this these those it its any all some more most less very much many total overall which what
who whom whose where when why how s t ll ve re d m hua hui hue kiya kiye kari kar karo hai hain tha thi the ho gaya gayi
make made makes went go goes gone take takes taken took anything something everything anywhere somewhere account accounts paying per each category categories
compared compare monthly daily weekly yearly annual big small large repeat repeats repeated drinks drink beverages withdrawn withdraw withdrawal""".split())
_TIME_WORDS = set("""today yesterday tomorrow week weeks month months year years day days ago last past previous prior this current since between until till during before
after next ytd mtd far morning evening night noon weekend weekends weekday weekdays quarter fortnight ever lifetime always recent recently latest""".split())
_DAYS = set("monday tuesday wednesday thursday friday saturday sunday mon tue tues wed thu thur thurs fri sat sun am pm".split())
_GENERIC = set("""spend spent spending expense expenses expenditure cost costs paid pay payment payments transaction transactions purchase purchases order orders
bill money amount rupees rupee rs inr often times average avg count number biggest largest highest top bottom most least list compare comparison
increase decrease higher lower rise fall change changed recurring regular subscriptions subscription unusual suspicious received income earn earned
saved savings left net summary overview""".split())
_CAT_TOKENS = {t for w, _ in _CAT_INDEX for t in re.findall(r"[a-z]+", w)}


def _unused_words(q: str, plan: QueryPlan, matched: str, cands: list) -> list[str]:
    """Content words of the question that influenced NOTHING: not the intent, a category, the time, a merchant or a filter.
    If any remain, the answer may not be what the user asked ("how much did my PARENTS SEND me") - so we say so."""
    known = _FILLER | _TIME_WORDS | _DAYS | _GENERIC | _CAT_TOKENS | set(timeparse.MONTHS) | set(timeparse.NUMWORDS) | {t.lower() for t in STOP}
    known |= set(re.findall(r"[a-z]+", matched))
    for m in ([plan.merchant] if plan.merchant else []) + ([c["name"] for _, c in cands[:6]] if (plan.clarify and cands) else []):
        known |= {t.lower() for t in tokens_of(m)}
    for w in ("local", "nearby", "kirana", "street", "friends", "friend", "people", "person", "persons", "p2p", "relatives", "roommates", "shops", "shop", "stores", "store",
              "merchants", "merchant", "places", "place", "visit", "visited", "frequent", "frequently", "favourite", "favorite", "weekend", "weekday"):
        known.add(w)
    out = []
    for w in re.findall(r"[a-z]+", q):
        if len(w) >= 3 and w not in known and w not in out:
            out.append(w)
    return out[:5]


def _set_period(plan: QueryPlan, q: str, ctx: Context) -> None:
    spec = timeparse.parse(q, ctx.ref, ctx.data_min, ctx.data_max)
    if plan.intent == "compare":
        _compare_periods(plan, q, ctx)
    elif spec:
        plan.period = spec.period
        plan.notes.append(spec.note)
    elif plan.intent in ("total_spend", "top_merchants", "top_categories", "largest", "count", "average", "income", "net",
                         "list_transactions", "anomalies", "summary"):
        plan.period = Period(ctx.data_min, ctx.data_max, f"all your data ({ctx.data_min:%d %b %Y} – {ctx.data_max:%d %b %Y})")
        plan.notes.append("no period mentioned → using all your data")


def plan_from_slots(s: dict, ctx: Context, question: str) -> QueryPlan:
    """Build a plan from the (already validated) slots an LLM produced.  The model supplied words only: merchants are resolved
    against YOUR merchant list, time phrases by timeparse.py, categories must be ours - so it cannot invent dates or numbers."""
    plan = QueryPlan(question=question.strip(), intent=s["intent"], direction=s["direction"], source="llm")
    plan.category = s["category"] or None
    if plan.category and taxonomy.group_of(plan.category) == "income":
        plan.direction = "credit"
    plan.kind = s["kind"] or None
    plan.local_only = bool(s["local_only"])
    plan.order_by = s["order_by"]
    plan.limit = s["limit"]
    plan.weekend = s["weekend"] or None
    plan.trend = s["trend"]
    if s["merchant_mention"]:
        cands = match_merchants(normalise(s["merchant_mention"]), ctx)
        if cands:
            top = cands[0][0]
            best = [m for sc, m in cands if sc >= top - 0.02]
            if len({m["name"].lower() for m in best}) == 1:
                plan.merchant_id, plan.merchant = best[0]["id"], best[0]["name"]
            else:
                plan.clarify = {"question": "Which one do you mean?", "options": [{"id": m["id"], "name": m["name"]} for m in best[:6]]}
    _set_period(plan, normalise(f"{s['time_phrase']} {s['compare_phrase']}"), ctx)
    plan.notes.append("question understood with the optional language model (it only filled word slots; all numbers are computed locally)")
    plan.confidence = 0.7
    return plan


def _compare_periods(plan: QueryPlan, q: str, ctx: Context) -> None:
    """Choose (recent, base).  Priority: two named months > this-vs-last month > default."""
    months = [m for m in re.finditer(rf"\b({timeparse._MON})\b(?:\s*,?\s*(\d{{4}}))?", q) if m[1] != "may" or m[2]]
    ref = ctx.ref
    if len(months) >= 2:
        ps = []
        for m in months[:2]:
            mo = timeparse.MONTHS[m[1]]
            p = month_period(int(m[2]), mo) if m[2] else timeparse._month_of(mo, ref)
            ps.append(p)
        ps.sort(key=lambda p: p.start, reverse=True)
        plan.period, plan.compare_to = ps[0], ps[1]
        plan.notes.append(f"comparing {ps[0].label} with {ps[1].label}")
        return
    if re.search(r"\bthis month\b", q) and re.search(r"\blast month\b|\bprevious month\b", q):
        a = month_period(ref.year, ref.month, ref.day)
        y, m = shift_month(ref.year, ref.month, -1)
        plan.period, plan.compare_to = a, month_period(y, m, ref.day)
        plan.notes.append(f"this month so far ({a.label}) vs the same days of last month")
        return
    spec = timeparse.parse(q, ref, ctx.data_min, ctx.data_max)
    if spec:
        plan.period = spec.period
        plan.compare_to = timeparse.previous_period(spec.period)
        plan.notes.append(f"{spec.note}; compared with the period before ({plan.compare_to.label})")
        return
    # default: last complete month vs the month before; if the current month is already well advanced compare month-to-date
    import calendar
    last_day = calendar.monthrange(ref.year, ref.month)[1]
    if ref.day >= last_day - 2:
        a = month_period(ref.year, ref.month)
    else:
        y, m = shift_month(ref.year, ref.month, -1)
        a = month_period(y, m)
    y, m = shift_month(a.start.year, a.start.month, -1)
    plan.period, plan.compare_to = a, month_period(y, m)
    plan.notes.append(f"no period mentioned → comparing the latest complete month ({a.label}) with the one before ({plan.compare_to.label})")


def _confidence(plan: QueryPlan, rule_hit: bool) -> float:
    if plan.intent == "unknown":
        return 0.0
    c = 0.9 if rule_hit else 0.55
    if plan.clarify:
        c = 0.5
    if plan.intent == "total_spend" and not (plan.merchant_id or plan.category or plan.kind) and rule_hit:
        c = 0.8
    return c
