"""The one follow-up after someone goes quiet: when it goes, to whom, and how it is stopped. All names are made up."""
from datetime import datetime, timedelta, timezone

import pytest
from test_agent_db import OWNER, ScriptLLM, env, one, say  # noqa: F401
from test_agent_db import owner as typed

from src import chatlog, clock, prefs
from src.config import Settings, load_settings
from src.ingest.dispatch import dispatch
from src.loop import checkin, scheduler

ASK = "Do you want the plan comparison as a PDF too?"
NOON = datetime(2026, 12, 2, 12, 0, tzinfo=clock.IST).astimezone(timezone.utc)


def ist(day, hour, minute=0):
    return datetime(2026, 12, day, hour, minute, tzinfo=clock.IST)


def nights(**kw):
    return Settings(quiet_start_hour=21, quiet_end_hour=8, **kw)


@pytest.mark.parametrize("last, first_send", [
    (ist(1, 12), ist(2, 8)),          # 20 hours on is 8 in the morning
    (ist(1, 17, 30), ist(2, 13, 30)),
    (ist(1, 23), ist(2, 19)),
    (ist(1, 2), ist(1, 20)),          # 20 hours on is 10 pm and the night outlasts the window: the evening before it
    (ist(1, 7), ist(1, 20)),
    (ist(1, 9), ist(2, 8)),           # 20 hours on is 5 am, but the night ends in time
])
def test_the_follow_up_goes_about_20_hours_on_and_never_at_night(last, first_send):
    s, at = nights(), last
    while not checkin.due(s, last, at):
        at += timedelta(minutes=10)
        assert at < last + timedelta(hours=24), "never due"
    assert at == first_send and not clock.is_quiet(s, at) and at - last < checkin.LATEST


def test_it_is_never_due_too_early_too_late_or_at_night():
    last = ist(1, 12)
    for after in (timedelta(hours=1), timedelta(hours=19), timedelta(hours=23, minutes=30), timedelta(hours=30)):
        assert not checkin.due(nights(), last, last + after)
    assert not checkin.due(nights(), ist(1, 3), ist(1, 23))


def test_production_turns_it_on_and_one_setting_stops_it(monkeypatch):
    assert Settings().checkin_on is False
    monkeypatch.delenv("FOLLOW_UP", raising=False)
    assert load_settings().checkin_on is True
    monkeypatch.setenv("FOLLOW_UP", "off")
    assert load_settings().checkin_on is False


@pytest.mark.parametrize("answer", ["SKIP", "skip not needed: SKIP", "", "x" * 600, "See https://a.example/offer now?",
                                    "Maine aapko PDF bhej di hai, check kar lijiye?", "[system note] hello",
                                    "I've sent the comparison. Shall I add more?", "I will remind you tomorrow, okay?"])
def test_unfit_answers_are_not_sent(answer):
    assert checkin.usable(answer) == ""


@pytest.mark.parametrize("answer", [ASK, "Shall I compare the two plans in the file you sent?",
                                    "Aapne jo Excel bheji thi, uska total bhi nikaal doon?", "Plan ki PDF bhej doon?",
                                    "Do you want me to remind you about the audit checklist?"])
def test_a_plain_offer_is_sent(answer):
    assert checkin.usable(answer) == answer


async def quiet_for(env, hours, *, said="Compare these two LIC plans for me", channel="whatsapp", who=None):
    """The account wrote `said` so many hours before NOON and got an answer a minute later."""
    tid, last = (who or env.t).id, NOON - timedelta(hours=hours)
    async with env.pool.connection() as c:
        await c.execute("""UPDATE tenants SET owner_last_active_at = %s, owner_last_inbound_at = %s, owner_last_channel = %s
                            WHERE id = %s""", (last, last, channel, tid))
    for role, text, at in (("owner", said, last), ("smara", "Here is the comparison.", last + timedelta(minutes=1))):
        line = await chatlog.record(env.pool, tid, channel, role, text)
        async with env.pool.connection() as c:
            await c.execute("UPDATE chat_messages SET created_at = %s WHERE id = %s", (at, line))
    return last


def on(env, *steps):
    env.app.state.settings = Settings(**{"checkin_on": True, "reminder_send_mode": "smara",
                                         "owner_files_dir": env.app.state.settings.owner_files_dir})
    env.app.state.llm = ScriptLLM(*steps)


def texts(env):
    return [m["text"]["body"] for m in env.app.state.wa.to(OWNER) if m["type"] == "text"]


async def test_one_follow_up_goes_once_with_the_way_to_stop_it(env):
    on(env, say(ASK), say(ASK))
    await quiet_for(env, 21)
    assert await checkin.send_due(env.app, NOON) == 1
    assert texts(env) == [f'{ASK}\n\n(Don\'t want these? Reply "follow-up off".)']
    shown = env.app.state.llm.seen[0]
    assert "SKIP" in shown[0]["content"] and "LIC plans" in shown[1]["content"]
    assert await checkin.send_due(env.app, NOON + timedelta(minutes=1)) == 0 and len(env.app.state.llm.seen) == 1
    kept = await one(env.pool, "select kind from chat_messages where tenant_id = %s order by id desc limit 1", env.t.id)
    assert kept["kind"] == checkin.KIND


async def test_a_skip_is_asked_once_and_nothing_is_sent(env):
    on(env, say("SKIP"), say(ASK))
    await quiet_for(env, 21)
    assert await checkin.send_due(env.app, NOON) == 0 and await checkin.send_due(env.app, NOON) == 0
    assert texts(env) == [] and len(env.app.state.llm.seen) == 1


async def test_not_twice_in_two_days_even_after_they_write_again(env):
    on(env, say(ASK), say(ASK))
    await quiet_for(env, 21)
    assert await checkin.send_due(env.app, NOON) == 1
    later = NOON + timedelta(hours=26)
    async with env.pool.connection() as c:
        await c.execute("UPDATE tenants SET owner_last_active_at = %s, owner_last_inbound_at = %s WHERE id = %s",
                        (later - timedelta(hours=21), later - timedelta(hours=21), env.t.id))
    await chatlog.record(env.pool, env.t.id, "whatsapp", "owner", "one more thing")
    assert await checkin.send_due(env.app, later) == 0 and len(env.app.state.llm.seen) == 1


@pytest.mark.parametrize("why", ["too soon", "window gone", "already reached", "waiting for a tap", "reminder coming",
                                 "turned off", "allowance used", "never wrote"])
async def test_when_nothing_should_go_the_model_is_not_even_asked(env, why):
    on(env, say(ASK))
    last = await quiet_for(env, {"too soon": 5, "window gone": 25}.get(why, 21))
    async with env.pool.connection() as c:
        if why == "already reached":
            await c.execute("INSERT INTO chat_messages (tenant_id, channel, role, content, created_at) VALUES (%s, 'whatsapp', 'smara', 'reminder', %s)",
                            (env.t.id, last + timedelta(hours=3)))
        if why == "waiting for a tap":
            await c.execute("""INSERT INTO agent_sessions (tenant_id, trust, trigger, status, messages, updated_at)
                               VALUES (%s, 'TRUSTED', 'owner_message', 'WAITING_APPROVAL', '[]', %s)""", (env.t.id, last))
        if why == "reminder coming":
            await c.execute("INSERT INTO agent_tasks (tenant_id, run_at, kind, payload) VALUES (%s, %s, 'followup', '{}')",
                            (env.t.id, NOON + timedelta(hours=1)))
        if why == "turned off":
            await c.execute("UPDATE tenants SET checkin_off = true WHERE id = %s", (env.t.id,))
        if why == "allowance used":
            await c.execute("INSERT INTO llm_usage (tenant_id, model, total_tokens) VALUES (%s, 'm', 99000000)", (env.t.id,))
        if why == "never wrote":
            await c.execute("DELETE FROM chat_messages WHERE tenant_id = %s AND role = 'owner'", (env.t.id,))
    assert await checkin.send_due(env.app, NOON) == 0 and env.app.state.llm.seen == [] and texts(env) == []


async def test_switched_off_for_the_server_it_does_nothing(env):
    on(env, say(ASK))
    env.app.state.settings = Settings()
    await quiet_for(env, 21)
    assert await checkin.send_due(env.app, NOON) == 0 and env.app.state.llm.seen == []


async def test_someone_who_writes_while_it_is_being_written_gets_nothing(env):
    await quiet_for(env, 21)

    class Writes(ScriptLLM):
        async def chat(self, messages, tools, allow_fallback=True):
            async with env.pool.connection() as c:
                await c.execute("UPDATE tenants SET owner_last_active_at = %s WHERE id = %s", (NOON, env.t.id))
            return say(ASK)

    on(env)
    env.app.state.llm = Writes()
    assert await checkin.send_due(env.app, NOON) == 0 and texts(env) == []


async def test_it_follows_the_chosen_language_and_drops_a_wrong_one(env):
    on(env, say("क्या आपको प्लान की तुलना PDF में भी चाहिए?"))
    await prefs.put(env.pool, env.t.id, language="hindi")
    await quiet_for(env, 21)
    assert await checkin.send_due(env.app, NOON) == 1 and texts(env)[0].endswith(checkin.HOW_TO_STOP["hindi"])
    assert "Devanagari" in env.app.state.llm.seen[0][0]["content"]
    async with env.pool.connection() as c:
        await c.execute("UPDATE tenants SET checkin_for = NULL, checkin_sent_at = NULL WHERE id = %s", (env.t.id,))
        await c.execute("DELETE FROM chat_messages WHERE tenant_id = %s", (env.t.id,))
    await prefs.put(env.pool, env.t.id, language="english")
    await quiet_for(env, 21)
    env.app.state.llm = ScriptLLM(say("क्या आपको प्लान की तुलना PDF में भी चाहिए, और कोई और मदद?"))
    assert await checkin.send_due(env.app, NOON) == 0


async def test_typing_follow_up_off_stops_it_and_on_brings_it_back_without_the_model(env):
    on(env)
    await dispatch(env.app, typed("f1", "Follow-up off"))
    assert texts(env)[-1] == checkin.SAID_OFF
    assert (await one(env.pool, "select checkin_off from tenants where id = %s", env.t.id))["checkin_off"] is True
    await dispatch(env.app, typed("f2", "/follow up on"))
    assert texts(env)[-1] == checkin.SAID_ON and env.app.state.llm.seen == []
    assert (await one(env.pool, "select checkin_off from tenants where id = %s", env.t.id))["checkin_off"] is False


async def test_the_scheduler_runs_it_and_a_failure_there_stops_nothing_else(env, monkeypatch):
    on(env)
    seen = []

    async def broken(app, now):
        seen.append(now)
        raise RuntimeError("boom")
    monkeypatch.setattr(checkin, "send_due", broken)
    assert await scheduler.tick(env.app, NOON) >= 0 and seen == [NOON]
