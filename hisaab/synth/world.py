"""A synthetic *world*: the money life of a fictional hostel student, with hidden ground truth.

Why synthetic?  Real statements are private (we never ask for them) and tiny.  A generator lets us (1) demo the product
with rich data, (2) MEASURE accuracy because we know the true merchant / kind / category of every row, and (3) test
hard cases on purpose: tea-sellers who accept money on a personal phone number, shops whose bank-registered name differs
from the signboard, truncated and mistyped names, price hikes, duplicates, a missing subscription ...

What it is NOT: evidence about real-world accuracy.  Numbers measured on this data show the system does what it was
designed to do; your own labelled month is the honest test.

Everything is seeded: the same seed gives the same world.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .. import seeds
from . import pools


# --------------------------------------------------------------------------------------------------------------------
@dataclass
class Entity:
    id: str
    kind: str                       # merchant | person | cash | bank | self
    name: str                       # the true, human name ("Ramesh Kirana Store")
    category: str                   # true category for debits (or income category for credits)
    is_brand: bool = False
    is_local: bool = False
    vpas: list[str] = field(default_factory=list)
    printed: list[str] = field(default_factory=list)   # how the bank prints the name (UPPERCASE). >1 = registered-name mismatch
    marker: str | None = None       # P2M / P2A as a bank that prints markers would print it
    personal_vpa: bool = False      # merchant that takes money on a personal account (the hard case)
    credit_category: str | None = None
    note: str = ""


@dataclass
class Rec:
    dt: datetime
    amount: float
    direction: str                  # debit | credit
    ent: str
    mode: str = "UPI"               # UPI | ATM | NEFT | CHARGE | INTEREST | WALLET
    vpa: str | None = None
    printed: str = ""
    remark: str = ""
    rrn: str = ""
    anomaly: str | None = None
    series: str | None = None
    balance: float = 0.0


# --------------------------------------------------------------------------------------------------------------------
def _poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        k += 1
        p *= rng.random()
        if p <= L:
            return k - 1


def _wchoice(rng: random.Random, items: list, weights: list[float]):
    return rng.choices(items, weights=weights, k=1)[0]


HOURS = {"morning": (7, 10), "noon": (12, 14), "evening": (16, 19), "night": (20, 23), "day": (9, 20), "late": (21, 23)}
WEEKEND_HEAVY = [0.8, 0.8, 0.8, 0.8, 1.1, 1.8, 1.6]
FLAT = [1.0] * 7
WEEKDAY_HEAVY = [1.3, 1.3, 1.3, 1.3, 1.2, 0.5, 0.4]


def _rand_time(rng: random.Random, d: date, hours: tuple[int, int]) -> datetime:
    h = rng.randint(hours[0], hours[1])
    return datetime(d.year, d.month, d.day, h, rng.randint(0, 59), rng.randint(0, 59))


def _days(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def sched_poisson(rng, start, end, rate_week, hours="day", wd=FLAT):
    out = []
    for d in _days(start, end):
        lam = rate_week / 7.0 * wd[d.weekday()]
        for _ in range(_poisson(rng, lam)):
            out.append(_rand_time(rng, d, HOURS[hours]))
    return out


def sched_daily(rng, start, end, p, hours="morning", wd=FLAT):
    return [_rand_time(rng, d, HOURS[hours]) for d in _days(start, end) if rng.random() < min(0.97, p * wd[d.weekday()])]


def sched_monthly(rng, start, end, day, jitter=1, hours="day"):
    out = []
    y, m = start.year, start.month
    while date(y, m, 1) <= end:
        try:
            d = date(y, m, min(day, 28)) + timedelta(days=rng.randint(-jitter, jitter) + max(0, day - 28))
        except ValueError:
            d = date(y, m, 28)
        if start <= d <= end:
            out.append(_rand_time(rng, d, HOURS[hours]))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def sched_interval(rng, start, end, every, jitter=2, hours="day", first_offset=None):
    out = []
    d = start + timedelta(days=first_offset if first_offset is not None else rng.randint(0, every - 1))
    while d <= end:
        out.append(_rand_time(rng, d, HOURS[hours]))
        d += timedelta(days=max(1, every + rng.randint(-jitter, jitter)))
    return out


def amt_lognorm(rng, median, sigma, lo=1, hi=1e9, step=1):
    x = median * math.exp(rng.gauss(0, sigma))
    x = min(max(x, lo), hi)
    return float(max(lo, round(x / step) * step))


def mutate_name(rng, name: str) -> str:
    """A realistic typo: swap two adjacent letters or drop a vowel (only if the name is long enough)."""
    if len(name) < 8:
        return name
    i = rng.randint(2, len(name) - 3)
    if rng.random() < 0.5 and name[i].isalpha() and name[i + 1].isalpha():
        return name[:i] + name[i + 1] + name[i] + name[i + 2:]
    for j in range(i, len(name)):
        if name[j] in "AEIOU":
            return name[:j] + name[j + 1:]
    return name


def _slug(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())[:14]


def _phone(rng) -> str:
    return str(rng.choice([6, 7, 8, 9])) + "".join(str(rng.randint(0, 9)) for _ in range(9))


class World:
    """Build a world for [start, end].  `scale` multiplies activity (different 'users')."""

    def __init__(self, seed: int = 7, start: date | None = None, end: date | None = None, scale: float = 1.0, shift: float = 0.0):
        """`shift` in [0,1] makes the world MESSIER than the one the models are trained on (friends pay odd/small amounts and rarely pay
        back, shops accept round amounts): used to test how models cope when reality differs from the training distribution."""
        self.rng = random.Random(seed)
        self.seed = seed
        self.shift = shift
        self.end = end or (date.today() - timedelta(days=1))
        self.start = start or (self.end - timedelta(days=130))
        self.scale = scale
        self.ents: dict[str, Entity] = {}
        self.recs: list[Rec] = []
        self.events: list[dict] = []            # event-level truth: overdue subscriptions, spikes ...
        self._n = 0
        self._used_names: set[str] = set()
        self._build()

    # ---- entity factories ------------------------------------------------------------------------------------------
    def _new_id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n:03d}"

    def _shop_name(self, category: str, ambiguous: bool = False) -> str:
        rng = self.rng
        for _ in range(60):
            tpl = rng.choice(pools.AMBIGUOUS_TEMPLATES if ambiguous else pools.SHOP_TEMPLATES[category])
            owner = rng.choice(pools.OWNER_FIRST) if rng.random() < 0.6 else rng.choice(pools.OWNER_LAST)
            owner2 = rng.choice(pools.OWNER_LAST)
            name = (tpl.replace("{O2}", owner2).replace("{O}", owner).replace("{D}", rng.choice(pools.DEITY))
                    .replace("{P}", rng.choice(pools.PREFIX)).replace("{L}", rng.choice(pools.PLACE))
                    .replace("{I}", rng.choice("KSRMPGAB") + " " + rng.choice("KSRMPGAB")))
            if name not in self._used_names:
                self._used_names.add(name)
                return name
        return name + " " + str(rng.randint(2, 99))

    def _person(self, role: str = "friend", first: str | None = None) -> tuple[str, str]:
        rng = self.rng
        used_first = {n.split()[0] for n in self._used_names}
        for _ in range(80):
            f = first or rng.choice(pools.FRIEND_FIRST)
            if first is None and f.split()[0] in used_first:
                continue                          # keep first names distinct so the demo reads naturally
            n = f"{f} {rng.choice(pools.FRIEND_LAST)}"
            if n not in self._used_names:
                self._used_names.add(n)
                return n, n
        return "FRIEND " + str(rng.randint(1, 999)), "FRIEND"

    def _vpa_merchant(self, name: str) -> tuple[str, str]:
        """(vpa, marker) for a merchant QR. Shapes follow what is commonly seen on statements - unverified guesses
        about specific prefixes; the classifier learns their usefulness from data/corrections rather than trusting them."""
        rng = self.rng
        r = rng.random()
        if r < 0.30:
            return f"paytmqr{rng.randint(10**11, 10**12 - 1)}{''.join(rng.choice('ABCDEFGHJKLMNPQRSTUVWXYZ') for _ in range(4))}@paytm".lower(), "P2M"
        if r < 0.55:
            return f"q{rng.randint(10**8, 10**9 - 1)}@ybl", "P2M"
        if r < 0.70:
            return f"{_slug(name)}{rng.randint(1, 99)}@{rng.choice(['okicici', 'okaxis', 'oksbi'])}", "P2M"
        if r < 0.82:
            return f"{_slug(name)}.{rng.randint(1000, 9999)}@{rng.choice(['paytm', 'ptsbi', 'ptyes'])}", "P2M"
        if r < 0.92:
            return f"bharatpe.{rng.randint(10**9, 10**10 - 1)}@fbpe", "P2M"
        return f"{_slug(name)}@upi", "P2M"

    def _vpa_person(self, name: str) -> str:
        rng = self.rng
        first, _, last = name.partition(" ")
        r = rng.random()
        if r < 0.40:
            return f"{_phone(rng)}@{rng.choice(['ybl', 'ybl', 'ibl', 'axl'])}"
        if r < 0.65:
            return f"{first.lower()}{last.lower()}{rng.randint(1, 99)}@oksbi"
        if r < 0.85:
            return f"{first.lower()}.{last.lower().replace(' ', '')}@okaxis"
        return f"{_phone(rng)}@paytm"

    def add_local(self, category: str, ambiguous: bool = False, personal: bool = False, two_qr: bool = False,
                  name: str | None = None, label: str | None = None) -> Entity:
        rng = self.rng
        if personal:
            pn = f"{rng.choice(pools.OWNER_FIRST)} {rng.choice(pools.OWNER_LAST)}"
            name = name or f"{pn.split()[0].title()}'s {label or 'stall'}"
        else:
            name = name or self._shop_name(category, ambiguous)
        e = Entity(self._new_id("L"), "merchant", name.title() if name.isupper() else name, category, is_local=True,
                   note="personal-qr" if personal else ("blank-name" if ambiguous else "typed-name"))
        if personal:
            e.vpas = [self._vpa_person(pn)]
            e.printed = [pn]
            e.marker = "P2A"
            e.personal_vpa = True
        else:
            v, mk = self._vpa_merchant(name)
            e.vpas = [v]
            e.marker = mk
            e.printed = [name]
            if two_qr:                      # a second QR whose registered name is the owner's, not the signboard's
                v2, _ = self._vpa_merchant(name)
                e.vpas.append(v2)
                e.printed.append(f"{rng.choice(pools.OWNER_FIRST)} {rng.choice(pools.OWNER_LAST)}")
        self.ents[e.id] = e
        return e

    def add_brand(self, name: str, category: str, printed: list[str], vpa: str, local=False, display: str | None = None) -> Entity:
        known = any(name.lower() == b["name"].lower().replace("'", "'") or name.split()[0].lower() in {a.split()[0].lower() for a in b["aliases"]}
                    for b in seeds.brands())
        e = Entity(self._new_id("B"), "merchant", display or name, category, is_brand=True, is_local=local,
                   vpas=[vpa], printed=printed, marker="P2M", note="brand-known" if known else "brand-unknown")
        self.ents[e.id] = e
        return e

    def add_person(self, category_credit: str = "Received from Friends", name: str | None = None) -> Entity:
        nm, _ = (name, name) if name else self._person()
        e = Entity(self._new_id("P"), "person", nm.title(), "Transfers (P2P)", vpas=[self._vpa_person(nm)], printed=[nm],
                   marker="P2A", credit_category=category_credit)
        self.ents[e.id] = e
        return e

    def add_special(self, kind: str, name: str, category: str, printed: str = "", credit_category: str | None = None) -> Entity:
        e = Entity(self._new_id("S"), kind, name, category, printed=[printed or name.upper()], credit_category=credit_category)
        self.ents[e.id] = e
        return e

    # ---- record emission -------------------------------------------------------------------------------------------
    def _rrn(self, dt: datetime) -> str:
        julian = dt.timetuple().tm_yday
        return f"{dt.year % 10}{julian:03d}{dt.hour:02d}{self.rng.randint(0, 999999):06d}"

    def emit(self, ent: Entity, dt: datetime, amount: float, direction: str = "debit", mode: str = "UPI",
             remark: str | None = None, series: str | None = None, anomaly: str | None = None) -> Rec:
        rng = self.rng
        if dt.date() < self.start or dt.date() > self.end:
            return None  # type: ignore[return-value]
        vi = rng.randrange(len(ent.vpas)) if ent.vpas else 0
        printed = ent.printed[min(vi, len(ent.printed) - 1)] if ent.printed else ent.name.upper()
        if ent.kind in ("merchant",) and rng.random() < 0.06 and not ent.is_brand:
            printed = mutate_name(rng, printed)
        if remark is None:
            if ent.kind == "person":
                remark = rng.choice(pools.REMARKS_P2P)
            else:
                remark = rng.choice(pools.REMARKS_GENERIC)
        rec = Rec(dt=dt, amount=round(float(amount), 2), direction=direction, ent=ent.id, mode=mode,
                  vpa=(ent.vpas[vi] if ent.vpas and mode == "UPI" else None), printed=printed, remark=remark,
                  rrn=self._rrn(dt), anomaly=anomaly, series=series)
        self.recs.append(rec)
        return rec

    # ---- the story -------------------------------------------------------------------------------------------------
    def _build(self) -> None:
        rng, S, E, sc = self.rng, self.start, self.end, self.scale

        def n(rate):                        # scale activity
            return rate * sc

        # ===== income =====
        dad = self.add_person("Family & Allowance", name="SURENDRA KUMAR")
        dad.name = "Surendra Kumar (Dad)"
        for dt in sched_monthly(rng, S, E, 2, 1, "morning"):
            self.emit(dad, dt, rng.choice([12000, 12000, 14000, 10000]), "credit", remark="pocket money", series="allowance")
        for dt in sched_poisson(rng, S, E, n(0.08), "day"):
            self.emit(dad, dt, rng.choice([500, 1000, 2000]), "credit", remark="extra")
        sch = self.add_special("merchant", "State Scholarship (DBT)", "Others", "DBT SCHOLARSHIP PORTAL", "Salary & Stipend")
        self.emit(sch, _rand_time(rng, S + timedelta(days=rng.randint(40, 55)), HOURS["day"]), 10000.0, "credit", mode="NEFT")
        intr = self.add_special("bank", "Savings interest", "Finance & Fees", "INT.PD", "Interest Earned")
        for d in (date(S.year, 6, 30), date(S.year, 9, 30), date(S.year, 12, 31)):
            if S <= d <= E:
                self.emit(intr, datetime(d.year, d.month, d.day, 2, 10), round(rng.uniform(24, 41), 2), "credit", mode="INTEREST", series="interest")
        cash_back = self.add_brand("PhonePe Rewards", "Others", ["PHONEPE REWARDS", "CASHBACK"], "phonepe.rewards@ybl", display="PhonePe Rewards")
        cash_back.credit_category = "Refund & Cashback"
        for dt in sched_poisson(rng, S, E, n(0.12), "day"):
            self.emit(cash_back, dt, rng.choice([5.0, 8.0, 12.0, 15.0, 25.0]), "credit", remark="Cashback received")

        # ===== food =====
        tapri1 = self.add_local("Food & Dining", name="SHARMA TEA STALL")
        tapri2 = self.add_local("Food & Dining", personal=True, label="chai tapri")
        for dt in sched_daily(rng, S, E, 0.55 * sc, "morning", WEEKDAY_HEAVY):
            self.emit(tapri1, dt, rng.choice([10, 10, 15, 20, 20, 25]))
        for dt in sched_daily(rng, S, E, 0.30 * sc, "evening"):
            self.emit(tapri2, dt, float(rng.choice([50, 100])) if rng.random() < 0.25 * self.shift else rng.choice([10, 10, 12, 15, 20]))
        canteen = self.add_local("Food & Dining", name=f"{rng.choice(pools.PLACE)} CANTEEN")
        for dt in sched_poisson(rng, S, E, n(3.2), "noon", WEEKDAY_HEAVY):
            self.emit(canteen, dt, amt_lognorm(rng, 62, 0.35, 30, 140))
        for cat_, rate, med, sg, hrs, ambiguous in [
            ("Food & Dining", 0.8, 90, 0.6, "evening", False),     # sweets
            ("Food & Dining", 0.5, 60, 0.5, "evening", False),     # bakery
            ("Food & Dining", 0.45, 70, 0.4, "evening", False),    # momos
            ("Food & Dining", 0.4, 45, 0.4, "day", False),         # juice
            ("Food & Dining", 0.35, 140, 0.5, "night", True),      # ambiguous name, restaurant-ish amounts
        ]:
            e = self.add_local(cat_, ambiguous=ambiguous, two_qr=rng.random() < 0.1)
            for dt in sched_poisson(rng, S, E, n(rate), hrs):
                self.emit(e, dt, amt_lognorm(rng, med, sg, 15, 700))
        resto = self.add_local("Food & Dining", name=f"HOTEL {rng.choice(pools.DEITY)}")
        for dt in sched_poisson(rng, S, E, n(0.4), "night", WEEKEND_HEAVY):
            self.emit(resto, dt, amt_lognorm(rng, 380, 0.4, 120, 1200))
        mess = self.add_local("Food & Dining", personal=True, label="hostel mess")
        for dt in sched_monthly(rng, S, E, 5, 2, "day"):
            self.emit(mess, dt, 3200.0, series="mess", remark="mess fee")
        for nm, cat_, aliases, vpa, rate, med in [
            ("Zomato", "Food & Dining", ["ZOMATO", "ZOMATO LIMITED", "ZOMATO ONLINE ORDERING", "ZOMATO MEDIA PVT LTD"], "zomato@icici", 0.7, 240),
            ("Swiggy", "Food & Dining", ["SWIGGY", "BUNDL TECHNOLOGIES", "BUNDL TECHNOLOGIES PVT", "SWIGGY BANGALORE"], "swiggy@icici", 0.9, 260),
            ("Domino's", "Food & Dining", ["DOMINOS PIZZA", "JUBILANT FOODWORKS", "DOMINOS"], "dominos.payu@hdfcbank", 0.15, 380),
        ]:
            e = self.add_brand(nm, cat_, aliases, vpa)
            for dt in sched_poisson(rng, S, E, n(rate), "night", WEEKEND_HEAVY):
                self.emit(e, dt, amt_lognorm(rng, med, 0.4, 90, 1200))
            if nm == "Swiggy":
                self._swiggy = e
        for nm, cat_, pr in [("Taco Bell", "Food & Dining", pools.EXTRA_BRANDS[0]), ("Blue Tokai Coffee", "Food & Dining", pools.EXTRA_BRANDS[1])]:
            e = self.add_brand(nm, cat_, pr[2], f"{_slug(nm)}@icici", local=False)
            for dt in sched_poisson(rng, S, E, n(0.1), "evening"):
                self.emit(e, dt, amt_lognorm(rng, 330, 0.35, 120, 900))

        # ===== groceries =====
        kirana = self.add_local("Groceries", name=f"{rng.choice(pools.OWNER_FIRST)} KIRANA STORE", two_qr=True)
        for dt in sched_poisson(rng, S, E, n(1.2), "day"):
            self.emit(kirana, dt, amt_lognorm(rng, 105, 0.7, 20, 700))
        kirana2 = self.add_local("Groceries", ambiguous=True)
        for dt in sched_poisson(rng, S, E, n(0.6), "day"):
            self.emit(kirana2, dt, amt_lognorm(rng, 110, 0.6, 20, 600, 50 if rng.random() < 0.4 else 1))
        fruit = self.add_local("Groceries", personal=True, label="fruit stall")
        for dt in sched_interval(rng, S, E, 7, 1, "morning", first_offset=rng.randint(0, 6)):
            self.emit(fruit, dt, float(rng.choice([50, 100, 100, 150])) if rng.random() < 0.5 else amt_lognorm(rng, 110, 0.35, 40, 260, 10), series="weekly_fruit")
        dairy = self.add_local("Groceries")
        for dt in sched_poisson(rng, S, E, n(0.7), "morning"):
            self.emit(dairy, dt, amt_lognorm(rng, 55, 0.4, 20, 200))
        for nm, aliases, vpa, rate, med, cat_ in [
            ("Blinkit", ["BLINKIT", "BLINK COMMERCE PRIVATE LIMITED", "BLINK COMMERCE"], "blinkit@icici", 0.5, 320, "Groceries"),
            ("Zepto", ["ZEPTO", "KIRANAKART TECHNOLOGIES PRIVATE", "ZEPTO MARKETPLACE"], "zepto.payu@axisbank", 0.35, 280, "Groceries"),
            ("BigBasket", ["BIGBASKET", "SUPERMARKET GROCERY SUPPLIES", "BIGBASKET.COM"], "bigbasket@hdfcbank", 0.12, 600, "Groceries"),
            ("DMart", ["DMART", "AVENUE SUPERMARTS LIMITED", "DMART CITY"], "dmart@okicici", 0.15, 900, "Groceries"),
        ]:
            e = self.add_brand(nm, cat_, aliases, vpa)
            for dt in sched_poisson(rng, S, E, n(rate), "evening"):
                self.emit(e, dt, amt_lognorm(rng, med, 0.5, 80, 3000))

        # ===== transport =====
        for i, rate in enumerate((1.1, 0.55)):
            a = self.add_local("Travel & Transport", personal=True, label="auto")
            for dt in sched_poisson(rng, S, E, n(rate), "day"):
                self.emit(a, dt, rng.choice([30, 40, 50, 50, 60, 80, 100, 120]) if rng.random() > 0.3 * self.shift else float(round(rng.uniform(25, 140))))
        for nm, aliases, vpa, rate, med in [
            ("Rapido", ["RAPIDO", "ROPPEN TRANSPORTATION SERVICES", "ROPPEN TRANSPORTATION"], "rapido@axisbank", 0.7, 70),
            ("Uber", ["UBER INDIA SYSTEMS PRIVATE", "UBER", "UBER INDIA"], "uber.india@hdfcbank", 0.25, 180),
            ("Ola", ["ANI TECHNOLOGIES", "OLA CABS", "OLA"], "olacabs@icici", 0.1, 150),
        ]:
            e = self.add_brand(nm, "Travel & Transport", aliases, vpa)
            for dt in sched_poisson(rng, S, E, n(rate), "day"):
                self.emit(e, dt, amt_lognorm(rng, med, 0.5, 30, 600))
        irctc = self.add_brand("IRCTC", "Travel & Transport", ["IRCTC", "IRCTC LTD", "INDIAN RAILWAY CATERING AND TOURISM"], "irctc@sbi")
        for off in (rng.randint(20, 35), rng.randint(85, 100)):
            self.emit(irctc, _rand_time(rng, S + timedelta(days=off), HOURS["late"]), amt_lognorm(rng, 620, 0.35, 250, 1600))
        redbus = self.add_brand("redBus", "Travel & Transport", ["REDBUS INDIA PRIVATE", "REDBUS"], "redbus@icici")
        self.emit(redbus, _rand_time(rng, S + timedelta(days=rng.randint(55, 70)), HOURS["late"]), 780.0)
        pump = self.add_local("Travel & Transport", name=f"{rng.choice(pools.OWNER_LAST)} FILLING STATION")
        for dt in sched_poisson(rng, S, E, n(0.4), "day"):
            self.emit(pump, dt, rng.choice([100, 150, 200, 300]))

        # ===== shopping =====
        for nm, aliases, vpa, rate, med in [
            ("Amazon", ["AMAZON PAY INDIA PRIVATE", "AMAZON SELLER SERVICES", "AMAZON", "AMAZON RETAIL INDIA"], "amazon@apl", 0.2, 380),
            ("Flipkart", ["FLIPKART INTERNET PRIVATE", "FLIPKART", "FLIPKART PAYMENTS"], "flipkart@axisbank", 0.09, 600),
            ("Myntra", ["MYNTRA DESIGNS PRIVATE", "MYNTRA"], "myntra@axisbank", 0.06, 650),
        ]:
            e = self.add_brand(nm, "Shopping", aliases, vpa)
            for dt in sched_poisson(rng, S, E, n(rate), "night"):
                rec = self.emit(e, dt, amt_lognorm(rng, med, 0.55, 99, 3000))
            if nm == "Amazon":
                self._amazon = e
        for nm, pr in (("Bata", pools.EXTRA_BRANDS[2]), ("Fabindia", pools.EXTRA_BRANDS[3])):
            e = self.add_brand(nm, "Shopping", pr[2], f"{_slug(nm)}@hdfcbank")
            self.emit(e, _rand_time(rng, S + timedelta(days=rng.randint(10, 120)), HOURS["evening"]), amt_lognorm(rng, 1400, 0.4, 500, 4000, 10))
        fashion = self.add_local("Shopping")
        for dt in sched_poisson(rng, S, E, n(0.07), "evening"):
            self.emit(fashion, dt, amt_lognorm(rng, 900, 0.5, 200, 3000, 10))
        mobile = self.add_local("Shopping", name=f"{rng.choice(pools.OWNER_FIRST)} MOBILE POINT")
        for dt in sched_poisson(rng, S, E, n(0.08), "day"):
            self.emit(mobile, dt, amt_lognorm(rng, 250, 0.7, 50, 1500, 10))

        # ===== bills & subscriptions =====
        jio = self.add_brand("Jio", "Bills & Recharge", ["RELIANCE JIO INFOCOMM LIMITED", "JIO PREPAID", "JIO"], "jio@icici")
        for dt in sched_monthly(rng, S, E, 11, 2, "day"):
            self.emit(jio, dt, 299.0, series="jio", remark="recharge")
        subs = [
            ("Netflix", "NETFLIX ENTERTAINMENT SERVICES INDIA LLP", "netflix@hdfcbank", 5, 199.0, "netflix"),
            ("Spotify", "SPOTIFY INDIA", "spotify@icici", 14, 59.0, "spotify"),
            ("YouTube Premium", "GOOGLE YOUTUBE PREMIUM", "youtube@okaxis", 21, 89.0, "youtube"),
        ]
        for nm, printed, vpa, day, price, sid in subs:
            e = self.add_brand(nm, "Subscriptions & Entertainment", [printed, nm.upper()], vpa)
            dts = sched_monthly(rng, S, E, day, 1, "morning")
            for idx, dt in enumerate(dts):
                if sid == "netflix" and idx == len(dts) - 1 and len(dts) >= 3:
                    self.events.append({"type": "missing_recurring", "entity": e.id, "series": sid, "expected": dt.date().isoformat()})
                    continue                # Netflix silently stopped: the 'overdue' test
                amt, an = price, None
                if sid == "spotify" and idx >= len(dts) - 2 and len(dts) >= 4:
                    amt = 89.0
                    an = "price_hike" if idx == len(dts) - 2 else None
                self.emit(e, dt, amt, series=sid, anomaly=an, remark="subscription")
        bms = self.add_brand("BookMyShow", "Subscriptions & Entertainment", ["BIGTREE ENTERTAINMENT PVT", "BOOKMYSHOW"], "bookmyshow@icici")
        for dt in sched_poisson(rng, S, E, n(0.12), "night", WEEKEND_HEAVY):
            self.emit(bms, dt, amt_lognorm(rng, 380, 0.4, 150, 1200))
        gplay = self.add_brand("Google Play", "Subscriptions & Entertainment", ["GOOGLE PLAY", "GOOGLE INDIA DIGITAL SERVICES"], "googleplay@okaxis")
        for dt in sched_poisson(rng, S, E, n(0.12), "night"):
            self.emit(gplay, dt, rng.choice([49.0, 99.0, 149.0, 399.0]))

        # ===== health / education / care =====
        med = self.add_local("Health & Medical", two_qr=rng.random() < 0.3)
        for dt in sched_poisson(rng, S, E, n(0.3), "day"):
            self.emit(med, dt, amt_lognorm(rng, 120, 0.7, 20, 700))
        clinic = self.add_local("Health & Medical")
        for dt in sched_poisson(rng, S, E, n(0.06), "day"):
            self.emit(clinic, dt, amt_lognorm(rng, 400, 0.3, 200, 800, 50))
        apollo = self.add_brand("Apollo Pharmacy", "Health & Medical", ["APOLLO PHARMACY", "APOLLO PHARMACIES LIMITED"], "apollo@icici")
        for dt in sched_poisson(rng, S, E, n(0.07), "day"):
            self.emit(apollo, dt, amt_lognorm(rng, 280, 0.6, 60, 1200))
        xerox = self.add_local("Education", name=f"{rng.choice(pools.PLACE)} XEROX & PRINTS")
        for dt in sched_poisson(rng, S, E, n(0.8), "day", WEEKDAY_HEAVY):
            self.emit(xerox, dt, amt_lognorm(rng, 30, 0.8, 5, 250))
        books = self.add_local("Education")
        for dt in sched_poisson(rng, S, E, n(0.1), "day"):
            self.emit(books, dt, amt_lognorm(rng, 300, 0.6, 60, 1200))
        udemy = self.add_brand("Udemy", "Education", ["UDEMY", "UDEMY INC"], "udemy@icici")
        self.emit(udemy, _rand_time(rng, S + timedelta(days=rng.randint(30, 100)), HOURS["night"]), 449.0)
        printo = self.add_brand("Printo", "Education", pools.EXTRA_BRANDS[5][2], "printo@icici")
        self.emit(printo, _rand_time(rng, S + timedelta(days=rng.randint(20, 100)), HOURS["day"]), 186.0)
        exam = self.add_special("merchant", "Institute exam section", "Education", "INSTITUTE EXAM SECTION")
        exam.is_local = True
        self.emit(exam, _rand_time(rng, S + timedelta(days=rng.randint(60, 90)), HOURS["day"]), 2500.0, mode="NEFT")
        salon = self.add_local("Personal Care")
        for dt in sched_interval(rng, S, E, 28, 4, "evening"):
            self.emit(salon, dt, amt_lognorm(rng, 120, 0.3, 60, 300, 10), series="salon")
        laundry = self.add_local("Personal Care")
        for dt in sched_interval(rng, S, E, 14, 3, "day"):
            self.emit(laundry, dt, amt_lognorm(rng, 150, 0.35, 60, 400, 10), series="laundry")
        gym = self.add_local("Personal Care", name=f"{rng.choice(pools.PREFIX)} FITNESS CLUB")
        for dt in sched_monthly(rng, S + timedelta(days=45), E, 1, 1, "morning"):
            self.emit(gym, dt, 500.0, series="gym", remark="gym fee")
        hostel = self.add_special("merchant", "Hostel maintenance fund", "Rent & Hostel", "HOSTEL MAINTENANCE FUND")
        hostel.is_local = True
        self.emit(hostel, _rand_time(rng, S + timedelta(days=rng.randint(10, 30)), HOURS["day"]), 1500.0, mode="NEFT")

        # ===== cash, fees, wallet =====
        atm = self.add_special("cash", "ATM withdrawal", "Cash Withdrawal", "ATM CASH WITHDRAWAL")
        for dt in sched_interval(rng, S, E, 22, 6, "day"):
            self.emit(atm, dt, float(rng.choice([500, 500, 1000, 1000, 2000])), mode="ATM", remark="", series="atm")
        sms = self.add_special("bank", "SMS alert charges", "Finance & Fees", "SMS ALERT CHARGES")
        for d in (date(S.year, 6, 28), date(S.year, 9, 28)):
            if S <= d <= E:
                self.emit(sms, datetime(d.year, d.month, d.day, 3, 5), 17.7, mode="CHARGE", series="sms", remark="")
        fee = self.add_special("bank", "Debit card annual fee", "Finance & Fees", "DEBIT CARD ANNUAL FEE")
        self.emit(fee, _rand_time(rng, S + timedelta(days=rng.randint(15, 100)), (3, 4)), 236.0, mode="CHARGE", remark="")
        wallet = self.add_special("self", "Paytm wallet top-up", "Self Transfer", "PAYTM WALLET")
        wallet.vpas = ["paytm@paytm"]
        for off in (rng.randint(20, 40), rng.randint(70, 110)):
            self.emit(wallet, _rand_time(rng, S + timedelta(days=off), HOURS["day"]), 500.0, remark="Add money")

        # ===== people =====
        friends = [self.add_person() for _ in range(6)]
        twin_first = friends[0].name.split()[0].upper()
        twin = Entity(self._new_id("P"), "person", "", "Transfers (P2P)", marker="P2A", credit_category="Received from Friends")
        nm, _ = self._person(first=twin_first)               # same first name, different surname: must NOT be merged
        twin.name, twin.printed, twin.vpas = nm.title(), [nm], [self._vpa_person(nm)]
        self.ents[twin.id] = twin
        friends.append(twin)

        def friend_amount() -> float:
            """Friends are NOT always round: bill splits give odd amounts (Rs 137, 246 ...)."""
            if rng.random() < 0.15 * self.shift:
                return float(rng.choice([10, 12, 15, 20, 25, 30]))               # a friend's share of a chai / auto: small, like a shop payment
            if rng.random() < 0.55 - 0.4 * self.shift:
                return float(rng.choice([20, 30, 50, 80, 100, 100, 150, 200, 250, 300, 500]))
            return float(round(rng.uniform(15, 700)))

        for i, f in enumerate(friends):
            rate = rng.choice([0.2, 0.3, 0.45, 0.7]) if i < 5 else 0.07          # two friends are paid only once or twice
            one_way = rng.random() < 0.25 + 0.3 * self.shift                      # some friends never pay back (in this window)
            for dt in sched_poisson(rng, S, E, n(rate), "day", WEEKEND_HEAVY):
                amt = friend_amount()
                self.emit(f, dt, amt)
                if not one_way and rng.random() < 0.45 - 0.25 * self.shift:     # split-bill settled back within a few days
                    back = dt + timedelta(days=rng.randint(1, 4), hours=rng.randint(0, 6))
                    self.emit(f, back, amt, "credit", remark=rng.choice(["return", "thanks", "split", "udhar"]))
            if not one_way:
                for dt in sched_poisson(rng, S, E, n(rate * 0.25), "day"):
                    self.emit(f, dt, friend_amount(), "credit")
        roomie = self.add_person(name="ROHAN MISHRA")
        roomie.name = "Rohan Mishra (roommate)"
        for dt in sched_poisson(rng, S, E, n(0.25), "night"):
            self.emit(roomie, dt, amt_lognorm(rng, 400, 0.5, 100, 1500, 10))

        # ===== injected anomalies (ground truth) =====
        w_start = S + timedelta(days=int((E - S).days * 0.80))
        ew = self.add_local("Shopping", name="ELECTRO WORLD")
        self.emit(ew, datetime(w_start.year, w_start.month, w_start.day, 2, 14, 31), 7999.0, anomaly="large_new_merchant_night")
        new_p = self.add_person(name="AMIT VERMA")
        self.emit(new_p, _rand_time(rng, w_start + timedelta(days=rng.randint(2, 8)), HOURS["day"]), 5000.0, anomaly="new_payee_large_p2p", remark="urgent")
        sw = [r for r in self.recs if r.ent == getattr(self, "_swiggy", Entity("", "", "", "")).id and r.direction == "debit"]
        if sw:
            base = rng.choice(sw[len(sw) // 2:])
            base.anomaly = base.anomaly or "duplicate_first"
            dup = Rec(dt=base.dt + timedelta(minutes=3), amount=base.amount, direction="debit", ent=base.ent, mode="UPI",
                      vpa=base.vpa, printed=base.printed, remark=base.remark, rrn=self._rrn(base.dt + timedelta(minutes=3)), anomaly="duplicate")
            self.recs.append(dup)
        # a food 'treat week' in the last complete month -> Food spend spikes (answers "where did my spending increase?")
        last_full_month_end = date(E.year, E.month, 1) - timedelta(days=1)
        spike_start = last_full_month_end - timedelta(days=9)
        for i in range(6):
            d = spike_start + timedelta(days=rng.randint(0, 8))
            self.emit(resto, _rand_time(rng, d, HOURS["night"]), amt_lognorm(rng, 650, 0.25, 400, 1100, 10), remark="treat")
        self.events.append({"type": "category_spike", "category": "Food & Dining", "month": last_full_month_end.strftime("%Y-%m")})

        # ===== order; when the account would go negative, 'dad sends extra' (what a real student does) =====
        self.recs = [r for r in self.recs if r is not None]
        self.recs.sort(key=lambda r: r.dt)
        bal, extra = 5000.0, []
        for r in self.recs:
            if r.direction == "credit":
                bal += r.amount
                continue
            if bal - r.amount < 0:
                need = r.amount - bal
                amt = float(max(1000, math.ceil((need + 500) / 500) * 500))
                t = r.dt - timedelta(minutes=rng.randint(20, 90))
                extra.append(Rec(dt=t, amount=amt, direction="credit", ent=dad.id, mode="UPI", vpa=dad.vpas[0],
                                 printed=dad.printed[0], remark="send money urgently", rrn=self._rrn(t)))
                bal += amt
            bal -= r.amount
        self.recs.extend(extra)
        self.recs.sort(key=lambda r: r.dt)
        bal = 5000.0
        for r in self.recs:
            bal += r.amount if r.direction == "credit" else -r.amount
            r.balance = round(bal, 2)

    # ---- introspection ---------------------------------------------------------------------------------------------
    def truth_category(self, r: Rec) -> str:
        e = self.ents[r.ent]
        if r.direction == "credit":
            return e.credit_category or ("Received from Friends" if e.kind == "person" else "Other Income")
        return e.category

    def summary(self) -> dict:
        deb = [r for r in self.recs if r.direction == "debit"]
        cre = [r for r in self.recs if r.direction == "credit"]
        return {"rows": len(self.recs), "debits": len(deb), "credits": len(cre), "entities": len(self.ents),
                "spend": round(sum(r.amount for r in deb), 2), "income": round(sum(r.amount for r in cre), 2),
                "start": self.recs[0].dt.date().isoformat(), "end": self.recs[-1].dt.date().isoformat()}
