import asyncio
import logging
from datetime import datetime, timedelta, timezone

from src import tenants
from src.bridge.outbox import drain
from src.loop.digest import build_digest
from src.loop.reminders import claim_due, draft_reminder, release_claim, reminder_card, state_card
from src.loop.owner_channel import send_to_owner
from src.loop.whatsapp import text_message

log = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))


async def tick(app, now: datetime) -> int:
    """One scheduler pass: claim due obligations, ask the owner about each."""
    pool = app.state.pool
    sent, known = 0, {}
    try:
        await drain(pool, app.state.continuum, now)
    except Exception:
        log.exception("outbox drain failed")  # must never block reminders
    for o in await claim_due(pool, now):
        tid = str(o["tenant_id"])
        if tid not in known:
            known[tid] = await tenants.by_id(pool, tid)
        t = known[tid]
        if t is None:
            continue
        card = reminder_card(t.owner_phone, o, draft_reminder(o, t.upi, app.state.settings.public_base_url), await state_card(app.state.continuum, o))
        try:
            await send_to_owner(app, t, card, kind="owner_reminder_card", obligation_id=o["id"])
        except Exception:
            log.exception("owner card send failed for obligation %s; will retry", o["id"])
            await release_claim(pool, o, now)
            continue
        sent += 1
    return sent


async def send_digests(app, local: datetime) -> int:
    """At most one digest per tenant per IST day, durable across restarts."""
    pool, sent = app.state.pool, 0
    for t in await tenants.all_tenants(pool):
        async with pool.connection() as conn:
            cur = await conn.execute(
                "INSERT INTO digest_log (tenant_id, digest_date) VALUES (%s, %s) ON CONFLICT DO NOTHING RETURNING 1",
                (t.id, local.date()))
            first = await cur.fetchone() is not None
        if not first:
            continue
        try:
            text = await build_digest(pool, t.id, local.date())
            await send_to_owner(app, t, text_message(t.owner_phone, text), kind="owner_digest")
            sent += 1
        except Exception:
            log.exception("digest failed for tenant %s", t.id)
            async with pool.connection() as conn:  # allow a retry on the next tick
                await conn.execute("DELETE FROM digest_log WHERE tenant_id = %s AND digest_date = %s", (t.id, local.date()))
    return sent


async def run_forever(app) -> None:
    while True:
        try:
            now = datetime.now(timezone.utc)
            await tick(app, now)
            local = now.astimezone(IST)
            if local.hour >= app.state.settings.digest_hour_ist:
                await send_digests(app, local)
        except Exception:
            log.exception("scheduler tick failed")
        await asyncio.sleep(app.state.settings.tick_seconds)
