"""Cold-start training data for the category model - so it works on day 1, before the user has taught it anything.

Built ONLY from the human-curated seed files (brands.json, lexicon.json, names.json) - not from the synthetic demo
world - so measuring on that world is not the model reading its own answer key.

Three kinds of examples:
  1. brands      each brand with several of its aliases/legal names, light typos, city suffixes, its UPI-address hints
  2. typed shops "<owner/deity/place> <shop-type word> [suffix]" for every lexicon category, with typos and truncation
  3. blank names "<owner>", "NEW CORNER" ... whose category cannot be read from the name: label drawn from everyday
     base rates, so the model learns to be UNSURE about them instead of confidently wrong
Amounts / hours come from rough per-category priors (tea ~Rs15 in the morning, restaurant ~Rs300 at night ...); the
context field has a small weight so these priors only matter when the words are silent.
"""
from __future__ import annotations

import math
import random

import pandas as pd

from .. import config, seeds, taxonomy
from .features import row_fields

# category -> list of (share, median amount, sigma, hour-bucket weights).  A mixture because one category has very different
# purchases: tea ~Rs15 in the morning vs a meal ~Rs120 at noon vs a restaurant bill ~Rs500 at night.  Everyday priors, rough.
#                                 unk  night morn  noon  eve   late
PRIORS: dict[str, list[tuple[float, float, float, list[float]]]] = {
    "Food & Dining": [(0.35, 15, 0.45, [0.2, 0.0, 0.5, 0.05, 0.2, 0.05]), (0.40, 90, 0.5, [0.2, 0.02, 0.1, 0.5, 0.15, 0.03]),
                      (0.25, 420, 0.5, [0.15, 0.02, 0.0, 0.1, 0.2, 0.53])],
    "Groceries": [(0.55, 70, 0.6, [0.2, 0.0, 0.3, 0.1, 0.3, 0.1]), (0.45, 320, 0.7, [0.2, 0.0, 0.1, 0.2, 0.35, 0.15])],
    "Travel & Transport": [(0.6, 55, 0.6, [0.2, 0.03, 0.3, 0.2, 0.2, 0.07]), (0.4, 520, 0.7, [0.2, 0.05, 0.1, 0.2, 0.2, 0.25])],
    "Shopping": [(1.0, 550, 0.9, [0.2, 0.02, 0.05, 0.2, 0.3, 0.23])],
    "Bills & Recharge": [(1.0, 299, 0.6, [0.2, 0.0, 0.2, 0.2, 0.2, 0.2])],
    "Subscriptions & Entertainment": [(1.0, 199, 0.8, [0.2, 0.02, 0.15, 0.1, 0.2, 0.33])],
    "Health & Medical": [(1.0, 180, 0.8, [0.2, 0.03, 0.15, 0.25, 0.25, 0.12])],
    "Education": [(0.5, 25, 0.7, [0.2, 0.0, 0.2, 0.4, 0.15, 0.05]), (0.5, 300, 0.8, [0.2, 0.0, 0.2, 0.3, 0.2, 0.1])],
    "Personal Care": [(1.0, 150, 0.6, [0.2, 0.0, 0.15, 0.2, 0.35, 0.1])],
    "Rent & Hostel": [(1.0, 1500, 1.0, [0.2, 0.0, 0.25, 0.25, 0.2, 0.1])],
    "Finance & Fees": [(1.0, 400, 1.2, [0.4, 0.05, 0.15, 0.15, 0.1, 0.15])],
    "Cash Withdrawal": [(1.0, 1000, 0.5, [0.2, 0.0, 0.2, 0.3, 0.2, 0.1])],
    "Others": [(1.0, 200, 1.0, [0.25, 0.02, 0.15, 0.2, 0.2, 0.18])],
}
HOUR_FOR_BUCKET = [None, 2, 8, 13, 17, 21]
# how often a nameless local shop falls into each category (everyday base rates, not tuned to any test world)
BLANK_NAME_RATES = {"Food & Dining": 0.30, "Groceries": 0.22, "Shopping": 0.10, "Travel & Transport": 0.09, "Health & Medical": 0.05,
                    "Education": 0.05, "Personal Care": 0.05, "Subscriptions & Entertainment": 0.03, "Bills & Recharge": 0.03,
                    "Others": 0.08}
_SUFFIXES = ["", "", "", "", "NAGAR", "CAMPUS", "PVT LTD", "& SONS", "& CO", "NO 1", "2"]
_CITIES = ["JAIPUR", "BANGALORE", "MUMBAI", "DELHI", "INDIA", "HYDERABAD", "PUNE", "KOLKATA", "GURGAON"]
_VPA_SHAPES = ["paytmqr{n12}{l4}@paytm", "q{n9}@ybl", "{slug}@okicici", "{phone}@ybl", "{slug}{n2}@oksbi", "bharatpe.{n10}@fbpe", "{slug}@upi"]
_REMARKS = ["", "", "", "Payment from PhonePe", "UPI", "Sent using Paytm", "thanks", "food", "lunch", "bill", "", ""]


def _typo(rng: random.Random, s: str) -> str:
    if len(s) < 7 or rng.random() > 0.2:
        return s
    i = rng.randint(2, len(s) - 3)
    r = rng.random()
    if r < 0.4 and s[i].isalpha() and s[i + 1].isalpha():
        return s[:i] + s[i + 1] + s[i] + s[i + 2:]              # swap
    if r < 0.7:
        return s[:i] + s[i + 1:]                                # drop a letter
    return s[:min(len(s), 16)]                                  # bank truncation


def _vpa(rng: random.Random, name: str) -> str:
    slug = "".join(ch for ch in name.lower() if ch.isalnum())[:12]
    return rng.choice(_VPA_SHAPES).format(
        n12="".join(rng.choice("0123456789") for _ in range(12)), l4="".join(rng.choice("abcdefghjkmnpqrstuvwxyz") for _ in range(4)),
        n9="".join(rng.choice("0123456789") for _ in range(9)), n10="".join(rng.choice("0123456789") for _ in range(10)),
        n2=str(rng.randint(1, 99)), slug=slug or "shop", phone=str(rng.choice([6, 7, 8, 9])) + "".join(rng.choice("0123456789") for _ in range(9)))


def _context(rng: random.Random, cat: str) -> tuple[float, int | None, int]:
    modes = PRIORS.get(cat, PRIORS["Others"])
    _, med, sg, hw = rng.choices(modes, weights=[m[0] for m in modes], k=1)[0]
    amt = max(5.0, round(med * math.exp(rng.gauss(0, sg))))
    hb = rng.choices(range(6), weights=hw, k=1)[0]
    hour = HOUR_FOR_BUCKET[hb]
    hour = None if hour is None else min(23, max(0, hour + rng.randint(-1, 2)))
    return float(amt), hour, rng.randint(0, 6)


def _example(rng, name: str, cat: str, label: str, kind: str = "merchant", vpa: str | None = None, remark: str | None = None) -> dict:
    amt, hour, wd = _context(rng, cat)
    v = vpa or _vpa(rng, name)
    f = row_fields(name, remark if remark is not None else rng.choice(_REMARKS), v, "UPI", "P2M" if kind == "merchant" else "P2A",
                   v.split("@")[1] if "@" in v else None, kind, amt, hour if hour is not None else float("nan"), wd, "debit")
    f["label"] = label
    f["weight"] = 1.0
    f["src"] = "seed"
    return f


def build(seed: int = 2024) -> pd.DataFrame:
    rng = random.Random(seed)
    rows: list[dict] = []
    # 1. brands --------------------------------------------------------------------------------------------------------
    for b in seeds.brands():
        cat = b["category"]
        for _ in range(config.SEED_SAMPLES_PER_BRAND):
            alias = rng.choice(b["aliases"])
            name = _typo(rng, alias)
            if rng.random() < 0.25:
                name += " " + rng.choice(_CITIES)
            hints = b.get("vpa_hints") or []
            vpa = f"{rng.choice(hints)}@{rng.choice(['icici', 'axisbank', 'hdfcbank', 'okaxis', 'paytm', 'ybl'])}" if hints and rng.random() < 0.6 else None
            rows.append(_example(rng, name, cat, cat, vpa=vpa))
    # 2. typed local shops ---------------------------------------------------------------------------------------------
    lex = seeds.lexicon()["categories"]
    first, sur = seeds.person_tokens()
    owners = sorted(first | sur)
    prefixes = sorted(seeds.shop_prefix_words())
    for cat, groups in lex.items():
        if cat not in taxonomy.EXPENSE_CATS:
            continue
        strong = groups["strong"]
        weak = groups["weak"]
        reps = config.SEED_EXAMPLES_PER_WORD if cat != "Cash Withdrawal" else 3
        for word in strong:                                       # word-first: EVERY shop-type word gets several examples
            for _ in range(reps):
                owner = rng.choice(owners)
                pat = rng.random()
                if pat < 0.45:
                    name = f"{owner} {word}"
                elif pat < 0.7:
                    name = f"{rng.choice(prefixes)} {word}"
                elif pat < 0.85:
                    name = f"{rng.choice(prefixes)} {owner} {word}"
                else:
                    name = f"{word} {rng.choice(['POINT', 'CORNER', 'CENTRE', 'HOUSE', 'WALA'])}"
                name = _typo(rng, f"{name} {rng.choice(_SUFFIXES)}".strip())
                rows.append(_example(rng, name, cat, cat))
        for word in weak:                                         # weak words: the owner name decides little, label stays
            for _ in range(2):
                rows.append(_example(rng, _typo(rng, f"{rng.choice(owners)} {word}"), cat, cat))
    # 3. blank names ---------------------------------------------------------------------------------------------------
    cats, weights = zip(*BLANK_NAME_RATES.items())
    for _ in range(320):
        cat = rng.choices(cats, weights=weights, k=1)[0]
        shape = rng.random()
        if shape < 0.4:
            name = rng.choice(owners)
        elif shape < 0.6:
            name = f"{rng.choice(owners)} {rng.choice(['ENTERPRISES', 'TRADERS', 'AGENCY', '& SONS', '& CO', 'BROTHERS'])}"
        elif shape < 0.8:
            name = f"{rng.choice(prefixes)} {rng.choice(prefixes)}"
        else:
            name = f"{rng.choice(owners)} {rng.choice(owners)}"
        rows.append(_example(rng, name, cat, cat))
    # 4. person payees: P2P 'category' is a fixed rule, not learned - nothing to add
    df = pd.DataFrame(rows)
    return df
