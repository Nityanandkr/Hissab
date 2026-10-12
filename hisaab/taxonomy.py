"""The category system - one place, so the model, the rules and the UI always agree.

Design notes (why these 14 + 6):
  * Short, human categories ("Food / Grocery / Travel / Bills / etc."). More classes
    means fewer examples per class, and one month of a personal statement only has a few hundred rows.
  * 'Transfers (P2P)' is a *category* AND the merchant-vs-person question is a separate dimension (`kind`):
    a payment to a person can still be Rent, Food (the mess-contractor) etc. once the user says so.
  * 'Self Transfer' is neutral: moving money between your own wallet/accounts is not spending.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    name: str
    group: str       # 'expense' | 'income' | 'neutral'
    blurb: str
    examples: str


CATEGORIES: list[Category] = [
    # ---- expenses (money out) ------------------------------------------------------------------------------
    Category("Food & Dining", "expense", "Eating out, tea/snacks, canteen/mess, food delivery", "Swiggy, chai tapri, canteen, bakery"),
    Category("Groceries", "expense", "Kirana, fruits & vegetables, milk, supermarkets, quick-commerce", "kirana store, Blinkit, DMart"),
    Category("Travel & Transport", "expense", "Cabs, autos, trains, buses, flights, fuel, tolls", "IRCTC, Rapido, auto, petrol pump"),
    Category("Shopping", "expense", "Clothes, electronics, marketplaces, gifts", "Amazon, Myntra, mobile shop"),
    Category("Bills & Recharge", "expense", "Mobile/DTH/broadband, electricity, gas, water", "Jio recharge, electricity bill"),
    Category("Subscriptions & Entertainment", "expense", "OTT, music, movies, games, app subscriptions", "Netflix, Spotify, BookMyShow"),
    Category("Health & Medical", "expense", "Pharmacy, doctor, lab tests", "medical store, clinic, Apollo"),
    Category("Education", "expense", "Fees, books, stationery, xerox/printing, courses", "xerox shop, Udemy, exam fee"),
    Category("Personal Care", "expense", "Salon, laundry, tailor, gym", "salon, dhobi, gym"),
    Category("Rent & Hostel", "expense", "Hostel fee, PG, rent, maintenance", "hostel fee, PG rent"),
    Category("Cash Withdrawal", "expense", "ATM cash", "ATM WDL"),
    Category("Finance & Fees", "expense", "Bank charges, EMI, insurance, investments", "SMS charges, EMI, SIP"),
    Category("Transfers (P2P)", "expense", "Money sent to people", "friend, relative"),
    Category("Others", "expense", "Anything that fits nowhere else", ""),
    # ---- neutral ----------------------------------------------------------------------------------------------
    Category("Self Transfer", "neutral", "Moving your own money (wallet top-up, own accounts)", "Add money to wallet"),
    # ---- income (money in) ------------------------------------------------------------------------------------
    Category("Family & Allowance", "income", "Pocket money / allowance from family", "monthly money from home"),
    Category("Salary & Stipend", "income", "Salary, stipend, scholarship", "stipend, scholarship DBT"),
    Category("Refund & Cashback", "income", "Refunds, reversals, cashback", "refund, cashback"),
    Category("Received from Friends", "income", "Money received from people (splits, returns)", "friend paid back"),
    Category("Interest Earned", "income", "Bank interest", "INT.PD"),
    Category("Other Income", "income", "Any other credit", ""),
]

NAMES: list[str] = [c.name for c in CATEGORIES]
BY_NAME: dict[str, Category] = {c.name: c for c in CATEGORIES}

EXPENSE_CATS: list[str] = [c.name for c in CATEGORIES if c.group == "expense"]
INCOME_CATS: list[str] = [c.name for c in CATEGORIES if c.group == "income"]
NEUTRAL_CATS: list[str] = [c.name for c in CATEGORIES if c.group == "neutral"]

# Constrained decoding: a debit can never be "Salary", a credit can never be "Groceries" (except via refunds).
ALLOWED_FOR_DEBIT: list[str] = EXPENSE_CATS + NEUTRAL_CATS
ALLOWED_FOR_CREDIT: list[str] = INCOME_CATS + NEUTRAL_CATS

UNCATEGORISED = "Needs review"

# What kinds of counterparty exist. `kind` answers "who did the money move to/from?"
KINDS = ("merchant", "person", "cash", "bank", "self")
KIND_LABEL = {
    "merchant": "Shop / business",
    "person": "Person (P2P)",
    "cash": "ATM / cash",
    "bank": "Bank (charges, interest)",
    "self": "Own account / wallet",
}

# Categories that are *not* "spending" in analytics.
NOT_SPEND = set(NEUTRAL_CATS)


def is_valid(name: str | None) -> bool:
    return name in BY_NAME


def group_of(name: str | None) -> str | None:
    c = BY_NAME.get(name or "")
    return c.group if c else None
