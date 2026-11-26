"""Runs the agent's scheduled work: follow-ups it planned and approved sends that
were deferred out of quiet hours. Never runs during quiet hours."""
import logging
from datetime import datetime, timedelta

from src import clock, limits, tenants
from src.agent.policy import SessionCtx
from src.loop.owner_channel import send_to_owner
from src.loop.whatsapp import text_message

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
STALE_RUNNING = timedelta(minutes=15)
RETRY_AFTER = timedelta(minutes=10)


async def run_due(app, now: datetime) -> int:
    """Claim and run due tasks (overlap-safe); returns how many finished."""
    if clock.is_quiet(app.state.settings, now):
        return 0
    async with app.state.pool.connection() as conn:
        rows = await (await conn.execute(
            """UPDATE agent_tasks SET status = 'RUNNING', started_at = %(now)s, attempts = attempts + 1
                WHERE id IN (SELECT id FROM agent_tasks
                              WHERE run_at <= %(now)s AND (status = 'PENDING' OR (status = 'RUNNING' AND started_at < %(stale)s))
                              ORDER BY run_at LIMIT 5 FOR UPDATE SKIP LOCKED)
            RETURNING *""", {"now": now, "stale": now - STALE_RUNNING})).fetchall()
    done = 0
    for t in rows:
        try:
            await _run_one(app, t)
        except Exception as e:
            log.exception("agent task %s failed", t["id"])
            final = t["attempts"] >= MAX_ATTEMPTS
            async with app.state.pool.connection() as conn:
                await conn.execute(
                    "UPDATE agent_tasks SET status = %s, last_error = %s, run_at = %s WHERE id = %s",
                    ("FAILED" if final else "PENDING", f"{type(e).__name__}: {e}"[:300], now + RETRY_AFTER, t["id"]))
            if final:
                await _chain(app, t, now)       # a failed run must not end a monthly reminder
                try:
                    await _say_it_failed(app, t)
                except Exception:
                    log.exception("could not tell the owner that task %s failed", t["id"])
            continue
        async with app.state.pool.connection() as conn:
            await conn.execute("UPDATE agent_tasks SET status = 'DONE' WHERE id = %s", (t["id"],))
        await _chain(app, t, now)
        done += 1
    return done


async def _chain(app, t: dict, now: datetime) -> None:
    """A repeating reminder (daily, weekly, monthly, yearly) schedules its own next time once it has run. Cancelling the pending
    one therefore ends the series."""
    import json
    from src.agent import extras
    rec = (t["payload"] or {}).get("recur")
    if not rec:
        return
    nxt = extras.next_run(t["run_at"], rec, now)
    if nxt is None:
        return
    async with app.state.pool.connection() as conn:
        await conn.execute("INSERT INTO agent_tasks (tenant_id, run_at, kind, payload, created_by_session) VALUES (%s, %s, 'followup', %s, %s)",
                           (t["tenant_id"], nxt, json.dumps(t["payload"]), t["created_by_session"]))


def _asked_for(payload: dict) -> str:
    """What the owner asked for, in their own words where they were kept (a retry set by an earlier run kept ours instead)."""
    said = str(payload.get("owner_said") or "")
    return (said if said and not said.startswith("A follow-up you scheduled") else str(payload.get("instruction") or ""))[:500]


async def _tell(app, tenant, text: str) -> None:
    await send_to_owner(app, tenant, text_message(tenant.owner_phone, text), kind="owner_note")


async def _say_it_failed(app, t: dict) -> None:
    """The last try failed too. Something the owner asked for is never just dropped: they get it in plain words (no model)."""
    tenant = await tenants.by_id(app.state.pool, str(t["tenant_id"]))
    if tenant is None:
        return
    if t["kind"] == "followup":
        await _tell(app, tenant, "I couldn't run something you scheduled. So that it isn't missed, this is what you asked for:\n"
                                 f"“{_asked_for(t['payload'])}”")
    else:
        who = t["payload"].get("label") or "your customer"
        await _tell(app, tenant, f"I couldn't send the reminder you approved for {who}. Please send it yourself, or ask me again.")


async def _out_of_allowance(app, tenant) -> bool:
    if tenant.limits_exempt:
        return False
    used = (await limits.usage(app.state.pool, tenant.id))["tokens"]
    return used >= limits.allowance(app.state.settings, tenant)["tokens"]


async def _run_one(app, t: dict) -> None:
    from src.agent import actions
    from src.agent_loop import start_session
    tenant = await tenants.by_id(app.state.pool, str(t["tenant_id"]))
    if tenant is None:
        return
    payload = t["payload"]
    if t["kind"] == "followup" and await _out_of_allowance(app, tenant):      # the reminder still arrives, without the model
        await _tell(app, tenant, f"Your reminder:\n“{_asked_for(payload)}”\n\n(Today's AI allowance is used up, so this is "
                                 "only what you asked for; I haven't checked or done anything else.)")
        return
    if t["kind"] == "followup":
        said = (f"The owner's exact words when they asked (trust these over the summary): \"{payload['owner_said']}\"\n"
                if payload.get("owner_said") else "")
        content = (f"A follow-up you scheduled has come due. What you asked yourself to do:\n{payload['instruction']}\n{said}"
                   f"It is now {clock.describe()}. If the owner should know the outcome, message them with "
                   f"send_whatsapp_message(to='owner'). If it cannot be done right now (e.g. their PC is off), tell the owner "
                   f"ONCE and at most retry once later; do not message them again about the same failure.")
        sid = await start_session(app, tenant=tenant, trust="SYSTEM", trigger="scheduled_followup", content=content,
                                  background=False)
        async with app.state.pool.connection() as conn:
            status = (await (await conn.execute("SELECT status FROM agent_sessions WHERE id = %s", (sid,))).fetchone())["status"]
        if status == "FAILED":                       # the model or a tool crashed: let the task retry
            raise RuntimeError("follow-up session failed")
        return
    ctx = SessionCtx(id=str(t["created_by_session"] or t["id"]), tenant_id=str(t["tenant_id"]), trust="TRUSTED")
    result = await actions.deliver_message(app, ctx, tenant, payload)
    if result.get("own_link"):      # own-WhatsApp mode: deliver_message already sent the owner the link to tap
        return
    who = payload.get("label") or "the customer"
    note = (f"Sent the reminder to {who} (approved earlier, delivered now that the shop is open)." if result.get("sent")
            else f"I could not send the approved reminder to {who}: {result.get('error', 'unknown reason')}.")
    await send_to_owner(app, tenant, text_message(tenant.owner_phone, note), kind="owner_note")
