import asyncio
import logging
from datetime import datetime, timedelta, timezone

from src.bridge.outbox import drain
from src.loop.digest import build_digest
from src.loop.reminders import claim_due, draft_reminder, reminder_card, state_card
from src.loop.whatsapp import text_message

log = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))


def owner_phone(settings, tenant_id: str) -> str | None:
    return next((p for p, t in settings.owner_tenants.items() if t == tenant_id), None)


async def tick(app, now: datetime) -> int:
    """One scheduler pass: claim due obligations, ask the owner about each."""
    s = app.state.settings
    sent = 0
    try:
        await drain(app.state.pool, app.state.continuum, now)
    except Exception:
        log.exception("outbox drain failed")  # must never block reminders
    for o in await claim_due(app.state.pool, now):
        phone = owner_phone(s, str(o["tenant_id"]))
        if not phone:
            continue
        draft = draft_reminder(o, s.tenant_upi.get(str(o["tenant_id"])))
        card = reminder_card(phone, o, draft, await state_card(app.state.continuum, o))
        await app.state.wa.send(card)
        sent += 1
    return sent


async def send_digests(app, today) -> None:
    s = app.state.settings
    for phone, tenant in s.owner_tenants.items():
        await app.state.wa.send(text_message(phone, await build_digest(app.state.pool, tenant, today)))


async def run_forever(app) -> None:
    last_digest = None
    while True:
        try:
            now = datetime.now(timezone.utc)
            await tick(app, now)
            local = now.astimezone(IST)
            if local.hour >= app.state.settings.digest_hour_ist and last_digest != local.date():
                last_digest = local.date()  # in-memory: a restart after 9am may resend once
                await send_digests(app, local.date())
        except Exception:
            log.exception("scheduler tick failed")
        await asyncio.sleep(app.state.settings.tick_seconds)
