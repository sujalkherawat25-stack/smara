"""Promises Smara makes to people, checked end to end: daily limits and how an admin raises them, reminders WhatsApp refuses,
scheduled work that fails, deleting an account on WhatsApp, 'digest on', waitlist replies and messages Smara cannot open.
All names and numbers here are made up."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from test_agent_db import OWNER, ScriptLLM, call, env, one, say  # noqa: F401
from test_agent_db import owner as typed

from src import clock, limits, ops_alert, prefs, tenants
from src.agent_loop import unbacked_claim
from src.config import Settings
from src.ingest import admin_wa, legal, onboarding
from src.ingest.dispatch import dispatch
from src.ingest.payload import InboundMessage, StatusUpdate, parse_messages
from src.loop import owner_channel, review, scheduler, tasks
from src.loop.outbound import apply_status
from src.loop.whatsapp import text_message

ADMIN = "919000000077"
STRANGER = "919000000088"


@pytest.fixture(autouse=True)
def _fresh_alerts():
    ops_alert._last_sent.clear()
    onboarding._misses.clear()


def settings(env, **kw):
    env.app.state.settings = Settings(**{"reminder_send_mode": "smara", "owner_files_dir": env.app.state.settings.owner_files_dir,
                                         "admin_whatsapp_numbers": (ADMIN,), **kw})


def bodies(env, to=OWNER):
    out = []
    for m in env.app.state.wa.to(to):
        if m["type"] == "text":
            out.append(m["text"]["body"])
        elif m["type"] == "interactive":
            out.append(m["interactive"]["body"]["text"])
    return out


def buttons(env, to):
    return [b["reply"]["id"] for m in env.app.state.wa.to(to) if m["type"] == "interactive"
            for b in m["interactive"]["action"].get("buttons", [])]


async def sql(env, query, *args):
    async with env.pool.connection() as c:
        return await c.execute(query, args)


async def counted(env):
    return (await one(env.pool, "select count(*) n from owner_inbound_log where tenant_id = %s", env.t.id))["n"]


def from_admin(wamid, text="", button=None):
    return InboundMessage(wamid=wamid, sender=ADMIN, kind="interactive" if button else "text", text=text, button_id=button)


# ---------------------------------------------------------------- daily limits

async def test_a_stopped_message_is_not_counted_and_the_commands_it_names_still_work(env):
    settings(env, owner_daily_msgs=2)
    env.app.state.llm = ScriptLLM(say("one"), say("two"))
    for i in range(2):
        await dispatch(env.app, typed(f"a{i}", f"question {i}"))
    await dispatch(env.app, typed("a2", "third question"))
    stop = bodies(env)[-1]
    assert "reached today's limit" in stop and "IST" in stop and "morning" not in stop
    await dispatch(env.app, typed("a3", "again"))
    assert await counted(env) == 2 and len(env.app.state.llm.seen) == 2          # trying again does not push the wait further
    for i, (said, expect) in enumerate((("help", "Commands:"), ("files", "files"), ("digest off", "nahi bhejunga"),
                                        ("delete my data", "DELETE MY ACCOUNT"), ("unpair browser", "browser"))):
        await dispatch(env.app, typed(f"f{i}", said))
        assert expect in bodies(env)[-1], said
    assert await counted(env) == 2 and len(env.app.state.llm.seen) == 2          # none of them used the model


async def test_the_admin_is_told_once_and_one_tap_lets_the_person_carry_on(env):
    settings(env, owner_daily_msgs=1)
    env.app.state.llm = ScriptLLM(say("first answer"), say("second answer"))
    await dispatch(env.app, typed("b0", "first"))
    await dispatch(env.app, typed("b1", "second"))
    await dispatch(env.app, typed("b2", "third"))
    alerts = [t for t in bodies(env, ADMIN) if "limit poori" in t]
    assert len(alerts) == 1 and "Shop" in alerts[0] and "messages 1/1" in alerts[0]
    assert "told the Smara team" in bodies(env)[-1]
    tap = buttons(env, ADMIN)[-1]
    assert tap == f"adm:limit:{OWNER}:2:1"

    await dispatch(env.app, typed("b3", button=tap))                             # the person cannot raise their own limit
    assert (await tenants.by_id(env.pool, env.t.id)).limit_times == 1

    await dispatch(env.app, from_admin("b4", button=tap))
    raised = await tenants.by_id(env.pool, env.t.id)
    assert limits.times(raised) == 2 and raised.limit_until < datetime.now(timezone.utc) + timedelta(hours=25)
    assert "2x limit" in bodies(env, ADMIN)[-1] and "Unhe bata diya" in bodies(env, ADMIN)[-1]
    assert "limit has been raised" in bodies(env)[-1]
    await dispatch(env.app, typed("b5", "fourth"))
    assert bodies(env)[-1] == "second answer"


async def test_the_person_is_not_told_the_team_knows_when_nobody_could_be_told(env):
    settings(env, owner_daily_msgs=0, admin_whatsapp_numbers=())
    await dispatch(env.app, typed("c0", "hello"))
    assert "reached today's limit" in bodies(env)[-1] and "Smara team" not in bodies(env)[-1]
    assert (await tenants.by_id(env.pool, env.t.id)).limit_alerted_at is None    # so it is tried again next time


async def test_the_limit_command_shows_use_sets_an_allowance_and_refuses_unlimited(env):
    settings(env, owner_daily_msgs=10)
    await limits.record(env.pool, env.t.id, "text")
    await dispatch(env.app, from_admin("d0", f"/limit {OWNER}"))
    assert "messages 1/10" in bodies(env, ADMIN)[-1] and "normal limit" in bodies(env, ADMIN)[-1]
    assert buttons(env, ADMIN) == [f"adm:limit:{OWNER}:2:1", f"adm:limit:{OWNER}:2:30", f"adm:limit:{OWNER}:1:0"]
    for i, wrong in enumerate(("25", "0", "3 400", "unlimited")):
        await dispatch(env.app, from_admin(f"d1{i}", f"/limit {OWNER} {wrong}"))
        assert "Aise likhiye" in bodies(env, ADMIN)[-1], wrong
    await dispatch(env.app, from_admin("d2", f"/limit {OWNER} 3 7"))
    t = await tenants.by_id(env.pool, env.t.id)
    assert limits.allowance(env.app.state.settings, t)["msgs"] == 30 and "30 messages" in bodies(env, ADMIN)[-1]
    await dispatch(env.app, from_admin("d3", f"/limit {OWNER} normal"))
    assert limits.times(await tenants.by_id(env.pool, env.t.id)) == 1
    await dispatch(env.app, from_admin("d4", "/limit 919000000066"))
    assert "koi account nahi" in bodies(env, ADMIN)[-1]


def test_the_limit_command_reads_numbers_written_the_ways_people_write_them():
    read = admin_wa._limit_args
    assert read("9000000001") == ("919000000001", None, 1)
    assert read("+91 90000 00001") == ("919000000001", None, 1)
    assert read("+91 90000 00001 3 7") == ("919000000001", 3, 7)
    assert read("919000000001 2x 30d") == ("919000000001", 2, 30)
    assert read("9000000001 4") == ("919000000001", 4, 1)
    assert read("tg123456 normal") == ("tg123456", 1, 1)
    assert read("ramesh") is None and read("") is None


async def test_an_allowance_ends_by_itself(env):
    await sql(env, "update tenants set limit_times = 5, limit_until = now() - interval '1 minute' where id = %s", env.t.id)
    assert limits.times(await tenants.by_id(env.pool, env.t.id)) == 1


async def test_the_time_given_is_when_enough_has_aged_out(env):
    settings(env, owner_daily_msgs=2, owner_daily_tokens=100)
    for hours in (20, 10, 1):                                                    # three in the window, limit two
        await sql(env, "insert into owner_inbound_log (tenant_id, kind, created_at) values (%s, 'text', now() - make_interval(hours => %s))",
                  env.t.id, hours)
    hit = await limits.reached(env.pool, env.app.state.settings, env.t)
    wait = hit.free_at - datetime.now(timezone.utc)                              # two must go: the one sent 10 hours ago too
    assert hit.what == "msgs" and timedelta(hours=13, minutes=55) < wait < timedelta(hours=14, minutes=5)
    for hours, used in ((23, 60), (2, 150)):
        await sql(env, "insert into llm_usage (tenant_id, model, total_tokens, created_at) values (%s, 'm', %s, now() - make_interval(hours => %s))",
                  env.t.id, used, hours)
    hit = await limits.reached(env.pool, env.app.state.settings, env.t)
    wait = hit.free_at - datetime.now(timezone.utc)                              # the longest wait is the one reported
    assert hit.what == "tokens" and timedelta(hours=21, minutes=55) < wait < timedelta(hours=22, minutes=5)


def test_the_wait_is_written_as_a_clock_time_in_india():
    now = datetime(2026, 1, 5, 10, 0, tzinfo=clock.IST)
    assert limits.when_text(datetime(2026, 1, 5, 16, 11, 20, tzinfo=clock.IST), now) == "4:15 PM today"
    assert limits.when_text(datetime(2026, 1, 6, 0, 0, tzinfo=clock.IST), now) == "12:00 AM tomorrow"
    assert limits.when_text(datetime(2026, 1, 6, 9, 5, tzinfo=clock.IST), now, hinglish=True) == "kal 9:05 AM"


async def test_the_file_limit_stops_files_but_not_typing(env):
    settings(env, owner_daily_files=1)
    await limits.record(env.pool, env.t.id, "file")
    photo = InboundMessage(wamid="e1", sender=OWNER, kind="video", media_id="m1")
    await dispatch(env.app, photo)
    assert "limit of 1 files" in bodies(env)[-1] and "still type" in bodies(env)[-1]
    env.app.state.llm = ScriptLLM(say("typing works"))
    await dispatch(env.app, typed("e2", "are you there"))
    assert bodies(env)[-1] == "typing works"


# ---------------------------------------------------------------- reminders WhatsApp refuses

async def test_a_reminder_whatsapp_refuses_later_arrives_when_they_next_write(env):
    note = text_message(OWNER, "Reminder: call Meera about the quote.")
    await owner_channel.send_to_owner(env.app, env.t, note, kind="owner_note")
    row = await one(env.pool, "select * from outbound_messages where kind = 'owner_note'")
    assert row["payload"]["text"]["body"] == note["text"]["body"]                # its words are kept
    await apply_status(env.app, StatusUpdate(row["msg_id"], "failed", OWNER, 131047, "outside the window"))
    await sql(env, "update outbound_messages set created_at = now() - interval '3 days' where msg_id = %s", row["msg_id"])
    env.app.state.wa.sent.clear()
    env.app.state.llm = ScriptLLM(say("hello"))
    await dispatch(env.app, typed("g1", "hi"))
    late = [t for t in bodies(env) if "call Meera" in t]
    assert len(late) == 1 and late[0].startswith("(Late: this was meant to reach you on") and "IST" in late[0]
    await dispatch(env.app, typed("g2", "hi again"))
    assert len([t for t in bodies(env) if "call Meera" in t]) == 1               # once


async def test_what_cannot_be_sent_now_keeps_waiting(env):
    await owner_channel.send_to_owner(env.app, env.t, text_message(OWNER, "first note"), kind="owner_note")
    await owner_channel.send_to_owner(env.app, env.t, text_message(OWNER, "second note"), kind="owner_note")
    await sql(env, "update outbound_messages set status = 'failed', error_code = 131047")
    real = env.app.state.wa.send

    async def down(payload, **kw):
        raise ConnectionError("WhatsApp is down")
    env.app.state.wa.send = down
    assert await owner_channel.resend_undelivered(env.app, env.t, channel="whatsapp") == 0
    assert (await one(env.pool, "select count(*) n from outbound_messages where status = 'held'"))["n"] == 2
    env.app.state.wa.send = real
    assert await owner_channel.resend_undelivered(env.app, env.t, channel="whatsapp") == 2
    assert [t for t in bodies(env)][-2:] == ["first note", "second note"]        # just refused: no 'late' line, same order


async def test_one_message_the_channel_refuses_does_not_hold_up_the_rest_or_get_lost(env):
    await owner_channel.send_to_owner(env.app, env.t, text_message(OWNER, "refused note"), kind="owner_note")
    await owner_channel.send_to_owner(env.app, env.t, text_message(OWNER, "good note"), kind="owner_note")
    await sql(env, "update outbound_messages set status = 'held'")
    real = env.app.state.wa.send

    async def picky(payload, **kw):
        if "refused" in payload["text"]["body"]:
            request = httpx.Request("POST", "https://example.test")
            raise httpx.HTTPStatusError("bad", request=request, response=httpx.Response(400, request=request))
        return await real(payload, **kw)
    env.app.state.wa.send = picky
    assert await owner_channel.resend_undelivered(env.app, env.t, channel="whatsapp") == 1
    assert bodies(env)[-1] == "good note"
    kept = await one(env.pool, "select count(*) n from outbound_messages where status = 'failed' and payload->'text'->>'body' = 'refused note'")
    assert kept["n"] == 1                                                       # waits for their next message; not every minute
    assert (await one(env.pool, "select count(*) n from outbound_messages where status = 'held'"))["n"] == 0


async def test_a_reminder_set_beyond_the_whatsapp_day_comes_with_a_warning(env):
    await sql(env, "update tenants set owner_last_inbound_at = now(), owner_last_channel = 'whatsapp' where id = %s", env.t.id)

    async def set_one(i, minutes, what):
        env.app.state.llm = ScriptLLM(call("schedule_followup", instruction=what, in_minutes=minutes), say("set"))
        await dispatch(env.app, typed(f"h{i}", what))
        return env.app.state.llm.last_tool_result()

    far = await set_one(1, 3 * 24 * 60, "remind me about the electricity bill")
    assert "24 hours" in far["delivery_warning"] and "delivery_warning" not in far.get("error", "")
    assert far["delivery_warning"] in far["next"]
    near = await set_one(2, 120, "remind me to stretch")
    assert "delivery_warning" not in near and near["task_id"]
    settings(env, wa_owner_template="owner_items")                               # an approved template can reach them
    assert "delivery_warning" not in await set_one(3, 4 * 24 * 60, "remind me to renew the domain")


# ---------------------------------------------------------------- scheduled work

async def add_task(env, said="remind me to pay the rent", instruction="Remind the owner to pay the rent"):
    await sql(env, """insert into agent_tasks (tenant_id, run_at, kind, payload)
                      values (%s, now() - interval '1 minute', 'followup', jsonb_build_object('instruction', %s::text, 'owner_said', %s::text))""",
              env.t.id, instruction, said)


async def test_scheduled_work_that_keeps_failing_is_never_just_dropped(env):
    await add_task(env)
    env.app.state.llm = ScriptLLM(*[RuntimeError("model broke")] * 6)
    now = datetime.now(timezone.utc)
    for attempt in range(tasks.MAX_ATTEMPTS):
        assert await tasks.run_due(env.app, now + timedelta(minutes=11 * attempt)) == 0
        if attempt < tasks.MAX_ATTEMPTS - 1:
            assert not [t for t in bodies(env) if "scheduled" in t]              # still trying: not a word yet
    assert (await one(env.pool, "select status from agent_tasks"))["status"] == "FAILED"
    said = [t for t in bodies(env) if "couldn't run something you scheduled" in t]
    assert len(said) == 1 and "remind me to pay the rent" in said[0]


async def test_a_reminder_still_arrives_when_the_ai_allowance_is_used_up(env):
    settings(env, owner_daily_tokens=100)
    await sql(env, "insert into llm_usage (tenant_id, model, total_tokens) values (%s, 'm', 500)", env.t.id)
    await add_task(env, said="A follow-up you scheduled has come due. ...", instruction="Remind the owner to water the plants")
    env.app.state.llm = ScriptLLM()
    assert await tasks.run_due(env.app, datetime.now(timezone.utc)) == 1
    assert env.app.state.llm.seen == [] and "water the plants" in bodies(env)[-1] and "allowance is used up" in bodies(env)[-1]


# ---------------------------------------------------------------- deleting the account on WhatsApp

class Memory:
    def __init__(self, down=False):
        self.down = down

    async def delete_user(self, user_id):
        if self.down:
            raise ConnectionError("memory service unreachable")


@pytest.mark.parametrize("down, expect", [(False, "hata diya gaya hai"), (True, "kuch bhi delete nahi hua")])
async def test_deleting_the_account_on_whatsapp_always_answers(env, down, expect):
    env.app.state.continuum = Memory(down)
    await dispatch(env.app, typed("i1", "DELETE MY ACCOUNT"))
    assert expect in bodies(env)[-1]
    assert (await tenants.by_id(env.pool, env.t.id) is None) is (not down)


# ---------------------------------------------------------------- 'digest on'

async def test_digest_on_really_brings_the_monday_summary(env):
    settings(env, proactive_default=False)                                       # as in production: nothing unless asked
    await sql(env, "update tenants set owner_last_inbound_at = now() where id = %s", env.t.id)
    monday = datetime(2027, 3, 1, 10, 0, tzinfo=clock.IST)
    assert await scheduler.send_digests(env.app, monday) == 0
    await dispatch(env.app, typed("j1", "digest on"))
    assert "24 ghante" in bodies(env)[-1]                                        # and says when WhatsApp can carry it
    assert await scheduler.send_digests(env.app, monday + timedelta(days=7)) == 1
    assert "Is hafte ka haal" in bodies(env)[-1]
    await dispatch(env.app, typed("j2", "digest off"))
    assert not await review.opted_in(env.pool, env.t.id)
    assert await scheduler.send_digests(env.app, monday + timedelta(days=14)) == 0


# ---------------------------------------------------------------- the waitlist and messages Smara cannot open

async def test_a_bad_invite_code_is_answered_even_after_the_daily_reply(env):
    async def stranger(wamid, text):
        await dispatch(env.app, InboundMessage(wamid=wamid, sender=STRANGER, kind="text", text=text))
    await stranger("k1", "hello")
    assert "waitlist" in bodies(env, STRANGER)[-1] and "send 'hi' again" in bodies(env, STRANGER)[-1]
    await stranger("k2", "JOIN SMA-ZZZZ22")
    assert "isn't working" in bodies(env, STRANGER)[-1]
    n = len(bodies(env, STRANGER))
    await stranger("k3", "namaste")                                             # a plain word is not a bad code
    assert len(bodies(env, STRANGER)) == n
    for i in range(12):
        await stranger(f"k4{i}", "JOIN SMA-ZZZZ22")
    assert len(bodies(env, STRANGER)) <= n + onboarding.GUESS_LIMIT             # guessing gets quiet


def webhook(message):
    return {"entry": [{"changes": [{"value": {"metadata": {"phone_number_id": "1"}, "messages": [
        {"from": OWNER, "id": "wamid.x", **message}]}}]}]}


def test_shared_contacts_places_and_template_buttons_are_read_as_words():
    card = parse_messages(webhook({"type": "contacts", "contacts": [
        {"name": {"formatted_name": "Meera\nShah"}, "phones": [{"phone": "+91 90000 00055"}]}]}))[0]
    assert card.text == "[Shared contact] Meera Shah: +91 90000 00055"
    place = parse_messages(webhook({"type": "location", "location": {"latitude": 26.9, "longitude": 75.8, "name": "Office"}}))[0]
    assert place.text == "[Shared location] Office, https://maps.google.com/?q=26.9,75.8"
    tapped = parse_messages(webhook({"type": "button", "button": {"text": "Yes please"}}))[0]
    assert tapped.text == "Yes please"
    assert parse_messages(webhook({"type": "unsupported"}))[0].text == ""


async def test_a_message_smara_cannot_open_gets_an_answer_not_silence(env):
    await dispatch(env.app, InboundMessage(wamid="l1", sender=OWNER, kind="unsupported"))
    assert "can't open that kind of message" in bodies(env)[-1]


# ---------------------------------------------------------------- answering well

async def test_many_searches_for_one_request_get_a_push_to_answer_and_are_never_blocked(env):
    searches = [call("web_search", query=f"threshold {i}") for i in range(8)]
    env.app.state.llm = ScriptLLM(*searches, say("here is what I found"))
    await dispatch(env.app, typed("m1", "please look up the threshold for me"))
    results = [m["content"] for m in env.app.state.llm.seen[-1] if m["role"] == "tool"]
    assert len(results) == 8
    assert all("advice" not in r for r in results[:3])
    assert "answer now with its source" in results[3] and "Stop searching" in results[6] and "Stop searching" in results[7]
    assert bodies(env)[-1] == "here is what I found"


def test_saying_something_was_not_done_is_not_a_false_claim():
    def turn(asked, *tools):
        made = [{"role": "assistant", "content": "", "tool_calls": [{"id": t, "function": {"name": t, "arguments": "{}"}}]}
                for t in tools]
        return [{"role": "user", "content": asked}, *made]

    fine = ("Abhi koi reminder set nahi hai.", "No reminder is set right now.", "You don't have any reminder set.",
            "File abhi nahi bheji hai, pehle data chahiye.", "I have not sent the file yet.")
    for reply in fine:
        assert unbacked_claim(turn("kya haal hai"), reply) == "", reply
    # what real use showed: an empty list asked for, answered correctly, must not push the model to schedule something
    assert unbacked_claim(turn("mere saare reminders dikhao", "list_followups"), "Abhi koi reminder set nahi hai.") == ""
    assert unbacked_claim(turn("what reminders do I have, show them", "list_followups"), "One reminder set for Friday.") == ""
    for reply in ("Reminder set ho gaya.", "Reminder set kar diya, ab aapko yaad rakhne ki zaroorat nahi.",
                  "Done, I'll remind you on Friday.", "I sent the file just now.", "No problem. Reminder set kar diya."):
        assert unbacked_claim(turn("kal yaad dilana", "list_followups"), reply), reply


def test_an_english_message_is_told_apart_from_hinglish():
    english = ("Make me a small Excel sheet to track project payments", "Search the web for the registration threshold, please",
               "Remind me 3 days from now at 11 am to send the invoice")
    other = ("mere saare reminders dikhao", "Gupta ne 5000 cash diya", "kal subah yaad dilana please", "ok thanks", "help",
             "मुझे कल याद दिलाना", "[The owner attached a file: file_ref=local:1:a.pdf; caption: -]")
    assert all(prefs.plain_english(t) for t in english)
    assert not any(prefs.plain_english(t) for t in other)


async def test_the_instructions_ask_for_english_after_an_english_message(env):
    env.app.state.llm = ScriptLLM(say("a"), say("b"))
    await dispatch(env.app, typed("n1", "Please make a list of what goes on an invoice"))
    assert "so answer in English" in env.app.state.llm.seen[-1][0]["content"]
    await dispatch(env.app, typed("n2", "ab isko Hindi me samjhao"))
    assert "so answer in English" not in env.app.state.llm.seen[-1][0]["content"]


# ---------------------------------------------------------------- public pages

async def test_the_public_pages_match_how_smara_is_used_and_how_long_backups_last():
    import httpx
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(legal.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://smara.test") as http:
        deletion = (await http.get("/data-deletion")).text
        privacy = (await http.get("/privacy")).text
    assert "Open WhatsApp" in deletion and "about two months" in deletion
    assert "on WhatsApp" in privacy and "web search engines" in privacy and "about two months" in privacy
