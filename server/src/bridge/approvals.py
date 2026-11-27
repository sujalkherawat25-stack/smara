"""Owner approvals from Smara Desktop: the same Approve / Approve + this site / Reject as the chat card.

Only for steps on the owner's PC (browser steps, the Smara Shared folder, hand-offs to the owner). Messages to customers
and ledger changes are answered in chat only. Authorised by an owner token that the owner mints from their own chat with
'desktop approvals'. The browser helper's device token cannot approve (a compromised helper must not approve its own
steps). First answer wins: the chat card and Desktop share one resume path, a second answer gets "already handled".
"""
import hashlib
import secrets
import time
from datetime import datetime, timedelta, timezone

from src.bridge.device import ALPHABET

PAIR_TTL = timedelta(hours=1)
DESKTOP_TOOLS = ("browser_step", "pc_files", "hand_off_to_owner")
CHOICES = ("approve", "site", "reject")
RATE_WINDOW_S, RATE_MAX = 600, 120        # decisions per token per 10 minutes: plenty for a person, a wall for a script
_recent: dict[str, list[float]] = {}


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


async def start_pairing(pool, tenant_id: str) -> str:
    code = new_code()
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM owner_desktop_pairings WHERE tenant_id = %s OR expires_at < now()", (tenant_id,))
        await conn.execute("INSERT INTO owner_desktop_pairings (code_hash, tenant_id, expires_at) VALUES (%s, %s, %s)",
                           (_hash(code), tenant_id, datetime.now(timezone.utc) + PAIR_TTL))
    return code


async def complete_pairing(pool, code: str, name: str) -> dict | None:
    """Exchange a one-time code for an owner approvals token (returned once, stored hashed)."""
    token = "sma_" + secrets.token_urlsafe(32)
    async with pool.connection() as conn:
        row = await (await conn.execute(
            "DELETE FROM owner_desktop_pairings WHERE code_hash = %s AND expires_at > now() RETURNING tenant_id",
            (_hash(code.strip().upper()),))).fetchone()
        if row is None:
            return None
        await conn.execute("INSERT INTO owner_desktop_tokens (tenant_id, name, token_hash, last_seen_at) VALUES (%s, %s, %s, now())",
                           (row["tenant_id"], (name or "PC")[:60], _hash(token)))
        shop = await (await conn.execute("SELECT name FROM tenants WHERE id = %s", (row["tenant_id"],))).fetchone()
    return {"token": token, "shop": shop["name"] if shop else ""}


async def owner_for_token(pool, token: str) -> dict | None:
    if not token or not token.startswith("sma_"):
        return None
    async with pool.connection() as conn:
        return await (await conn.execute(
            """UPDATE owner_desktop_tokens SET last_seen_at = now() WHERE token_hash = %s AND revoked_at IS NULL
               RETURNING id, tenant_id, name""", (_hash(token),))).fetchone()


async def revoke(pool, tenant_id: str) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute("UPDATE owner_desktop_tokens SET revoked_at = now() WHERE tenant_id = %s AND revoked_at IS NULL",
                                 (tenant_id,))
        await conn.execute("DELETE FROM owner_desktop_pairings WHERE tenant_id = %s", (tenant_id,))
        return cur.rowcount


def _safe_args(name: str, args: dict) -> dict:
    """What Desktop may show next to the card: the step, never typed secrets (those steps are refused anyway)."""
    keep = {"browser_step": ("action", "url", "ref", "text", "value"), "pc_files": ("action", "path"),
            "hand_off_to_owner": ("reason",)}.get(name, ())
    return {k: str(args[k])[:300] for k in keep if args.get(k) not in (None, "")}


async def pending(pool, tenant_id: str) -> list[dict]:
    async with pool.connection() as conn:
        rows = await (await conn.execute(
            """SELECT id, pending, updated_at FROM agent_sessions
                WHERE tenant_id = %s AND status = 'WAITING_APPROVAL' AND pending->>'name' = ANY(%s)
                ORDER BY updated_at DESC LIMIT 50""", (tenant_id, list(DESKTOP_TOOLS)))).fetchall()
    return [{"id": str(r["id"]), "tool": r["pending"].get("name"), "card": str(r["pending"].get("card") or "")[:1024],
             "step": _safe_args(r["pending"].get("name"), r["pending"].get("args") or {}),
             "site_choice": r["pending"].get("site_choice", r["pending"].get("name") == "browser_step"),
             "asked_at": r["updated_at"].isoformat() if r["updated_at"] else None} for r in rows]


def rate_ok(token_id: str) -> bool:
    now = time.monotonic()
    recent = [t for t in _recent.get(token_id, []) if now - t < RATE_WINDOW_S]
    if len(recent) >= RATE_MAX:
        _recent[token_id] = recent
        return False
    _recent[token_id] = recent + [now]
    return True


async def decide(app, tenant_id: str, sid: str, choice: str, *, background: bool = True) -> str:
    """'done' | 'already' (nothing waiting, or answered in chat first) | 'not_here' (not a PC approval)."""
    if choice not in CHOICES:
        raise ValueError("choice must be approve, site or reject")
    async with app.state.pool.connection() as conn:
        row = await (await conn.execute(
            "SELECT status, pending FROM agent_sessions WHERE id::text = %s AND tenant_id = %s", (sid, tenant_id))).fetchone()
    if row is None or row["status"] != "WAITING_APPROVAL":
        return "already"
    name = (row["pending"] or {}).get("name")
    if name not in DESKTOP_TOOLS:
        return "not_here"
    from src.agent_loop import grant_site, resume
    if choice == "site" and name == "browser_step":
        await grant_site(app, tenant_id, sid)
    ok = await resume(app, tenant_id, sid=sid, approved=choice != "reject", background=background)
    if not ok:
        return "already"
    from src import chatlog
    first = str((row["pending"] or {}).get("card") or name).splitlines()[0][:200]
    word = {"approve": "Approved", "site": "Approved (and this site for 15 minutes)", "reject": "Rejected"}[choice]
    await chatlog.record(app.state.pool, tenant_id, "desktop", "owner", f"[{word} in Smara Desktop] {first}",
                         kind="owner_desktop_decision", session_id=sid)
    return "done"
