"""Merchant normalisation: a payee string (+ UPI address)  ->  ONE canonical merchant entity.

Three questions are answered here, in this order:

 1. Is it a KNOWN BRAND?   "BUNDL TECHNOLOGIES" is Swiggy.  Brand aliases include legal-entity names; a UPI-address hint
    ("swiggy@icici") also counts.  Short aliases (OLA, JIO, KFC) are matched strictly to avoid false hits.
 2. Have we seen THIS UPI ADDRESS or THIS NAME before?  A UPI address is the strongest identity key a statement gives us:
    "RAMESH KIRANA ST" and "RAMESH KIRANA STORE" paying `paytmqr…@paytm` are the same shop.
 3. Otherwise, is it a SPELLING / TRUNCATION VARIANT of a shop we know?  Banks cut names at 16-20 characters and people
    mistype; we compare names token by token with guards so that look-alikes are NOT merged:
        SHARMA SWEETS  vs  SHARMA MEDICAL     (same owner, different business  -> different shops)
        RAHUL KUMAR    vs  RAHUL KUMARI       (two people                       -> never fuzzy-merged)
        RAHUL KUM      vs  {RAHUL KUMAR, RAHUL KUMARI}  (ambiguous truncation  -> left separate; the user can merge)

When unsure we DO NOT merge: a wrongly merged shop silently corrupts two merchants' history, a wrongly split shop is one
click to fix (Merchants -> Merge).
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .. import config, seeds
from ..textutil import seq_ratio, title_name, tokens_of, vpa_parts

# ---- edit distance -----------------------------------------------------------------------------------------------
def osa_distance(a: str, b: str) -> int:
    """Damerau-Levenshtein (optimal string alignment): insert / delete / substitute / SWAP-adjacent each cost 1.
    "SHARMA" vs "SHARAM" = 1 (one swap).  Plain SequenceMatcher would score that pair much lower."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if abs(la - lb) > 3:
        return abs(la - lb)
    d = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        d[i][0] = i
    for j in range(lb + 1):
        d[0][j] = j
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[la][lb]


def token_sim(a: str, b: str) -> float:
    """1.0 equal; 0.92 one edit in a token of >= 5 letters (two edits if >= 9); else the plain character ratio."""
    if a == b:
        return 1.0
    m = min(len(a), len(b))
    dist = osa_distance(a, b)
    if m >= 5 and dist <= 1:
        return 0.92
    if m >= 9 and dist <= 2:
        return 0.88
    return seq_ratio(a, b)


# ---- cleaning ----------------------------------------------------------------------------------------------------
LEGAL_SUFFIX = {"PVT", "PRIVATE", "LTD", "LIMITED", "LLP", "INC", "CORP", "CORPORATION", "CO", "PLC", "OPC"}
TITLES = {"MR", "MRS", "MS", "SMT", "MISS", "DR_"}      # 'DR' is kept: "DR ROY CLINIC"
NOISE = {"UPI", "PAYMENT", "PAID", "TO", "FROM", "VIA", "PHONEPE", "GPAY", "BHIM", "TXN", "REF", "ORDER", "ONLINE", "TRANSFER", "INV", "INVOICE"}
ABBREV = {
    "ENT": "ENTERPRISES", "ENTP": "ENTERPRISES", "ENTERPRISE": "ENTERPRISES", "GEN": "GENERAL", "GENL": "GENERAL",
    "STR": "STORE", "STRS": "STORES", "STORS": "STORES", "MED": "MEDICAL", "MEDI": "MEDICAL", "MEDICALS": "MEDICAL",
    "RESTRNT": "RESTAURANT", "RESTAURNT": "RESTAURANT", "TRDRS": "TRADERS", "TRADRS": "TRADERS", "TRDS": "TRADERS",
    "KIRNA": "KIRANA", "KIRANAA": "KIRANA", "KRIANA": "KIRANA", "KIRNAA": "KIRANA", "CTR": "CENTRE", "CNTR": "CENTRE",
    "CENTER": "CENTRE", "SWTS": "SWEETS", "BKRY": "BAKERY", "HTL": "HOTEL", "PHARM": "PHARMACY", "BROS": "BROTHERS",
}
_STORE_STUBS = {"ST", "STO", "STOR"}
_GENERIC_TYPOS = {"STRE": "STORE", "STOE": "STORE", "STOER": "STORE", "SOTRE": "STORE", "STORRE": "STORE",
                  "TARDERS": "TRADERS", "TRADRS": "TRADERS", "ENTERPRISS": "ENTERPRISES", "ENTERPRSES": "ENTERPRISES"}


@dataclass
class CleanName:
    text: str
    tokens: list[str]
    core: list[str]


def _fix_last_generic(tokens: list[str]) -> list[str]:
    """A typo inside the closing generic word ("... CONRER", "... STROE", "... PIONT") is read as that word, so it cannot make two spellings of one
    shop look different.  Only the LAST token of a name with >= 3 tokens; never a known business word or a person's name (PAINT is not POINT)."""
    if len(tokens) < 3:
        return tokens
    t = tokens[-1]
    generic = seeds.generic_words()
    if len(t) < 5 or t in generic or t in seeds.business_words():
        return tokens
    first, sur = seeds.person_tokens()
    if t in first or t in sur:
        return tokens
    for w in sorted(generic):
        if len(w) >= 5 and w not in ("LIMITED", "COMPANY") and _slip_of(t, w):
            return tokens[:-1] + [w]
    return tokens


def _slip_of(t: str, w: str) -> bool:
    """t is w with ONE typing slip: two neighbouring letters swapped, one letter missing, or one letter added.  (Not a substituted letter:
    PAINT is a different word from POINT, STONE from STORE.)"""
    if len(t) == len(w):
        diff = [i for i in range(len(t)) if t[i] != w[i]]
        return len(diff) == 2 and diff[1] == diff[0] + 1 and t[diff[0]] == w[diff[1]] and t[diff[1]] == w[diff[0]]
    a, b = (t, w) if len(t) < len(w) else (w, t)
    return len(b) - len(a) == 1 and any(b[:i] + b[i + 1:] == a for i in range(len(b)))


def clean_payee(raw: str | None) -> CleanName:
    toks = tokens_of(raw)
    out: list[str] = []
    for t in toks:
        if t in LEGAL_SUFFIX or t in TITLES:
            continue
        if t.isdigit() and len(t) >= 4:
            continue
        out.append(_GENERIC_TYPOS.get(t, ABBREV.get(t, t)))
    out = [t for t in out if t not in NOISE or len(out) <= 1]
    # NOTE: a truncated tail ("RAMESH KIRANA ST") is left as it is - ST could be STORE or STALL, and inventing the word would be
    # wrong. The matcher (is_truncation) already knows a stub is a prefix of the full name; best_display() drops a dangling stub.
    dedup = _fix_last_generic([t for i, t in enumerate(out) if i == 0 or t != out[i - 1]])
    generic = seeds.generic_words()
    core = [t for t in dedup if t not in generic] or dedup
    return CleanName(" ".join(dedup), dedup, core)


# ---- brands ------------------------------------------------------------------------------------------------------
@dataclass
class BrandHit:
    brand: dict
    how: str                 # alias | alias-inside | vpa-hint | fuzzy
    confidence: float
    evidence: str


class BrandIndex:
    def __init__(self) -> None:
        self.by_first: dict[str, list[tuple[tuple[str, ...], dict, bool]]] = {}
        self.vpa_hints: dict[str, dict] = {}
        for b in seeds.brands():
            for a in b["aliases"]:
                toks = tuple(clean_payee(a).tokens)
                if not toks:
                    continue
                strict = sum(len(t) for t in toks) <= 3
                self.by_first.setdefault(toks[0], []).append((toks, b, strict))
            for h in b.get("vpa_hints", []):
                self.vpa_hints.setdefault(h.lower(), b)
        for lst in self.by_first.values():
            lst.sort(key=lambda x: -len(x[0]))
        self.single_alias = {toks[0]: b for first, lst in self.by_first.items() for toks, b, _ in lst if len(toks) == 1 and len(toks[0]) >= 6}

    def match(self, tokens: list[str], vpa: str | None) -> BrandHit | None:
        n = len(tokens)
        best: BrandHit | None = None
        for i in range(n):
            for toks, b, strict in self.by_first.get(tokens[i], []):
                L = len(toks)
                if tuple(tokens[i:i + L]) != toks:
                    continue
                whole = (i == 0 and L == n)
                if strict and not whole:
                    continue
                hit = BrandHit(b, "alias" if whole else "alias-inside", 0.97 if whole else 0.9,
                               f"name matches alias '{' '.join(toks)}'")
                if best is None or L > len(best.evidence):
                    best = hit
                break
            if best and best.how == "alias":
                break
        if best:
            return best
        if vpa:
            local, _h = vpa_parts(vpa)
            parts = [p for p in re.split(r"[._\-]+|\d+", local.lower()) if p]
            for p in parts:
                b = self.vpa_hints.get(p)
                if b:
                    return BrandHit(b, "vpa-hint", 0.85, f"UPI address contains '{p}'")
            if local.lower() in self.vpa_hints:
                return BrandHit(self.vpa_hints[local.lower()], "vpa-hint", 0.85, f"UPI address is '{local.lower()}'")
        # typo of a long single-token alias (ZOMTO -> ZOMATO)
        for t in tokens:
            if len(t) >= 5:
                for alias, b in self.single_alias.items():
                    if alias[0] == t[0] and abs(len(alias) - len(t)) <= 2 and seq_ratio(alias, t) >= 0.88 and alias != t:
                        return BrandHit(b, "fuzzy", 0.75, f"'{t}' is a close misspelling of '{alias}'")
        return None


_BRANDS: BrandIndex | None = None


def brand_index() -> BrandIndex:
    global _BRANDS
    if _BRANDS is None:
        _BRANDS = BrandIndex()
    return _BRANDS


# ---- similarity --------------------------------------------------------------------------------------------------
def personish(tokens: list[str]) -> bool:
    """Looks like a human name: no business/type words, at least one known given name or surname, 1-4 tokens."""
    if not (1 <= len(tokens) <= 4):
        return False
    if any(t in seeds.business_words() for t in tokens) or seeds.strong_types(tokens):
        return False
    first, sur = seeds.person_tokens()
    return any(t in first or t in sur for t in tokens)


TRUNCATION_MIN_CHARS = 15      # banks cut LONG names (our SBI-style layout cuts at 16); a complete name such as "RAGHAV JOSHI" (12) is not a cut of "RAGHAV JOSHIA"


def is_truncation(short: list[str], long: list[str]) -> bool:
    """Bank cut the name: all tokens but the last equal, last token is a prefix of the corresponding long token.
    Not a cut: a number ("SHOP 1" vs "SHOP 12") or a name too short to have been cut ("RAHUL KUMAR" vs "RAHUL KUMARI" are two people)."""
    if len(short) < 2 or len(short) > len(long):
        return False
    k = len(short)
    if short[:k - 1] != long[:k - 1]:
        return False
    if not short[k - 1].isalpha() or len(" ".join(short)) < TRUNCATION_MIN_CHARS:
        return False
    return long[k - 1].startswith(short[k - 1]) and (len(long) > k or len(long[k - 1]) > len(short[k - 1]))


def name_similarity(a: CleanName, b: CleanName) -> float:
    """0..1.  Conservative: returns a low score whenever the names could be different businesses/people."""
    if not a.tokens or not b.tokens:
        return 0.0
    if a.text == b.text:
        return 1.0
    ca, cb = a.core, b.core
    both_people = personish(a.tokens) and personish(b.tokens)
    ta, tb = (a.tokens, b.tokens) if len(a.tokens) <= len(b.tokens) else (b.tokens, a.tokens)
    if is_truncation(ta, tb):
        return 0.93 if not both_people else 0.9
    if both_people:
        # two people: only identical full names (handled above) or one-letter typos in a long token
        if len(ca) == len(cb) and all(x == y or (min(len(x), len(y)) >= 6 and osa_distance(x, y) <= 1) for x, y in zip(ca, cb)):
            return 0.88
        return 0.0
    # different business types -> different shops
    sa, sb = seeds.strong_types(a.tokens), seeds.strong_types(b.tokens)
    if sa and sb and not (sa & sb):
        return 0.0
    # different numbers ("SHOP 12" vs "SHOP 15")
    na = {t for t in a.tokens if t.isdigit()}
    nb = {t for t in b.tokens if t.isdigit()}
    if na and nb and na != nb:
        return 0.0
    ja, jb = " ".join(ca), " ".join(cb)
    ratio = max(seq_ratio(ja, jb), 1.0 - osa_distance(ja, jb) / max(len(ja), len(jb), 1))
    # token level: every token of the shorter side should have a (near-)equal partner on the other side
    small, big = (ca, cb) if len(ca) <= len(cb) else (cb, ca)
    wsum = sum(len(t) for t in small) or 1
    matched = 0.0
    for t in small:
        best = max((token_sim(t, u) for u in big), default=0.0)
        if best >= 0.85:
            matched += len(t) * best
    token_score = matched / wsum
    extra = 1.0 - (sum(len(t) for t in big) - sum(len(t) for t in small)) / max(1, sum(len(t) for t in big)) * 0.35
    score = (0.5 * ratio + 0.5 * token_score) * (extra if len(small) < len(big) else 1.0)
    if len(small) == 1 and len(big) == 1 and min(len(small[0]), len(big[0])) < 5:
        return 0.0 if small[0] != big[0] else 1.0
    return score


# ---- the registry ------------------------------------------------------------------------------------------------
@dataclass
class Entity:
    id: int
    canonical: str
    is_brand: bool = False
    brand_key: str | None = None
    brand_category: str | None = None
    names: Counter = field(default_factory=Counter)      # cleaned-name variants -> count
    vpas: set = field(default_factory=set)
    user_named: bool = False
    placeholder: bool = False        # no readable name: canonical is a stand-in like "QR merchant ·JEPX (paytm)"

    def best_display(self) -> str:
        if self.is_brand and self.brand_key:
            return self.brand_key
        if not self.names:
            return self.canonical
        variants = list(self.names)
        toks = {v: v.split() for v in variants}

        def truncated(v: str) -> bool:
            return any(w != v and is_truncation(toks[v], toks[w]) for w in variants)

        pool = [v for v in variants if not truncated(v)] or variants
        best = max(pool, key=lambda v: (self.names[v], len(v))).split()
        if len(best) >= 3 and best[-1] in _STORE_STUBS:          # only a truncated spelling exists: drop the dangling stub
            best = best[:-1]
        return title_name(best)


@dataclass
class Resolution:
    entity: Entity
    method: str          # brand | vpa | name | fuzzy | new
    confidence: float
    evidence: list[str]
    created: bool = False


def placeholder_name(vpa: str | None) -> str:
    """A readable name for payees that have no name at all (address-only statements)."""
    if not vpa:
        return "Unknown payee"
    local, handle = vpa_parts(vpa)
    ll = local.lower()
    if re.fullmatch(r"(?:\+?91)?[6-9]\d{9}", ll):
        return f"UPI {ll[:2]}XXXXXX{ll[-2:]} ({handle})"
    if ll.startswith("paytmqr") or re.fullmatch(r"q\d{7,}", ll):
        tail = re.sub(r"\d+", "", ll)[-4:].upper() or ll[-4:].upper()
        return f"QR merchant ·{tail} ({handle})"
    guess = re.sub(r"[._\-]+", " ", re.sub(r"\d{4,}", " ", local)).strip()
    return title_name(tokens_of(guess)) if guess else f"UPI {local[:6]}… ({handle})"


class Registry:
    """All merchants known so far + the keys that point at them.  Loaded from / saved to SQLite by core/store.py."""

    def __init__(self) -> None:
        self.entities: dict[int, Entity] = {}
        self.keys: dict[str, int] = {}              # 'vpa:x' | 'name:X' -> entity id
        self._next = 1
        self._name_cache: dict[str, int | None] = {}
        self.dirty: set[int] = set()

    # -- creation / lookup -------------------------------------------------------------------------------------------
    def _new(self, canonical: str, **kw) -> Entity:
        e = Entity(self._next, canonical, **kw)
        self._next += 1
        self.entities[e.id] = e
        self.dirty.add(e.id)
        return e

    def _attach(self, e: Entity, vpa: str | None, clean: CleanName) -> None:
        if vpa:
            k = f"vpa:{vpa}"
            if k not in self.keys:
                self.keys[k] = e.id
                e.vpas.add(vpa)
                self.dirty.add(e.id)
        if clean.text:
            k = f"name:{clean.text}"
            if k not in self.keys:
                self.keys[k] = e.id
            e.names[clean.text] += 1
            self.dirty.add(e.id)
            if not e.user_named and not e.is_brand:
                e.canonical = e.best_display()

    @staticmethod
    def _phone_conflict(e: Entity, vpa: str | None, clean: CleanName) -> bool:
        """Two different phone-number UPI addresses => two different people, even with the same name."""
        if not vpa or not personish(clean.tokens):
            return False
        local, _h = vpa_parts(vpa)
        if not re.fullmatch(r"(?:91)?[6-9]\d{9}", local):
            return False
        for ov in e.vpas:
            ol, _oh = vpa_parts(ov)
            if re.fullmatch(r"(?:91)?[6-9]\d{9}", ol) and ol != local:
                return True
        return False

    def _best_similar(self, clean: CleanName, vpa: str | None) -> tuple[Entity | None, float, str]:
        scores: list[tuple[float, Entity]] = []
        for e in self.entities.values():
            if e.is_brand:
                continue
            best = 0.0
            for v in e.names:
                s = name_similarity(clean, CleanName(v, v.split(), [t for t in v.split() if t not in seeds.generic_words()] or v.split()))
                best = max(best, s)
                if best >= 1.0:
                    break
            if best >= config.NAME_MERGE_THRESHOLD:
                scores.append((best, e))
        if not scores:
            return None, 0.0, ""
        scores.sort(key=lambda x: -x[0])
        top, e = scores[0]
        # ambiguous (two look-alikes within 0.05)  ->  do not merge
        if len(scores) > 1 and scores[1][0] >= top - 0.05 and top < 1.0:
            return None, 0.0, f"ambiguous between '{e.canonical}' and '{scores[1][1].canonical}'"
        if self._phone_conflict(e, vpa, clean):
            return None, 0.0, "different phone numbers"
        return e, top, ""

    # -- the main entry ----------------------------------------------------------------------------------------------
    def resolve(self, payee_raw: str | None, vpa: str | None, name_trusted: bool = True) -> Resolution:
        """`name_trusted=False` = the name was only guessed (from the UPI address / a weak text match): it may NAME a new
        entity but is never used to merge two entities (only the UPI address or a known brand may do that)."""
        clean = clean_payee(payee_raw)
        ev: list[str] = []
        hit = brand_index().match(clean.tokens, vpa)
        if hit:
            bk = hit.brand["name"]
            key = f"brand:{bk}"
            e = self.entities.get(self.keys.get(key, -1))
            created = False
            if e is None:
                e = self._new(bk, is_brand=True, brand_key=bk, brand_category=hit.brand["category"])
                self.keys[key] = e.id
                created = True
            self._attach(e, vpa, clean)
            return Resolution(e, "brand", hit.confidence, [f"Known brand '{bk}': {hit.evidence}."], created)
        if vpa and f"vpa:{vpa}" in self.keys:
            e = self.entities[self.keys[f"vpa:{vpa}"]]
            self._attach(e, vpa, clean)
            return Resolution(e, "vpa", 0.98, ["Same UPI address as earlier payments → same merchant."])
        if not name_trusted and vpa:
            # identity = the UPI address only
            if clean.text:
                e = self._new(title_name(clean.tokens))
                e.canonical = title_name(clean.tokens)
            else:
                e = self._new(placeholder_name(vpa), placeholder=True)
            self.keys[f"vpa:{vpa}"] = e.id
            e.vpas.add(vpa)
            return Resolution(e, "new", 0.55, ["No trustworthy name in the narration - identified by its UPI address only."], True)
        if clean.text and f"name:{clean.text}" in self.keys:
            e = self.entities[self.keys[f"name:{clean.text}"]]
            if self._phone_conflict(e, vpa, clean):
                ev.append("Same name as an earlier person but a different phone-number UPI address - kept separate.")
            else:
                self._attach(e, vpa, clean)
                return Resolution(e, "name", 0.95, [f"Same name '{clean.text.title()}' seen before."])
        if clean.text and not ev:
            e, score, why = self._best_similar(clean, vpa)
            if e is not None:
                near = max(e.names, key=lambda v: name_similarity(clean, CleanName(v, v.split(), v.split())))      # BEFORE attaching: the closest name we already knew
                self._attach(e, vpa, clean)
                return Resolution(e, "fuzzy", round(score, 2), [f"'{clean.text.title()}' is a spelling/truncation variant of '{near.title()}' (similarity {score:.2f})."])
            if why:
                ev.append(f"Not merged: {why}.")
        # new entity
        if clean.text:
            e = self._new(title_name(clean.tokens))
            self._attach(e, vpa, clean)
            e.canonical = e.best_display()
            return Resolution(e, "new", 0.6, ev + ["First time this merchant appears."], True)
        e = self._new(placeholder_name(vpa), placeholder=True)
        self._attach(e, vpa, clean)
        return Resolution(e, "new", 0.5, ev + ["No readable name - identified by its UPI address only."], True)

    # -- user actions ------------------------------------------------------------------------------------------------
    def merge(self, keep_id: int, drop_id: int) -> None:
        keep, drop = self.entities[keep_id], self.entities.pop(drop_id)
        for k, eid in list(self.keys.items()):
            if eid == drop_id:
                self.keys[k] = keep_id
        keep.names.update(drop.names)
        keep.vpas |= drop.vpas
        self.dirty.add(keep_id)
        if not keep.user_named and not keep.is_brand:
            keep.canonical = keep.best_display()

    def rename(self, eid: int, name: str) -> None:
        e = self.entities[eid]
        e.canonical, e.user_named = name.strip(), True
        self.dirty.add(eid)
