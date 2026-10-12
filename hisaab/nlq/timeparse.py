"""Time expressions in questions  ->  a concrete date range.   "last month", "in September", "last 30 days", "pichle hafte" ...

KEY DESIGN CHOICE: "today" is the date of the LATEST transaction in the data, not the computer's clock.  A statement is a
historical file: if you import March's statement in June, "last month" should still mean the month before the data ends,
and the same question must give the same answer tomorrow.  The interpretation is always echoed back to the user.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

from ..core.analytics import Period, month_period, shift_month

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
MONTHS["sept"] = 9
_MON = "|".join(sorted(MONTHS, key=len, reverse=True))
NUMWORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30}


@dataclass
class TimeSpec:
    period: Period
    note: str                       # human sentence: "last month = September 2026"
    explicit: bool = True


def _n(tok: str) -> int | None:
    tok = tok.lower()
    return int(tok) if tok.isdigit() else NUMWORDS.get(tok)


def _month_of(m: int, ref: date) -> Period:
    """The most recent month named `m` that is not after the reference date."""
    y = ref.year if m <= ref.month else ref.year - 1
    return month_period(y, m)


def parse(text: str, ref: date, data_min: date, data_max: date) -> TimeSpec | None:
    t = " " + text.lower() + " "
    t = re.sub(r"\s+", " ", t)

    def mk(p: Period, note: str) -> TimeSpec:
        return TimeSpec(p, note)

    # ---- explicit ranges ------------------------------------------------------------------------------------------------
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b.*?\b(\d{4})-(\d{2})-(\d{2})\b", t)
    if m:
        a, b = date(int(m[1]), int(m[2]), int(m[3])), date(int(m[4]), int(m[5]), int(m[6]))
        return mk(Period(min(a, b), max(a, b), f"{a:%d %b %Y} – {b:%d %b %Y}"), "explicit dates")
    m = re.search(rf"\b(?:between|from)\s+(\d{{1,2}})(?:st|nd|rd|th)?\s*({_MON})?\s*,?\s*(?:(\d{{4}}))?\s*(?:to|and|till|until|-)\s*(\d{{1,2}})(?:st|nd|rd|th)?\s*({_MON})\b\s*(\d{{4}})?", t)
    if m:
        d1, m1, y1, d2, m2, y2 = m[1], m[2], m[3], m[4], m[5], m[6]
        mo2 = MONTHS[m2]
        mo1 = MONTHS[m1] if m1 else mo2
        yr2 = int(y2) if y2 else (ref.year if mo2 <= ref.month else ref.year - 1)
        yr1 = int(y1) if y1 else yr2
        try:
            a, b = date(yr1, mo1, int(d1)), date(yr2, mo2, int(d2))
            return mk(Period(a, b, f"{a:%d %b} – {b:%d %b %Y}"), "explicit dates")
        except ValueError:
            pass
    # ---- since / from <month> ------------------------------------------------------------------------------------------------
    m = re.search(rf"\b(?:since|from|after)\s+({_MON})\b(?:\s+(\d{{4}}))?", t)
    if m:
        mo = MONTHS[m[1]]
        yr = int(m[2]) if m[2] else (ref.year if mo <= ref.month else ref.year - 1)
        a = date(yr, mo, 1)
        return mk(Period(a, ref, f"since {calendar.month_name[mo]} {yr}"), f"from {a:%d %b %Y} to the latest transaction")
    # ---- named month ("in September", "sept 2026", "september spending") ---------------------------------------------------
    for m in re.finditer(rf"\b({_MON})\b(?:\s*,?\s*(\d{{4}}))?", t):
        if m[1] == "may" and not (m[2] or re.search(r"\b(in|during|for|of|on|since|from|to)\s*$", t[max(0, m.start() - 12):m.start()])):
            continue                                    # "may" the verb ("how much may I spend")
        mo = MONTHS[m[1]]
        if m[2]:
            p = month_period(int(m[2]), mo)
            return mk(p, p.label)
        p = _month_of(mo, ref)
        return mk(p, f"{p.label} (the latest {calendar.month_name[mo]} up to {ref:%d %b %Y})")
    # ---- relative: last N days/weeks/months --------------------------------------------------------------------------------
    m = re.search(r"\b(?:last|past|previous|recent)\s+(\w+)\s+(day|days|week|weeks|month|months)\b", t)
    if m and _n(m[1]):
        n, unit = _n(m[1]), m[2]
        if unit.startswith("day"):
            p = Period(ref - timedelta(days=n - 1), ref, f"last {n} days")
        elif unit.startswith("week"):
            p = Period(ref - timedelta(days=7 * n - 1), ref, f"last {n} weeks")
        else:
            y, mo = shift_month(ref.year, ref.month, -n)
            start = min(date(y, mo, min(ref.day, calendar.monthrange(y, mo)[1])) + timedelta(days=1), ref)
            p = Period(start, ref, f"last {n} months")
        return mk(p, f"{p.label}: {p.start:%d %b} – {p.end:%d %b %Y}")
    # ---- fixed phrases -----------------------------------------------------------------------------------------------------
    if re.search(r"\b(today|aaj)\b", t):
        return mk(Period(ref, ref, f"today ({ref:%d %b})"), f"today = {ref:%d %b %Y} (your latest transaction)")
    if re.search(r"\b(yesterday)\b", t):
        d = ref - timedelta(days=1)
        return mk(Period(d, d, f"yesterday ({d:%d %b})"), f"yesterday = {d:%d %b %Y}")
    if re.search(r"\blast week\b|\bprevious week\b", t):
        monday = ref - timedelta(days=ref.weekday())
        a, b = monday - timedelta(days=7), monday - timedelta(days=1)
        return mk(Period(a, b, f"last week ({a:%d %b} – {b:%d %b})"), f"last week = Mon {a:%d %b} – Sun {b:%d %b %Y}")
    if re.search(r"\bthis week\b|\bcurrent week\b", t):
        monday = ref - timedelta(days=ref.weekday())
        return mk(Period(monday, ref, f"this week (from {monday:%d %b})"), f"this week = {monday:%d %b} to {ref:%d %b %Y}")
    if re.search(r"\blast month\b|\bprevious month\b|\bprior month\b", t):
        y, mo = shift_month(ref.year, ref.month, -1)
        p = month_period(y, mo)
        return mk(p, f"last month = {p.label} (the month before {calendar.month_name[ref.month]} {ref.year}, the month of your latest transaction)")
    if re.search(r"\bthis month\b|\bcurrent month\b|\bmonth so far\b|\bmonth to date\b", t):
        last = calendar.monthrange(ref.year, ref.month)[1]
        p = month_period(ref.year, ref.month, ref.day)
        return mk(p, f"this month = {calendar.month_name[ref.month]} {ref.year}" + ("" if ref.day == last else f", 1–{ref.day} (so far)"))
    if re.search(r"\blast year\b|\bprevious year\b", t):
        return mk(Period(date(ref.year - 1, 1, 1), date(ref.year - 1, 12, 31), f"{ref.year - 1}"), f"last year = {ref.year - 1}")
    if re.search(r"\bthis year\b|\bcurrent year\b|\byear to date\b|\bytd\b", t):
        return mk(Period(date(ref.year, 1, 1), ref, f"{ref.year} so far"), f"this year = {ref.year}, up to {ref:%d %b}")
    if re.search(r"\b(recently|lately|these days|nowadays)\b", t):
        return mk(Period(ref - timedelta(days=29), ref, "last 30 days"), "recently = the last 30 days")
    if re.search(r"\b(all time|overall|ever|altogether|in total|since the beginning|whole|entire|so far)\b", t):
        return mk(Period(data_min, data_max, f"all time ({data_min:%d %b %Y} – {data_max:%d %b %Y})"), "all your data")
    return None


def previous_period(p: Period) -> Period:
    """The period of the same length immediately before p (or the previous calendar month if p is a calendar month)."""
    if p.start.day == 1 and (p.end == date(p.end.year, p.end.month, calendar.monthrange(p.end.year, p.end.month)[1])):
        y, m = shift_month(p.start.year, p.start.month, -1)
        return month_period(y, m)
    if p.start.day == 1 and p.start.month == p.end.month:                      # month to date -> same days of the previous month
        y, m = shift_month(p.start.year, p.start.month, -1)
        return month_period(y, m, p.end.day)
    days = p.days
    return Period(p.start - timedelta(days=days), p.start - timedelta(days=1), "the period before")
