"""Collection reminders. Invariant 3: every debtor-bound message pauses in
WAITING_OWNER_APPROVAL until the owner taps [Send]."""
import logging
from datetime import datetime, timedelta, timezone

from src.bridge.continuum import ContinuumClient, scope_user
from src import clock, tenants
from src.bridge.outbox import enqueue_standalone
from src.loop.outbound import record
from src.loop.upi import pay_url, rupees, upi_text
from src.loop.whatsapp import from_shop, template_message, text_message

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 4
SNOOZE = timedelta(days=3)
RETRY_AFTER = timedelta(days=3)

_TONES = (
    "Namaste {party} ji, {ref} ka {amt} baaki hai (due {due}). Kripya payment kar dein.",
    "{party} ji, yaad dilana tha: {ref} ka {amt} abhi pending hai. Jaldi settle kar dein.",
    "{party} ji, {ref} ka {amt} ab bhi pending hai. Kab tak payment hoga, bata dein?",
    "{party} ji, {ref} ka {amt} kaafi din se pending hai. Aaj payment ya date confirm karein.",
)


def draft_reminder(o: dict, upi: dict | None, base_url: str = "") -> str:
    text = _TONES[min(o["attempt_count"], len(_TONES) - 1)].format(
        party=o["canonical_name"], ref=o["invoice_ref"], amt=rupees(o["amount_paise"]),
        due=o["due_date"].strftime("%d %b"))
    if upi:
        text += "\n" + upi_text(upi["vpa"], o["amount_paise"], o["invoice_ref"])
        if base_url:
            text += "\nTap to pay: " + pay_url(base_url, upi["vpa"], upi.get("name", ""), o["amount_paise"], o["invoice_ref"])
    return text


async def _event(conn, oid, frm, to, actor, source_id=None):
    await conn.execute(
        """INSERT INTO obligation_events (obligation_id, from_state, to_state, actor, continuum_source_id)
           VALUES (%s, %s, %s, %s, %s)""", (oid, frm, to, actor, source_id))


async def claim_due(pool, now: datetime, limit: int = 20) -> list[dict]:
    """Postgres decides what is due (never semantic search). Claims rows by
    moving them to WAITING_OWNER_APPROVAL; SKIP LOCKED makes ticks overlap-safe."""
    async with pool.connection() as conn:
        async with conn.transaction():
            cur = await conn.execute(
                """WITH due AS (
                     SELECT id, state AS prev FROM obligations
                      WHERE state IN ('ACTIVE', 'DISPATCHED', 'SNOOZED')
                        AND next_followup_at <= %s AND attempt_count < %s
                      ORDER BY next_followup_at LIMIT %s FOR UPDATE SKIP LOCKED)
                   UPDATE obligations o SET state = 'WAITING_OWNER_APPROVAL'
                     FROM due, parties p
                    WHERE o.id = due.id AND p.id = o.party_id
                RETURNING o.id, o.tenant_id, o.party_id, o.invoice_ref, o.amount_paise, o.due_date,
                          o.attempt_count, p.canonical_name, due.prev""",
                (now, MAX_ATTEMPTS, limit),
            )
            rows = await cur.fetchall()
            for r in rows:
                await _event(conn, r["id"], r["prev"], "WAITING_OWNER_APPROVAL", "SYSTEM_CRON")
    return rows


async def state_card(continuum: ContinuumClient, o: dict) -> str:
    """Why/proof context for the owner. Best effort: a Continuum outage must not block reminders."""
    try:
        res = await continuum.resolve(
            user_id=scope_user(str(o["tenant_id"]), str(o["party_id"])),
            query=f"{o['canonical_name']} {o['invoice_ref']} payment promises disputes")
        return (res.get("resolution", {}).get("state_card") or "")[:350]
    except Exception:
        log.warning("resolve failed for party %s", o["party_id"])
        return ""


async def release_claim(pool, o: dict, now: datetime) -> None:
    """The owner could not be asked: put the obligation back and retry shortly,
    instead of leaving it stuck in WAITING_OWNER_APPROVAL."""
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE obligations SET state = %s, next_followup_at = %s WHERE id = %s AND state = 'WAITING_OWNER_APPROVAL'",
            (o["prev"], now + timedelta(minutes=10), o["id"]))
        await _event(conn, o["id"], "WAITING_OWNER_APPROVAL", o["prev"], "SYSTEM_CRON")


def reminder_card(to: str, o: dict, draft: str, context: str) -> dict:
    body = f"Send this to {o['canonical_name']}?\n\n\"{draft}\""
    if context and "No relevant state" not in context:
        body += f"\n\nContext: {context}"
    oid = str(o["id"])
    btn = lambda k, t: {"type": "reply", "reply": {"id": f"{k}:{oid}", "title": t}}
    return {"messaging_product": "whatsapp", "to": to, "type": "interactive", "interactive": {
        "type": "button", "body": {"text": body[:1024]},
        "action": {"buttons": [btn("send", "Send"), btn("snooze", "Snooze 3 days"), btn("paid", "Mark paid")]}}}


WINDOW = timedelta(hours=24)


def _debtor_payload(o: dict, tenant, settings, now: datetime) -> dict:
    """Free text inside the 24h reply window, the approved template outside it."""
    last = o.get("last_inbound_at")
    if (last and now - last < WINDOW) or not settings.wa_reminder_template:
        return text_message(o["whatsapp_phone"], from_shop(tenant, draft_reminder(o, tenant.upi, settings.public_base_url)))
    return template_message(o["whatsapp_phone"], settings.wa_reminder_template, settings.wa_reminder_lang,
                            [o["canonical_name"], o["invoice_ref"], rupees(o["amount_paise"]), f"{o['due_date']:%d %b}"])


async def decide_reminder(app, tenant_id: str, obligation_id: str, action: str, now: datetime) -> str | None:
    """Owner tap on a reminder card. Returns a reply for the owner, None if stale."""
    pool, s = app.state.pool, app.state.settings
    if action == "send" and clock.is_quiet(s, now):
        return (f"Abhi raat ka samay hai ({s.quiet_start_hour}:00-{s.quiet_end_hour}:00 IST); customers ko is waqt message nahi "
                f"bhejte. Subah {s.quiet_end_hour}:00 ke baad Send dabayein.")
    async with pool.connection() as conn:
        async with conn.transaction():
            cur = await conn.execute(
                """SELECT o.*, p.canonical_name, p.whatsapp_phone, p.last_inbound_at FROM obligations o
                     JOIN parties p ON p.id = o.party_id
                    WHERE o.id = %s AND o.tenant_id = %s AND o.state = 'WAITING_OWNER_APPROVAL'
                    FOR UPDATE OF o""", (obligation_id, tenant_id))
            o = await cur.fetchone()
            if o is None:
                return None
            to, nxt = {"send": ("DISPATCHED", now + RETRY_AFTER), "snooze": ("SNOOZED", now + SNOOZE),
                       "paid": ("PAID", None)}[action]
            await conn.execute(
                """UPDATE obligations SET state = %s, next_followup_at = %s,
                          attempt_count = attempt_count + %s WHERE id = %s""",
                (to, nxt, int(action == "send"), obligation_id))
            await _event(conn, o["id"], o["state"], to, "OWNER")
    if action != "send":
        return f"Okay, {o['invoice_ref']} {'marked paid' if action == 'paid' else 'snoozed 3 days'}."
    try:
        tenant = await tenants.by_id(pool, tenant_id)
        msg_id = await app.state.wa.send(_debtor_payload(o, tenant, s, now), number_id=tenant.wa_phone_number_id)
        if msg_id:
            await record(pool, msg_id, tenant_id=tenant_id, kind="debtor_reminder",
                         to_phone=o["whatsapp_phone"], obligation_id=o["id"])
    except Exception:
        log.exception("debtor send failed for %s", obligation_id)
        async with pool.connection() as conn:
            await conn.execute(
                """UPDATE obligations SET state = 'ACTIVE', next_followup_at = %s,
                          attempt_count = attempt_count - 1 WHERE id = %s""", (now + timedelta(minutes=10), obligation_id))
            await _event(conn, o["id"], "DISPATCHED", "ACTIVE", "SYSTEM_CRON")
        return "Could not send the reminder; I will retry in 10 minutes."
    await _record_sent(app, o, tenant_id)
    return f"Sent to {o['canonical_name']}."


async def _record_sent(app, o: dict, tenant_id: str) -> None:
    n = o["attempt_count"] + 1
    try:
        await enqueue_standalone(
            app.state.pool, user_id=scope_user(tenant_id, str(o["party_id"])),
            source_id=f"reminder_{o['id']}_{n}", trusted=True, stamp_obligation_id=str(o["id"]),
            text=f"Owner-approved reminder #{n} sent for invoice {o['invoice_ref']}",
            metadata={"actor": "OWNER", "obligation_id": str(o["id"])})
    except Exception:
        log.exception("could not queue reminder evidence for %s", o["id"])
