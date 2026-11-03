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
    "new_amount_rupees (the total they say they owe after a discount, or null), "
    "promise_date (YYYY-MM-DD they promise to pay by, or null). "
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
    if llm.enabled:
        try:
            out = await llm.json(SYSTEM.format(today=today.isoformat()), text)
            if out.get("type") not in TYPES:
                return None
            amt = out.get("new_amount_rupees")
            amount = round(float(amt) * 100) if amt is not None else None
            when = date.fromisoformat(out["promise_date"]) if out.get("promise_date") else None
            return Claim(
                out["type"],
                amount if amount is not None and 0 < amount < outstanding_paise else None,
                when if when and when >= today else None,
            )
        except Exception:
            log.exception("llm extraction failed; using heuristics")
    return _heuristic(text, outstanding_paise, today)
