import asyncio
import logging
from datetime import datetime, timedelta, timezone

from src import clock, health_watch, tenants
from src.bridge.outbox import drain
from src.ingest import owner_files
from src.loop import wa_link
from src.loop.digest import build_digest
from src.loop.reminders import (claim_due, draft_reminder, release_claim, reminder_card, reopen_unanswered,
                                stale_sync_note, state_card)
from src.loop.owner_channel import flush_held, send_to_owner
from src.loop.tasks import run_due
from src.loop.whatsapp import text_message

log = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))
CLAIM_PER_TICK = 50        # due invoices taken per scheduler tick (it ticks every minute)
AGENT_DRAFTS_PER_TICK = 6  # of those, how many the agent drafts; the others get the ready-made card
DRAFTS_AT_ONCE = 3         # agent drafts running at the same time


async def tick(app, now: datetime) -> int:
    """One scheduler pass: claim due obligations, ask the owner about each."""
    pool = app.state.pool
    sent, known = 0, {}
    try:
        await drain(pool, app.state.continuum, now)
    except Exception:
        log.exception("outbox drain failed")  # must never block reminders
    try:
        await wa_link.reconcile(app)      # a restarted sidecar: start the shops' own numbers again (also at night)
    except Exception:
        log.exception("link reconcile failed")
    try:                                  # files the owner deleted are removed for good 30 days after that (restorable before)
        await asyncio.to_thread(owner_files.purge_due, app.state.settings.owner_files_dir, now)
    except Exception:
        log.exception("owner file purge pass failed")
    try:                                  # the hourly error report to the Smara team (no tokens; holds itself at night)
        await health_watch.report_due(app, now)
    except Exception:
        log.warning("health report pass failed")
    if clock.is_quiet(app.state.settings, now):
        return 0          # nothing proactive at night: reminders wait, tasks wait, cards stay held
    try:
        await flush_held(app)
        await run_due(app, now)
    except Exception:
        log.exception("held-card flush / scheduled tasks failed")
    try:                                  # a lost or unanswered card must not park an invoice for ever
        await reopen_unanswered(pool, now)
    except Exception:
        log.exception("unanswered-approval sweep failed")
    claimed = await claim_due(pool, now, limit=CLAIM_PER_TICK)
    gate = asyncio.Semaphore(DRAFTS_AT_ONCE)
    results = await asyncio.gather(*(_remind(app, pool, now, o, i < AGENT_DRAFTS_PER_TICK, gate, known)
                                     for i, o in enumerate(claimed)), return_exceptions=True)
    for r in results:
        if isinstance(r, BaseException):
            log.error("reminder step failed", exc_info=r)
    return sum(1 for r in results if r is True)


async def _remind(app, pool, now, o: dict, agent_draft: bool, gate: asyncio.Semaphore, known: dict) -> bool:
    """One due invoice: ask the owner about it. At most AGENT_DRAFTS_PER_TICK of them per tick are drafted by the agent (it
    reads memory for promises and disputes, 4-10 model calls each, a few at a time). The rest, and any the agent could not
    draft, get the ready-made card at once: same amount, due date and UPI line, no model. So a morning with many shops'
    reminders due costs seconds of waiting, not a queue of model calls that holds up everything else the scheduler does."""
    tid = str(o["tenant_id"])
    if tid not in known:
        known[tid] = await tenants.by_id(pool, tid)
    t = known[tid]
    if t is None:
        return False
    if await wa_link.paused(pool, tid, app=app):    # the shop's own number is down: ask nothing until it is back
        await release_claim(pool, o, now)
        return False
    if agent_draft and getattr(getattr(app.state, "llm", None), "enabled", False):
        async with gate:
            if await _agent_reminder(app, t, o):
                return True
    context = await state_card(app.state.continuum, o)
    if o.get("source") == "TALLY":
        note = await stale_sync_note(pool, str(o["tenant_id"]), now)
        if note:
            context = f"{context}\n{note}" if context and "No relevant state" not in context else note
    card = reminder_card(t.owner_phone, o, draft_reminder(o, t.upi, app.state.settings.public_base_url),
                         context, mode=app.state.settings.reminder_send_mode)
    try:
        await send_to_owner(app, t, card, kind="owner_reminder_card", obligation_id=o["id"])
    except Exception:
        log.exception("owner card send failed for obligation %s; will retry", o["id"])
        await release_claim(pool, o, now)
        return False
    return True


async def _agent_reminder(app, tenant, o: dict) -> bool:
    """Let the agent read memory and draft the reminder; True if it is now waiting on the owner.

    False means the deterministic card should go out instead, so a due invoice never sits unasked."""
    from src.agent_loop import start_session
    from src.loop.upi import rupees, upi_text

    pay = upi_text(tenant.upi["vpa"], o["amount_paise"], o["invoice_ref"]) if tenant.upi else ""
    content = (f"Invoice {o['invoice_ref']} of {o['canonical_name']} (party_id={o['party_id']}, obligation_id={o['id']}) "
               f"for {rupees(o['amount_paise'])} was due {o['due_date']}; this is reminder #{o['attempt_count'] + 1}. "
               f"Check resolve_continuum_memory for promises or disputes first. Then draft ONE short, polite reminder "
               f"in Hinglish and call send_whatsapp_message with to=<party_id> and obligation_id. "
               f"Include this payment line exactly: {pay or '(no UPI configured)'}")
    try:
        sid = await start_session(app, tenant=tenant, trust="SYSTEM", trigger="cron_reminder", content=content,
                                  background=False)
    except Exception:
        log.exception("agent reminder failed for %s", o["id"])
        return False
    async with app.state.pool.connection() as conn:
        row = await (await conn.execute("SELECT status FROM agent_sessions WHERE id = %s", (sid,))).fetchone()
    return row["status"] == "WAITING_APPROVAL"


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


def digest_window_open(settings, now: datetime) -> bool:
    """The morning digest goes out between digest_hour and +3h IST, never at night and never late in the evening."""
    h = now.astimezone(IST).hour
    return settings.digest_hour_ist <= h < settings.digest_hour_ist + 3 and not clock.is_quiet(settings, now)


async def run_forever(app) -> None:
    while True:
        try:
            now = datetime.now(timezone.utc)
            local = now.astimezone(IST)
            if digest_window_open(app.state.settings, now):      # quick and once a day: before the reminders, never behind them
                await send_digests(app, local)
            await tick(app, now)
        except Exception:
            log.exception("scheduler tick failed")
        await asyncio.sleep(app.state.settings.tick_seconds)
