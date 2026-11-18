"""Optional memory sharing between Smara Desktop and hosted Continuum.

Off until the owner turns it on (per shop); turning it off stops both directions at once, and 'forget' erases what Desktop
shared. Desktop notes go to their own scope ({tenant}_desktop) and are always labelled UNTRUSTED: they come from model chats
on a PC, so they inform hosted Smara but never count as ledger facts. Secrets, card numbers and OTP/PIN values are
removed before anything is stored.
"""
import hashlib
import re

from src.bridge.continuum import scope_user
from src.bridge.outbox import enqueue_standalone

MAX_NOTE = 4000
_SECRET = re.compile(r"\b(?:sk_mem_|sk_|sma_|tvly-|ghp_|github_pat_|xox[a-z]-)[A-Za-z0-9_-]{8,}|(?i:Bearer)\s+[A-Za-z0-9._~-]{12,}")
_ASSIGN = re.compile(r"(?im)\b([A-Za-z0-9_-]{0,64}(?:api[_-]?key|token|secret|password|passwd|pin|otp|cvv|vault)\s*[=:]\s*)[^\r\n,;]+")
_PEM = re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.S)
_DIGITS = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_OTP = re.compile(r"(?i)\b(otp|one[- ]time (?:password|code)|upi pin|pin)\b[^\d\r\n]{0,20}\d{4,8}\b")


def desktop_scope(tenant_id: str) -> str:
    return scope_user(tenant_id, "desktop")


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
    """Remove what must never be stored: keys, tokens, private keys, OTP/PIN values, card numbers."""
    text = _PEM.sub("[private key removed]", text)
    text = _SECRET.sub("[credential removed]", text)
    text = _ASSIGN.sub(r"\1[credential removed]", text)
    text = _OTP.sub("[code removed]", text)
    return _DIGITS.sub(lambda m: "[card number removed]" if _luhn(m.group(0)) else m.group(0), text)


async def is_enabled(pool, tenant_id: str) -> bool:
    async with pool.connection() as conn:
        row = await (await conn.execute("SELECT enabled FROM owner_memory_sharing WHERE tenant_id = %s", (tenant_id,))).fetchone()
    return bool(row and row["enabled"])


async def set_enabled(pool, tenant_id: str, enabled: bool) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            """INSERT INTO owner_memory_sharing (tenant_id, enabled, updated_at) VALUES (%s, %s, now())
               ON CONFLICT (tenant_id) DO UPDATE SET enabled = EXCLUDED.enabled, updated_at = now()""", (tenant_id, enabled))


async def add_note(app, tenant_id: str, text: str, source: str = "desktop") -> dict:
    clean = scrub(text.strip())[:MAX_NOTE]
    if not clean:
        return {"stored": False}
    sid = "desk_" + hashlib.sha256(f"{tenant_id}|{clean}".encode()).hexdigest()[:40]      # the same note twice is stored once
    await enqueue_standalone(app.state.pool, user_id=desktop_scope(tenant_id), source_id=sid, text=clean,
                             trusted=False, metadata={"actor": "desktop", "origin": source[:40]})
    return {"stored": True, "source_id": sid}


async def search(app, tenant_id: str, query: str) -> dict:
    """What hosted Smara knows about the shop itself (never about customers: those stay on the server side)."""
    res = await app.state.continuum.resolve(user_id=scope_user(tenant_id, "shop"), query=query[:500], max_claims=5)
    r = res.get("resolution", {})
    return {"state_card": scrub(r.get("state_card") or "")[:2500]}


async def forget(app, tenant_id: str) -> None:
    """Erase everything Desktop shared: queued notes first, then the Continuum scope."""
    async with app.state.pool.connection() as conn:
        await conn.execute("DELETE FROM continuum_outbox WHERE user_id = %s", (desktop_scope(tenant_id),))
    await app.state.continuum.delete_user(desktop_scope(tenant_id))
