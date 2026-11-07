"""check_gstin_status: offline format + checksum validation.

The GST portal's taxpayer search sits behind a captcha, which we do not
automate, so a live "Active/Cancelled" status is a manual step: the result
carries the portal link for the owner.
"""
import re

PORTAL_URL = "https://services.gst.gov.in/services/searchtp"
_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_SHAPE = re.compile(r"^(\d{2})([A-Z]{5}\d{4}[A-Z])([1-9A-Z])Z([0-9A-Z])$")


def checksum_char(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14):
        v = _ALPHABET.index(ch) * (1 if i % 2 == 0 else 2)
        total += v // 36 + v % 36
    return _ALPHABET[(36 - total % 36) % 36]


def check_gstin_status(gstin: str) -> dict:
    g = re.sub(r"\s", "", gstin or "").upper()
    m = _SHAPE.match(g)
    base = {"gstin": g, "portal_url": PORTAL_URL}
    if not m:
        return {**base, "valid": False, "reason": "bad format (expected 15 characters)"}
    state = int(m.group(1))
    if not (1 <= state <= 38 or state in (97, 99)):
        return {**base, "valid": False, "reason": f"unknown state code {m.group(1)}"}
    if checksum_char(g[:14]) != g[14]:
        return {**base, "valid": False, "reason": "checksum mismatch (likely a typo)"}
    return {**base, "valid": True, "state_code": m.group(1), "pan": m.group(2),
            "status": "format_valid_registration_unverified"}
