"""Sign in Smara Desktop with one tap from the owner's own chat.

1. The PC asks for a login (``start``) and shows a short code. Nothing is trusted yet.
2. The owner opens a link (or sends ``login ABCD-EFGH``) from the WhatsApp or Telegram that is linked to their shop.
   That chat is what proves who they are, so the code lands on *their* shop (``ask``).
3. Smara asks "Connect <PC name>? Reply YES" and the owner compares the code with the one on their PC screen, so a code
   somebody else made up cannot be slipped in. YES approves it (``answer``).
4. The PC polls with its secret and receives both tokens once (``poll``): the browser helper's device token and the
   approvals token. Only hashes are stored, and the tokens are never kept here.
"""
import hashlib
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

from src.bridge.device import ALPHABET

TTL = timedelta(minutes=10)
POLL_S = 3
MAX_OPEN = 300                      # unanswered logins at any moment: a wall against a script, plenty for real owners
_starts: dict[str, list[float]] = {}
CODE_RE = re.compile(r"^(?:login|log in|sign in|signin|connect pc)[ _:]*([a-z0-9]{4})-?([a-z0-9]{4})$")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def parse_code(text: str) -> str | None:
    """'login ABCD-EFGH', 'login_abcdefgh' (Telegram link) -> 'ABCD-EFGH'; anything else None."""
    m = CODE_RE.match(" ".join(text.strip().lower().split()))
    return f"{m.group(1)}-{m.group(2)}".upper() if m else None


def start_ok(ip: str) -> bool:
    now = time.monotonic()
    recent = [t for t in _starts.get(ip, []) if now - t < 600]
    _starts[ip] = recent
    if len(recent) >= 20:
        return False
    recent.append(now)
    return True


async def start(pool, name: str) -> dict | None:
    code, secret = _code(), "sdl_" + secrets.token_urlsafe(32)
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM desktop_logins WHERE expires_at < now() - interval '1 hour'")
        open_now = await (await conn.execute("SELECT count(*) AS n FROM desktop_logins WHERE expires_at > now() AND status IN ('pending','asked')")).fetchone()
        if open_now["n"] >= MAX_OPEN:
            return None
        await conn.execute("INSERT INTO desktop_logins (code_hash, secret_hash, name, expires_at) VALUES (%s, %s, %s, %s)",
                           (_hash(code), _hash(secret), (name or "PC").strip()[:60] or "PC", datetime.now(timezone.utc) + TTL))
    return {"code": code, "secret": secret, "expires_in": int(TTL.total_seconds())}


async def ask(pool, tenant_id: str, code: str) -> str:
    """The owner sent the code from their linked chat. Returns the reply to send."""
    async with pool.connection() as conn:
        await conn.execute("UPDATE desktop_logins SET status = 'denied' WHERE tenant_id = %s AND status = 'asked'", (tenant_id,))
        row = await (await conn.execute(
            """UPDATE desktop_logins SET status = 'asked', tenant_id = %s
                WHERE code_hash = %s AND status = 'pending' AND expires_at > now() RETURNING name""",
            (tenant_id, _hash(code.upper())))).fetchone()
        shop = await (await conn.execute("SELECT name FROM tenants WHERE id = %s", (tenant_id,))).fetchone()
    if row is None:
        return "That sign-in code is wrong, used or expired. On your PC press Sign in again to get a new one."
    return (f"Connect your PC \"{row['name']}\" to {shop['name'] if shop else 'your shop'}?\n"
            f"Only say yes if you just pressed Sign in on that PC and it shows the code {code.upper()}.\n\n"
            "Reply YES to connect, NO to cancel.")


async def has_asked(pool, tenant_id: str) -> bool:
    async with pool.connection() as conn:
        return await (await conn.execute(
            "SELECT 1 FROM desktop_logins WHERE tenant_id = %s AND status = 'asked' AND expires_at > now()", (tenant_id,))).fetchone() is not None


async def answer(pool, tenant_id: str, yes: bool) -> str | None:
    async with pool.connection() as conn:
        row = await (await conn.execute(
            """UPDATE desktop_logins SET status = %s
                WHERE id = (SELECT id FROM desktop_logins WHERE tenant_id = %s AND status = 'asked' AND expires_at > now()
                             ORDER BY created_at DESC LIMIT 1) RETURNING name""",
            ("approved" if yes else "denied", tenant_id))).fetchone()
    if row is None:
        return None
    if yes:
        return (f"Connected. Smara Desktop on \"{row['name']}\" is signed in: your PC browser and approvals work now. "
                "Send 'unpair browser' any time to disconnect it.")
    return "Cancelled. Nothing was connected."


async def logout(pool, owner: dict) -> None:
    """Desktop's Sign out: this PC's approvals token and its browser helper token stop working."""
    async with pool.connection() as conn:
        await conn.execute("UPDATE owner_desktop_tokens SET revoked_at = now() WHERE id = %s AND revoked_at IS NULL", (owner["id"],))
        await conn.execute("UPDATE bridge_devices SET revoked_at = now() WHERE tenant_id = %s AND name = %s AND revoked_at IS NULL",
                           (owner["tenant_id"], owner["name"]))


async def poll(pool, secret: str) -> dict:
    """What the PC asked for: pending / asked (waiting for YES) / denied / expired, or both tokens exactly once."""
    if not secret or not secret.startswith("sdl_"):
        return {"status": "expired"}
    async with pool.connection() as conn:
        row = await (await conn.execute(
            "SELECT id, status, name, tenant_id, expires_at > now() AS live FROM desktop_logins WHERE secret_hash = %s", (_hash(secret),))).fetchone()
        if row is None or (not row["live"] and row["status"] != "approved"):
            return {"status": "expired"}
        if row["status"] in ("pending", "asked", "denied"):
            return {"status": row["status"]}
        if row["status"] != "approved":
            return {"status": "expired"}
        won = await (await conn.execute(
            "UPDATE desktop_logins SET status = 'done' WHERE id = %s AND status = 'approved' RETURNING tenant_id", (row["id"],))).fetchone()
        if won is None:
            return {"status": "expired"}
        tenant_id, name = won["tenant_id"], row["name"]
        device, owner = secrets.token_urlsafe(32), "sma_" + secrets.token_urlsafe(32)
        # Signing in again from the same PC replaces its old tokens instead of piling up.
        await conn.execute("UPDATE bridge_devices SET revoked_at = now() WHERE tenant_id = %s AND name = %s AND revoked_at IS NULL", (tenant_id, name))
        await conn.execute("UPDATE owner_desktop_tokens SET revoked_at = now() WHERE tenant_id = %s AND name = %s AND revoked_at IS NULL", (tenant_id, name))
        await conn.execute("INSERT INTO bridge_devices (tenant_id, name, token_hash, last_seen_at) VALUES (%s, %s, %s, now())", (tenant_id, name, _hash(device)))
        await conn.execute("INSERT INTO owner_desktop_tokens (tenant_id, name, token_hash, last_seen_at) VALUES (%s, %s, %s, now())", (tenant_id, name, _hash(owner)))
        shop = await (await conn.execute("SELECT name FROM tenants WHERE id = %s", (tenant_id,))).fetchone()
    return {"status": "approved", "device_token": device, "owner_token": owner, "shop": shop["name"] if shop else ""}
