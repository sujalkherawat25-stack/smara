"""A shop's own WhatsApp number, linked through the Baileys sidecar (server/wa_link).

UNOFFICIAL. WhatsApp can restrict or ban a number used this way, and the owner accepted that risk. The guardrails
below are enforced here, in the app, not only in the sidecar:
  - every debtor message is approved by the owner first (reminders.py, agent actions); this module sends approved text;
  - one recipient per send, text only: no templates, no bulk, no marketing;
  - a customer who said STOP gets nothing proactive; quiet hours hold proactive sends (IST, clock.py);
  - a daily cap per shop and a pause of wa_link_send_gap_s between sends;
  - if WhatsApp logs the number out, every send pauses until the owner re-pairs, and the owner is told.
A reply to the customer's own message (a STOP or START acknowledgement) is not proactive, so it skips the quiet-hours
and STOP checks. It still needs a connected link and still counts toward the cap.

Credentials never touch disk or logs: the sidecar holds them in memory and the app stores them encrypted in the vault.
"""
import asyncio
import logging
import re
import time
from datetime import datetime, time as dtime, timedelta, timezone

import httpx

from src import clock, vault
from src.config import normalize_phone

log = logging.getLogger(__name__)
AUTH_SECRET = "wa_link_auth"
_MEDIA_REF = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
PROACTIVE, REPLY = "proactive", "reply"
STATUSES = ("pairing", "connected", "reconnecting", "disconnected", "logged_out")
DETAIL = {
    "pairing": "waiting for the QR scan",
    "connected": "linked and sending",
    "reconnecting": "reconnecting",
    "disconnected": "stopped by the owner",
    "logged_out": "logged out by WhatsApp; re-pair to resume",
}
_pacing: dict[str, float] = {}      # shop -> monotonic time its last send finished (pauses between sends)
_locks: dict[str, asyncio.Lock] = {}


class SendBlocked(RuntimeError):
    """A guardrail refused the send, or the link could not send it. Nothing was sent; `retry_after` says when to ask again."""

    def __init__(self, reason: str, retry_after: timedelta = timedelta(minutes=10)):
        super().__init__(reason)
        self.reason = reason
        self.retry_after = retry_after


class SidecarError(RuntimeError):
    pass


class SidecarClient:
    """Calls the internal sidecar API (bearer token). Sends are never retried here: a retry after a timeout could
    deliver the same message twice."""

    def __init__(self, base_url: str, token: str, *, transport: httpx.AsyncBaseTransport | None = None):
        self._http = httpx.AsyncClient(base_url=base_url.rstrip("/"), headers={"Authorization": f"Bearer {token}"},
                                       timeout=httpx.Timeout(30.0), transport=transport)

    async def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        try:
            res = await self._http.request(method, path, json=body)
        except httpx.HTTPError as e:
            raise SidecarError("the WhatsApp link service is unreachable") from e
        if res.status_code >= 400:
            raise SidecarError(f"the WhatsApp link service answered {res.status_code}")
        return res.json()

    async def health(self) -> dict:
        return await self._call("GET", "/healthz")

    async def status(self, tenant_id: str) -> dict:
        return await self._call("GET", f"/sessions/{tenant_id}")

    async def pair(self, tenant_id: str, auth: str | None) -> dict:
        return await self._call("POST", f"/sessions/{tenant_id}/pair", {"auth": auth})

    async def send(self, tenant_id: str, to: str, text: str) -> str:
        res = await self._call("POST", f"/sessions/{tenant_id}/send", {"to": to, "text": text})
        return str(res.get("message_id") or "")

    async def disconnect(self, tenant_id: str) -> dict:
        return await self._call("POST", f"/sessions/{tenant_id}/disconnect")

    async def media(self, tenant_id: str, ref: str) -> bytes:
        """One file the owner sent on the SIM. The sidecar keeps it a few minutes and hands it over once."""
        if not _MEDIA_REF.fullmatch(ref or ""):
            raise SidecarError("bad file reference")
        try:
            res = await self._http.get(f"/sessions/{tenant_id}/media/{ref}", timeout=httpx.Timeout(120.0))
        except httpx.HTTPError as e:
            raise SidecarError("the WhatsApp link service is unreachable") from e
        if res.status_code >= 400:
            raise SidecarError(f"the WhatsApp link service answered {res.status_code}")
        return res.content

    async def aclose(self) -> None:
        await self._http.aclose()


def client(app) -> SidecarClient:
    c = getattr(app.state, "wa_link", None)
    if c is None:
        raise SidecarError("the WhatsApp link is not configured")
    return c


def configured(app) -> bool:
    return getattr(app.state, "wa_link", None) is not None


async def media(app, tenant_id: str, ref: str) -> bytes:
    """A file the owner sent on the shop's SIM, fetched from the sidecar. Raises SidecarError if it is no longer there."""
    return await client(app).media(tenant_id, ref)


# ---------------------------------------------------------------- database

async def link_row(pool, tenant_id: str) -> dict | None:
    async with pool.connection() as conn:
        return await (await conn.execute(
            "SELECT status, phone, detail, connected_at FROM wa_links WHERE tenant_id = %s", (tenant_id,))).fetchone()


async def uses_link(pool, tenant_id: str) -> bool:
    """A shop whose own number has been connected at least once sends its debtor messages through that number, and only
    that number: when the link is down the sends pause instead of falling back to the Cloud API. A shop that only
    started a pairing and never finished it keeps the Cloud path."""
    async with pool.connection() as conn:
        return await (await conn.execute(
            "SELECT 1 FROM wa_links WHERE tenant_id = %s AND connected_at IS NOT NULL", (tenant_id,))).fetchone() is not None


async def paused(pool, tenant_id: str) -> bool:
    if not await uses_link(pool, tenant_id):
        return False
    row = await link_row(pool, tenant_id)
    return row["status"] != "connected"


async def set_status(pool, tenant_id: str, status: str, *, phone: str = "", detail: str | None = None) -> str | None:
    """Store the link's status. Returns the previous status, or None if the shop had no link yet."""
    async with pool.connection() as conn:
        async with conn.transaction():
            prev = await (await conn.execute("SELECT status FROM wa_links WHERE tenant_id = %s FOR UPDATE",
                                             (tenant_id,))).fetchone()
            await conn.execute(
                """INSERT INTO wa_links (tenant_id, status, phone, detail, connected_at, updated_at)
                   VALUES (%s, %s, %s, %s, CASE WHEN %s = 'connected' THEN now() END, now())
                   ON CONFLICT (tenant_id) DO UPDATE SET
                     status = EXCLUDED.status,
                     phone = CASE WHEN EXCLUDED.phone = '' THEN wa_links.phone ELSE EXCLUDED.phone END,
                     detail = EXCLUDED.detail,
                     connected_at = CASE WHEN EXCLUDED.status = 'connected' AND wa_links.status <> 'connected'
                                         THEN now() ELSE wa_links.connected_at END,
                     updated_at = now()""",
                (tenant_id, status, phone, detail or DETAIL.get(status, ""), status))
    return prev["status"] if prev else None


async def _stored_auth(pool, tenant_id: str) -> str | None:
    raw = await vault.get_secret(pool, tenant_id, AUTH_SECRET)
    return raw.decode() if raw else None


async def summary(pool) -> dict[str, int]:
    """How many shops have a link in each state (for the health endpoint; no shop names, no numbers)."""
    async with pool.connection() as conn:
        rows = await (await conn.execute("SELECT status, count(*) AS n FROM wa_links GROUP BY status")).fetchall()
    return {r["status"]: r["n"] for r in rows}


# ---------------------------------------------------------------- pairing (owner's page)

async def begin_pairing(app, tenant_id: str) -> dict:
    """The owner opened the link page. With stored credentials this reconnects the number they already linked; without
    them it starts a new pairing, and the QR appears on the page."""
    pool = app.state.pool
    row = await link_row(pool, tenant_id)
    auth = None if row is None or row["status"] == "logged_out" else await _stored_auth(pool, tenant_id)
    res = await client(app).pair(tenant_id, auth)
    if auth is None:
        await set_status(pool, tenant_id, "pairing")
    return res


async def view(app, tenant_id: str) -> dict:
    """What the owner's page shows: the live state from the sidecar (with the QR while pairing), else the stored state."""
    pool = app.state.pool
    row = await link_row(pool, tenant_id)
    stored = row["status"] if row else "unpaired"
    phone = row["phone"] if row else ""
    try:
        live = await client(app).status(tenant_id)
    except SidecarError:
        return {"status": stored, "phone": phone, "qr_svg": "", "message": "The link service is not reachable right now."}
    state = live.get("status", "absent")
    if state == "absent":                          # the device is not running: it is restarted by reconcile()
        state = "reconnecting" if stored == "connected" else stored
    if state == "pairing":
        return {"status": "pairing", "phone": phone, "qr_svg": live.get("qr_svg", ""),
                "message": "Open WhatsApp on the SIM phone: Linked devices, then Link a device, and scan this code."}
    return {"status": state, "phone": live.get("phone") or phone, "qr_svg": "", "message": DETAIL.get(state, state)}


async def disconnect(app, tenant_id: str) -> None:
    try:
        await client(app).disconnect(tenant_id)
    except SidecarError:
        log.warning("link service unreachable while disconnecting a shop's link")
    await set_status(app.state.pool, tenant_id, "disconnected")


# ---------------------------------------------------------------- events from the sidecar

async def on_state(pool, tenant_id: str, auth: str) -> None:
    """The sidecar's credentials changed: store them encrypted in the vault."""
    await vault.put_secret(pool, tenant_id, AUTH_SECRET, auth)


async def on_status(app, tenant_id: str, status: str, phone: str = "") -> None:
    if status not in STATUSES:
        raise ValueError(f"unknown link status {status!r}")
    pool = app.state.pool
    prev = await set_status(pool, tenant_id, status, phone=normalize_phone(phone))
    if status == "logged_out":
        await vault.delete_secret(pool, tenant_id, AUTH_SECRET)   # WhatsApp no longer accepts these credentials
        if prev != "logged_out":
            await _tell_owner(app, tenant_id, "WhatsApp logged your number out, so every message to customers is paused. "
                                              "Send 'connect whatsapp' to link it again.")
    elif status == "connected" and prev not in ("connected", "reconnecting"):   # a brief reconnect is not news
        await _consume_pairing_links(pool, tenant_id)
        shown = normalize_phone(phone)
        await _tell_owner(app, tenant_id, f"WhatsApp number {'+' + shown if shown else ''} is now linked to Smara. "
                                          "Customer messages will go out from it, and only after your approval.")


async def _consume_pairing_links(pool, tenant_id: str) -> None:
    async with pool.connection() as conn:
        await conn.execute("UPDATE whatsapp_connect_links SET used_at = now() WHERE tenant_id = %s AND used_at IS NULL",
                           (tenant_id,))


async def _tell_owner(app, tenant_id: str, text: str) -> None:
    from src import tenants
    from src.loop.owner_channel import send_to_owner
    from src.loop.whatsapp import text_message

    try:
        t = await tenants.by_id(app.state.pool, tenant_id)
        if t:
            await send_to_owner(app, t, text_message(t.owner_phone, text), kind="owner_note")
    except Exception:
        log.warning("could not tell the owner about the WhatsApp link", exc_info=True)


async def reconcile(app) -> int:
    """Start again every linked number whose device is not running (the sidecar was restarted). Returns how many."""
    if not configured(app):
        return 0
    pool, restarted = app.state.pool, 0
    async with pool.connection() as conn:
        rows = await (await conn.execute(
            "SELECT tenant_id FROM wa_links WHERE status IN ('connected', 'reconnecting')")).fetchall()
    for r in rows:
        tid = str(r["tenant_id"])
        try:
            if (await client(app).status(tid)).get("status") != "absent":
                continue
            auth = await _stored_auth(pool, tid)
            if auth is None:
                await set_status(pool, tid, "disconnected", detail="stored credentials are missing; re-pair")
                await _tell_owner(app, tid, "The WhatsApp link lost its saved credentials, so messages are paused. "
                                            "Send 'connect whatsapp' to link it again.")
                continue
            await client(app).pair(tid, auth)
            restarted += 1
        except SidecarError:
            log.warning("link service unreachable during reconcile")
            break
    return restarted


def health_text(row: dict | None, *, always: bool = False) -> str:
    if row is None:
        return ("WhatsApp link: not set up. Send 'connect whatsapp' to link your SIM number." if always else "")
    st = row["status"]
    if st == "connected":
        number = f"+{row['phone']}" if row["phone"] else "number on file"
        return f"WhatsApp link: connected ({number}). Customer messages go out from it, after your approval."
    if st == "reconnecting":
        return "WhatsApp link: reconnecting. Sends wait until it is back."
    if st == "logged_out":
        return "WhatsApp link: logged out by WhatsApp. ALL customer messages are paused. Send 'connect whatsapp' to re-pair."
    if st == "disconnected":
        return "WhatsApp link: disconnected. Customer messages are paused. Send 'connect whatsapp' to reconnect."
    return "WhatsApp link: waiting for the QR scan. Send 'connect whatsapp' to see it again."


async def health_line(pool, tenant_id: str, *, always: bool = False) -> str:
    return health_text(await link_row(pool, tenant_id), always=always)


# ---------------------------------------------------------------- sending (guardrails live here)

async def _check(pool, s, tenant_id: str, phone: str, purpose: str, now: datetime) -> None:
    async with pool.connection() as conn:
        row = await (await conn.execute("SELECT status FROM wa_links WHERE tenant_id = %s", (tenant_id,))).fetchone()
        status = row["status"] if row else "unpaired"
        if status != "connected":
            retry = timedelta(minutes=10) if status == "reconnecting" else timedelta(days=1)
            raise SendBlocked(f"the shop's WhatsApp link is {status.replace('_', ' ')}", retry_after=retry)
        if purpose == PROACTIVE:
            if clock.is_quiet(s, now):
                raise SendBlocked("it is quiet hours", retry_after=clock.next_open(s, now) - now)
            opted_out = await (await conn.execute(
                """SELECT 1 FROM parties WHERE tenant_id = %s AND whatsapp_phone = %s AND opted_out_at IS NOT NULL LIMIT 1""",
                (tenant_id, phone))).fetchone()
            if opted_out:
                raise SendBlocked("the customer asked to stop messages (STOP)", retry_after=timedelta(days=30))
        day = clock.now().date()
        counted = await (await conn.execute(
            """INSERT INTO wa_link_daily_sends (tenant_id, send_date, sent) VALUES (%s, %s, 1)
               ON CONFLICT (tenant_id, send_date) DO UPDATE SET sent = wa_link_daily_sends.sent + 1
               WHERE wa_link_daily_sends.sent < %s RETURNING sent""",
            (tenant_id, day, s.wa_link_daily_cap))).fetchone()
    if counted is None:
        tomorrow = datetime.combine(day + timedelta(days=1), dtime(0), tzinfo=clock.IST)
        raise SendBlocked(f"today's limit of {s.wa_link_daily_cap} messages from this number is reached",
                          retry_after=tomorrow - now)


async def _pause_before_send(tenant_id: str, gap_s: float) -> None:
    last = _pacing.get(tenant_id)
    if last is not None:
        wait = gap_s - (time.monotonic() - last)
        if wait > 0:
            await asyncio.sleep(wait)


async def send_text(app, tenant, to_phone: str, text: str, *, purpose: str, kind: str, obligation_id=None) -> str:
    """Send one approved text to one customer from the shop's own number. Returns WhatsApp's message id.
    Raises SendBlocked when a guardrail refuses (nothing is sent). `kind` is for the caller's outbound log."""
    if purpose not in (PROACTIVE, REPLY):
        raise ValueError(f"unknown purpose {purpose!r}")
    phone = normalize_phone(to_phone)
    if not 8 <= len(phone) <= 15 or not text.strip() or len(text) > 4000:
        raise ValueError("refusing to send an empty, oversized or malformed message")
    pool, s = app.state.pool, app.state.settings
    tid = str(tenant.id)
    lock = _locks.setdefault(tid, asyncio.Lock())
    async with lock:                                      # one send at a time per shop
        await _check(pool, s, tid, phone, purpose, datetime.now(timezone.utc))
        await _pause_before_send(tid, float(s.wa_link_send_gap_s))
        try:
            msg_id = await client(app).send(tid, phone, text)
        except SidecarError as e:
            raise SendBlocked(f"the WhatsApp link did not send it: {e}") from e
        finally:
            _pacing[tid] = time.monotonic()
    log.info("sent %s via shop link (%s) to ...%s", kind, purpose, phone[-4:])
    return msg_id
