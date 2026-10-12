"""OPTIONAL LLM helper for the question layer - OFF by default.

Enable with   HISAAB_LLM=anthropic   and an API key in the environment (ANTHROPIC_API_KEY, or `ant auth login`).
Model:  HISAAB_LLM_MODEL (default claude-opus-5-5; a small model such as claude-haiku-5-5 is plenty for this narrow task).

WHAT IT DOES   When the offline rules parser cannot understand a question (confidence < 0.5), ask the model to fill a SMALL slot
               schema - intent, category, merchant *mention*, time *phrase*, a few flags - via structured outputs (a JSON schema
               the API enforces).  The slots are then resolved by OUR code: merchant mention -> fuzzy match against your merchants,
               time phrase -> timeparse.py, category -> must be one of our category names.  The executor then does all the maths.
WHAT IT NEVER DOES   see a transaction, an amount, a merchant list or a date range; do arithmetic; write the answer text.
WHAT IS SENT   the question text (<= 300 chars), the list of intent names and category names, today's role instructions.  Nothing else.
FAILURE        any error (no key, offline, refusal, bad JSON) -> return None and the rules result stands; the UI shows why.
Note: the live API path has not been exercised end to end from this codebase; the request/response handling was checked against a stand-in client.
"""
from __future__ import annotations

import json
import os

from .. import taxonomy
from . import parser as P

DEFAULT_MODEL = "claude-opus-5-5"
_client = None            # injectable for tests
last_error: str | None = None

SYSTEM = (
    "You convert a user's question about their own UPI/bank spending into a JSON query description. "
    "You do NOT answer the question and you never invent numbers or dates. Fill the schema from the wording only:\n"
    "- intent: total_spend (how much), top_merchants (which shops/places most), top_categories (what do I spend on), compare (where did spending "
    "change between two periods), recurring (subscriptions / repeating payments), anomalies (unusual/duplicate/suspicious), list_transactions, "
    "largest (biggest payments), count (how many times), average, income (money received), net (savings), summary, needs_review, help, unknown.\n"
    "- direction: credit only if the user asks about money received, else debit.\n"
    "- category: one of the allowed names, or empty if none is mentioned.\n"
    "- merchant_mention: the shop/person name exactly as the user wrote it, or empty.\n"
    "- time_phrase: the time expression exactly as written (e.g. 'last month', 'in September', 'past 3 weeks'), or empty. Never convert it to dates.\n"
    "- compare_phrase: for compare questions, the second time expression if the user gave two, else empty.\n"
    "- kind: person if the question is about friends/people/P2P, merchant if about shops/companies, else empty.\n"
    "- local_only: true if the user asks about local/small/neighbourhood shops.\n"
    "- order_by: visits if the user asks about frequency ('visit most', 'how often'), else amount.\n"
    "- limit: how many items the user asked for (default 5).\n"
    "- weekend: weekend or weekday if the user restricts to those, else empty.\n"
    "- trend: decrease if the user asks where spending went DOWN, else increase.\n"
    "Questions may mix English and Hindi (Hinglish). If the question is not about the user's spending at all, use intent 'unknown'."
)


def _schema() -> dict:
    cats = taxonomy.NAMES + [""]
    return {
        "type": "object",
        "properties": {
            "intent": {"type": "string", "enum": P.INTENTS},
            "direction": {"type": "string", "enum": ["debit", "credit"]},
            "category": {"type": "string", "enum": cats},
            "merchant_mention": {"type": "string"},
            "time_phrase": {"type": "string"},
            "compare_phrase": {"type": "string"},
            "kind": {"type": "string", "enum": ["", "merchant", "person"]},
            "local_only": {"type": "boolean"},
            "order_by": {"type": "string", "enum": ["amount", "visits"]},
            "limit": {"type": "integer"},
            "weekend": {"type": "string", "enum": ["", "weekend", "weekday"]},
            "trend": {"type": "string", "enum": ["increase", "decrease"]},
        },
        "required": ["intent", "direction", "category", "merchant_mention", "time_phrase", "compare_phrase", "kind", "local_only",
                     "order_by", "limit", "weekend", "trend"],
        "additionalProperties": False,
    }


def model_name() -> str:
    return os.environ.get("HISAAB_LLM_MODEL", DEFAULT_MODEL)


def enabled() -> bool:
    if os.environ.get("HISAAB_LLM", "").lower() != "anthropic":
        return False
    if _client is not None:
        return True
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


def status() -> dict:
    want = os.environ.get("HISAAB_LLM", "").lower() == "anthropic"
    try:
        import anthropic  # noqa: F401
        have_sdk = True
    except ImportError:
        have_sdk = False
    return {"requested": want, "sdk_installed": have_sdk or _client is not None, "active": enabled(), "model": model_name(), "last_error": last_error,
            "sends": "only the question text and the list of intent/category names - never transactions, amounts or merchant names"}


def set_client(c) -> None:
    """Tests inject a fake client with a .messages.create(...) method."""
    global _client
    _client = c


def _get_client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(timeout=30.0, max_retries=1)
    return _client


def slots(question: str) -> dict | None:
    """Ask the model for the slot JSON. Returns a validated dict or None."""
    global last_error
    try:
        client = _get_client()
        resp = client.messages.create(
            model=model_name(),
            max_tokens=4096,
            system=SYSTEM,
            messages=[{"role": "user", "content": question}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": _schema()}},
        )
        if getattr(resp, "stop_reason", None) not in ("end_turn", None):
            last_error = f"model stopped with {resp.stop_reason}"
            return None
        text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), None)
        if not text:
            last_error = "empty response"
            return None
        data = json.loads(text)
        return _validate(data)
    except Exception as e:                      # offline, no key, refusal, rate limit ... : never break the app
        last_error = f"{type(e).__name__}: {str(e)[:160]}"
        return None


def _validate(d: dict) -> dict | None:
    """Never trust the model's JSON blindly: re-check every field against our own vocabularies."""
    if not isinstance(d, dict) or d.get("intent") not in P.INTENTS:
        return None
    out = {
        "intent": d["intent"],
        "direction": d.get("direction") if d.get("direction") in ("debit", "credit") else "debit",
        "category": d.get("category") if d.get("category") in taxonomy.NAMES else "",
        "merchant_mention": str(d.get("merchant_mention") or "")[:80],
        "time_phrase": str(d.get("time_phrase") or "")[:60],
        "compare_phrase": str(d.get("compare_phrase") or "")[:60],
        "kind": d.get("kind") if d.get("kind") in ("merchant", "person") else "",
        "local_only": bool(d.get("local_only")),
        "order_by": d.get("order_by") if d.get("order_by") in ("amount", "visits") else "amount",
        "limit": max(1, min(int(d.get("limit") or 5), 50)) if str(d.get("limit", "")).lstrip("-").isdigit() or isinstance(d.get("limit"), int) else 5,
        "weekend": d.get("weekend") if d.get("weekend") in ("weekend", "weekday") else "",
        "trend": d.get("trend") if d.get("trend") in ("increase", "decrease") else "increase",
    }
    return out


def refine(question: str, ctx: P.Context, rules_plan: P.QueryPlan) -> P.QueryPlan | None:
    s = slots(question)
    if not s or s["intent"] == "unknown":
        return None
    return P.plan_from_slots(s, ctx, question)
