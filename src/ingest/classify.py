"""Phase 1 heuristic claim classifier. Phase 2 replaces this with the LLM loop
(and fills proposed_amount_paise / proposed_date). Returns None when the
message makes no claim; it is then kept as evidence only."""
import re

_UTR = re.compile(r"\b(?:utr|ref(?:erence)?|txn|transaction)\b.*?\b[A-Za-z0-9]{12,22}\b|\b\d{12}\b", re.I)
_DISCOUNT = re.compile(r"\b(discount|kam|less|rebate|cut|deduct|short)\b", re.I)
_DATE = re.compile(r"\b(kal|parso|next (?:week|month)|agle|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|baad mein|later|\d{1,2}\s?(?:th|st|nd|rd)?\s?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*)\b", re.I)


def classify_claim(text: str) -> str | None:
    if _UTR.search(text):
        return "UTR_PROOF"
    if _DISCOUNT.search(text):
        return "DISCOUNT_CLAIM"
    if _DATE.search(text):
        return "PROMISE_DATE_CHANGE"
    return None
