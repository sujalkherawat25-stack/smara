"""Approve hosted Smara's PC steps from Smara Desktop (the same card the owner gets in Telegram/WhatsApp).

The owner sends 'desktop approvals' to Smara, pastes the one-time code here, and Desktop receives an owner approvals
token. It is kept encrypted with Windows DPAPI for this Windows account (``hosted-approvals.json`` holds only the
protected value, the server address and the shop name) and is separate from the browser helper's device token, which
the server never accepts for approvals. Only PC steps come here (browser, Smara Shared folder, hand-offs); messages to
customers and book changes are approved in chat only. First answer wins.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re

from .native_profiles import protect, read_object, state_path, unprotect, write_object

CHOICES = ("approve", "site", "reject")
_SID = re.compile(r"^[0-9a-fA-F-]{8,64}$")
TIMEOUT_S = 15


def store_path() -> Path:
    return state_path().parent / "hosted-approvals.json"


def bridge_server() -> str:
    """The hosted Smara address the PC helper was paired with (``~/.smara-bridge/config.json``), if any."""
    home = Path(os.environ.get("SMARA_BRIDGE_HOME", Path.home() / ".smara-bridge"))
    try:
        value = json.loads((home / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    server = str(value.get("server") or "") if isinstance(value, dict) else ""
    return server.rstrip("/") if server.startswith("https://") or server.startswith("http://127.0.0.1") else ""


def _saved() -> dict:
    return read_object(store_path())


def _client(server: str):
    import httpx
    return httpx.Client(base_url=server, timeout=TIMEOUT_S, follow_redirects=False)


def status() -> dict:
    saved = _saved()
    return {"connected": bool(saved.get("token")), "shop": saved.get("shop", ""), "server": saved.get("server") or bridge_server()}


def pair(code: str, name: str = "Smara Desktop", server: str = "") -> dict:
    server = (server or bridge_server()).rstrip("/")
    if not server:
        raise ValueError("Pair this PC's browser with Smara first (Settings > Browser), then connect approvals")
    if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9]{4}-?[A-Za-z0-9]{4}", code.strip()):
        raise ValueError("Paste the code Smara sent (like ABCD-EFGH)")
    with _client(server) as http:
        reply = http.post("/bridge/approvals/pair", json={"code": code.strip().upper(), "name": name[:60]})
    if reply.status_code == 400:
        raise ValueError("That code is wrong, used, or expired. Send 'desktop approvals' to Smara for a new one.")
    reply.raise_for_status()
    data = reply.json()
    write_object(store_path(), {"server": server, "shop": data.get("shop", ""), "token": protect(data["owner_token"])})
    return status()


def disconnect() -> dict:
    """Forget the token on this PC. (Sending 'stop desktop approvals' to Smara revokes it on the server too.)"""
    store_path().unlink(missing_ok=True)
    return status()


def _authorised():
    saved = _saved()
    if not saved.get("token"):
        raise ValueError("Approvals from Telegram are not connected on this PC")
    return saved["server"], {"Authorization": "Bearer " + unprotect(saved["token"])}


def pending() -> dict:
    server, headers = _authorised()
    with _client(server) as http:
        reply = http.get("/bridge/approvals", headers=headers)
    if reply.status_code == 401:
        disconnect()
        raise ValueError("Smara switched approvals off for this PC (or it was unpaired). Connect again with a new code.")
    reply.raise_for_status()
    rows = reply.json().get("approvals") or []
    return {"approvals": [{k: row.get(k) for k in ("id", "tool", "card", "step", "site_choice", "asked_at")} for row in rows[:50]], **status()}


def decide(identity: str, choice: str) -> dict:
    if choice not in CHOICES or not isinstance(identity, str) or not _SID.match(identity):
        raise ValueError("Invalid approval")
    server, headers = _authorised()
    with _client(server) as http:
        reply = http.post(f"/bridge/approvals/{identity}", json={"choice": choice}, headers=headers)
    if reply.status_code == 409:
        return {"outcome": "already"}
    if reply.status_code == 403:
        return {"outcome": "chat_only"}
    if reply.status_code == 429:
        raise ValueError("Too many answers in a short time; wait a few minutes")
    if reply.status_code == 401:
        disconnect()
        raise ValueError("Smara switched approvals off for this PC. Connect again with a new code.")
    reply.raise_for_status()
    return {"outcome": "done"}


def manage(request: dict) -> dict:
    action = request.get("action", "status")
    if set(request) - {"operation", "action", "code", "id", "choice"}:
        raise ValueError("Unexpected approvals request fields")
    if action == "status":
        return status()
    if action == "pair":
        return pair(str(request.get("code") or ""))
    if action == "list":
        return pending()
    if action == "decide":
        return decide(str(request.get("id") or ""), str(request.get("choice") or ""))
    if action == "disconnect":
        return disconnect()
    raise ValueError("Unsupported approvals action")
