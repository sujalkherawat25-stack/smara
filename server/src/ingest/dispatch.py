"""Route one inbound message to Thread A (Owner, trusted) or Thread B (Debtor, untrusted)."""
from src import tenants
from src.ingest.debtor_flow import handle_debtor
from src import limits
from src.ingest.onboarding import _code_from, handle_stranger
from src.ingest.owner_flow import handle_owner
from src.loop.owner_channel import send_to_owner
from src.loop.whatsapp import text_message
from src.ingest.payload import InboundMessage
from src.loop.owner_channel import resend_undelivered


async def _claim(pool, wamid: str) -> bool:
    """True the first time a WhatsApp message id is seen (retries and replays are dropped)."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO processed_messages (wamid) VALUES (%s) ON CONFLICT DO NOTHING RETURNING wamid", (wamid,))
        return await cur.fetchone() is not None


async def _is_stranger(pool, msg: InboundMessage) -> bool:
    """Not a shop owner. A customer of some shop stays in the customer flow, unless they are joining with a code."""
    async with pool.connection() as conn:
        sess = await (await conn.execute("SELECT 1 FROM onboarding_sessions WHERE phone = %s AND state <> 'DONE'", (msg.sender,))).fetchone()
        if sess:
            return True
        known = await (await conn.execute("SELECT 1 FROM parties WHERE whatsapp_phone = %s LIMIT 1", (msg.sender,))).fetchone()
    if not known:
        return True
    return _code_from(msg.text or "") is not None and bool(msg.text) and len((msg.text or "").split()) <= 3 and \
        (msg.text or "").strip().lower().startswith(("join", "code", "invite"))


async def dispatch(app, msg: InboundMessage) -> None:
    pool = app.state.pool
    if not await _claim(pool, msg.wamid):
        return
    try:
        owner = await tenants.by_owner_phone(pool, msg.sender)
        if owner:
            await tenants.touch_owner_inbound(pool, owner.id, msg.channel)   # remembers the channel; WhatsApp also opens the 24h window
            owner = await tenants.by_id(pool, owner.id)
            if msg.channel == "whatsapp" and not msg.button_id:
                await limits.record(pool, owner.id, limits.kind_of(msg))
            if (stop := await limits.check(app, owner, msg)):
                await send_to_owner(app, owner, text_message(owner.owner_phone, stop), kind="owner_text",
                                    reply_channel=msg.channel, number_id=msg.number_id or None)
            else:
                await handle_owner(app, owner, msg)
            await resend_undelivered(app, owner, channel="whatsapp")
        elif msg.channel == "whatsapp" and await _is_stranger(pool, msg):
            await handle_stranger(app, msg)                 # a new owner (or someone to put on the waitlist): never dropped
        else:
            await handle_debtor(app, msg)
    except Exception:
        async with pool.connection() as conn:  # let a redelivery try again
            await conn.execute("DELETE FROM processed_messages WHERE wamid = %s", (msg.wamid,))
        raise
