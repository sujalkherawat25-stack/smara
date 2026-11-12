"""Collection reminders. Invariant 3: every debtor-bound message pauses in
WAITING_OWNER_APPROVAL until the owner taps [Send]."""
import logging
from datetime import datetime, timedelta, timezone

from src.bridge.continuum import ContinuumClient, scope_user
from src import clock, tenants
from src.bridge.outbox import enqueue_standalone
from src.loop import wa_link
from src.loop.outbound import record
from src.loop.upi import pay_url, rupees, upi_text
from src.loop.whatsapp import from_shop, own_whatsapp_link, reminder_params, template_message, text_message

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
                        AND NOT EXISTS (SELECT 1 FROM parties x WHERE x.id = obligations.party_id AND x.opted_out_at IS NOT NULL)
                        AND EXISTS (SELECT 1 FROM parties x WHERE x.id = obligations.party_id AND x.whatsapp_phone IS NOT NULL)
                      ORDER BY next_followup_at LIMIT %s FOR UPDATE SKIP LOCKED)
                   UPDATE obligations o SET state = 'WAITING_OWNER_APPROVAL'
                     FROM due, parties p
                    WHERE o.id = due.id AND p.id = o.party_id
                RETURNING o.id, o.tenant_id, o.party_id, o.invoice_ref, o.amount_paise, o.due_date,
                          o.attempt_count, o.source, p.canonical_name, due.prev""",
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


ASK_AGAIN_AFTER = timedelta(hours=48)      # an unanswered reminder card is sent again after this
APPROVAL_EXPIRES = timedelta(days=3)        # an agent step the owner never answered is closed after this


async def reopen_unanswered(pool, now: datetime) -> dict:
    """Nothing may wait for the owner for ever.

    A reminder card can be lost: sent before a restart, refused by WhatsApp, or sent to a channel the owner no longer
    reads. Its invoice then sat in WAITING_OWNER_APPROVAL with no way out, so it was never reminded again. After
    ASK_AGAIN_AFTER the invoice goes back to its previous state and the next tick asks the owner again. An invoice an
    agent run is still waiting on is left alone. A tap on the old card still works while the new one waits, and the
    first tap wins, so nothing is sent twice.

    An agent step waiting for approval longer than APPROVAL_EXPIRES is closed (DONE, not FAILED, so it does not raise a
    health alert): acting on a days-old approval would use a page or a ledger that has moved on. A late tap is answered
    "already handled"."""
    async with pool.connection() as conn:
        async with conn.transaction():
            rows = await (await conn.execute(
                """WITH stuck AS (
                     SELECT o.id, coalesce(
                              (SELECT e.from_state FROM obligation_events e
                                WHERE e.obligation_id = o.id AND e.to_state = 'WAITING_OWNER_APPROVAL'
                                ORDER BY e.created_at DESC LIMIT 1), 'ACTIVE') AS prev
                       FROM obligations o
                      WHERE o.state = 'WAITING_OWNER_APPROVAL'
                        AND coalesce((SELECT max(e.created_at) FROM obligation_events e
                                       WHERE e.obligation_id = o.id AND e.to_state = 'WAITING_OWNER_APPROVAL'),
                                     '-infinity') < %(cut)s
                        AND NOT EXISTS (SELECT 1 FROM agent_sessions s
                                         WHERE s.tenant_id = o.tenant_id AND s.status = 'WAITING_APPROVAL'
                                           AND s.updated_at >= %(expired)s AND s.pending::text LIKE '%%' || o.id::text || '%%')
                      FOR UPDATE OF o SKIP LOCKED)
                   UPDATE obligations o
                      SET state = CASE WHEN stuck.prev IN ('ACTIVE', 'DISPATCHED', 'SNOOZED') THEN stuck.prev ELSE 'ACTIVE' END,
                          next_followup_at = %(now)s
                     FROM stuck WHERE o.id = stuck.id
                RETURNING o.id, o.state""",
                {"cut": now - ASK_AGAIN_AFTER, "expired": now - APPROVAL_EXPIRES, "now": now})).fetchall()
            for r in rows:
                await _event(conn, r["id"], "WAITING_OWNER_APPROVAL", r["state"], "SYSTEM_CRON")
            closed = (await conn.execute(
                """UPDATE agent_sessions SET status = 'DONE', pending = NULL, updated_at = now()
                    WHERE status = 'WAITING_APPROVAL' AND updated_at < %s""", (now - APPROVAL_EXPIRES,))).rowcount
    if rows or closed:
        log.info("asked again about %d unanswered reminder(s); closed %d expired approval(s)", len(rows), closed)
    return {"reopened": len(rows), "expired": closed}


def reminder_card(to: str, o: dict, draft: str, context: str, mode: str = "smara") -> dict:
    body = f"Send this to {o['canonical_name']}?\n\n\"{draft}\""
    if context and "No relevant state" not in context:
        body += f"\n\nContext: {context}"
    oid = str(o["id"])
    btn = lambda k, t: {"type": "reply", "reply": {"id": f"{k}:{oid}", "title": t}}
    # own: the owner sends it from their own WhatsApp (wa.me link). Reply buttons are capped at three.
    first = btn("own", "Send myself") if mode == "own" else btn("send", "Send")
    return {"messaging_product": "whatsapp", "to": to, "type": "interactive", "interactive": {
        "type": "button", "body": {"text": body[:1024]},
        "action": {"buttons": [first, btn("snooze", "Snooze 3 days"), btn("paid", "Mark paid")]}}}


WINDOW = timedelta(hours=24)
STALE_SYNC = timedelta(hours=24)


async def stale_sync_note(pool, tenant_id: str, now: datetime) -> str:
    """A warning when the Tally snapshot is old: a bill may already be paid. Empty when the sync is recent."""
    async with pool.connection() as conn:
        row = await (await conn.execute(
            "SELECT max(created_at) AS last FROM tally_syncs WHERE tenant_id = %s", (tenant_id,))).fetchone()
    last = row["last"] if row else None
    if last is None:
        return "Note: Tally se abhi tak sync nahi hua; ye bill pehle hi chuk gaya ho sakta hai."
    if now - last > STALE_SYNC:
        hours = int((now - last).total_seconds() // 3600)
        return f"Note: Tally se last sync {hours} ghante pehle hua. Ye bill shayad pehle hi chuk gaya ho."
    return ""


def _debtor_payload(o: dict, tenant, settings, now: datetime) -> dict:
    """Free text inside the 24h reply window, the approved template outside it."""
    last = o.get("last_inbound_at")
    if (last and now - last < WINDOW) or not settings.wa_reminder_template:
        return text_message(o["whatsapp_phone"], from_shop(tenant, draft_reminder(o, tenant.upi, settings.public_base_url)))
    return template_message(o["whatsapp_phone"], settings.wa_reminder_template, settings.wa_reminder_lang,
                            reminder_params(settings, o, tenant))


async def decide_reminder(app, tenant_id: str, obligation_id: str, action: str, now: datetime) -> str | None:
    """Owner tap on a reminder card. Returns a reply for the owner, None if stale."""
    pool, s = app.state.pool, app.state.settings
    sends = action in ("send", "own")          # both release a reminder to the customer, so both obey the same gates
    if sends and clock.is_quiet(s, now):
        return (f"Abhi raat ka samay hai ({s.quiet_start_hour}:00-{s.quiet_end_hour}:00 IST); customers ko is waqt message nahi "
                f"bhejte. Subah {s.quiet_end_hour}:00 ke baad Send dabayein.")
    async with pool.connection() as conn:
        async with conn.transaction():
            cur = await conn.execute(
                """SELECT o.*, p.canonical_name, p.whatsapp_phone, p.last_inbound_at, p.opted_out_at FROM obligations o
                     JOIN parties p ON p.id = o.party_id
                    WHERE o.id = %s AND o.tenant_id = %s AND o.state = 'WAITING_OWNER_APPROVAL'
                    FOR UPDATE OF o""", (obligation_id, tenant_id))
            o = await cur.fetchone()
            if o is None:
                return None
            if action == "send" and not o["whatsapp_phone"]:
                return (f"{o['canonical_name']} ka WhatsApp number saved nahi hai, isliye Smara se message nahi ja sakta. "
                        "Number bhejiye (jaise: " + o['canonical_name'] + " 98xxxxxxxx), ya 'Send myself' se apne WhatsApp se bhejiye.")
            if sends and o["opted_out_at"]:
                await conn.execute("UPDATE obligations SET state = 'SNOOZED', next_followup_at = %s WHERE id = %s",
                                   (now + timedelta(days=30), obligation_id))
                return (f"{o['canonical_name']} ne WhatsApp updates band karne ko kaha tha (STOP), isliye message nahi bheja. "
                        "Aap unhe call kar sakte hain.")
            to, nxt = {"send": ("DISPATCHED", now + RETRY_AFTER), "own": ("DISPATCHED", now + RETRY_AFTER),
                       "snooze": ("SNOOZED", now + SNOOZE), "paid": ("PAID", None)}[action]
            await conn.execute(
                """UPDATE obligations SET state = %s, next_followup_at = %s,
                          attempt_count = attempt_count + %s WHERE id = %s""",
                (to, nxt, int(sends), obligation_id))
            await _event(conn, o["id"], o["state"], to, "OWNER")
    if action == "own":
        tenant = await tenants.by_id(pool, tenant_id)
        draft = draft_reminder(o, tenant.upi, s.public_base_url)     # no shop prefix: it comes from the owner's own number
        await _record_sent(app, o, tenant_id)
        if not o["whatsapp_phone"]:
            return (f"{o['canonical_name']} ka WhatsApp number saved nahi hai, isliye link nahi bana. "
                    f"Ye message copy karke bhejiye:\n\n{draft}")
        link = own_whatsapp_link(o["whatsapp_phone"], draft)
        return (f"{o['canonical_name']} ko apne WhatsApp se bhejiye (message pehle se likha hai, bas Send dabaiye):\n{link}\n\n"
                "Bhejne ke baad agar payment aaye to 'paid' bataiye.")
    if action != "send":
        return f"Okay, {o['invoice_ref']} {'marked paid' if action == 'paid' else 'snoozed 3 days'}."
    try:
        tenant = await tenants.by_id(pool, tenant_id)
        if await wa_link.uses_link(pool, tenant_id):      # the shop's own SIM (server/wa_link): the approved text, once
            msg_id = await wa_link.send_text(app, tenant, o["whatsapp_phone"],
                                             from_shop(tenant, draft_reminder(o, tenant.upi, s.public_base_url)),
                                             purpose=wa_link.PROACTIVE, kind="debtor_reminder", obligation_id=o["id"])
        else:
            msg_id = await app.state.wa.send(_debtor_payload(o, tenant, s, now), number_id=tenant.wa_phone_number_id)
        if msg_id:
            await record(pool, msg_id, tenant_id=tenant_id, kind="debtor_reminder",
                         to_phone=o["whatsapp_phone"], obligation_id=o["id"])
    except wa_link.SendBlocked as e:                       # a guardrail said no: nothing was sent, ask again later
        log.info("reminder for %s held: %s", obligation_id, e.reason)
        await _put_back(pool, o, now + e.retry_after)
        return f"Not sent to {o['canonical_name']}: {e.reason}. I will ask again later."
    except Exception:
        log.exception("debtor send failed for %s", obligation_id)
        await _put_back(pool, o, now + timedelta(minutes=10))
        return "Could not send the reminder; I will retry in 10 minutes."
    await _record_sent(app, o, tenant_id)
    return f"Sent to {o['canonical_name']}."


async def _put_back(pool, o: dict, when: datetime) -> None:
    """The reminder was not sent: the invoice goes back to ACTIVE and is asked about again at `when`."""
    async with pool.connection() as conn:
        await conn.execute(
            """UPDATE obligations SET state = 'ACTIVE', next_followup_at = %s,
                      attempt_count = attempt_count - 1 WHERE id = %s""", (when, o["id"]))
        await _event(conn, o["id"], "DISPATCHED", "ACTIVE", "SYSTEM_CRON")


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
