"""A scheduled job that keeps failing (the owner's PC was off) retried every 15-35 minutes from 8 AM to 11 AM on 15 and
16 Nov, messaging the owner each time. Follow-up runs may now re-schedule themselves soon only twice per 12 hours."""
from datetime import datetime
from uuid import uuid4

from test_agent_db import env  # noqa: F401

from src import clock
from src.agent import extras
from src.agent.policy import SessionCtx
from src.config import Settings
from src.loop.scheduler import IST


async def _session(env, trigger: str) -> SessionCtx:
    sid = str(uuid4())
    async with env.pool.connection() as conn:
        await conn.execute("INSERT INTO agent_sessions (id, tenant_id, trust, trigger, messages) VALUES (%s, %s, 'SYSTEM', %s, '[]')",
                           (sid, env.t.id, trigger))
    return SessionCtx(sid, env.t.id, "SYSTEM")


async def test_followup_runs_get_two_quick_retries_then_must_stop(env, monkeypatch):
    env.app.state.settings = Settings(quiet_start_hour=21, quiet_end_hour=8)
    monkeypatch.setattr(clock, "now", lambda: datetime(2026, 11, 4, 9, 0, tzinfo=IST))
    outcomes = []
    for i in range(4):
        ctx = await _session(env, "scheduled_followup")
        outcomes.append(await extras.schedule_followup(env.app, ctx, {"instruction": f"retry digest {i}", "in_minutes": 30}))
    assert ["error" in o for o in outcomes] == [False, False, True, True]
    assert "retry limit" in outcomes[2]["error"] and "ONCE" not in outcomes[2]["error"]
    ctx = await _session(env, "scheduled_followup")             # the next day's run of a daily job is still allowed
    assert "task_id" in await extras.schedule_followup(env.app, ctx, {"instruction": "tomorrow's digest", "in_minutes": 23 * 60})


async def test_the_owner_asking_is_never_limited_by_the_retry_cap(env, monkeypatch):
    env.app.state.settings = Settings(quiet_start_hour=21, quiet_end_hour=8)
    monkeypatch.setattr(clock, "now", lambda: datetime(2026, 11, 4, 9, 0, tzinfo=IST))
    for i in range(3):
        await extras.schedule_followup(env.app, await _session(env, "scheduled_followup"), {"instruction": f"r{i}", "in_minutes": 20})
    owner_ctx = await _session(env, "owner_message")
    for i in range(3):
        assert "task_id" in await extras.schedule_followup(env.app, owner_ctx, {"instruction": f"remind me {i}", "in_minutes": 15 + i})
