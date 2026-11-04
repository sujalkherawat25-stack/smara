"""The one path for every owner-bound message.

- Telegram if the owner linked it (free, no 24h window).
- WhatsApp otherwise. Outside the owner's 24h window a free-form card would be
  dropped by WhatsApp, so with WA_OWNER_TEMPLATE set we hold the card, send one
  approved "N items waiting" template, and re-send held cards when the owner replies.
Builders produce WhatsApp payloads; Telegram gets a translation of the same card.
"""
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from src import clock
from src.loop.outbound import record
from src.loop.whatsapp import document_message, owner_approval_card, template_message

log = logging.getLogger(__name__)
WINDOW = timedelta(hours=24)
# Things the owner did not ask for just now: held at night / when the WhatsApp window is closed.
CARD_KINDS = ("owner_reminder_card", "owner_claim_card", "owner_agent_card", "owner_note")


def window_open(tenant, now: datetime) -> bool:
    last = tenant.owner_last_inbound_at
    return bool(last) and now - last < WINDOW


def pick_channel(app, tenant, now: datetime, reply_channel: str | None = None) -> str:
    """Where an owner-bound message goes. A reply stays on the channel the owner wrote on. Anything else
    (approvals, reminders, files the agent started) follows the owner's last-used channel. If WhatsApp is the
    preferred one but its 24h window is closed and Telegram is linked, Telegram carries it instead of holding it."""
    tg_ok = bool(getattr(app.state, "tg", None) and tenant.telegram_chat_id)
    if reply_channel:
        return "telegram" if reply_channel == "telegram" and tg_ok else "whatsapp"
    last = tenant.owner_last_channel or ("telegram" if tg_ok else "whatsapp")
    if tg_ok and (last == "telegram" or not window_open(tenant, now)):
        return "telegram"
    return "whatsapp"


def wa_to_telegram(payload: dict, chat_id: int) -> dict:
    if payload.get("type") == "interactive":
        it = payload["interactive"]
        rows = [[{"text": b["reply"]["title"], "callback_data": b["reply"]["id"]}] for b in it["action"]["buttons"]]
        return {"chat_id": chat_id, "text": it["body"]["text"][:4096], "reply_markup": {"inline_keyboard": rows}}
    return {"chat_id": chat_id, "text": (payload.get("text") or {}).get("body", "")[:4096]}


async def send_to_owner(app, tenant, payload: dict, *, kind: str, obligation_id=None, adjustment_id=None,
                        reply_channel: str | None = None, number_id: str | None = None,
                        agent_session_id=None) -> str:
    """Returns the channel used: 'telegram', 'whatsapp' or 'held'."""
    pool, s, now = app.state.pool, app.state.settings, datetime.now(timezone.utc)
    tg = getattr(app.state, "tg", None)
    if reply_channel is None and kind in CARD_KINDS and clock.is_quiet(s):
        # Quiet hours: nobody gets pinged at night for something they did not ask for. The card waits, and
        # flush_held() delivers it when the shop opens (or the owner writes first).
        await record(pool, f"held:{uuid4()}", tenant_id=tenant.id, kind=kind, to_phone=tenant.owner_phone,
                     status="held", obligation_id=obligation_id, adjustment_id=adjustment_id,
                     agent_session_id=agent_session_id, payload=payload if kind == "owner_note" else None)
        return "held"
    if pick_channel(app, tenant, now, reply_channel) == "telegram":
        await tg.send(wa_to_telegram(payload, tenant.telegram_chat_id))
        return "telegram"
    if reply_channel is None and s.wa_owner_template and not window_open(tenant, now) \
            and kind in (*CARD_KINDS, "owner_digest"):
        if kind in CARD_KINDS:
            await record(pool, f"held:{uuid4()}", tenant_id=tenant.id, kind=kind, to_phone=tenant.owner_phone,
                         status="held", obligation_id=obligation_id, adjustment_id=adjustment_id,
                         agent_session_id=agent_session_id, payload=payload if kind == "owner_note" else None)
        await nudge(app, tenant)
        return "held"
    msg_id = await app.state.wa.send({**payload, "to": tenant.owner_phone},
                                     number_id=number_id or tenant.wa_phone_number_id)
    if msg_id:
        await record(pool, msg_id, tenant_id=tenant.id, kind=kind, to_phone=tenant.owner_phone,
                     obligation_id=obligation_id, adjustment_id=adjustment_id, agent_session_id=agent_session_id)
    return "whatsapp"


async def send_document_to_owner(app, tenant, data: bytes, filename: str, mime: str, caption: str,
                                 reply_channel: str | None = None) -> str:
    """Send a file to the owner: Telegram if linked, else WhatsApp (needs the 24h window).
    Returns 'telegram', 'whatsapp' or 'window_closed'."""
    if pick_channel(app, tenant, datetime.now(timezone.utc), reply_channel) == "telegram":
        await app.state.tg.send_document(tenant.telegram_chat_id, data, filename, caption)
        return "telegram"
    if not window_open(tenant, datetime.now(timezone.utc)):
        return "window_closed"
    media_id = await app.state.wa.upload_media(data, mime, filename, number_id=tenant.wa_phone_number_id)
    msg_id = await app.state.wa.send(document_message(tenant.owner_phone, media_id, filename, caption),
                                     number_id=tenant.wa_phone_number_id)
    if msg_id:
        await record(app.state.pool, msg_id, tenant_id=tenant.id, kind="owner_document", to_phone=tenant.owner_phone)
    return "whatsapp"


async def flush_held(app) -> int:
    """When the shop opens, deliver what waited overnight. On WhatsApp with a closed window that is one
    template nudge (the cards follow the moment the owner replies); on Telegram it is the cards themselves."""
    from src import tenants
    s = app.state.settings
    if clock.is_quiet(s):
        return 0
    async with app.state.pool.connection() as conn:
        ids = [str(r["tenant_id"]) for r in await (await conn.execute(
            "SELECT DISTINCT tenant_id FROM outbound_messages WHERE status = 'held'")).fetchall()]
    delivered = 0
    for tid in ids:
        t = await tenants.by_id(app.state.pool, tid)
        if t is None:
            continue
        via = pick_channel(app, t, datetime.now(timezone.utc))
        if via == "telegram":
            delivered += await resend_undelivered(app, t, channel="telegram")
        elif window_open(t, datetime.now(timezone.utc)):
            delivered += await resend_undelivered(app, t, channel="whatsapp")
        elif s.wa_owner_template:
            await nudge(app, t)
    return delivered


async def nudge(app, tenant) -> None:
    """One approved template per closed window: 'You have N items waiting, reply to see them.'"""
    if tenant.owner_nudged_at and (not tenant.owner_last_inbound_at or tenant.owner_nudged_at > tenant.owner_last_inbound_at):
        return
    async with app.state.pool.connection() as conn:
        cur = await conn.execute(
            """SELECT (SELECT count(*) FROM obligations WHERE tenant_id = %(t)s AND state = 'WAITING_OWNER_APPROVAL')
                    + (SELECT count(*) FROM proposed_adjustments a JOIN obligations o ON o.id = a.obligation_id
                        WHERE o.tenant_id = %(t)s AND a.status = 'PENDING') AS n""", {"t": tenant.id})
        n = (await cur.fetchone())["n"]
        await conn.execute("UPDATE tenants SET owner_nudged_at = now() WHERE id = %s", (tenant.id,))
    s = app.state.settings
    await app.state.wa.send(template_message(tenant.owner_phone, s.wa_owner_template, s.wa_reminder_lang, [str(max(n, 1))]),
                            number_id=tenant.wa_phone_number_id)


async def resend_undelivered(app, tenant, channel: str) -> int:
    """Owner is reachable again: re-send held or failed cards that still need a decision."""
    pool = app.state.pool
    async with pool.connection() as conn:
        cur = await conn.execute(
            """UPDATE outbound_messages SET status = 'resent', updated_at = now()
                WHERE tenant_id = %s AND status IN ('held', 'failed') AND kind = ANY(%s)
            RETURNING kind, obligation_id, adjustment_id, agent_session_id, payload""", (tenant.id, list(CARD_KINDS)))
        rows = await cur.fetchall()
    sent = 0
    for r in rows:
        payload = await _rebuild(app, tenant, r)
        if payload:
            await send_to_owner(app, tenant, payload, kind=r["kind"], obligation_id=r["obligation_id"],
                                adjustment_id=r["adjustment_id"], reply_channel=channel,
                                agent_session_id=r["agent_session_id"])
            sent += 1
    return sent


async def _rebuild(app, tenant, r: dict) -> dict | None:
    from src.loop.reminders import draft_reminder, reminder_card
    from src.loop.upi import rupees

    if r["kind"] == "owner_note":
        return r["payload"]
    async with app.state.pool.connection() as conn:
        if r["kind"] == "owner_agent_card":
            cur = await conn.execute("SELECT id, pending FROM agent_sessions WHERE id = %s AND status = 'WAITING_APPROVAL'",
                                     (r["agent_session_id"],))
            sess = await cur.fetchone()
            if not sess:
                return None
            btn = lambda k, t: {"type": "reply", "reply": {"id": f"{k}:{sess['id']}", "title": t}}
            return {"messaging_product": "whatsapp", "to": tenant.owner_phone, "type": "interactive", "interactive": {
                "type": "button", "body": {"text": sess["pending"]["card"][:1024]},
                "action": {"buttons": [btn("ag_ok", "Approve"), btn("ag_no", "Reject")]}}}
        if r["kind"] == "owner_reminder_card":
            cur = await conn.execute(
                """SELECT o.*, p.canonical_name FROM obligations o JOIN parties p ON p.id = o.party_id
                    WHERE o.id = %s AND o.state = 'WAITING_OWNER_APPROVAL'""", (r["obligation_id"],))
            o = await cur.fetchone()
            return reminder_card(tenant.owner_phone, o, draft_reminder(o, tenant.upi, app.state.settings.public_base_url), "") if o else None
        cur = await conn.execute(
            """SELECT a.*, o.invoice_ref, p.canonical_name FROM proposed_adjustments a
                 JOIN obligations o ON o.id = a.obligation_id JOIN parties p ON p.id = o.party_id
                WHERE a.id = %s AND a.status = 'PENDING'""", (r["adjustment_id"],))
        a = await cur.fetchone()
    if not a:
        return None
    bits = ([f"new amount {rupees(a['proposed_amount_paise'])}"] if a["proposed_amount_paise"] else []) + \
           ([f"pay by {a['proposed_date']:%d %b}"] if a["proposed_date"] else [])
    return owner_approval_card(tenant.owner_phone, adjustment_id=str(a["id"]), party_name=a["canonical_name"],
                               invoice_ref=a["invoice_ref"], adjustment_type=a["adjustment_type"],
                               evidence=a["raw_evidence_text"], proposal=", ".join(bits))
