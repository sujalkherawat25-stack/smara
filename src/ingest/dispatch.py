"""Route one inbound message to Thread A (Owner, trusted) or Thread B (Debtor, untrusted)."""
from src import tenants
from src.ingest.debtor_flow import handle_debtor
from src.ingest.owner_flow import handle_owner
from src.ingest.payload import InboundMessage


async def _claim(pool, wamid: str) -> bool:
    """True the first time a WhatsApp message id is seen (retries and replays are dropped)."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO processed_messages (wamid) VALUES (%s) ON CONFLICT DO NOTHING RETURNING wamid", (wamid,))
        return await cur.fetchone() is not None


async def dispatch(app, msg: InboundMessage) -> None:
    pool = app.state.pool
    if not await _claim(pool, msg.wamid):
        return
    try:
        owner = await tenants.by_owner_phone(pool, msg.sender)
        if owner:
            await handle_owner(app, owner, msg)
        else:
            await handle_debtor(app, msg)
    except Exception:
        async with pool.connection() as conn:  # let a redelivery try again
            await conn.execute("DELETE FROM processed_messages WHERE wamid = %s", (msg.wamid,))
        raise
