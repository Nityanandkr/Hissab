"""Who did the money go to?  A shop / business ("merchant"), a person ("person" = P2P), cash, the bank, or yourself.

THE HARD PART.  Technically every UPI payment looks the same.  The cases that matter:
  * a friend:           9876543210@ybl, name "RAHUL KUMAR", Rs 100/150/500, money flows both ways, irregular
  * a proper shop:      a merchant-QR address, name "SHARMA SWEETS", Rs 47/85/132, one-way, frequent
  * a tiny shop on a PERSONAL QR (chai-wala, auto driver, mess contractor, fruit seller): phone-number address, a PERSON's
    name, bank marker "P2A" - everything static says "person"; only the BEHAVIOUR gives it away (small non-round amounts,
    regular, one-way).  This is the hard case the problem statement hints at ("particularly local merchants").

APPROACH.  Compute ~19 interpretable features per payee (static: address shape, bank marker, name shape; behavioural: amount
roundness, two-way flow, regularity, repetition) and feed them to a small logistic regression.  Why not a big model?
 - few examples per user -> a linear model with sensible features is hard to beat and cannot "memorise" noise
 - every decision must be explainable: contribution = weight x feature is shown as the reason
 - it retrains in milliseconds when the user corrects a payee
Rules run first for the unambiguous lines (ATM, bank charges, wallet top-ups, known brands).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .. import config, seeds
from ..textutil import day_numbers, tokens_of, vpa_parts

# name -> (text when the feature value is POSITIVE, text when NEGATIVE).  The text always describes what is actually PRESENT in the data
# (an earlier version described the *opposite* for shop-leaning features, e.g. said "amount changes" about a payee paid the same amount
# five times - an explanation that contradicts the data is worse than none).  Only amt_log_median is signed (small vs large amounts).
FEATURES: list[tuple[str, str, str]] = [
    ("marker_merchant", "the bank marks these payments as merchant payments (P2M)", ""),
    ("marker_person", "the bank marks these as person-to-person / account transfers (P2A/P2P)", ""),
    ("vpa_phone", "paid to a phone-number UPI address", ""),
    ("vpa_merchant_shape", "the UPI address looks like a merchant QR code", ""),
    ("vpa_named", "the UPI address contains a person's name", ""),
    ("name_business_words", "the name has shop/business words (store, kirana, sweets, traders…)", ""),
    ("name_person_like", "the name looks like a person's name", ""),
    ("name_shop_prefix", "the name is shop-style (Sri, Maa, New, …)", ""),
    ("name_tokens", "the name is long", "the name is short"),
    ("amt_round", "the amounts are round numbers (multiples of 50)", ""),
    ("amt_log_median", "the typical amount is large", "the typical amount is small"),
    ("amt_cv", "the amounts vary a lot", ""),
    ("two_way", "money also comes back from this payee", ""),
    ("credit_share", "most of the money flows FROM this payee to you", ""),
    ("n_log", "you pay this payee often", ""),
    ("regular_gap", "the payments come at regular intervals", ""),
    ("imps_neft", "sent as an IMPS/NEFT bank transfer", ""),
    ("fixed_amount", "exactly the same amount every time", ""),
    ("small_repeated", "small payments repeated often (a habit, like tea or auto fare)", ""),
]
FEATURE_NAMES = [f[0] for f in FEATURES]

_PHONE = re.compile(r"^(?:91)?[6-9]\d{9}$")
_MERCHANT_SHAPE = re.compile(r"^(?:paytmqr|q\d{7,}$|bharatpe|bharatqr|gpay-\d|razorpay|rzp|payu|cashfree|billdesk|pinelabs|ezetap|merchant|vyapar|mswipe)|\.rzp|\.payu|\.cf$")


def _vpa_local(v: str) -> str:
    return vpa_parts(v)[0].lower()


def entity_features(g: pd.DataFrame, canonical: str) -> np.ndarray:
    """One feature vector for one payee, from all of its transactions `g` (columns of Stage1.df)."""
    n = len(g)
    toks = tokens_of(canonical)
    first, sur = seeds.person_tokens()
    biz = seeds.business_words()
    amounts = g["amount"].to_numpy(dtype=float)
    markers = g["marker"].fillna("")
    vpas = {v for v in g["vpa"].dropna().unique()}
    locals_ = [_vpa_local(v) for v in vpas]

    f: dict[str, float] = {}
    f["marker_merchant"] = float((markers == "P2M").mean())
    f["marker_person"] = float(markers.isin(["P2A", "P2P", "P2V"]).mean())
    phones = bool(g["phone"].notna().any()) if "phone" in g else False
    f["vpa_phone"] = 1.0 if (any(_PHONE.match(l) for l in locals_) or phones) else 0.0
    f["vpa_merchant_shape"] = 1.0 if any(_MERCHANT_SHAPE.search(l) for l in locals_) else 0.0
    named = 0.0
    for l in locals_:
        if _PHONE.match(l) or _MERCHANT_SHAPE.search(l):
            continue
        if any(len(w) >= 4 and w.lower() in l for w in biz):
            continue
        parts = [p for p in re.split(r"[._\-\d]+", l) if p]
        hit = any(p.upper() in first or p.upper() in sur for p in parts) or any(len(w) >= 4 and w.lower() in l for w in first)
        named = max(named, 1.0 if hit else 0.0)
    f["vpa_named"] = named
    nbiz = sum(1 for t in toks if t in biz or t in seeds.generic_words() and t not in ("NEW", "OLD", "SRI", "SHRI", "SHREE", "MAA", "MATA", "JAI", "JAY", "THE", "OF", "AND"))
    nbiz += 1 if seeds.strong_types(toks) else 0
    f["name_business_words"] = min(nbiz, 2) / 2.0
    pshare = sum(1 for t in toks if t in first or t in sur) / max(1, len(toks))
    if f["name_business_words"] == 0 and len(toks) in (2, 3) and all(t.isalpha() for t in toks):
        pshare = max(pshare, 0.25)
    f["name_person_like"] = pshare
    f["name_shop_prefix"] = 1.0 if (len(toks) >= 2 and any(t in seeds.shop_prefix_words() for t in toks)) else 0.0
    f["name_tokens"] = min(len(toks), 4) / 4.0
    f["amt_round"] = float(np.mean(np.isclose(amounts % 50, 0)))
    med = max(float(np.median(amounts)), 1.0)
    f["amt_log_median"] = (math.log10(med) - 2.0) / 1.5
    cv = float(np.std(amounts) / np.mean(amounts)) if n >= 3 and np.mean(amounts) > 0 else 0.5
    f["amt_cv"] = min(cv, 2.0) / 2.0
    dirs = g["direction"]
    f["two_way"] = 1.0 if (dirs == "credit").any() and (dirs == "debit").any() else 0.0
    f["credit_share"] = float((dirs == "credit").mean())
    f["n_log"] = min(math.log1p(n) / math.log(30), 1.2)
    reg = 0.0
    if n >= 4:
        d = np.sort(day_numbers(g["ts"]))
        gaps = np.diff(d).astype(float)
        gaps = gaps[gaps > 0]
        if len(gaps) >= 3 and gaps.mean() > 0:
            reg = 1.0 - min(float(np.std(gaps) / gaps.mean()), 1.5) / 1.5
    f["regular_gap"] = reg
    f["imps_neft"] = float(g["mode"].isin(["IMPS", "NEFT", "RTGS"]).mean())
    f["fixed_amount"] = 1.0 if (n >= 3 and cv < 0.01) else 0.0
    f["small_repeated"] = 1.0 if (n >= 5 and med < 120 and f["amt_round"] < 0.5) else 0.0
    return np.array([f[k] for k in FEATURE_NAMES], dtype=float)


# --------------------------------------------------------------------------------------------------------------------
@dataclass
class KindResult:
    kind: str                         # merchant | person | cash | bank | self
    p_person: float                   # model probability (1.0 / 0.0 for rule decisions)
    confidence: float
    source: str                       # rule | brand | model | user
    reasons: list[str] = field(default_factory=list)
    contributions: list[tuple[str, float]] = field(default_factory=list)


# Features the model is NOT allowed to use.  "imps_neft" separated shops from people in our synthetic worlds only because the generator pays
# NEFT/IMPS to institutions (exam fee, hostel fund) and never to friends - in real life rent, family and friends are also sent by IMPS/NEFT, so
# the weight would be an artefact of the generator.  The feature stays in the vector (and in the Explain text) but its weight is held at 0.
IGNORED_FEATURES = {"imps_neft"}
_IGNORED_IDX = [i for i, n in enumerate(FEATURE_NAMES) if n in IGNORED_FEATURES]


class KindModel:
    def __init__(self) -> None:
        self.coef: np.ndarray | None = None
        self.intercept: float = 0.0
        self.n_train = 0

    def fit(self, X: np.ndarray, y: np.ndarray, w: np.ndarray | None = None) -> "KindModel":
        from sklearn.linear_model import LogisticRegression
        clf = LogisticRegression(C=1.0, class_weight="balanced", max_iter=2000)
        X = np.array(X, dtype=float, copy=True)
        X[:, _IGNORED_IDX] = 0.0                     # a constant column gets weight 0
        clf.fit(X, y, sample_weight=w)
        self.coef, self.intercept, self.n_train = clf.coef_[0].copy(), float(clf.intercept_[0]), len(y)
        self.coef[_IGNORED_IDX] = 0.0
        return self

    def proba(self, X: np.ndarray) -> np.ndarray:
        z = X @ self.coef + self.intercept
        return 1.0 / (1.0 + np.exp(-z))

    def explain(self, x: np.ndarray, top: int = 3) -> list[tuple[str, float]]:
        contrib = self.coef * x
        order = np.argsort(-np.abs(contrib))[:top]
        return [(FEATURE_NAMES[i], float(contrib[i])) for i in order if abs(contrib[i]) > 0.05]

    def to_json(self) -> dict:
        return {"coef": self.coef.tolist(), "intercept": self.intercept, "features": FEATURE_NAMES, "n_train": self.n_train}

    @classmethod
    def from_json(cls, d: dict) -> "KindModel":
        m = cls()
        m.coef, m.intercept, m.n_train = np.array(d["coef"]), float(d["intercept"]), int(d.get("n_train", 0))
        return m


def service_like(g: pd.DataFrame) -> str | None:
    """A payee classified as a person that behaves like a shop/service - returns the reason or None."""
    deb = g[g["direction"] == "debit"]
    cre = g[g["direction"] == "credit"]
    if len(deb) < 3 or len(cre) > 0:
        return None
    amt = deb["amount"].to_numpy(dtype=float)
    cv = float(np.std(amt) / np.mean(amt)) if np.mean(amt) else 9
    days = np.sort(day_numbers(deb["ts"]))
    gaps = np.diff(days).astype(float)
    regular = len(gaps) >= 2 and gaps.mean() > 0 and float(np.std(gaps) / gaps.mean()) < 0.35
    if cv < 0.02:
        return f"paid the same amount {len(deb)} times, never received anything back"
    if regular:
        return f"paid at regular intervals ({len(deb)} times), never received anything back"
    if len(deb) >= 8 and np.median(amt) < 150:
        return f"small payments, {len(deb)} times, never received anything back (like a tea stall / auto driver)"
    return None


def reason_text(feature: str, contribution: float, x: float = 1.0) -> str:
    for name, pos, neg in FEATURES:
        if name == feature:
            return (neg if (x < 0 and neg) else pos)
    return feature


# --------------------------------------------------------------------------------------------------------------------
def _rule_kind(g: pd.DataFrame, canonical: str, is_brand: bool) -> KindResult | None:
    flags = g["flags"].tolist()
    nflags = lambda f: sum(1 for fl in flags if f in fl) / max(1, len(flags))
    toks = tokens_of(canonical)
    if nflags("atm") >= 0.5 or (g["mode"] == "ATM").mean() >= 0.5:
        return KindResult("cash", 1.0, 0.99, "rule", ["ATM cash withdrawal."])
    if nflags("bank_charge") >= 0.5 and not g["vpa"].notna().any():
        return KindResult("bank", 0.0, 0.97, "rule", ["Bank charge wording (SMS alert / annual fee / GST …)."])
    if nflags("interest") >= 0.5 and not g["vpa"].notna().any():
        return KindResult("bank", 0.0, 0.97, "rule", ["Bank interest wording."])
    if nflags("wallet_topup") >= 0.5 or nflags("self") >= 0.5 or (g["special"] == "WALLET TOP UP").any() or \
            ("WALLET" in toks and any(t in toks for t in ("PAYTM", "PHONEPE", "AMAZON", "MOBIKWIK", "FREECHARGE", "GOOGLE"))):
        return KindResult("self", 0.0, 0.95, "rule", ["Adding money to your own wallet / own account - not spending."])
    if is_brand:
        return KindResult("merchant", 0.0, 0.99, "brand", ["Known brand / company."])
    return None


def classify(df: pd.DataFrame, canon: dict[int, str], brand: dict[int, bool], model: KindModel,
             user_kinds: dict[int, str] | None = None, placeholders: set[int] | None = None) -> dict[int, KindResult]:
    """Kind for every merchant id in `df` (Stage1 DataFrame).  `placeholders` = entities that have no real name (a stand-in such as
    'QR merchant ·JEPX' was made up from the UPI address): their name is NOT evidence, so the name features see an empty name."""
    user_kinds = user_kinds or {}
    placeholders = placeholders or set()
    out: dict[int, KindResult] = {}
    for mid, g in df.groupby("merchant_id", sort=False):
        mid = int(mid)
        if mid in user_kinds:
            k = user_kinds[mid]
            out[mid] = KindResult(k, 1.0 if k == "person" else 0.0, 1.0, "user", ["You told us this."])
            continue
        r = _rule_kind(g, canon[mid], brand.get(mid, False))
        if r:
            out[mid] = r
            continue
        x = entity_features(g, "" if mid in placeholders else canon[mid])
        p = float(model.proba(x[None, :])[0])
        kind = "person" if p >= 0.5 else "merchant"
        conf = max(p, 1 - p)
        contrib = model.explain(x)
        xv = dict(zip(FEATURE_NAMES, x))
        reasons = [f"{'→ person: ' if c > 0 else '→ shop: '}{reason_text(n, c, xv[n])}" for n, c in contrib]
        out[mid] = KindResult(kind, p, conf, "model", reasons, contrib)
    return out


# --------------------------------------------------------------------------------------------------------------------
def build_training(worlds: list[tuple]) -> tuple[np.ndarray, np.ndarray]:
    """Entity-level training rows from synthetic (world, layout) pairs: features computed from the *predicted* entities
    (so noise from parsing / merging is part of the training data), label = majority true kind of the entity's rows."""
    import tempfile
    from pathlib import Path
    from ..ingest.importer import read_statement
    from ..synth import layouts as L
    from . import pipeline

    X, y = [], []
    with tempfile.TemporaryDirectory() as td:
        for world, layout in worlds:
            path, tpath = L.export(world, layout, Path(td), "csv")
            truth = pd.read_csv(tpath).set_index("src_row")
            pv = read_statement(path.read_bytes(), path.name)
            st = pipeline.stage1(pv.rows)
            df = st.df.copy()
            df["true_kind"] = df["src_row"].map(truth["kind"])
            for mid, g in df.groupby("merchant_id"):
                tk = g["true_kind"].mode().iat[0]
                if tk not in ("merchant", "person"):
                    continue
                e = st.registry.entities[int(mid)]
                if e.is_brand or _rule_kind(g, e.canonical, e.is_brand):
                    continue
                X.append(entity_features(g, "" if e.placeholder else e.canonical))
                y.append(1 if tk == "person" else 0)
    return np.array(X), np.array(y)


def seed_worlds(seeds_: tuple[int, ...] = (101, 102, 103, 104, 105, 106)) -> list[tuple]:
    """Synthetic (world, layout) pairs used to learn the base model: different seeds, activity levels and layouts from the
    demo world."""
    from datetime import date
    from ..synth.world import World
    pairs = []
    lays = ["sbi", "hdfc", "axis", "kotak", "phonepe", "paytm", "icici", "demo"]
    for i, s in enumerate(seeds_):
        w = World(seed=s, end=date(2026, 10, 10), scale=0.6 + 0.15 * (i % 4))
        pairs += [(w, lays[(2 * i) % len(lays)]), (w, lays[(2 * i + 3) % len(lays)])]
    return pairs


def train_seed_arrays(seeds_: tuple[int, ...] = (101, 102, 103, 104, 105, 106)) -> tuple[np.ndarray, np.ndarray]:
    return build_training(seed_worlds(seeds_))


def train_seed_model(seeds_: tuple[int, ...] = (101, 102, 103, 104, 105, 106)) -> KindModel:
    X, y = train_seed_arrays(seeds_)
    return KindModel().fit(X, y)
