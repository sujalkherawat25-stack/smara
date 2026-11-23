"""Sign this PC in to hosted Smara with one tap from the owner's own chat.

Desktop asks the server for a sign-in and shows a short code. The owner taps a Telegram or WhatsApp link (or sends
``login ABCD-EFGH``) from the chat that is linked to their shop and answers YES. Desktop then receives two tokens once:
the browser helper's device token (written to ``~/.smara-bridge/config.json``, the helper's one config) and the owner
approvals token (DPAPI-protected, ``hosted-approvals.json``). No password is ever typed or stored.
"""
from __future__ import annotations

import json
import os
import platform
import re
import time
from pathlib import Path

from .hosted_approvals import bridge_server, status as approvals_status, store_path as approvals_path
from .native_profiles import protect, read_object, state_path, unprotect, write_object

DEFAULT_SERVER = "https://ai.syntarus.com/munim"
TIMEOUT_S = 15
_CODE = re.compile(r"^[A-Za-z0-9]{4}-[A-Za-z0-9]{4}$")
_HOSTS = {"t.me", "wa.me"}


def _pending_path() -> Path:
    return state_path().parent / "hosted-login.json"


def _home() -> Path:
    return Path(os.environ.get("SMARA_BRIDGE_HOME", Path.home() / ".smara-bridge"))


def _server(value: str = "") -> str:
    server = (value or bridge_server() or DEFAULT_SERVER).rstrip("/")
    if not (server.startswith("https://") or server.startswith("http://127.0.0.1") or server.startswith("http://localhost")):
        raise ValueError("The Smara server address must start with https://")
    return server


def _client(server: str):
    import httpx
    return httpx.Client(base_url=server, timeout=TIMEOUT_S, follow_redirects=False)


def _safe_link(url: object) -> str:
    """Only plain https links to Telegram or WhatsApp are offered to the owner."""
    from urllib.parse import urlparse
    parsed = urlparse(str(url or ""))
    return str(url) if parsed.scheme == "https" and parsed.hostname in _HOSTS and not parsed.username else ""


def _refused_by_server(token: str) -> bool:
    """The browser helper writes signed_out into status.json when the server refused its token (signed out in chat, or
    the account was reset). Only that exact token counts: a new sign-in brings a new token."""
    if not token:
        return False
    try:
        cur = json.loads((_home() / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    import hashlib
    return bool(cur.get("signed_out")) and cur.get("signed_out_for") == hashlib.sha256(token.encode()).hexdigest()[:16]


def status() -> dict:
    """What the Sign-in card shows: signed in, waiting for the owner's tap, or signed out."""
    saved = approvals_status()
    helper = {}
    try:
        helper = json.loads((_home() / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    pending = read_object(_pending_path())
    waiting = bool(pending.get("secret")) and float(pending.get("expires_at") or 0) > time.time()
    refused = _refused_by_server(helper.get("token") or "")
    signed_in = bool(saved.get("connected")) and bool(helper.get("token")) and not refused
    partial = (bool(saved.get("connected")) or bool(helper.get("token"))) and not signed_in and not refused   # paired the old way, one half only
    return {"signed_in": signed_in, "partial": partial, "signed_out": refused and not waiting, "shop": saved.get("shop") or helper.get("shop") or "",
            "server": saved.get("server") or helper.get("server") or "",
            "waiting": waiting and not signed_in,
            "code": pending.get("code", "") if waiting else "", "message": pending.get("message", "") if waiting else "",
            "links": pending.get("links", {}) if waiting else {}, "expires_at": pending.get("expires_at", 0) if waiting else 0}


def start(server: str = "") -> dict:
    server = _server(server)
    name = f"{os.environ.get('COMPUTERNAME') or platform.node() or 'PC'} (Smara Desktop)"[:60]
    with _client(server) as http:
        reply = http.post("/bridge/login/start", json={"name": name})
    if reply.status_code == 429:
        raise ValueError("Too many sign-in attempts. Wait a few minutes and try again.")
    if reply.status_code >= 400:
        raise ValueError("Smara could not start a sign-in right now. Try again in a minute.")
    data = reply.json()
    code = str(data.get("code") or "")
    if not _CODE.match(code):
        raise ValueError("Smara sent an unexpected reply. Update Smara Desktop and try again.")
    links = {k: _safe_link(v) for k, v in (data.get("links") or {}).items() if k in ("telegram", "whatsapp")}
    write_object(_pending_path(), {"server": server, "secret": protect(str(data["device_secret"])), "code": code,
                                   "message": f"login {code}", "links": {k: v for k, v in links.items() if v},
                                   "expires_at": time.time() + int(data.get("expires_in") or 600)})
    return status()


def cancel() -> dict:
    _pending_path().unlink(missing_ok=True)
    return status()


def poll() -> dict:
    """One check. When the owner has said YES this stores both tokens and returns ``approved``."""
    pending = read_object(_pending_path())
    if not pending.get("secret"):
        return {**status(), "state": "none"}
    server = _server(str(pending.get("server") or ""))
    with _client(server) as http:
        reply = http.post("/bridge/login/poll", json={"device_secret": unprotect(pending["secret"])})
    if reply.status_code >= 400:
        raise ValueError("Smara did not answer. Check your internet connection.")
    data = reply.json()
    state = str(data.get("status") or "expired")
    if state == "approved":
        home = _home()
        home.mkdir(parents=True, exist_ok=True)
        config = home / "config.json"
        config.write_text(json.dumps({"server": server, "token": data["device_token"], "shop": data.get("shop", "")}), encoding="utf-8")
        try:
            config.chmod(0o600)
        except OSError:
            pass
        write_object(approvals_path(), {"server": server, "shop": data.get("shop", ""), "token": protect(data["owner_token"])})
        _pending_path().unlink(missing_ok=True)
    elif state in ("denied", "expired"):
        _pending_path().unlink(missing_ok=True)
    return {**status(), "state": state}


def sign_out() -> dict:
    """Forget both tokens on this PC and ask the server to revoke them. The helper is stopped by Desktop."""
    saved = read_object(approvals_path())
    server = str(saved.get("server") or bridge_server() or "")
    if saved.get("token") and server:
        try:
            with _client(_server(server)) as http:
                http.post("/bridge/logout", headers={"Authorization": "Bearer " + unprotect(saved["token"])})
        except Exception:
            pass        # the owner can always send 'unpair browser' in chat to revoke it on the server
    approvals_path().unlink(missing_ok=True)
    (_home() / "config.json").unlink(missing_ok=True)
    _pending_path().unlink(missing_ok=True)
    return status()


def manage(request: dict) -> dict:
    if set(request) - {"operation", "action", "server"}:
        raise ValueError("Unsupported sign-in request")
    action = request.get("action", "status")
    if action == "status":
        return status()
    if action == "start":
        return start(str(request.get("server") or ""))
    if action == "poll":
        return poll()
    if action == "cancel":
        return cancel()
    if action == "sign_out":
        return sign_out()
    raise ValueError("Unsupported sign-in action")
