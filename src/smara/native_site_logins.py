"""Site logins Smara may use on this PC: the owner saves them once in Smara Desktop (Settings > Browser > Site logins).

The password is kept in Smara Desktop's Windows-protected vault (DPAPI, readable only by this Windows user), next to the
model keys. It is never shown to the AI model, never sent to Smara's server and never written to a log: the browser tool
``browser_login`` reads it here and types it straight into the password field of the matching site. A login is only used
on its own site (the exact host, or a subdomain of it), so a look-alike page gets nothing."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

HOST = re.compile(r"^(?=.{3,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def alias(host: str) -> str:
    return "SITE_LOGIN_" + hashlib.sha256(host.encode()).hexdigest()[:24].upper()


def clean_host(value: str) -> str:
    raw = str(value or "").strip().lower()
    raw = re.sub(r"^[a-z]+://", "", raw).split("/")[0].split(":")[0]
    raw = raw.removeprefix("www.")
    if not HOST.fullmatch(raw):
        raise ValueError("Enter the site's address, such as gst.gov.in")
    return raw


def listing(saved: dict) -> list[dict]:
    """What Settings shows: hosts and usernames only."""
    return [{"host": item["host"], "username": item["username"], "updated_at": item.get("updated_at", "")}
            for item in saved.get("site_logins") or [] if isinstance(item, dict) and item.get("host")]


def save(saved: dict, host: str, username: str, password: str) -> dict:
    from .native_management import put_credential
    host = clean_host(host)
    username = str(username or "").strip()
    if not 1 <= len(username) <= 200:
        raise ValueError("Enter the username or email for this site")
    if not isinstance(password, str) or not 1 <= len(password) <= 500:
        raise ValueError("Enter the password for this site")
    put_credential(alias(host), json.dumps({"host": host, "password": password}), "site_login")
    rows = [item for item in saved.get("site_logins") or [] if isinstance(item, dict) and item.get("host") != host]
    rows.append({"host": host, "username": username, "updated_at": datetime.now(timezone.utc).isoformat()})
    saved["site_logins"] = rows[-100:]
    return saved


def delete(saved: dict, host: str) -> dict:
    from .native_management import vault_path
    from .native_profiles import read_object, write_object
    host = clean_host(host)
    records = read_object(vault_path())
    records.pop(alias(host), None)
    write_object(vault_path(), records)
    saved["site_logins"] = [item for item in saved.get("site_logins") or [] if isinstance(item, dict) and item.get("host") != host]
    return saved


def for_url(url: str) -> tuple[str, str, str] | None:
    """(host, username, password) saved for this page's site, or None. Exact host or a parent domain the owner saved."""
    from urllib.parse import urlsplit
    from .native_management import settings_path, vault_path
    from .native_profiles import read_object, unprotect
    page = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    if not page:
        return None
    rows = listing(read_object(settings_path()))
    match = max((r for r in rows if page == r["host"] or page.endswith("." + r["host"])), key=lambda r: len(r["host"]), default=None)
    if match is None:
        return None
    record = read_object(vault_path()).get(alias(match["host"]))
    if not isinstance(record, dict):
        return None
    secret = json.loads(unprotect(record["protected"]))
    if secret.get("host") != match["host"]:           # the vault entry must belong to this very site
        return None
    return match["host"], match["username"], secret["password"]
