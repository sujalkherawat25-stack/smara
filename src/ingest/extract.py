"""Debtor-claim extraction: LLM when configured, regex heuristics otherwise.
The result is only ever a *proposal*; nothing here touches obligations."""
from __future__ import annotations

import calendar
import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta

from src.ingest.classify import classify_claim
from src.loop.llm import LLM

log = logging.getLogger(__name__)
TYPES = ("DISCOUNT_CLAIM", "PROMISE_DATE_CHANGE", "UTR_PROOF")
_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}

SYSTEM = (
    "You read a debtor's WhatsApp reply (Hinglish/Hindi/English) about an unpaid invoice. "
    "Return JSON: type ('DISCOUNT_CLAIM'|'PROMISE_DATE_CHANGE'|'UTR_PROOF'|null), "
    "new_amount_rupees (the new total they would pay after a discount, computed from the "
    "outstanding amount of Rs {outstanding}; null if no discount), "
    "promise_date (YYYY-MM-DD they promise to pay by; null if no specific day; "
    "'today' and 'aaj' are not a date change). "
    "Use type 'PROMISE_DATE_CHANGE' for any promise to pay later, even without a date. "
    "Today is {today}. Use null if the message makes no such claim."
)


@dataclass(frozen=True)
class Claim:
    type: str
    amount_paise: int | None = None
    date: date | None = None


_WEEKDAYS = {n: i for i, n in enumerate(
    ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"))}


def parse_promise_date(text: str, today: date) -> date | None:
    t = text.lower()
    if re.search(r"\bparso\b|परसों", t):
        return today + timedelta(days=2)
    if re.search(r"\b(kal|tomorrow)\b|कल", t) and not re.search(r"\bkal (ka|ki|ke)\b", t):
        return today + timedelta(days=1)
    if re.search(r"\bnext week\b|\bagl[ea] (hafte|hafta)\b|अगले हफ्ते", t):
        return today + timedelta(days=7)
    if re.search(r"\bend tak\b|month end|mahine ke end", t):
        return today.replace(day=calendar.monthrange(today.year, today.month)[1])
    if m := re.search(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", t):
        return today + timedelta(days=(_WEEKDAYS[m.group(1)] - today.weekday()) % 7 or 7)
    if m := re.search(r"\b(\d{1,2})\s?(?:th|st|nd|rd)?\s?(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*", t):
        for year in (today.year, today.year + 1):
            try:
                d = date(year, _MONTHS[m.group(2)], int(m.group(1)))
            except ValueError:
                return None
            if d >= today:
                return d
    if m := re.search(r"(\d{1,2})\s?(?:tarikh|tareekh|तारीख|तारिख)", t):
        day = int(m.group(1))
        for offset in (0, 1):
            month = (today.month - 1 + offset) % 12 + 1
            year = today.year + (today.month - 1 + offset) // 12
            if 1 <= day <= calendar.monthrange(year, month)[1] and date(year, month, day) >= today:
                return date(year, month, day)
    return None


_PCT = re.compile(r"(\d{1,2}(?:\.\d+)?)\s?(?:%|percent|pct)", re.I)
_NUM = r"(\d[\d,]*)"
_LESS = re.compile(_NUM + r"\s*(?:rupay|rupaye|rs)?\s*(?:kam|less|off|कम)", re.I)
_THOUSANDS = re.compile(r"(\d{1,3})\s*(?:hazaar|hazar|हजार)\s*(?:kam|less|off|कम)", re.I)
_NEW_TOTAL = re.compile(_NUM + r"\s*(?:mein final|me final|में फाइनल|de dunga|dunga|lelo|le lo|bhejta|bhejunga)", re.I)


def _discount_total(text: str, outstanding: int) -> int | None:
    """New total (paise) the debtor proposes to pay, or None if no number is stated."""
    if (m := _PCT.search(text)) and 0 < float(m.group(1)) < 100:
        return round(outstanding * (1 - float(m.group(1)) / 100))
    if m := _THOUSANDS.search(text):
        return outstanding - int(m.group(1)) * 1000 * 100
    if m := _LESS.search(text):
        return outstanding - int(m.group(1).replace(",", "")) * 100
    if m := _NEW_TOTAL.search(text):
        return int(m.group(1).replace(",", "")) * 100
    return None


def _heuristic(text: str, outstanding: int, today: date) -> Claim | None:
    kind = classify_claim(text)
    if kind is None:
        return None
    amount = _discount_total(text, outstanding) if kind == "DISCOUNT_CLAIM" else None
    return Claim(kind, amount if amount and 0 < amount < outstanding else None,
                 parse_promise_date(text, today) if kind != "UTR_PROOF" else None)


async def extract_claim(llm: LLM, text: str, *, outstanding_paise: int, today: date) -> Claim | None:
    """The model reads intent; deterministic rules supply numbers and dates.

    A wrong amount or date on an approval card is worse than a missing one, so
    when the rules find a figure they win, and the model's figure is used only
    if the rules found none (and it passes the same sanity checks).
    """
    rules = _heuristic(text, outstanding_paise, today)
    if not llm.enabled:
        return rules
    try:
        out = await llm.json(SYSTEM.format(today=today.isoformat(), outstanding=outstanding_paise // 100), text)
    except Exception:
        log.exception("llm extraction failed; using rules")
        return rules
    kind = out.get("type") if out.get("type") in TYPES else (rules.type if rules else None)
    if kind is None:
        return None
    return Claim(kind, _pick_amount(kind, rules, out, outstanding_paise), _pick_date(kind, rules, out, today))


def _pick_amount(kind: str, rules: Claim | None, out: dict, outstanding: int) -> int | None:
    if kind != "DISCOUNT_CLAIM":
        return None
    if rules and rules.amount_paise:
        return rules.amount_paise
    try:
        amount = round(float(out["new_amount_rupees"]) * 100)
    except (KeyError, TypeError, ValueError):
        return None
    return amount if 0 < amount < outstanding else None


def _pick_date(kind: str, rules: Claim | None, out: dict, today: date) -> date | None:
    if kind == "UTR_PROOF":
        return None
    if rules and rules.date:
        return rules.date
    try:
        when = date.fromisoformat(out["promise_date"])
    except (KeyError, TypeError, ValueError):
        return None
    return when if when > today else None   # "today" is not a date change
