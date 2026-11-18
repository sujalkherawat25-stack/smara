"""Optional: share Smara Desktop's conversation notes with the shop's hosted Smara memory.

OFF until the owner turns it on, and only for the shop this PC is connected to (Approvals link). Turning it off stops
sharing at once on this PC and on the server; "Forget what I shared" erases the notes Desktop sent. Only scrubbed text
leaves the PC: passwords, keys, tokens, card numbers and OTP/PIN values are removed first, and the vault is never read.
Sharing is best effort and never blocks or fails a chat turn.
"""
from __future__ import annotations

import re
import threading

from . import hosted_approvals as approvals
from .hosted_approvals import store_path
from .native_memory import redact
from .native_profiles import read_object, state_path, write_object

MAX_NOTE = 4000
_OTP = re.compile(r"(?i)\b(otp|one[- ]time (?:password|code)|upi pin|pin|cvv)\b[^\d\r\n]{0,20}\d{3,8}\b")
_DIGITS = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")


def _luhn(number: str) -> bool:
    digits = [int(c) for c in number if c.isdigit()][::-1]
    if len(digits) < 13:
        return False
    total = 0
    for i, d in enumerate(digits):
        if i % 2:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return total % 10 == 0


def scrub(text: str) -> str:
    text = redact(text)
    text = _OTP.sub("[code removed]", text)
    return _DIGITS.sub(lambda m: "[card number removed]" if _luhn(m.group(0)) else m.group(0), text)


def _path():
    return state_path().parent / "hosted-memory.json"


def enabled() -> bool:
    """On only if the owner turned it on for the shop this PC is connected to right now."""
    saved = read_object(_path())
    link = approvals.status()
    return bool(saved.get("enabled") and link["connected"] and saved.get("shop") == link["shop"] and saved.get("server") == link["server"])


def status() -> dict:
    link = approvals.status()
    return {"connected": link["connected"], "shop": link["shop"], "enabled": enabled()}


def _call(method: str, path: str, **body):
    server, headers = approvals._authorised()
    with approvals._client(server) as http:
        reply = http.request(method, path, headers=headers, **({"json": body} if body else {}))
    if reply.status_code == 401:
        approvals.disconnect()
        raise ValueError("Smara switched approvals off for this PC. Connect again with a new code.")
    return reply


def set_enabled(value: bool) -> dict:
    if type(value) is not bool:
        raise ValueError("Choose on or off")
    link = approvals.status()
    if not link["connected"]:
        raise ValueError("Connect this PC to your Smara first (Approvals), then you can choose to share memory")
    reply = _call("POST", "/bridge/memory/enable", enabled=value)
    reply.raise_for_status()
    write_object(_path(), {"enabled": value, "shop": link["shop"], "server": link["server"]})
    return status()


def forget() -> dict:
    _call("POST", "/bridge/memory/forget").raise_for_status()
    return {"forgotten": True, **status()}


def search(query: str) -> dict:
    if not enabled():
        raise ValueError("Memory sharing is off")
    if not isinstance(query, str) or len(query.strip()) < 3:
        raise ValueError("Type a few words to look for")
    reply = _call("POST", "/bridge/memory/search", query=query.strip()[:500])
    if reply.status_code == 403:
        write_object(_path(), {"enabled": False})
        raise ValueError("Memory sharing is off on Smara's side")
    reply.raise_for_status()
    return {"state_card": scrub(str(reply.json().get("state_card") or ""))}


def share_turn(user: str, assistant: str) -> bool:
    """Send one finished conversation turn, scrubbed. Never raises; False when nothing was sent."""
    try:
        if not enabled():
            return False
        text = scrub(f"Owner asked on Smara Desktop: {user}\nSmara Desktop answered: {assistant}")[:MAX_NOTE]
        reply = _call("POST", "/bridge/memory/notes", text=text)
        if reply.status_code == 403:                      # turned off on the server: stop here too
            write_object(_path(), {"enabled": False})
            return False
        return reply.status_code == 200
    except Exception:
        return False


def share_turn_async(user: str, assistant: str) -> None:
    threading.Thread(target=share_turn, args=(user, assistant), name="smara-share-memory", daemon=True).start()


def manage(request: dict) -> dict:
    action = request.get("action", "status")
    if set(request) - {"operation", "action", "enabled", "query"}:
        raise ValueError("Unexpected memory-sharing request fields")
    if action == "status":
        return status()
    if action == "set":
        return set_enabled(request.get("enabled"))
    if action == "search":
        return search(str(request.get("query") or ""))
    if action == "forget":
        return forget()
    raise ValueError("Unsupported memory-sharing action")


__all__ = ["manage", "status", "enabled", "share_turn", "share_turn_async", "scrub", "store_path"]
