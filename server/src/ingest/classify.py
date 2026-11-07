"""Offline heuristic claim classifier (Roman Hinglish, English, Devanagari).

Fallback for when no LLM is configured or it fails; the LLM path in extract.py
is primary. Returns None when the message makes no claim, so it is kept as
evidence only. Order matters: explicit payment reference, then discount, then
"already paid", then promise-to-pay.
"""
import re

_I = re.IGNORECASE
_REF = re.compile(r"\b(?:utr|ref(?:erence)?(?: no| number)?|txn(?: id)?|transaction(?: id)?)\b.*?\b[A-Za-z0-9]{12,22}\b|\b\d{12}\b", _I)
_DISCOUNT = re.compile(r"\b(discount|kam|less|rebate|deduct|off|final|maaf|round off|lelo|le lo|kaat)\b|कम|डिस्काउंट|छूट|माफ", _I)
_PAID = re.compile(
    r"kar diya|kar di\b|bhej diya|bhej diye|bhej di\b|\bbheja\b|ho gaya|ho chuka|kar chuka|transfer kiya|"
    r"(?<!as )\bpaid\b|paisa bhej|कर दिया|भेज दिया|भेज दिए|हो गया|हो चुका", _I)
_PROMISE_VERB = re.compile(
    r"\b(dunga|dungi|karunga|karungi|bhejunga|deta h(?:u|oon)|dete hain|jayega|jaega|will pay|i'?ll pay|pay by)\b"
    r"|दूंगा|दूँगा|करूंगा|करूँगा|जाएगा|हो जाएगा", _I)
_DATE_EXPR = re.compile(
    r"\d{1,2}\s*(?:tarikh|tareekh|तारीख|तारिख)|\bparso\b|परसों|\bagl[ea] (?:hafte|hafta)\b|अगले हफ्ते|next (?:week|month)|"
    r"\b(?:kal|tomorrow)\s+(?:tak|subah|shaam|raat)\b|कल तक|\btomorrow\b|end tak|month end|mahine ke end|"
    r"\b(?:mon|tues|wednes|thurs|fri|satur|sun)day\s+(?:tak|ko)\b|"
    r"\d{1,2}\s?(?:th|st|nd|rd)?\s?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*", _I)
_DELAY = re.compile(r"baad mein|\bjaldi\b|time do|\bruko\b|ruk jao|salary|diwali|kuch din|टाइम दीजिए|जल्दी", _I)


def classify_claim(text: str) -> str | None:
    if _REF.search(text):
        return "UTR_PROOF"
    if _DISCOUNT.search(text):
        return "DISCOUNT_CLAIM"
    if _PAID.search(text):
        return "UTR_PROOF"
    if _PROMISE_VERB.search(text) or _DATE_EXPR.search(text) or _DELAY.search(text):
        return "PROMISE_DATE_CHANGE"
    return None
