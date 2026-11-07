"""Outbound message log and Meta delivery receipts.

WhatsApp accepts a send with 200 and reports failure later (e.g. outside the
24h window). Every send is logged here so a failed receipt can be acted on:
a failed debtor reminder is rolled back and the owner told why; a failed owner
card stays queued and is re-sent the next time the owner messages us.
"""
import json
import logging

from src.ingest.payload import StatusUpdate

log = logging.getLogger(__name__)
_FORWARD = {"sent": ("held",), "delivered": ("held", "sent"), "read": ("held", "sent", "delivered")}
HINTS = {
    131047: "they have not messaged in the last 24 hours, so WhatsApp needs an approved template (set WA_REMINDER_TEMPLATE)",
    131026: "the number may not be on WhatsApp",
    131030: "the number is not an allowed test recipient yet",
    131049: "WhatsApp limited messages to this user for now",
}


def hint(code: int | None, title: str) -> str:
    return HINTS.get(code or 0, title or "WhatsApp reported a delivery failure")


async def record(pool, msg_id: str, *, tenant_id: str, kind: str, to_phone: str, status: str = "sent",
                 obligation_id=None, adjustment_id=None, agent_session_id=None, payload=None) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            """INSERT INTO outbound_messages
                 (msg_id, tenant_id, kind, to_phone, status, obligation_id, adjustment_id, agent_session_id, payload)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (msg_id) DO NOTHING""",
            (msg_id, tenant_id, kind, to_phone, status, obligation_id, adjustment_id, agent_session_id,
             json.dumps(payload) if payload is not None else None))


async def apply_status(app, st: StatusUpdate) -> None:
    pool = app.state.pool
    async with pool.connection() as conn:
        if st.status == "failed":
            cur = await conn.execute(
                """UPDATE outbound_messages SET status = 'failed', error_code = %s, error_title = %s, updated_at = now()
                    WHERE msg_id = %s AND status NOT IN ('failed', 'resent') RETURNING *""",
                (st.error_code, st.error_title[:200], st.msg_id))
        elif st.status in _FORWARD:   # never move backwards (receipts can arrive out of order)
            cur = await conn.execute(
                "UPDATE outbound_messages SET status = %s, updated_at = now() WHERE msg_id = %s AND status = ANY(%s) RETURNING *",
                (st.status, st.msg_id, list(_FORWARD[st.status])))
        else:
            return
        row = await cur.fetchone()
    if row and st.status == "failed":
        log.warning("delivery failed for %s (%s): %s %s", row["kind"], row["msg_id"], st.error_code, st.error_title)
        if row["kind"] == "debtor_reminder" and row["obligation_id"]:
            await _undo_reminder(app, row, st)


async def _undo_reminder(app, row: dict, st: StatusUpdate) -> None:
    """The reminder never reached the debtor: put the invoice back and tell the owner."""
    from src import tenants
    from src.loop.owner_channel import send_to_owner
    from src.loop.whatsapp import text_message

    pool = app.state.pool
    async with pool.connection() as conn:
        async with conn.transaction():
            cur = await conn.execute(
                """SELECT o.id, o.invoice_ref, p.canonical_name FROM obligations o JOIN parties p ON p.id = o.party_id
                    WHERE o.id = %s AND o.state = 'DISPATCHED' FOR UPDATE OF o""", (row["obligation_id"],))
            o = await cur.fetchone()
            if o is None:
                return
            await conn.execute(
                """UPDATE obligations SET state = 'ACTIVE', attempt_count = greatest(attempt_count - 1, 0),
                          next_followup_at = now() + interval '1 day' WHERE id = %s""", (o["id"],))
            await conn.execute(
                """INSERT INTO obligation_events (obligation_id, from_state, to_state, actor)
                   VALUES (%s, 'DISPATCHED', 'ACTIVE', 'SYSTEM_CRON')""", (o["id"],))
    tenant = await tenants.by_id(pool, str(row["tenant_id"]))
    if tenant:
        text = (f"Reminder to {o['canonical_name']} for {o['invoice_ref']} was not delivered: "
                f"{hint(st.error_code, st.error_title)}. I'll ask you again tomorrow.")
        await send_to_owner(app, tenant, text_message(tenant.owner_phone, text), kind="owner_text")
