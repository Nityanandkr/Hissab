"""Loads the seed knowledge in hisaab/seed/*.json once and derives the lookup tables the rest of the code needs.

Nothing here is "learned": it is human-curated starting knowledge (brands, shop words, person names). The learned part
(model + the user's corrections) sits on top of it.
"""
from __future__ import annotations

import json
from functools import lru_cache

from . import config
from .textutil import tokens_of


def _load(name: str) -> dict:
    with open(config.SEED_DIR / name, encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def brands() -> list[dict]:
    return _load("brands.json")["brands"]


@lru_cache(maxsize=1)
def lexicon() -> dict:
    return _load("lexicon.json")


@lru_cache(maxsize=1)
def names() -> dict:
    return _load("names.json")


@lru_cache(maxsize=1)
def business_words() -> frozenset[str]:
    lex = lexicon()
    words = set(lex["business_words"])
    for cat in lex["categories"].values():
        for w in cat["strong"]:
            words.update(tokens_of(w))
    # 'weak' words (MOBILE, GENERAL ...) are deliberately NOT business words: too common in person names/remarks
    return frozenset(words)


@lru_cache(maxsize=1)
def generic_words() -> frozenset[str]:
    return frozenset(lexicon()["generic_words"])


@lru_cache(maxsize=1)
def shop_prefix_words() -> frozenset[str]:
    return frozenset(lexicon()["shop_prefix_words"])


@lru_cache(maxsize=1)
def person_tokens() -> tuple[frozenset[str], frozenset[str]]:
    n = names()
    return frozenset(n["first_names"]), frozenset(n["surnames"])


@lru_cache(maxsize=1)
def phrase_index() -> dict[tuple[str, ...], list[tuple[str, float]]]:
    """token-tuple -> [(category, weight)].  strong=1.0, weak=0.3.  Single words are 1-tuples."""
    idx: dict[tuple[str, ...], list[tuple[str, float]]] = {}
    for cat, groups in lexicon()["categories"].items():
        for w in groups["strong"]:
            idx.setdefault(tuple(tokens_of(w)), []).append((cat, 1.0))
        for w in groups["weak"]:
            idx.setdefault(tuple(tokens_of(w)), []).append((cat, 0.3))
    return idx


def lexicon_scores(tokens: list[str]) -> dict[str, float]:
    """Sum of lexicon weights per category for a token list (each phrase counted once). Used as a fallback rule,
    to generate cold-start training data, and to tell two shops with the same owner name apart."""
    idx = phrase_index()
    scores: dict[str, float] = {}
    seen: set[tuple[str, ...]] = set()
    n = len(tokens)
    for i in range(n):
        for L in (3, 2, 1):
            if i + L > n:
                continue
            key = tuple(tokens[i:i + L])
            if key in idx and key not in seen:
                seen.add(key)
                for cat, w in idx[key]:
                    scores[cat] = scores.get(cat, 0.0) + w
    return scores


def strong_types(tokens: list[str]) -> set[str]:
    """Categories for which the tokens contain at least one STRONG shop-type word (weight 1.0)."""
    idx = phrase_index()
    out: set[str] = set()
    n = len(tokens)
    for i in range(n):
        for L in (3, 2, 1):
            if i + L > n:
                continue
            for cat, w in idx.get(tuple(tokens[i:i + L]), []):
                if w >= 1.0:
                    out.add(cat)
    return out
