"""The categorisation CASCADE: for every (merchant, direction) decide a category and say WHICH LAYER decided and how sure it is.

    layer 0  USER MEMORY      the user told us (a correction) - always wins, confidence 1.0
    layer 1  HARD RULES       by who the money went to: ATM -> Cash Withdrawal, bank charge -> Finance & Fees, own wallet ->
                              Self Transfer, a person -> Transfers (P2P) / Received from Friends, refunds, interest, salary words
    layer 2  KNOWN BRAND      Swiggy -> Food & Dining (brand dictionary, incl. legal-entity names)
    layer 3  ML MODEL         TF-IDF + logistic regression over the merchant's name/remark/amount/time; average of the
                              probabilities of all its payments; only categories that make sense for a debit are allowed
    layer 4  LEXICON          a shop-type word in the name ("...KIRANA...") when the model is unsure
    layer 5  NEEDS REVIEW     we honestly do not know - ranked in the review queue by (how unsure) x (how much money)

Statuses: confirmed (user) | auto (confident) | suggested (best guess, please confirm) | unknown (needs review).
Why the layers are ordered this way: certain knowledge (the user's word, hard facts about the payment) must never be
overruled by a statistical guess; the model only speaks when nothing more reliable applies.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .. import config, seeds, taxonomy
from ..textutil import tokens_of
from .model import CategoryModel
from .p2p import KindResult, service_like

MODEL_CLASSES_FOR_SHOPS = [c for c in taxonomy.EXPENSE_CATS if c not in ("Transfers (P2P)", "Cash Withdrawal")]


@dataclass
class CatResult:
    category: str
    conf: float
    source: str                 # user | rule | brand | model | lexicon | none
    status: str                 # confirmed | auto | suggested | unknown
    top3: list[tuple[str, float]] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


def _status(conf: float) -> str:
    return "auto" if conf >= config.CAT_ACCEPT else "suggested" if conf >= config.CAT_SUGGEST else "unknown"


def _rule_for(kind: str, direction: str, g: pd.DataFrame, canonical: str, is_brand: bool, brand_cat: str | None, service: str | None = None) -> CatResult | None:
    flags = [f for fl in g["flags"] for f in (fl if isinstance(fl, list) else [])]
    has = lambda f: f in flags
    toks = set(tokens_of(canonical))
    if kind == "self":
        return CatResult("Self Transfer", 0.95, "rule", "auto", evidence=["Moving your own money (wallet / own account) is not spending."])
    if kind == "cash":
        if direction == "debit":
            return CatResult("Cash Withdrawal", 0.99, "rule", "auto", evidence=["ATM withdrawal."])
        return CatResult("Other Income", 0.7, "rule", "auto", evidence=["Cash deposit."])
    if kind == "bank":
        if direction == "credit":
            return CatResult("Interest Earned" if (has("interest") or "INTEREST" in toks) else "Other Income", 0.9, "rule", "auto",
                             evidence=["Bank interest."] if has("interest") else ["Credit from the bank."])
        return CatResult("Finance & Fees", 0.95, "rule", "auto", evidence=["Bank charge / fee."])
    # salary-like words win for credits from anyone
    if direction == "credit" and (has("income_hint") or toks & {"SCHOLARSHIP", "STIPEND", "SALARY", "PAYROLL", "DBT"}):
        return CatResult("Salary & Stipend", 0.85, "rule", "auto", evidence=["The narration mentions salary / stipend / scholarship."])
    if direction == "credit" and (has("refund") or toks & {"REFUND", "CASHBACK", "REVERSAL", "REWARDS"}):
        return CatResult("Refund & Cashback", 0.9, "rule", "auto", evidence=["The narration mentions refund / cashback."])
    if kind == "person":
        if direction == "debit":
            if service:        # "a person" who is paid like a shop (regular small one-way payments): do not call it settled
                return CatResult("Transfers (P2P)", 0.5, "rule", "suggested", evidence=["Money sent to a person.", f"But this person is paid like a shop or service ({service}) - please confirm what it is."])
            return CatResult("Transfers (P2P)", 0.9, "rule", "auto", evidence=["Money sent to a person."])
        credits = g[g["direction"] == "credit"]
        big = credits[credits["amount"] >= 2000]
        if len(big) >= 2 and (big["ts"].max() - big["ts"].min()).days >= 20:
            return CatResult("Family & Allowance", 0.6, "rule", "suggested",
                             evidence=["Regular large credits from one person - looks like an allowance from family."])
        return CatResult("Received from Friends", 0.8, "rule", "auto", evidence=["Money received from a person."])
    if kind == "merchant" and direction == "credit":
        return CatResult("Refund & Cashback", 0.8, "rule", "auto", evidence=["Money coming back from a shop / company is usually a refund or cashback."])
    if is_brand and brand_cat and direction == "debit":
        return CatResult(brand_cat, 0.97, "brand", "auto", evidence=["Known brand."])
    return None


def categorize(df: pd.DataFrame, frame: pd.DataFrame, canon: dict[int, str], kinds: dict[int, KindResult], is_brand: dict[int, bool],
               brand_cat: dict[int, str | None], memory: dict[tuple[int, str], str], model: CategoryModel) -> dict[tuple[int, str], CatResult]:
    """`df` = Stage1 frame, `frame` = its model-feature frame (same index). Returns a CatResult per (merchant_id, direction)."""
    out: dict[tuple[int, str], CatResult] = {}
    todo: list[tuple[tuple[int, str], pd.Index]] = []
    persons = {mid for mid, k in kinds.items() if k.kind == "person" and k.source != "user"}
    person_rows = {int(mid): g for mid, g in df[df["merchant_id"].isin(persons)].groupby("merchant_id")} if persons else {}     # all rows of each person-payee, both directions
    for (mid, direction), g in df.groupby(["merchant_id", "direction"], sort=False):
        mid = int(mid)
        key = (mid, direction)
        if key in memory:
            out[key] = CatResult(memory[key], 1.0, "user", "confirmed", evidence=["You set this category."])
            continue
        kind = kinds[mid].kind
        service = None
        if kind == "person" and direction == "debit" and kinds[mid].source != "user":
            service = service_like(person_rows[mid]) if mid in person_rows else None
        r = _rule_for(kind, direction, g, canon[mid], is_brand.get(mid, False), brand_cat.get(mid), service)
        if r:
            kr = kinds[mid]
            # a category derived from "it is a person" can only be as sure as the shop-or-person call itself
            if kind in ("person", "merchant") and kr.source == "model" and kr.confidence < config.KIND_CONF_AUTO and r.source == "rule":
                r.conf = min(r.conf, kr.confidence)
                r.status = "suggested"
                r.evidence = r.evidence + [f"Only {kr.confidence:.0%} sure this is a {'person' if kind == 'person' else 'shop'} - please confirm."]
            out[key] = r
            continue
        if direction == "debit":
            todo.append((key, g.index))
        else:
            out[key] = CatResult("Other Income", 0.4, "none", "suggested", evidence=["Unclassified credit."])
    if todo:
        idx = [i for _, ix in todo for i in ix]
        P = model.predict_proba(frame.loc[idx])
        classes = model.classes_
        allowed = np.array([c in MODEL_CLASSES_FOR_SHOPS for c in classes])
        Pm = P[:, allowed]
        Pm = Pm / np.clip(Pm.sum(axis=1, keepdims=True), 1e-9, None)
        cls = [c for c, a in zip(classes, allowed) if a]
        pos = {ix_val: n for n, ix_val in enumerate(idx)}
        for key, ix in todo:
            rows = [pos[i] for i in ix]
            avg = Pm[rows].mean(axis=0)
            order = np.argsort(-avg)
            top3 = [(cls[j], float(avg[j])) for j in order[:3]]
            best, conf = top3[0]
            mid = key[0]
            ev = []
            lex = seeds.lexicon_scores(tokens_of(canon[mid]))
            if conf < config.CAT_SUGGEST and lex:
                lc, ls = max(lex.items(), key=lambda kv: kv[1])
                if ls >= 1.0 and lc in MODEL_CLASSES_FOR_SHOPS:
                    out[key] = CatResult(lc, 0.40, "lexicon", "suggested", top3, [f"The name contains a '{lc}' shop word."])
                    continue
            if conf < config.CAT_SUGGEST:
                ev.append("The name and the payment pattern do not clearly point to a category.")
            out[key] = CatResult(best if conf >= config.CAT_SUGGEST else taxonomy.UNCATEGORISED, conf, "model", _status(conf), top3, ev)
    return out
