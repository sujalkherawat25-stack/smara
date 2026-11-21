"""Reminders that come back by themselves (every month on the 11th, every Monday...) and may start up to a year ahead.
An accountant lives on dates like these; one-off reminders limited to 60 days could not serve them."""
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from test_agent_db import ScriptLLM, env, one, say  # noqa: F401
from test_agent_v2_db import NIGHT

from src import clock
from src.agent import extras
from src.agent.policy import SessionCtx
from src.loop import tasks
from src.loop.scheduler import IST


def ist(y, m, d, h=9, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=IST)


# --------------------------------------------------------------------------------------------------------------- the rule
def test_monthly_keeps_its_day_and_rolls_over_the_year():
    r = {"every": "monthly", "day": 11}
    assert extras.next_run(ist(2026, 12, 11), r, ist(2026, 12, 11, 9, 1)) == ist(2027, 1, 11)
    assert extras.next_run(ist(2026, 11, 11), r, ist(2026, 11, 11, 9, 1)) == ist(2026, 12, 11)


def test_monthly_on_the_31st_uses_the_last_day_of_shorter_months_then_comes_back():
    r = {"every": "monthly", "day": 31}
    feb = extras.next_run(ist(2027, 1, 31), r, ist(2027, 1, 31, 10))
    assert feb == ist(2027, 2, 28)
    assert extras.next_run(feb, r, feb) == ist(2027, 3, 31)                         # the day is remembered, not lost in February


def test_daily_weekly_yearly_and_leap_day():
    assert extras.next_run(ist(2026, 11, 21), {"every": "daily"}, ist(2026, 11, 21, 10)) == ist(2026, 11, 22)
    assert extras.next_run(ist(2026, 11, 21), {"every": "weekly"}, ist(2026, 11, 21, 10)) == ist(2026, 11, 28)
    assert extras.next_run(ist(2027, 3, 15), {"every": "yearly", "day": 15}, ist(2027, 3, 15, 10)) == ist(2028, 3, 15)
    assert extras.next_run(ist(2028, 2, 29), {"every": "yearly", "day": 29}, ist(2028, 3, 1)) == ist(2029, 2, 28)


def test_after_a_long_outage_it_skips_to_the_next_future_date_not_a_burst():
    r = {"every": "monthly", "day": 11}
    assert extras.next_run(ist(2026, 11, 11), r, ist(2027, 2, 20)) == ist(2027, 3, 11)


def test_it_stops_after_its_last_date():
    r = {"every": "monthly", "day": 11, "until": "2027-01-31"}
    assert extras.next_run(ist(2026, 12, 11), r, ist(2026, 12, 11, 9, 1)) == ist(2027, 1, 11)
    assert extras.next_run(ist(2027, 1, 11), r, ist(2027, 1, 11, 9, 1)) is None
    assert extras.next_run(ist(2026, 12, 11), {"every": "fortnightly"}, ist(2026, 12, 11, 9, 1)) is None


# ------------------------------------------------------------------------------------------------------------- end to end
async def test_a_monthly_reminder_schedules_its_own_next_time_and_cancelling_ends_it(env, monkeypatch):
    env.app.state.settings = NIGHT
    monkeypatch.setattr(clock, "now", lambda: ist(2026, 11, 21, 10))
    ctx = SessionCtx(str(uuid4()), env.t.id, "TRUSTED")
    r = await extras.schedule_followup(env.app, ctx, {"instruction": "GSTR-1 ka data CA ko bhejna", "at_ist": "2026-12-11 09:00", "repeat": "monthly"})
    assert r["will_run_at_ist"] == "2026-12-11 09:00" and r["repeats"] == "monthly until cancelled" and r["then_next"] == "2027-01-11 09:00"
    assert (await extras.list_followups(env.app, ctx, {}))["pending"][0]["repeats"] == "monthly"

    env.app.state.llm = ScriptLLM(say("done"), say("done"))
    monkeypatch.setattr(clock, "now", lambda: ist(2026, 12, 11, 9, 1))
    assert await tasks.run_due(env.app, ist(2026, 12, 11, 9, 1)) == 1
    async with env.pool.connection() as c:
        rows = await (await c.execute("select status, run_at, payload from agent_tasks order by run_at")).fetchall()
    assert [x["status"] for x in rows] == ["DONE", "PENDING"]
    assert rows[1]["run_at"] == ist(2027, 1, 11) and rows[1]["payload"]["recur"]["day"] == 11
    assert rows[1]["payload"]["instruction"] == "GSTR-1 ka data CA ko bhejna"

    nxt = await one(env.pool, "select id from agent_tasks where status = 'PENDING'")
    assert (await extras.cancel_followup(env.app, ctx, {"task_id": str(nxt["id"])})) == {"cancelled": True}
    monkeypatch.setattr(clock, "now", lambda: ist(2027, 1, 11, 9, 1))
    assert await tasks.run_due(env.app, ist(2027, 1, 11, 9, 1)) == 0                  # nothing left: the series is over
    async with env.pool.connection() as c:
        n = (await (await c.execute("select count(*) n from agent_tasks where status = 'PENDING'")).fetchone())["n"]
    assert n == 0


async def test_a_run_that_fails_for_good_still_keeps_the_monthly_series_alive(env, monkeypatch):
    env.app.state.settings = NIGHT
    monkeypatch.setattr(clock, "now", lambda: ist(2026, 11, 21, 10))
    ctx = SessionCtx(str(uuid4()), env.t.id, "TRUSTED")
    await extras.schedule_followup(env.app, ctx, {"instruction": "TDS jama karna", "at_ist": "2026-12-07 09:00", "repeat": "monthly"})
    env.app.state.llm = ScriptLLM(*[RuntimeError("down")] * 12)
    for i in range(4):
        await tasks.run_due(env.app, ist(2026, 12, 7, 9, 1) + timedelta(minutes=11 * (i + 1)))
    async with env.pool.connection() as c:
        rows = await (await c.execute("select status, run_at from agent_tasks order by run_at")).fetchall()
    assert any(r["status"] == "PENDING" and r["run_at"] > ist(2026, 12, 31) for r in rows), rows


async def test_limits_and_bad_input(env, monkeypatch):
    env.app.state.settings = NIGHT
    monkeypatch.setattr(clock, "now", lambda: ist(2026, 11, 21, 10))
    ctx = SessionCtx(str(uuid4()), env.t.id, "TRUSTED")
    ok = await extras.schedule_followup(env.app, ctx, {"instruction": "advance tax", "at_ist": "2027-03-15 09:00"})
    assert "task_id" in ok                                                             # 15 March is 114 days out: allowed now
    for bad in ({"instruction": "x", "at_ist": "2028-05-01 09:00"},                    # more than 400 days
                {"instruction": "x", "at_ist": "2026-12-01 09:00", "repeat": "fortnightly"},
                {"instruction": "x", "at_ist": "2026-12-01 09:00", "repeat": "monthly", "until_ist": "2026-11-01"},
                {"instruction": "x", "at_ist": "2026-12-01 09:00", "repeat": "monthly", "until_ist": "next march"}):
        assert "error" in await extras.schedule_followup(env.app, ctx, bad), bad
    for i in range(extras.MAX_SERIES):
        assert "task_id" in await extras.schedule_followup(env.app, ctx, {"instruction": f"r{i}", "at_ist": "2026-12-02 09:00", "repeat": "weekly"})
    over = await extras.schedule_followup(env.app, ctx, {"instruction": "one too many", "at_ist": "2026-12-02 09:00", "repeat": "daily"})
    assert "error" in over and "repeating" in over["error"]
    assert "task_id" in await extras.schedule_followup(env.app, ctx, {"instruction": "a plain one-off still works", "at_ist": "2026-12-02 09:00"})


async def test_a_night_time_repeat_moves_to_the_morning_and_stays_there(env, monkeypatch):
    env.app.state.settings = NIGHT
    monkeypatch.setattr(clock, "now", lambda: ist(2026, 11, 21, 10))
    ctx = SessionCtx(str(uuid4()), env.t.id, "TRUSTED")
    r = await extras.schedule_followup(env.app, ctx, {"instruction": "shutter band karna", "at_ist": "2026-11-22 23:00", "repeat": "daily"})
    assert r["will_run_at_ist"] == "2026-11-23 08:00" and r["moved_out_of_quiet_hours"] is True and r["then_next"] == "2026-11-24 08:00"
