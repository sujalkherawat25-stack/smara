"""Hosted-agent upgrade: time awareness, quiet hours, scheduled work, shop memory, files to the owner,
voice notes, payment requests, context compaction. Reuses the doubles from test_agent_db."""
import json
from datetime import date, datetime, timedelta, timezone

import pytest
from test_agent_db import (DEBTOR, FakeBrowsers, FakeContinuum, FakeWA, OWNER, ScriptLLM, call, env, one,  # noqa: F401
                           owner, say)

from src import clock, tenants
from src.agent import actions, context
from src.agent.policy import SessionCtx, pre_tool_use
from src.agent.tools import TOOL_NAMES
from src.box.sandbox import Sandbox
from src.config import Settings
from src.ingest.dispatch import dispatch
from src.ingest.importer import import_rows
from src.ingest.payload import InboundMessage
from src.ingest.sheet_mapper import SheetRow
from src.loop import tasks
from src.loop.owner_channel import flush_held
from src.loop.scheduler import IST, digest_window_open, tick

NIGHT = Settings(quiet_start_hour=21, quiet_end_hour=8)


def at(h, m=0, day=4):
    return datetime(2026, 11, day, h, m, tzinfo=IST)


# ------------------------------------------------------------------ pure: clock, compaction, policy

def test_quiet_hours_and_opening_time():
    assert clock.is_quiet(NIGHT, at(3, 44)) and clock.is_quiet(NIGHT, at(21, 0)) and clock.is_quiet(NIGHT, at(7, 59))
    assert not clock.is_quiet(NIGHT, at(8, 0)) and not clock.is_quiet(NIGHT, at(20, 59))
    assert clock.next_open(NIGHT, at(3, 44)) == at(8) and clock.next_open(NIGHT, at(23, 10)) == at(8, day=5)
    assert clock.next_open(NIGHT, at(11)) == at(11)                              # already open: now
    assert not clock.is_quiet(Settings(), at(3))                                 # a bare Settings never blocks
    assert clock.parse_quiet_hours("22-7") == (22, 7) and clock.parse_quiet_hours("junk") == (21, 8)


def test_digest_window_is_morning_only():
    s = Settings(quiet_start_hour=21, quiet_end_hour=8, digest_hour_ist=9)
    assert [digest_window_open(s, at(h)) for h in (3, 8, 9, 11, 12, 17, 23)] == [False, False, True, True, False, False, False]
    assert digest_window_open(s, at(10).astimezone(timezone.utc))                # tz-agnostic input


def _turn(i):
    return [{"role": "user", "content": f"q{i}"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": f"c{i}", "type": "function", "function": {
                "name": "run_python", "arguments": json.dumps({"code": "x" * 3000})}}]},
            {"role": "tool", "tool_call_id": f"c{i}", "content": "y" * 5000},
            {"role": "assistant", "content": f"a{i}"}]


def test_compaction_truncates_old_results_and_drops_whole_turns_keeping_pairs_valid():
    msgs = [{"role": "system", "content": "sys"}] + [m for i in range(12) for m in _turn(i)]
    out = context.compact(msgs, budget_chars=20_000)
    assert out[0] == msgs[0] and out[-4:] == msgs[-4:]                          # system + last turn untouched
    assert context.size(out) <= 20_000 and context.is_note(out[1])
    ids = [c["id"] for m in out if m.get("tool_calls") for c in m["tool_calls"]]
    assert ids == [m["tool_call_id"] for m in out if m["role"] == "tool"]       # every call still has its result
    assert all(json.loads(c["function"]["arguments"]) for m in out if m.get("tool_calls") for c in m["tool_calls"])
    small = context.compact(msgs, budget_chars=10**9)                            # no budget pressure: only truncation
    assert len(small) == len(msgs) and "truncated" in small[3]["content"] and small[-2] == msgs[-2]
    assert len([m for m in context.compact(msgs, budget_chars=5_000) if context.is_note(m)]) == 1


def test_new_tool_tiers_and_limits():
    t = SessionCtx("s", "t", "TRUSTED", files=("sandbox:abc:r.pdf", "whatsapp:m:x.csv"))
    v = lambda ctx, n, a: pre_tool_use(ctx, n, a, TOOL_NAMES)
    for n in ("get_current_time", "check_gstin", "build_payment_request", "list_followups"):
        assert v(t, n, {}).verdict == "allow" and v(t, n, {}).tier == "T0"
    for n in ("schedule_followup", "cancel_followup"):
        assert v(t, n, {}).verdict == "allow" and v(t, n, {}).tier == "T1"
    assert v(t, "send_file_to_owner", {"file_ref": "sandbox:abc:r.pdf"}).verdict == "allow"
    assert v(t, "send_file_to_owner", {"file_ref": "whatsapp:m:x.csv"}).verdict == "deny"      # not something we created
    assert v(t, "send_file_to_owner", {"file_ref": "sandbox:other:x.pdf"}).verdict == "deny"   # not in this session
    d = SessionCtx("s", "t", "UNTRUSTED", party_id="p1", files=("sandbox:abc:r.pdf",))
    for n, a in (("schedule_followup", {}), ("send_file_to_owner", {"file_ref": "sandbox:abc:r.pdf"}),
                 ("get_current_time", {}), ("build_payment_request", {}), ("check_gstin", {})):
        assert v(d, n, a).verdict == "deny", n                                  # counterparties get none of them
    assert v(d, "write_continuum_evidence", {"party_id": "shop", "text": "x"}).verdict == "deny"   # no shop-wide writes


# ------------------------------------------------------------------ time awareness and fresh context

async def test_every_turn_gets_a_fresh_system_prompt_and_a_time_tool(env, monkeypatch):
    llm = env.app.state.llm = ScriptLLM(say("ok"), call("get_current_time"), lambda m: say(f"t={json.loads(m[-1]['content'])['date']}"))
    monkeypatch.setattr(clock, "now", lambda: at(1, 59, day=4))
    await dispatch(env.app, owner("a1", "hi"))
    assert "Wednesday 04 November 2026, 01:59 IST" in llm.seen[0][0]["content"]
    monkeypatch.setattr(clock, "now", lambda: at(10, 21, day=5))                 # same conversation, a later day
    await dispatch(env.app, owner("a2", "aaj kya date hai?"))
    assert "Thursday 05 November 2026, 10:21 IST" in llm.seen[1][0]["content"]  # not the stale first-turn date
    assert llm.last_tool_result(2)["date"] == "2026-11-05" and llm.last_tool_result(2)["weekday"] == "Thursday"
    assert env.app.state.wa.sent[-1]["text"]["body"] == "t=2026-11-05"


async def test_current_time_reports_business_hours(env, monkeypatch):
    env.app.state.settings = NIGHT
    monkeypatch.setattr(clock, "now", lambda: at(3, 44))
    from src.agent.extras import get_current_time
    r = await get_current_time(env.app, SessionCtx("s", env.t.id, "TRUSTED"), {})
    assert r["business_hours_open"] is False and r["next_open_ist"] == "2026-11-04 08:00" and r["now_ist"] == "2026-11-04 03:44"


# ------------------------------------------------------------------ scheduled follow-ups

async def test_schedule_list_cancel_followups_with_limits_and_isolation(env, monkeypatch):
    from src.agent import extras
    env.app.state.settings = NIGHT
    monkeypatch.setattr(clock, "now", lambda: at(11))
    from uuid import uuid4
    ctx = SessionCtx(str(uuid4()), env.t.id, "TRUSTED")
    ok = await extras.schedule_followup(env.app, ctx, {"instruction": "check Ravi payment", "at_ist": "2026-11-04 15:30"})
    assert ok["will_run_at_ist"] == "2026-11-04 15:30" and ok["moved_out_of_quiet_hours"] is False
    late = await extras.schedule_followup(env.app, ctx, {"instruction": "nudge me", "at_ist": "2026-11-04 23:15"})
    assert late["will_run_at_ist"] == "2026-11-05 08:00" and late["moved_out_of_quiet_hours"] is True   # never at night
    for bad in ({"instruction": "x", "at_ist": "2026-11-04 10:59"}, {"instruction": "x", "in_minutes": 90 * 24 * 60},
                {"instruction": "x", "at_ist": "tomorrow"}, {"instruction": "", "in_minutes": 5}, {"instruction": "x"}):
        assert "error" in await extras.schedule_followup(env.app, ctx, bad), bad
    assert len((await extras.list_followups(env.app, ctx, {}))["pending"]) == 2
    other = SessionCtx(str(uuid4()), env.other.id, "TRUSTED")
    assert "error" in await extras.cancel_followup(env.app, other, {"task_id": ok["task_id"]})   # not their task
    assert (await extras.cancel_followup(env.app, ctx, {"task_id": ok["task_id"]})) == {"cancelled": True}
    assert "error" in await extras.cancel_followup(env.app, ctx, {"task_id": "not-a-uuid"})
    for i in range(30):
        r = await extras.schedule_followup(env.app, ctx, {"instruction": f"t{i}", "in_minutes": 120 + i})
        if "error" in r:
            assert "too many" in r["error"] and i == 24                          # 25 pending max (1 left + 24 new)
            break
    else:
        pytest.fail("pending cap not enforced")


async def test_due_followup_wakes_the_agent_and_messages_the_owner(env, monkeypatch):
    env.app.state.settings = NIGHT
    llm = env.app.state.llm = ScriptLLM(
        call("schedule_followup", instruction="Check whether Ravi paid INV-1 and tell me", at_ist="2026-11-04 15:00"),
        say("Scheduled."),
        call("query_ledger_sql", sql="select state from obligations where invoice_ref = 'INV-1'"),
        call("send_whatsapp_message", to="owner", text="Ravi ka INV-1 abhi bhi ACTIVE hai."), say("done"))
    monkeypatch.setattr(clock, "now", lambda: at(11))
    await dispatch(env.app, owner("f1", "3 baje check karna Ravi ne pay kiya ya nahi"))
    task = await one(env.pool, "select id, run_at, status from agent_tasks")
    assert task["status"] == "PENDING" and task["run_at"] == at(15)
    said = (await one(env.pool, "select payload from agent_tasks"))["payload"]["owner_said"]
    assert said == "3 baje check karna Ravi ne pay kiya ya nahi"                  # the owner's words, verbatim
    assert await tasks.run_due(env.app, at(14, 59)) == 0                         # not yet due
    assert await tasks.run_due(env.app, at(22, 30)) == 0                         # due but it is night: waits
    n_before = len(env.app.state.wa.sent)
    monkeypatch.setattr(clock, "now", lambda: at(15, 1))
    assert await tasks.run_due(env.app, at(15, 1)) == 1
    assert (await one(env.pool, "select status from agent_tasks"))["status"] == "DONE"
    sess = await one(env.pool, "select trust, trigger from agent_sessions where trigger = 'scheduled_followup'")
    assert sess == {"trust": "SYSTEM", "trigger": "scheduled_followup"}
    assert any("INV-1 abhi bhi ACTIVE" in m.get("text", {}).get("body", "") for m in env.app.state.wa.sent[n_before:])
    assert "Check whether Ravi paid" in llm.seen[2][1]["content"] and "15:01 IST" in llm.seen[2][1]["content"]
    assert "exact words" in llm.seen[2][1]["content"] and "3 baje check karna Ravi ne pay kiya ya nahi" in llm.seen[2][1]["content"]


async def test_failed_task_retries_then_gives_up(env):
    async with env.pool.connection() as c:
        await c.execute("insert into agent_tasks (tenant_id, run_at, kind, payload) values (%s, now() - interval '1 minute', 'followup', %s)",
                        (env.t.id, json.dumps({"instruction": "x"})))
    env.app.state.llm = ScriptLLM(RuntimeError("down"), RuntimeError("down"), RuntimeError("down"), RuntimeError("down"))
    t = datetime.now(timezone.utc)
    for i in range(4):
        await tasks.run_due(env.app, t + timedelta(minutes=11 * (i + 1)))
    row = await one(env.pool, "select status, attempts, last_error from agent_tasks")
    assert row["attempts"] == 3 and row["status"] in ("FAILED", "DONE")


# ------------------------------------------------------------------ quiet hours

async def test_nothing_proactive_at_night_then_released_when_the_shop_opens(env, monkeypatch):
    env.app.state.settings = Settings(quiet_start_hour=21, quiet_end_hour=8, wa_owner_template="owner_waiting")
    env.app.state.llm.enabled = False                                            # deterministic (no model) path
    async with env.pool.connection() as c:
        await c.execute("update obligations set next_followup_at = now() - interval '1 hour' where invoice_ref = 'INV-1'")
        await c.execute("update tenants set owner_last_inbound_at = now() - interval '30 hours' where id = %s", (env.t.id,))
    monkeypatch.setattr(clock, "now", lambda: at(3, 44))
    assert await tick(env.app, at(3, 44).astimezone(timezone.utc)) == 0          # 3:44 AM: reminders do not even start
    assert (await one(env.pool, "select state from obligations where invoice_ref = 'INV-1'"))["state"] == "ACTIVE"
    assert env.app.state.wa.sent == []
    # a customer writes at 3:44 AM: it is recorded and a card is held, the owner is not pinged
    await dispatch(env.app, InboundMessage(wamid="n1", sender=DEBTOR, kind="text", text="kal tak bhej dunga"))
    assert (await one(env.pool, "select count(*) n from outbound_messages where status = 'held'"))["n"] == 1
    assert env.app.state.wa.sent == [] and await flush_held(env.app) == 0        # still night: nothing moves
    # 8:30: the shop opens; the owner's WhatsApp window is closed, so exactly one template nudge goes out
    monkeypatch.setattr(clock, "now", lambda: at(8, 30))
    await flush_held(env.app)
    await flush_held(env.app)                                                    # idempotent: no second nudge
    assert [m["type"] for m in env.app.state.wa.sent] == ["template"]
    assert (await one(env.pool, "select count(*) n from outbound_messages where status = 'held'"))["n"] == 1
    # the owner replies: the held card arrives
    await dispatch(env.app, owner("n2", "hi"))
    cards = [m for m in env.app.state.wa.sent if m.get("type") == "interactive"]
    assert len(cards) == 1 and "PROMISE_DATE_CHANGE" in cards[0]["interactive"]["body"]["text"]


async def test_telegram_owner_gets_held_cards_when_the_shop_opens(env, monkeypatch):
    env.app.state.settings = Settings(quiet_start_hour=21, quiet_end_hour=8)
    env.app.state.llm.enabled = False

    class TG:
        sent = []

        async def send(self, payload):
            self.sent.append(payload)
    env.app.state.tg = TG()
    async with env.pool.connection() as c:
        await c.execute("update tenants set telegram_chat_id = 555 where id = %s", (env.t.id,))
    monkeypatch.setattr(clock, "now", lambda: at(2))
    await dispatch(env.app, InboundMessage(wamid="n3", sender=DEBTOR, kind="text", text="10% discount de do"))
    assert TG.sent == [] and (await one(env.pool, "select count(*) n from outbound_messages where status = 'held'"))["n"] == 1
    monkeypatch.setattr(clock, "now", lambda: at(8, 5))
    assert await flush_held(env.app) == 1
    assert TG.sent[0]["chat_id"] == 555 and "DISCOUNT_CLAIM" in TG.sent[0]["text"]


async def test_approved_customer_message_defers_overnight_and_goes_out_when_open(env, monkeypatch):
    text = "Namaste Ravi ji, INV-1 ka Rs 10,000 baaki hai."
    async with env.pool.connection() as c:
        await c.execute("update parties set last_inbound_at = now() where id = %s", (env.pid,))
        await c.execute("update obligations set state = 'WAITING_OWNER_APPROVAL' where id = %s", (env.oid,))
    env.app.state.llm = ScriptLLM(call("send_whatsapp_message", to=env.pid, obligation_id=env.oid, text=text),
                                  lambda m: say(json.loads(m[-1]["content"]).get("note", "?")))
    env.app.state.settings = NIGHT
    monkeypatch.setattr(clock, "now", lambda: at(2, 10))
    await dispatch(env.app, owner("q1", "Ravi ko reminder bhejo"))
    sid = (await one(env.pool, "select id from agent_sessions where status = 'WAITING_APPROVAL'"))["id"]
    await dispatch(env.app, owner("q2", button=f"ag_ok:{sid}"))
    assert env.app.state.wa.to(DEBTOR) == []                                     # approved, but it is 2 AM
    assert "quiet hours" in env.app.state.wa.sent[-1]["text"]["body"]
    task = await one(env.pool, "select kind, run_at, payload from agent_tasks")
    assert task["kind"] == "send_message" and task["run_at"] == at(8) and task["payload"]["text"] == text
    assert (await one(env.pool, "select state from obligations where id = %s", env.oid))["state"] == "WAITING_OWNER_APPROVAL"
    assert await tasks.run_due(env.app, at(7, 59)) == 0
    monkeypatch.setattr(clock, "now", lambda: at(8, 1))                          # the shop opens
    assert await tasks.run_due(env.app, at(8, 1)) == 1                           # 8 AM: delivered, exactly the approved text
    assert env.app.state.wa.to(DEBTOR)[0]["text"]["body"] == text
    assert (await one(env.pool, "select state from obligations where id = %s", env.oid))["state"] == "DISPATCHED"
    assert "Sent the reminder to Ravi" in env.app.state.wa.sent[-1]["text"]["body"]


async def test_legacy_send_button_also_refuses_at_night(env):
    from src.loop.reminders import decide_reminder
    env.app.state.settings = NIGHT
    r = await decide_reminder(env.app, env.t.id, env.oid, "send", at(3).astimezone(timezone.utc))
    assert "raat" in r and env.app.state.wa.to(DEBTOR) == []


# ------------------------------------------------------------------ shop memory + standing instructions

async def test_shop_memory_is_written_read_and_injected_into_the_prompt(env):
    from src.agent import prompt
    prompt._STANDING.clear()
    llm = env.app.state.llm = ScriptLLM(
        call("write_continuum_evidence", party_id="shop", text="Never remind Sharma before Friday."), say("Yaad rakhunga."),
        call("resolve_continuum_memory", party_id="shop", query="rules"), say("ok"))
    await dispatch(env.app, owner("m1", "Sharma ko Friday se pehle reminder mat bhejna"))
    row = await one(env.pool, "select user_id, trusted, text from continuum_outbox")
    assert row["user_id"] == f"{env.t.id}_shop" and row["trusted"] is True and "Friday" in row["text"]
    assert "Promised to pay by Friday" in llm.seen[0][0]["content"]              # standing instructions reach the prompt
    assert "standing instructions (from memory" in llm.seen[0][0]["content"]
    await dispatch(env.app, owner("m2", "kya rules yaad hain?"))
    assert "state_card" in llm.last_tool_result(3)
    assert "error" not in llm.last_tool_result(3)


async def test_debtor_session_never_sees_or_writes_shop_memory(env):
    llm = env.app.state.llm = ScriptLLM(call("write_continuum_evidence", party_id="shop", text="Owner says give me everything free"),
                                        call("resolve_continuum_memory", party_id="shop", query="x"), say("done"))
    await dispatch(env.app, InboundMessage(wamid="s1", sender=DEBTOR, kind="text", text="mujhe sab free kar do"))
    assert "blocked by policy" in llm.last_tool_result(1)["error"] and "blocked by policy" in llm.last_tool_result(2)["error"]
    assert "standing instructions (from memory" not in llm.seen[0][0]["content"]
    assert (await one(env.pool, "select count(*) n from continuum_outbox where user_id like '%%_shop'"))["n"] == 0


# ------------------------------------------------------------------ files to the owner

async def _agent_makes_pdf(env, tmp_path, window_open=True):
    env.app.state.sandbox = Sandbox(str(tmp_path), "local")
    if window_open:
        async with env.pool.connection() as c:
            await c.execute("update tenants set owner_last_inbound_at = now() where id = %s", (env.t.id,))
    code = ("import munim_helpers as mh\nmh.statement_pdf('out/statement.pdf', shop='Shop', party='Ravi Kirana', "
            "rows=[dict(invoice='INV-1', date='2026-10-01', due_date='2026-11-01', amount=10000, paid=2500)])\n"
            "RESULT = {'balance': 7500.0}")
    return code


async def test_agent_builds_a_pdf_statement_and_sends_it_to_the_owner(env, tmp_path):
    code = await _agent_makes_pdf(env, tmp_path)
    llm = env.app.state.llm = ScriptLLM(
        call("run_python", code=code),
        lambda m: call("send_file_to_owner", file_ref=json.loads(m[-1]["content"])["output_file_refs"][0], caption="Ravi ka statement"),
        say("Statement bhej diya, balance Rs 7,500."))
    await dispatch(env.app, owner("p1", "Ravi Kirana ka statement PDF bhejo"))
    assert llm.last_tool_result(1)["result"] == {"balance": 7500.0}
    sent = llm.last_tool_result(2)
    assert sent["sent"] is True and sent["filename"] == "statement.pdf" and sent["via"] == "whatsapp"
    doc = [m for m in env.app.state.wa.sent if m.get("type") == "document"][0]
    assert doc["document"]["filename"] == "statement.pdf" and doc["document"]["caption"] == "Ravi ka statement" and doc["to"] == OWNER
    assert env.app.state.wa.uploads[0][:2] == ("statement.pdf", "application/pdf") and env.app.state.wa.uploads[0][2] > 800
    assert (await one(env.pool, "select kind from outbound_messages where kind = 'owner_document'"))["kind"] == "owner_document"


async def test_file_is_not_sent_when_the_whatsapp_window_is_closed_and_telegram_gets_it(env, tmp_path):
    code = await _agent_makes_pdf(env, tmp_path, window_open=False)
    async with env.pool.connection() as c:
        await c.execute("update tenants set owner_last_inbound_at = now() - interval '30 hours' where id = %s", (env.t.id,))
    ctx = SessionCtx("s", env.t.id, "SYSTEM")
    res = await env.app.state.sandbox.run(code, {})
    from src.agent.extras import send_file_to_owner
    ref = f"sandbox:{res['job_id']}:statement.pdf"
    r = await send_file_to_owner(env.app, ctx, {"file_ref": ref})
    assert r["sent"] is False and "24-hour" in r["error"] and env.app.state.wa.uploads == []

    class TG:
        docs = []

        async def send_document(self, chat, data, name, caption=""):
            self.docs.append((chat, name, len(data)))
    env.app.state.tg = TG()
    async with env.pool.connection() as c:
        await c.execute("update tenants set telegram_chat_id = 555 where id = %s", (env.t.id,))
    r = await send_file_to_owner(env.app, ctx, {"file_ref": ref})
    assert r["sent"] is True and r["via"] == "telegram" and TG.docs[0][:2] == (555, "statement.pdf")
    bad = await send_file_to_owner(env.app, ctx, {"file_ref": f"sandbox:{res['job_id']}:run.exe"})
    assert "cannot send" in bad["error"]


async def test_sandbox_xlsx_helper_makes_a_real_workbook(env, tmp_path):
    sb = env.app.state.sandbox = Sandbox(str(tmp_path), "local")
    res = await sb.run("import pandas as pd, munim_helpers as mh\n"
                       "mh.write_xlsx({'Overdue': pd.DataFrame({'party': ['Ravi'], 'rupees': [12500.5]})}, 'out/r.xlsx')\n", {})
    assert res["exit_code"] == 0 and res["outputs"] == ["r.xlsx"]
    import io
    from openpyxl import load_workbook
    ws = load_workbook(io.BytesIO(sb.read_output(res["job_id"], "r.xlsx"))).active
    assert ws.freeze_panes == "A2" and [c.value for c in ws[2]] == ["Ravi", 12500.5]


# ------------------------------------------------------------------ voice notes, GSTIN, payment requests

async def test_owner_voice_note_is_transcribed_and_answered(env, monkeypatch):
    from src.ingest import owner_flow
    async def fake_fetch(app, msg):
        return b"ogg-bytes", "audio/ogg"
    async def fake_stt(key, audio, mime):
        assert audio == b"ogg-bytes" and mime == "audio/ogg"
        return "Ravi Kirana ka kitna baaki hai"
    monkeypatch.setattr(owner_flow, "fetch_media", fake_fetch)
    monkeypatch.setattr(owner_flow, "transcribe", fake_stt)
    llm = env.app.state.llm = ScriptLLM(say("Rs 10,000"))
    await dispatch(env.app, InboundMessage(wamid="v1", sender=OWNER, kind="audio", media_id="A1"))
    assert "[voice note, transcribed] Ravi Kirana ka kitna baaki hai" in llm.seen[0][-1]["content"]
    assert env.app.state.wa.sent[-1]["text"]["body"] == "Rs 10,000"
    async def silent(key, audio, mime):
        return ""
    monkeypatch.setattr(owner_flow, "transcribe", silent)
    await dispatch(env.app, InboundMessage(wamid="v2", sender=OWNER, kind="audio", media_id="A2"))
    assert "Voice note samajh nahi aaya" in env.app.state.wa.sent[-1]["text"]["body"]
    assert len(llm.seen) == 1                                                    # the model was not asked about silence


async def test_gstin_and_payment_request_tools(env):
    from src.agent.extras import build_payment_request, check_gstin
    ctx = SessionCtx("s", env.t.id, "TRUSTED")
    assert (await check_gstin(env.app, ctx, {"gstin": "27AAPFU0939F1ZV"}))["valid"] is True
    assert "checksum" in (await check_gstin(env.app, ctx, {"gstin": "27AAPFU0939F1ZX"}))["reason"]
    env.app.state.settings = Settings(public_base_url="https://ai.example/munim")
    r = await build_payment_request(env.app, ctx, {"obligation_id": env.oid})
    assert r["amount_rupees"] == 10000.0 and "Pay ₹10,000 to shop@upi (UPI). Ref: INV-1" in r["text"]
    assert r["link"].startswith("https://ai.example/munim/pay?pa=shop@upi") and "am=10000.00" in r["link"]
    assert "unknown" in (await build_payment_request(env.app, SessionCtx("s", env.other.id, "TRUSTED"), {"obligation_id": env.oid}))["error"]


async def test_scheduled_note_to_owner_waits_when_their_window_is_closed_and_arrives_after_a_reply(env):
    env.app.state.settings = Settings(wa_owner_template="owner_waiting")
    async with env.pool.connection() as c:
        await c.execute("update tenants set owner_last_inbound_at = now() - interval '30 hours' where id = %s", (env.t.id,))
    env.app.state.llm = ScriptLLM(call("send_whatsapp_message", to="owner", text="Ravi ne abhi tak pay nahi kiya."), say("done"))
    await tasks.start_followup_for_test(env) if hasattr(tasks, "start_followup_for_test") else None
    from src.agent_loop import start_session
    tenant = await tenants.by_id(env.pool, env.t.id)
    await start_session(env.app, tenant=tenant, trust="SYSTEM", trigger="scheduled_followup", content="tell the owner", background=False)
    assert [m["type"] for m in env.app.state.wa.sent] == ["template"]            # one nudge, not the note itself
    assert (await one(env.pool, "select count(*) n from outbound_messages where status = 'held' and kind = 'owner_note'"))["n"] == 1
    env.app.state.llm = ScriptLLM(say("hello"))
    await dispatch(env.app, owner("r1", "hi"))
    bodies = [m.get("text", {}).get("body", "") for m in env.app.state.wa.sent]
    assert any("Ravi ne abhi tak pay nahi kiya" in b for b in bodies)            # delivered the moment the owner wrote


async def test_arithmetic_for_part_payments_and_percent_discounts_is_computed_not_guessed(env):
    ctx = SessionCtx("s", env.t.id, "TRUSTED")
    a = {"obligation_id": env.oid, "kind": "PARTIAL_PAYMENT", "paid_rupees": 2500, "new_amount_rupees": 1, "evidence": "cash"}
    assert await actions.validate_gated(env.app, ctx, "propose_ledger_adjustment", a) is None
    assert a["new_amount_rupees"] == 7500.0                                      # the model's own figure was overridden
    d = {"obligation_id": env.oid, "kind": "DISCOUNT_CLAIM", "discount_percent": 12.5, "evidence": "e"}
    assert await actions.validate_gated(env.app, ctx, "propose_ledger_adjustment", d) is None
    assert d["new_amount_rupees"] == 8750.0
    for bad in ({"kind": "PARTIAL_PAYMENT", "paid_rupees": 10000}, {"kind": "PARTIAL_PAYMENT", "paid_rupees": 0},
                {"kind": "PARTIAL_PAYMENT", "paid_rupees": "lots"}, {"kind": "DISCOUNT_CLAIM", "discount_percent": 100},
                {"kind": "PARTIAL_PAYMENT"}):
        r = await actions.validate_gated(env.app, ctx, "propose_ledger_adjustment", {"obligation_id": env.oid, "evidence": "e", **bad})
        assert r and "paid_rupees" in r or "discount_percent" in r or "give paid_rupees" in r, bad
    from src.agent.policy import approval_text
    assert "new amount Rs 7500.0" in approval_text("propose_ledger_adjustment", a)


async def test_empty_model_replies_are_retried_not_treated_as_answers(env):
    llm = env.app.state.llm = ScriptLLM({"content": ""}, {"content": "  "}, say("Rs 10,000 baaki hai."))
    await dispatch(env.app, owner("e1", "Ravi ka kitna baaki hai?"))
    assert env.app.state.wa.sent[-1]["text"]["body"] == "Rs 10,000 baaki hai."
    assert (await one(env.pool, "select status from agent_sessions"))["status"] == "DONE"
    assert not any(m["role"] == "assistant" and not m.get("content") and not m.get("tool_calls") for m in llm.seen[-1])


async def test_persistently_empty_replies_tell_the_owner_instead_of_going_silent(env):
    env.app.state.llm = ScriptLLM({"content": ""}, {"content": ""}, {"content": ""}, {"content": ""})
    await dispatch(env.app, owner("e2", "hello"))
    assert (await one(env.pool, "select status from agent_sessions"))["status"] == "FAILED"
    assert "Sorry, I could not finish" in env.app.state.wa.sent[-1]["text"]["body"]


async def test_llm_calls_are_limited_to_the_configured_concurrency():
    import asyncio
    import httpx
    from src.loop.llm import LLM
    live = {"now": 0, "peak": 0}

    async def handler(request):
        live["now"] += 1
        live["peak"] = max(live["peak"], live["now"])
        await asyncio.sleep(0.05)
        live["now"] -= 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    llm = LLM("https://llm.test", "k", "m", max_concurrency=3,
              client=httpx.AsyncClient(base_url="https://llm.test", transport=httpx.MockTransport(handler)))
    await asyncio.gather(*[llm.chat([], []) for _ in range(12)])
    assert live["peak"] == 3                                                     # 12 requests, never more than 3 in flight


# ---------------------------------------------------------------- approvals follow the owner's channel

class TG:
    def __init__(self):
        self.sent, self.docs, self.answered = [], [], []

    async def send(self, payload):
        self.sent.append(payload)

    async def send_document(self, chat_id, data, filename, caption=""):
        self.docs.append((chat_id, filename))

    async def answer(self, cb_id):
        self.answered.append(cb_id)


async def _approval(env):
    from src.loop.owner_channel import send_to_owner
    t = await tenants.by_id(env.pool, env.t.id)
    payload = {"messaging_product": "whatsapp", "to": t.owner_phone, "type": "text", "text": {"body": "Approve?"}}
    return await send_to_owner(env.app, t, payload, kind="owner_agent_card")


async def _set(env, **cols):
    async with env.pool.connection() as c:
        for k, v in cols.items():
            await c.execute(f"update tenants set {k} = %s where id = %s", (v, env.t.id))


async def test_approval_goes_to_the_channel_the_owner_last_used(env):
    env.app.state.tg = TG()
    await _set(env, telegram_chat_id=555)
    await tenants.touch_owner_inbound(env.pool, env.t.id, "whatsapp")
    assert await _approval(env) == "whatsapp"                      # chatting on WhatsApp: stay there
    await tenants.touch_owner_inbound(env.pool, env.t.id, "telegram")
    assert await _approval(env) == "telegram" and env.app.state.tg.sent[-1]["chat_id"] == 555


async def test_a_telegram_message_does_not_open_the_whatsapp_window(env):
    from src.loop.owner_channel import window_open
    env.app.state.tg = TG()
    await _set(env, telegram_chat_id=555, owner_last_inbound_at=datetime.now(timezone.utc) - timedelta(days=2))
    await tenants.touch_owner_inbound(env.pool, env.t.id, "telegram")
    t = await tenants.by_id(env.pool, env.t.id)
    assert not window_open(t, datetime.now(timezone.utc)) and t.owner_last_channel == "telegram"


async def test_closed_whatsapp_window_falls_back_to_telegram_instead_of_holding(env):
    env.app.state.tg = TG()
    await _set(env, telegram_chat_id=555, owner_last_channel="whatsapp",
               owner_last_inbound_at=datetime.now(timezone.utc) - timedelta(days=2))
    assert await _approval(env) == "telegram"
    await _set(env, telegram_chat_id=None)
    env.app.state.tg = None
    assert await _approval(env) in ("whatsapp", "held")             # nothing else to try


async def test_telegram_owner_approves_a_browser_click_end_to_end(env):
    from src.ingest import telegram as tgmod
    tg = env.app.state.tg = TG()
    await _set(env, telegram_chat_id=555)
    br = env.app.state.browsers
    env.app.state.llm = ScriptLLM(call("browser_step", action="navigate", url="https://example.com/form"),
                                  call("browser_step", action="click", selector="#go"),
                                  say("Clicked and checked the page."))
    await tgmod.handle_update(env.app, {"message": {"chat": {"id": 555, "type": "private"}, "message_id": 1,
                                                    "text": "submit the form on example.com"}})
    card = tg.sent[-1]
    assert "click" in card["text"] and "Form" in card["text"] and "example.com/form" in card["text"]   # page context shown
    sid = (await one(env.pool, "select id from agent_sessions where status = 'WAITING_APPROVAL'"))["id"]
    assert [b[0]["callback_data"] for b in card["reply_markup"]["inline_keyboard"]] == [f"ag_ok:{sid}", f"ag_site:{sid}", f"ag_no:{sid}"]
    await tgmod.handle_update(env.app, {"callback_query": {"id": "cb1", "data": f"ag_ok:{sid}", "message": {"chat": {"id": 555}}}})
    assert [s["action"] for s in br.steps] == ["navigate", "click"]
    assert tg.sent[-1]["text"] == "Clicked and checked the page." and env.app.state.wa.sent == []   # all on Telegram


async def test_browser_never_types_passwords_or_otps(env):
    env.app.state.llm = ScriptLLM(call("browser_step", action="navigate", url="https://example.com/login"),
                                  call("browser_step", action="fill", selector="input[name=password]", value="hunter2"),
                                  call("browser_step", action="fill", selector="#code", value="OTP 482913"),
                                  lambda m: say(json.dumps([json.loads(x["content"]).get("error", "")[:30] for x in m if x["role"] == "tool"][-2:])))
    await dispatch(env.app, owner("bl1", "log in to the portal"))
    assert [s["action"] for s in env.app.state.browsers.steps] == ["navigate"]          # no fill ever reached the page
    assert (await one(env.pool, "select count(*) n from agent_sessions where status = 'WAITING_APPROVAL'"))["n"] == 0
