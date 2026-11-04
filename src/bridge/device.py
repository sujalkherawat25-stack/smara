"""Browser on the owner's own device.

The owner's PC runs the Smara Browser Bridge, which connects OUT to this server (nothing on the PC is exposed),
long-polls for browser commands for its shop, runs them in the owner's own Brave/Chrome and posts the result.
Because the browser is the owner's, requests leave from their home IP and logins stay on their machine.

Security:
- pairing needs a one-time code the owner asks for in their authenticated chat (one hour, single use);
- the device token is random, shown once, and stored only as a hash; the owner can revoke it from chat;
- commands are the same small set the server browser accepts, and the same policy gates them (reads free,
  clicks and typing need approval or an owner-granted site) before a job is ever created here.
"""
import asyncio
import hashlib
import json
import secrets
import time
from datetime import datetime, timedelta, timezone

PAIR_TTL = timedelta(hours=1)
ONLINE_WITHIN = timedelta(seconds=45)       # the bridge long-polls every ~25 s, so a live one is seen well inside this
JOB_TIMEOUT_S = 60
POLL_S = 25
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


async def start_pairing(pool, tenant_id: str) -> str:
    code = new_code()
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM bridge_pairings WHERE tenant_id = %s OR expires_at < now()", (tenant_id,))
        await conn.execute("INSERT INTO bridge_pairings (code_hash, tenant_id, expires_at) VALUES (%s, %s, %s)",
                           (_hash(code), tenant_id, datetime.now(timezone.utc) + PAIR_TTL))
    return code


async def complete_pairing(pool, code: str, device_name: str) -> dict | None:
    """Exchange a valid one-time code for a device token (returned once)."""
    token = secrets.token_urlsafe(32)
    async with pool.connection() as conn:
        row = await (await conn.execute(
            "DELETE FROM bridge_pairings WHERE code_hash = %s AND expires_at > now() RETURNING tenant_id",
            (_hash(code.strip().upper()),))).fetchone()
        if row is None:
            return None
        await conn.execute("INSERT INTO bridge_devices (tenant_id, name, token_hash, last_seen_at) VALUES (%s, %s, %s, now())",
                           (row["tenant_id"], device_name[:60] or "PC", _hash(token)))
        t = await (await conn.execute("SELECT name FROM tenants WHERE id = %s", (row["tenant_id"],))).fetchone()
    return {"token": token, "shop": t["name"]}


async def device_for_token(pool, token: str) -> dict | None:
    async with pool.connection() as conn:
        return await (await conn.execute(
            """UPDATE bridge_devices SET last_seen_at = now() WHERE token_hash = %s AND revoked_at IS NULL
               RETURNING id, tenant_id, name""", (_hash(token),))).fetchone()


async def revoke_all(pool, tenant_id: str) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute("UPDATE bridge_devices SET revoked_at = now() WHERE tenant_id = %s AND revoked_at IS NULL",
                                 (tenant_id,))
        await conn.execute("UPDATE bridge_jobs SET status = 'EXPIRED' WHERE tenant_id = %s AND status IN ('PENDING','CLAIMED')",
                           (tenant_id,))
        return cur.rowcount


async def online_device(pool, tenant_id: str) -> dict | None:
    async with pool.connection() as conn:
        return await (await conn.execute(
            """SELECT id, name FROM bridge_devices WHERE tenant_id = %s AND revoked_at IS NULL AND last_seen_at > %s
                ORDER BY last_seen_at DESC LIMIT 1""", (tenant_id, datetime.now(timezone.utc) - ONLINE_WITHIN))).fetchone()


async def claim_job(pool, device: dict) -> dict | None:
    async with pool.connection() as conn:
        row = await (await conn.execute(
            """UPDATE bridge_jobs SET status = 'CLAIMED', device_id = %(d)s, claimed_at = now()
                WHERE id = (SELECT id FROM bridge_jobs WHERE tenant_id = %(t)s AND status = 'PENDING'
                             AND created_at > now() - interval '2 minutes' ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED)
            RETURNING id, command""", {"d": device["id"], "t": device["tenant_id"]})).fetchone()
    return {"job_id": str(row["id"]), "command": row["command"]} if row else None


async def finish_job(pool, device: dict, job_id: str, result: dict) -> bool:
    try:
        async with pool.connection() as conn:
            row = await (await conn.execute(
                """UPDATE bridge_jobs SET status = 'DONE', result = %s, done_at = now()
                    WHERE id::text = %s AND tenant_id = %s AND status = 'CLAIMED' RETURNING id""",
                (json.dumps(result, default=str)[:200_000], job_id, device["tenant_id"]))).fetchone()
    except Exception:
        return False
    return row is not None


class DeviceBrowser:
    """Same interface as BrowserSessions, executed on the owner's device via the job queue."""

    def __init__(self, pool, step_timeout_s: int = JOB_TIMEOUT_S):
        self.pool, self.step_timeout_s = pool, step_timeout_s
        self._pages: dict[str, dict] = {}
        self._open: set[str] = set()

    def alive(self, sid: str) -> bool:
        return sid in self._open

    def page(self, sid: str) -> dict:
        return self._pages.get(sid, {})

    async def step(self, sid: str, args: dict, tenant_id: str | None = None) -> dict:
        if not tenant_id:
            return {"error": "no shop for this browser step"}
        if not await online_device(self.pool, tenant_id):
            self._open.discard(sid)
            return {"error": "the owner's browser bridge is offline; start Smara Browser Bridge on the PC, or ask me to use the server browser"}
        cmd = {k: args[k] for k in ("action", "url", "selector", "value") if args.get(k) is not None}
        if cmd.get("action") != "navigate" and sid not in self._open:
            return {"error": "no open page: navigate first"}
        async with self.pool.connection() as conn:
            row = await (await conn.execute(
                "INSERT INTO bridge_jobs (tenant_id, session_id, command) VALUES (%s, %s, %s) RETURNING id",
                (tenant_id, sid, json.dumps(cmd)))).fetchone()
        job_id, deadline = row["id"], time.monotonic() + self.step_timeout_s
        while time.monotonic() < deadline:
            async with self.pool.connection() as conn:
                job = await (await conn.execute("SELECT status, result FROM bridge_jobs WHERE id = %s", (job_id,))).fetchone()
            if job["status"] == "DONE":
                out = job["result"] or {}
                if cmd.get("action") == "navigate" and not out.get("error"):
                    self._open.add(sid)
                if out.get("url"):
                    self._pages[sid] = {"url": out["url"], "title": out.get("title", "")}
                return out
            if job["status"] == "EXPIRED":
                return {"error": "the browser bridge was disconnected"}
            await asyncio.sleep(0.4)
        async with self.pool.connection() as conn:
            await conn.execute("UPDATE bridge_jobs SET status = 'EXPIRED' WHERE id = %s AND status IN ('PENDING','CLAIMED')", (job_id,))
        return {"error": f"the browser on the owner's PC did not answer within {self.step_timeout_s}s"}

    async def close(self, sid: str) -> None:
        self._open.discard(sid)
        self._pages.pop(sid, None)

    async def close_all(self) -> None:
        self._open.clear()
        self._pages.clear()


class BrowserRouter:
    """Picks the browser for each agent session: the owner's own device when their bridge is online,
    otherwise the server's browser (a hosted remote browser when BROWSER_CDP_URL is set)."""

    def __init__(self, pool, server_browser, device_browser: DeviceBrowser | None = None):
        self.server, self.device = server_browser, device_browser or DeviceBrowser(pool)
        self.pool = pool
        self._route: dict[str, object] = {}

    def _backend(self, sid: str):
        return self._route.get(sid)

    def alive(self, sid: str) -> bool:
        b = self._backend(sid)
        return bool(b) and b.alive(sid)

    def page(self, sid: str) -> dict:
        b = self._backend(sid)
        return b.page(sid) if b else {}

    def where(self, sid: str) -> str:
        b = self._backend(sid)
        return "device" if b is self.device else ("server" if b else "")

    async def step(self, sid: str, args: dict, tenant_id: str | None = None) -> dict:
        b = self._backend(sid)
        if b is None:
            if args.get("action") != "navigate":
                return {"error": "no open page: navigate first"}
            on_device = bool(tenant_id) and bool(await online_device(self.pool, tenant_id))
            b = self._route[sid] = self.device if on_device else self.server
        if b is self.device:
            return await b.step(sid, args, tenant_id=tenant_id)
        return await b.step(sid, args)

    async def close(self, sid: str) -> None:
        b = self._route.pop(sid, None)
        if b:
            await b.close(sid)

    async def close_all(self) -> None:
        await self.server.close_all()
        await self.device.close_all()
        self._route.clear()
