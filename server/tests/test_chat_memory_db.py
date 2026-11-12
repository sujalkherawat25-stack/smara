"""Smara must not forget: the conversation log, recall across channels and runs, search, and the Continuum lookup."""
import json
import pathlib
import re

import pytest
from test_agent_db import OWNER, ScriptLLM, call, env, one, owner, say  # noqa: F401

from src import chatlog
from src.agent import prompt, tools
from src.agent.policy import SessionCtx
from src.agent.prompt import build_system
from src.ingest.dispatch import dispatch
from src.ingest.payload import InboundMessage


@pytest.fixture(autouse=True)
def _fresh_memory_state():
    prompt._memory_down_until = 0.0
    prompt._STATS.update(lookups=0, failed=0)
    yield
    prompt._memory_down_until = 0.0


async def rows(env, sql="select role, channel, kind, content, session_id from chat_messages order by id", *a):
    async with env.pool.connection() as c:
        return await (await c.execute(sql, a)).fetchall()


async def say_in_past(env, hours_ago, role, text, channel="telegram"):
    async with env.pool.connection() as c:
        await c.execute("""INSERT INTO chat_messages (tenant_id, channel, role, content, created_at)
                           VALUES (%s, %s, %s, %s, now() - %s * interval '1 hour')""", (env.t.id, channel, role, text, hours_ago))


# ---------------------------------------------------------------- the log itself

async def test_what_the_owner_and_smara_say_is_logged_and_tied_to_the_run(env):
    env.app.state.llm = ScriptLLM(say("Ravi ka Rs 10,000 baaki hai."))
    await dispatch(env.app, owner("c1", "Ravi kirana ka kitna baaki hai?"))
    got = await rows(env)
    assert [(r["role"], r["channel"], r["content"]) for r in got] == [
        ("owner", "whatsapp", "Ravi kirana ka kitna baaki hai?"), ("smara", "whatsapp", "Ravi ka Rs 10,000 baaki hai.")]
    sid = str((await one(env.pool, "select id from agent_sessions"))["id"])
    assert {str(r["session_id"]) for r in got} == {sid}          # both lines belong to the run that holds them


async def test_button_taps_are_not_logged_but_voice_notes_and_files_are(env, monkeypatch):
    from src.ingest import owner_flow

    async def fake_transcribe(app, msg):
        return "Sharma ji ko kal tak ruk jao bolna", None
    monkeypatch.setattr(owner_flow, "_transcribe", fake_transcribe)
    env.app.state.llm = ScriptLLM(say("theek hai"), say("file mil gayi"))
    await dispatch(env.app, InboundMessage(wamid="v1", sender=OWNER, kind="audio", media_id="m1"))
    await dispatch(env.app, owner("tap1", button=f"snooze:{env.oid}"))
    await dispatch(env.app, InboundMessage(wamid="f1", sender=OWNER, kind="document", media_id="m2", filename="dues.xlsx",
                                           caption="october ki list"))
    got = [(r["role"], r["kind"], r["content"]) for r in await rows(env)]
    assert ("owner", "voice", "Sharma ji ko kal tak ruk jao bolna") in got
    assert any(k == "file" and "dues.xlsx" in c and "october ki list" in c for _, k, c in got)
    assert not any("snooze:" in c for _, _, c in got)


async def test_logging_failures_never_break_a_reply(env, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("log down")
    monkeypatch.setattr(chatlog, "record", chatlog.record)          # real function, but with a pool that raises:
    real = env.app.state.pool

    class BadPool:
        def connection(self):
            raise RuntimeError("db down")
    assert await chatlog.record(BadPool(), env.t.id, "telegram", "owner", "hello") is None
    assert real is env.app.state.pool


# ---------------------------------------------------------------- recall across runs and channels

async def test_a_new_chat_sees_the_conversation_from_other_channels_and_days_ago(env):
    await say_in_past(env, 26, "owner", "Sharma ji ko Friday se pehle reminder mat bhejna", "telegram")
    await say_in_past(env, 26, "smara", "Theek hai, Friday se pehle Sharma ji ko reminder nahi bhejunga.", "telegram")
    await say_in_past(env, 5, "owner", "Ravi ne bola Monday ko payment karega", "whatsapp")
    llm = env.app.state.llm = ScriptLLM(say("ok"))
    await dispatch(env.app, owner("n1", "Sharma ka kya hua tha?"))
    system = llm.seen[0][0]["content"]
    assert "Earlier chats with the owner on Telegram and WhatsApp" in system
    assert "Sharma ji ko Friday se pehle reminder mat bhejna" in system and "[" in system and "Telegram]" in system
    assert "Ravi ne bola Monday ko payment karega" in system and "WhatsApp]" in system
    assert system.index("Sharma ji ko Friday") < system.index("Ravi ne bola Monday")       # oldest first
    assert "Sharma ka kya hua tha?" not in system                    # the message being answered is not repeated in its own history


async def test_a_running_chat_does_not_see_itself_twice_but_the_next_one_sees_all_of_it(env):
    llm = env.app.state.llm = ScriptLLM(say("pehla jawab"), say("doosra jawab"), say("teesra"))
    await dispatch(env.app, owner("s1", "pehla sawaal"))
    await dispatch(env.app, owner("s2", "doosra sawaal"))            # within 30 minutes: same run continues
    assert "Earlier chats with the owner" not in llm.seen[1][0]["content"]
    async with env.pool.connection() as c:                           # then a long pause
        await c.execute("update agent_sessions set updated_at = now() - interval '3 hours'")
    await dispatch(env.app, owner("s3", "teesra sawaal"))
    system = llm.seen[2][0]["content"]
    for line in ("pehla sawaal", "pehla jawab", "doosra sawaal", "doosra jawab"):
        assert line in system
    assert "teesra sawaal" not in system


async def test_the_recent_conversation_is_bounded(env):
    for i in range(60):
        await say_in_past(env, 1, "owner" if i % 2 else "smara", f"line {i:02d} " + "x" * 600)
    text = await chatlog.recent(env.pool, env.t.id)
    assert len(text) <= chatlog.RECENT_CHARS + chatlog.LINE_CHARS + 80
    assert "line 59" in text and "line 00" not in text               # newest kept, oldest dropped
    assert all(len(l) <= chatlog.LINE_CHARS + 60 for l in text.splitlines())


# ---------------------------------------------------------------- search

async def test_search_finds_old_messages_for_this_shop_only_and_ranks_by_matching_words(env):
    await say_in_past(env, 24 * 20, "owner", "Gupta traders ka 45000 ka cheque Tuesday ko aayega")
    await say_in_past(env, 24 * 2, "owner", "Gupta ji ne phone kiya tha")
    await say_in_past(env, 24 * 3, "owner", "kuch aur bilkul alag baat")
    async with env.pool.connection() as c:
        await c.execute("""INSERT INTO chat_messages (tenant_id, channel, role, content) VALUES (%s, 'telegram', 'owner',
                           'Gupta traders ka secret jo doosre shop ka hai')""", (env.other.id,))
    ctx = SessionCtx("s", env.t.id, "TRUSTED")
    out = await tools.search_chat_history(env.app, ctx, {"query": "Gupta traders cheque"})
    texts = [m["text"] for m in out["matches"]]
    assert texts[0].startswith("Gupta traders ka 45000")             # matches three of the words
    assert any("phone kiya" in t for t in texts) and not any("alag baat" in t for t in texts)
    assert not any("secret" in t for t in texts)                      # another shop's messages never appear
    assert out["matches"][0]["who"] == "Owner" and out["matches"][0]["channel"] == "Telegram"
    assert (await tools.search_chat_history(env.app, ctx, {"query": "zz"}))["error"]
    empty = await tools.search_chat_history(env.app, ctx, {"query": "kabhi nahi kaha", "days": 5})
    assert empty["matches"] == [] and empty["note"]


async def test_a_wildcard_in_the_query_is_just_text(env):
    await say_in_past(env, 1, "owner", "discount 10% ka matlab kya hai")
    out = await chatlog.search(env.pool, env.t.id, "100% _ discount%")
    assert [m["text"] for m in out] == ["discount 10% ka matlab kya hai"]


async def test_customer_sessions_cannot_search_the_owners_chat():
    from src.agent.policy import pre_tool_use
    ctx = SessionCtx("s", "t", "UNTRUSTED", party_id="p1")
    assert pre_tool_use(ctx, "search_chat_history", {"query": "anything"}, frozenset({"search_chat_history"})).verdict == "deny"


# ---------------------------------------------------------------- the Continuum lookup on every owner message

async def test_every_owner_message_gets_memory_about_the_customer_it_names(env):
    class Mem:
        def __init__(self):
            self.asked = []

        async def resolve(self, *, user_id, query, max_claims=8):
            self.asked.append(user_id)
            card = "Promised to pay by Friday." if user_id.endswith(env.pid) else "Owner prefers Hinglish and morning reminders."
            return {"resolution": {"state_card": card}}
    env.app.state.continuum = Mem()
    llm = env.app.state.llm = ScriptLLM(say("ok"))
    await dispatch(env.app, owner("m1", "Ravi ne kya bola tha payment ke baare mein?"))
    system = llm.seen[0][0]["content"]
    assert "Memory about this message" in system
    assert "- Ravi: Promised to pay by Friday." in system
    assert "- the shop: Owner prefers Hinglish" in system
    assert any(a.endswith(env.pid) for a in env.app.state.continuum.asked)
    assert prompt.memory_stats()["failed"] == 0


async def test_a_memory_outage_is_retried_then_said_out_loud_and_does_not_slow_every_message(env, monkeypatch):
    calls = []

    class Down:
        async def resolve(self, **kw):
            calls.append(kw["user_id"])
            raise ConnectionError("continuum down")
    env.app.state.continuum = Down()
    monkeypatch.setattr(prompt, "MEMORY_TIMEOUT_S", 0.5)
    llm = env.app.state.llm = ScriptLLM(say("pehla"), say("doosra"))
    await dispatch(env.app, owner("o1", "Ravi ka kya hua tha?"))
    first = llm.seen[0][0]["content"]
    assert prompt.MEMORY_DOWN_NOTE in first and "Memory about this message" not in first
    assert len(calls) >= 2 * prompt.MEMORY_TRIES                      # tried twice for each lookup, not once
    assert prompt.memory_stats()["failed"] >= 2
    before = len(calls)
    await dispatch(env.app, owner("o2", "aur Ravi ka?"))               # within the pause: no waiting, still honest
    assert len(calls) == before
    assert prompt.MEMORY_DOWN_NOTE in llm.seen[1][0]["content"]


async def test_a_flaky_lookup_that_answers_the_second_time_is_not_a_failure(env):
    class Flaky:
        def __init__(self):
            self.n = 0

        async def resolve(self, **kw):
            self.n += 1
            if self.n == 1:
                raise ConnectionError("blip")
            return {"resolution": {"state_card": "Owner wants weekly summaries on Monday."}}
    env.app.state.continuum = Flaky()
    system = await build_system(env.app, await __import__("src.tenants", fromlist=["x"]).by_id(env.pool, env.t.id),
                                "TRUSTED", None, query="weekly summary kab aata hai")
    assert "weekly summaries on Monday" in system and prompt.MEMORY_DOWN_NOTE not in system


async def test_no_lookup_for_customer_sessions_and_scheduled_runs(env):
    class Spy:
        calls = 0

        async def resolve(self, **kw):
            Spy.calls += 1
            return {"resolution": {"state_card": ""}}
    env.app.state.continuum = Spy()
    tenant = await __import__("src.tenants", fromlist=["x"]).by_id(env.pool, env.t.id)
    await build_system(env.app, tenant, "UNTRUSTED", None, query="Ravi ne kya bola")
    assert Spy.calls == 0
    await build_system(env.app, tenant, "SYSTEM", None, query="Ravi ne kya bola")
    assert Spy.calls == 1                                              # only the standing-instructions read, none for the message


async def test_healthz_reports_the_memory_counters(env):
    assert prompt.memory_stats() == {"lookups": 0, "failed": 0}


# ---------------------------------------------------------------- earlier conversations are carried over

async def test_the_migration_carries_existing_conversations_into_the_log(env):
    sql = pathlib.Path(__file__).resolve().parents[1].joinpath("migrations/022_chat_log.sql").read_text(encoding="utf-8")
    backfill = sql[sql.index("INSERT INTO chat_messages"):]
    msgs = [{"role": "system", "content": "prompt"},
            {"role": "user", "content": "Ravi ka kitna baaki hai?"},
            {"role": "assistant", "content": "checking", "tool_calls": [{"id": "1", "type": "function",
                                                                          "function": {"name": "query_ledger_sql", "arguments": "{}"}}]},
            {"role": "tool", "content": json.dumps({"rows": []})},
            {"role": "assistant", "content": "Ravi ka Rs 10,000 baaki hai."},
            {"role": "user", "content": "[system note] You have made this exact call 3 times"},
            {"role": "user", "content": "[Earlier conversation omitted to save space: 4 messages.]"},
            {"role": "user", "content": [{"type": "text", "text": "an image message"}]}]
    async with env.pool.connection() as c:
        await c.execute("""INSERT INTO agent_sessions (tenant_id, trust, trigger, status, messages, channel)
                           VALUES (%s, 'TRUSTED', 'owner_message', 'DONE', %s::jsonb, 'telegram')""", (env.t.id, json.dumps(msgs)))
        await c.execute("""INSERT INTO agent_sessions (tenant_id, trust, trigger, status, messages)
                           VALUES (%s, 'UNTRUSTED', 'debtor_message', 'DONE', %s::jsonb)""",
                        (env.t.id, json.dumps([{"role": "user", "content": "customer ka message"}])))
        await c.execute("DELETE FROM chat_messages")
        await c.execute(backfill)
    got = [(r["role"], r["channel"], r["content"]) for r in await rows(env)]
    assert got == [("owner", "telegram", "Ravi ka kitna baaki hai?"), ("smara", "telegram", "Ravi ka Rs 10,000 baaki hai.")]
    assert not re.search(r"customer ka message|checking|system note|Earlier conversation|image message", json.dumps(got))


# ---------------------------------------------------------------- Continuum cards must not crowd out the facts

def _fact(text, day, n=0):
    return (f"[UNVERSIONED EVIDENCE] [TRUSTED] {text} (source=mem_{n:04d}-aaaa-bbbb-cccc-dddddddddddd_0, turn=turn:1, "
            f"valid_at=2026-11-{day:02d}T06:02:31.043644+00:00)")


HEADER = ("RETRIEVED EVIDENCE (no temporal state inferred)\n"
          "UNVERSIONED EVIDENCE (no current/past ordering or supersession inferred)\n")


def test_a_card_is_reduced_to_its_facts_oldest_first_without_repeats():
    card = HEADER + "\n".join([_fact("Owner wants reminders after 11 AM only.", 9, 1), _fact("Owner wants reminders before 10 AM only.", 5, 2),
                               _fact("Owner wants reminders after 11 AM only.", 9, 3)])
    out = prompt.compact_card(card)
    assert out.splitlines() == ["  \u2022 Owner wants reminders before 10 AM only. (noted 05/11)",
                                "  \u2022 Owner wants reminders after 11 AM only. (noted 09/11)"]     # the later decision reads last
    assert "source=" not in out and "UNVERSIONED" not in out
    assert prompt.compact_card("Promised to pay by Friday.") == "Promised to pay by Friday."          # other shapes untouched
    assert prompt.compact_card("") == ""


async def test_all_the_owners_standing_rules_reach_the_agent_not_just_the_first_few(env):
    """Six rules arrive as ~1,500 characters of tags and ids; the old 900-character cut dropped the last ones."""
    rules = [f"Standing rule number {i}: keep this exact detail {'x' * 60}" for i in range(1, 7)]

    class Mem:
        async def resolve(self, **kw):
            return {"resolution": {"state_card": HEADER + "\n".join(_fact(r, 3 + i, i) for i, r in enumerate(rules))}}
    assert len(HEADER + "\n".join(_fact(r, 3, i) for i, r in enumerate(rules))) > 1400
    env.app.state.continuum = Mem()
    llm = env.app.state.llm = ScriptLLM(say("ok"))
    await dispatch(env.app, owner("st1", "aaj ka kaam batao"))
    system = llm.seen[0][0]["content"]
    for r in rules:
        assert r in system, f"lost: {r[:30]}"
    assert "the LAST one listed is the newer decision" in system


def test_two_facts_noted_the_same_day_keep_the_order_they_were_given_in():
    """A rule changed minutes after it was set must still read as the newer one."""
    def at(text, hhmm, n):
        return (f"[UNVERSIONED EVIDENCE] [TRUSTED] {text} (source=mem_{n:04d}-aaaa, turn=turn:1, "
                f"valid_at=2026-11-12T{hhmm}:00.000000+00:00)")
    card = HEADER + "\n".join([at("Rule now: Wednesday.", "07:15", 2), at("Rule: Friday.", "07:02", 1)])    # relevance order, not time order
    assert prompt.compact_card(card).splitlines() == ["  • Rule: Friday. (noted 12/11)", "  • Rule now: Wednesday. (noted 12/11)"]


async def test_search_keeps_short_words_like_ca_and_drops_filler(env):
    await say_in_past(env, 24 * 70, "owner", "Humare GST filing ke liye CA Mr. Verma hain, Udaipur mein")
    await say_in_past(env, 24 * 3, "owner", "Aaj ka status batao")
    found = await chatlog.search(env.pool, env.t.id, "Humara CA kaun hai?")
    assert [m["text"] for m in found][0].startswith("Humare GST filing ke liye CA Mr. Verma")
    assert await chatlog.search(env.pool, env.t.id, "ka ke hai kya") == []            # only filler words: nothing to look for
