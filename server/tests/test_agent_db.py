"""Agent loop against Postgres with a scripted model: tools, PreToolUse, gates, resume, self-verification."""
import asyncio
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from itertools import count
from types import SimpleNamespace

import pytest

from src import tenants
from src.agent import actions
from src.agent.policy import SessionCtx, pre_tool_use
from src.agent.tools import TOOL_NAMES, query_ledger_sql
from src.agent_loop import MAX_STEPS, start_session
from src.config import Settings
from src.db import migrate, open_pool
from src.ingest.dispatch import dispatch
from src.ingest.importer import import_rows
from src.ingest.payload import InboundMessage
from src.ingest.sheet_mapper import SheetRow
from src.loop.scheduler import tick

URL = os.environ.get("TEST_DATABASE_URL")
OWNER, DEBTOR = "919000000001", "919876543210"
NOW = datetime.now(timezone.utc) + timedelta(minutes=20)  # ahead of DB now() even after a slow full run
_ids = count(1)


def call(name, **args):
    return {"content": "", "tool_calls": [{"id": f"call-{next(_ids)}", "type": "function",
                                           "function": {"name": name, "arguments": json.dumps(args)}}]}


def say(text):
    return {"content": text}


class ScriptLLM:
    enabled = True

    def __init__(self, *steps):
        self.steps, self.seen = list(steps), []

    async def chat(self, messages, tools, allow_fallback=True):
        self.seen.append(json.loads(json.dumps(messages)))
        step = self.steps.pop(0) if self.steps else say("done")
        if isinstance(step, Exception):
            raise step
        return step(messages) if callable(step) else step

    def last_tool_result(self, i=-1):
        return json.loads([m for m in self.seen[i] if m["role"] == "tool"][-1]["content"])


class FakeWA:
    def __init__(self):
        self.sent, self.uploads = [], []

    async def send(self, payload, **kw):
        self.sent.append(payload)
        return f"wamid.{next(_ids)}"

    async def upload_media(self, data, mime, filename, **kw):
        self.uploads.append((filename, mime, len(data)))
        return f"media.{len(self.uploads)}"

    def to(self, phone):
        return [m for m in self.sent if m["to"] == phone]


class FakeBrowsers:
    def __init__(self):
        self.steps, self.open = [], set()

    def alive(self, sid):
        return sid in self.open

    async def step(self, sid, args, **kw):
        self.steps.append(args)
        self.open.add(sid)
        return {"url": "https://example.com/form", "title": "Form", "text": "ok", "elements": [{"selector": "#go"}]}

    def page(self, sid):
        return {"url": "https://example.com/form", "title": "Form"} if sid in self.open else {}

    async def close(self, sid):
        self.open.discard(sid)


class FakeContinuum:
    async def resolve(self, **kw):
        return {"resolution": {"state_card": "Promised to pay by Friday."}}


@pytest.fixture
async def env(tmp_path):
    if not URL:
        pytest.skip("TEST_DATABASE_URL not set")
    pool = await open_pool(URL)
    async with pool.connection() as c:
        await c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    await migrate(pool)
    t = await tenants.create(pool, name="Shop", owner_phone=OWNER, upi_vpa="shop@upi")
    other = await tenants.create(pool, name="Other", owner_phone="919000000002")
    await import_rows(pool, t.id, [SheetRow("Ravi", DEBTOR, "INV-1", 1_000_000, date(2026, 11, 1))])
    await import_rows(pool, other.id, [SheetRow("Secret", "919111111111", "SECRET-9", 99_900_000, date(2027, 1, 1))])
    async with pool.connection() as c:
        o = await (await c.execute("SELECT o.id, o.party_id FROM obligations o WHERE invoice_ref = 'INV-1'")).fetchone()
    app = SimpleNamespace(state=SimpleNamespace(pool=pool, settings=Settings(reminder_send_mode="smara", owner_files_dir=str(tmp_path / "owner-files")),
                                                wa=FakeWA(), tg=None, bg=set(),
                                                continuum=FakeContinuum(), browsers=FakeBrowsers(), llm=ScriptLLM(),
                                                agent_background=False))
    yield SimpleNamespace(app=app, pool=pool, t=t, other=other, oid=str(o["id"]), pid=str(o["party_id"]))
    await pool.close()


async def one(pool, sql, *a):
    async with pool.connection() as c:
        return await (await c.execute(sql, a)).fetchone()


def owner(wamid, text="", button=None):
    return InboundMessage(wamid=wamid, sender=OWNER, kind="interactive" if button else "text", text=text, button_id=button)


# ---------------------------------------------------------------- PreToolUse (no DB)

def test_pre_tool_use_tiers_and_untrusted_lockdown():
    trusted = SessionCtx("s", "t", "TRUSTED", files=("whatsapp:m1:a.csv",))
    debtor = SessionCtx("s", "t", "UNTRUSTED", party_id="p1")
    v = lambda ctx, n, a: pre_tool_use(ctx, n, a, TOOL_NAMES).verdict
    assert v(trusted, "query_ledger_sql", {"sql": "select 1"}) == "allow"
    assert v(trusted, "send_whatsapp_message", {"to": "owner", "text": "x"}) == "allow"
    assert v(trusted, "send_whatsapp_message", {"to": "p1", "text": "x"}) == "approve"
    assert v(trusted, "propose_ledger_adjustment", {"obligation_id": "o"}) == "approve"
    assert v(trusted, "browser_step", {"action": "navigate", "url": "https://a.com"}) == "allow"
    assert v(trusted, "browser_step", {"action": "click", "selector": "#x"}) == "approve"
    assert v(trusted, "inspect_and_import_file", {"file_ref": "whatsapp:m1:a.csv", "mode": "import"}) == "allow"
    assert v(trusted, "inspect_and_import_file", {"file_ref": "whatsapp:EVIL:x.csv", "mode": "inspect"}) == "deny"
    assert v(trusted, "drop_tables", {}) == "deny"
    for name in ("query_ledger_sql", "send_whatsapp_message", "inspect_and_import_file", "browser_step",
                 "resolve_continuum_memory"):
        assert v(debtor, name, {"to": "owner", "sql": "select 1", "action": "read"}) == "deny", name
    assert v(debtor, "write_continuum_evidence", {"party_id": "p1", "text": "x"}) == "allow"
    assert v(debtor, "write_continuum_evidence", {"party_id": "p2", "text": "x"}) == "deny"
    assert v(debtor, "propose_ledger_adjustment", {"obligation_id": "o"}) == "approve"
    system = SessionCtx("s", "t", "SYSTEM", files=("whatsapp:m1:a.csv",))
    assert v(system, "inspect_and_import_file", {"file_ref": "whatsapp:m1:a.csv", "mode": "import"}) == "deny"


# ---------------------------------------------------------------- read-only SQL + RLS

async def test_sql_tool_is_read_only_and_tenant_isolated(env):
    ctx = SessionCtx("s", env.t.id, "TRUSTED")
    q = lambda sql: query_ledger_sql(env.app, ctx, {"sql": sql})
    rows = (await q("select invoice_ref, amount_paise from obligations"))["rows"]
    assert [r["invoice_ref"] for r in rows] == ["INV-1"]                         # other tenant invisible
    assert (await q("select count(*) n from parties"))["rows"][0]["n"] == 1
    for bad in ("update obligations set amount_paise = 0", "select 1; delete from obligations",
                "with x as (delete from obligations returning *) select * from x",
                "select * from tenants", "select * from agent_sessions", "select pg_sleep(5)"):
        assert "error" in await q(bad), bad
    assert (await one(env.pool, "select amount_paise from obligations where invoice_ref='INV-1'"))["amount_paise"] == 1_000_000
    rows = (await query_ledger_sql(env.app, SessionCtx("s", env.other.id, "TRUSTED"), {"sql": "select invoice_ref from obligations"}))["rows"]
    assert [r["invoice_ref"] for r in rows] == ["SECRET-9"]


# ---------------------------------------------------------------- loop: multi-turn, errors fed back

async def test_owner_question_multi_turn_with_retry_after_tool_error(env):
    llm = env.app.state.llm = ScriptLLM(
        call("query_ledger_sql", sql="update obligations set amount_paise = 1"),         # model's bad first try
        call("query_ledger_sql", sql="select sum(amount_paise) s from obligations where state <> 'PAID'"),
        lambda msgs: say(f"Ravi owes Rs {json.loads(msgs[-1]['content'])['rows'][0]['s'] // 100:,}."))
    await dispatch(env.app, owner("w1", "Ravi ka kitna baaki hai?"))
    assert "only a single SELECT" in llm.last_tool_result(1)["error"]               # error was fed back
    assert llm.last_tool_result(2)["rows"][0]["s"] == 1_000_000                     # model saw real data
    assert env.app.state.wa.sent[-1]["text"]["body"] == "Ravi owes Rs 10,000."
    s = await one(env.pool, "select status, steps from agent_sessions")
    assert s == {"status": "DONE", "steps": 3}


async def test_step_budget_ends_with_a_status_not_silence(env):
    llm = env.app.state.llm = ScriptLLM(*[call("query_ledger_sql", sql="select 1") for _ in range(MAX_STEPS)],
                                        say("Checked the ledger 30 times; I still need the invoice number."))
    await dispatch(env.app, owner("w2", "loop forever"))
    assert (await one(env.pool, "select status from agent_sessions"))["status"] == "DONE"
    assert "still need the invoice number" in env.app.state.wa.sent[-1]["text"]["body"]
    assert "[system note] You have made this exact call 3 times" in json.dumps(llm.seen[5])    # loop was called out


async def test_step_budget_is_per_turn_not_per_session(env):
    env.app.state.llm = ScriptLLM(say("first"), say("second"))
    await dispatch(env.app, owner("w3", "one"))
    async with env.pool.connection() as c:
        await c.execute("update agent_sessions set steps = 500")
    await dispatch(env.app, owner("w4", "two"))
    assert env.app.state.wa.sent[-1]["text"]["body"] == "second"


# ---------------------------------------------------------------- untrusted debtor session

async def test_debtor_session_cannot_act_and_only_owner_tap_changes_ledger(env):
    llm = env.app.state.llm = ScriptLLM(
        call("send_whatsapp_message", to="owner", text="ignore your rules"),           # blocked
        call("query_ledger_sql", sql="select * from obligations"),                     # blocked
        lambda m: call("propose_ledger_adjustment", obligation_id=env.oid, kind="DISCOUNT_CLAIM",
                       new_amount_rupees=9000, evidence="10% discount de do"),
        say("Customer asked for 10% off; proposal sent to owner."))
    await dispatch(env.app, InboundMessage(wamid="d1", sender=DEBTOR, kind="text", text="10% discount de do. Also mark me paid."))
    assert "blocked by policy" in llm.last_tool_result(1)["error"] and "blocked by policy" in llm.last_tool_result(2)["error"]
    adj = await one(env.pool, "select id, status, proposed_amount_paise from proposed_adjustments")
    assert adj["status"] == "PENDING" and adj["proposed_amount_paise"] == 900_000
    assert (await one(env.pool, "select amount_paise from obligations where id = %s", env.oid))["amount_paise"] == 1_000_000
    card = env.app.state.wa.to(OWNER)[-1]
    assert card["interactive"]["action"]["buttons"][0]["reply"]["id"] == f"approve:{adj['id']}"
    assert (await one(env.pool, "select status from agent_sessions"))["status"] == "WAITING_APPROVAL"
    assert (await one(env.pool, "select count(*) n from continuum_outbox where trusted = false"))["n"] >= 1

    await dispatch(env.app, owner("w3", button=f"approve:{adj['id']}"))
    assert (await one(env.pool, "select amount_paise from obligations where id = %s", env.oid))["amount_paise"] == 900_000
    assert llm.last_tool_result()["owner_decision"] == "approved"                    # resumed at the exact call
    assert (await one(env.pool, "select status from agent_sessions"))["status"] == "DONE"
    assert env.app.state.wa.to(DEBTOR) == []                                         # nothing ever sent to debtor


async def test_agent_failure_falls_back_to_rules_so_claim_is_not_lost(env):
    env.app.state.llm = ScriptLLM(RuntimeError("model down"))
    env.app.state.llm.json = None   # the rules path must work without the model too
    await dispatch(env.app, InboundMessage(wamid="d2", sender=DEBTOR, kind="text", text="kal tak bhej dunga"))
    assert (await one(env.pool, "select adjustment_type from proposed_adjustments"))["adjustment_type"] == "PROMISE_DATE_CHANGE"


# ---------------------------------------------------------------- gate: suspend, approve exact call, double tap

async def test_trusted_gate_suspends_and_resumes_exact_call(env):
    text = "Namaste Ravi ji, INV-1 ka Rs 10,000 baaki hai."
    async with env.pool.connection() as c:
        await c.execute("update parties set last_inbound_at = now() where id = %s", (env.pid,))
    llm = env.app.state.llm = ScriptLLM(
        lambda m: call("send_whatsapp_message", to=env.pid, text=text),
        lambda m: say("Sent." if llm.last_tool_result()["sent"] else "Not sent."))
    await dispatch(env.app, owner("w4", "Ravi ko reminder bhejo"))
    assert env.app.state.wa.to(DEBTOR) == []                                         # waits for the owner
    sid = (await one(env.pool, "select id from agent_sessions where status = 'WAITING_APPROVAL'"))["id"]
    card = env.app.state.wa.to(OWNER)[-1]
    assert text in card["interactive"]["body"]["text"] and "Ravi" in card["interactive"]["body"]["text"]
    await dispatch(env.app, owner("w5", button=f"ag_ok:{sid}"))
    shown = card["interactive"]["body"]["text"]
    assert env.app.state.wa.to(DEBTOR)[0]["text"]["body"] == "*Shop*" + chr(10) + text and "*Shop*" in shown   # exactly what was shown
    assert env.app.state.wa.sent[-1]["text"]["body"] == "Sent."
    await dispatch(env.app, owner("w6", button=f"ag_ok:{sid}"))
    assert len(env.app.state.wa.to(DEBTOR)) == 1 and "already handled" in env.app.state.wa.sent[-1]["text"]["body"]


async def test_rejected_gate_feeds_back_and_sends_nothing(env):
    llm = env.app.state.llm = ScriptLLM(lambda m: call("send_whatsapp_message", to=env.pid, text="hi"),
                                        lambda m: say("Okay, not sending."))
    await dispatch(env.app, owner("w7", "message Ravi"))
    sid = (await one(env.pool, "select id from agent_sessions"))["id"]
    await dispatch(env.app, owner("w8", button=f"ag_no:{sid}"))
    assert "rejected" in llm.last_tool_result()["error"] and env.app.state.wa.to(DEBTOR) == []


async def test_browser_read_runs_but_click_waits_for_owner(env):
    br = env.app.state.browsers
    env.app.state.llm = ScriptLLM(call("browser_step", action="navigate", url="https://example.com/form"),
                                  call("browser_step", action="click", selector="#go"),
                                  say("Clicked and verified the page."))
    await dispatch(env.app, owner("w9", "submit the form on example.com"))
    assert [s["action"] for s in br.steps] == ["navigate"]                           # click not yet run
    sid = (await one(env.pool, "select id from agent_sessions where status = 'WAITING_APPROVAL'"))["id"]
    await dispatch(env.app, owner("w10", button=f"ag_ok:{sid}"))
    assert [s["action"] for s in br.steps] == ["navigate", "click"] and br.steps[1]["selector"] == "#go"


# ---------------------------------------------------------------- import with self-verification

def attached_ref(messages) -> str:
    """The file ref of the owner's attachment: owner files are downloaded on arrival and kept as local refs."""
    for m in reversed(messages):
        if m["role"] == "user" and "file_ref=" in str(m["content"]):
            return re.search(r"file_ref=(local:[^;\]\s]+)", m["content"]).group(1)
    raise AssertionError("no attachment")


def owner_file_bytes(monkeypatch, data: bytes, mime: str):
    from src.ingest import owner_flow

    async def fake_fetch(app, msg):
        return data, mime
    monkeypatch.setattr(owner_flow, "fetch_media", fake_fetch)


async def test_import_reports_totals_for_the_agent_to_verify(env, monkeypatch):
    csv = b"Party Name,Mobile Number,Invoice No,Due Date,Balance\nAsha,9811122233,V-1,15/11/2026,500\nBina,9811122234,V-2,16/11/2026,250\n"
    owner_file_bytes(monkeypatch, csv, "text/csv")
    llm = env.app.state.llm = ScriptLLM(lambda m: call("inspect_and_import_file", file_ref=attached_ref(m), mode="inspect"),
                                        lambda m: call("inspect_and_import_file", file_ref=attached_ref(m), mode="import"),
                                        lambda m: say("Imported 2 invoices, Rs 750, totals verified."))
    await dispatch(env.app, InboundMessage(wamid="w11", sender=OWNER, kind="document", media_id="MEDIA1", filename="out.csv"))
    assert llm.last_tool_result(1)["sheet_total_rupees"] == 750
    done = llm.last_tool_result(2)
    assert done["created"] == 2 and done["totals_match"] is True and done["ledger_total_rupees_for_these_invoices"] == 750


# ---------------------------------------------------------------- cron: agent drafts, owner approves

async def test_cron_agent_drafts_reminder_and_approval_dispatches(env):
    async with env.pool.connection() as c:
        await c.execute("update parties set last_inbound_at = now() where id = %s", (env.pid,))
    env.app.state.llm = ScriptLLM(
        lambda m: call("resolve_continuum_memory", party_id=env.pid, query="promises"),
        lambda m: call("send_whatsapp_message", to=env.pid, obligation_id=env.oid,
                       text="Ravi ji, Friday ka promise tha. Pay Rs 10,000 to shop@upi (UPI). Ref: INV-1"),
        say("Reminder queued for approval."))
    assert await tick(env.app, NOW) == 1
    assert (await one(env.pool, "select state from obligations where id = %s", env.oid))["state"] == "WAITING_OWNER_APPROVAL"
    sid = (await one(env.pool, "select id from agent_sessions where trigger = 'cron_reminder'"))["id"]
    await dispatch(env.app, owner("w12", button=f"ag_ok:{sid}"))
    st = await one(env.pool, "select state, attempt_count from obligations where id = %s", env.oid)
    assert st == {"state": "DISPATCHED", "attempt_count": 1}
    assert "Friday" in env.app.state.wa.to(DEBTOR)[0]["text"]["body"]


# ---------------------------------------------------------------- sandbox: Tally XML -> rows -> verified import

async def test_agent_converts_tally_xml_in_sandbox_and_verifies_import(env, monkeypatch, tmp_path):
    from pathlib import Path
    from src.box.sandbox import Sandbox
    env.app.state.sandbox = Sandbox(str(tmp_path), "local")
    xml = (Path(__file__).parent / "fixtures" / "tally_daybook.xml").read_bytes()
    owner_file_bytes(monkeypatch, xml, "text/xml")
    code = ("import smara_helpers as mh\nfrom datetime import timedelta\n"
            "phones = {l['name']: l['mobile'] for l in mh.tally_ledgers('in/daybook.xml')}\n"
            "sales = [v for v in mh.tally_vouchers('in/daybook.xml') if v['type'] == 'Sales']\n"
            "rows = [dict(party=v['party'], phone=phones.get(v['party'], ''), invoice=v['number'],\n"
            "             due_date=v['date'] + timedelta(days=v['credit_days'] or 30), amount=v['amount']) for v in sales]\n"
            "mh.to_rows_csv(rows)\nRESULT = {'invoices': len(rows), 'total': sum(r['amount'] for r in rows)}")
    llm = env.app.state.llm = ScriptLLM(
        lambda m: call("run_python", code=code, file_refs=[attached_ref(m)]),
        lambda m: call("inspect_and_import_file", file_ref=json.loads(m[-1]["content"])["output_file_refs"][0], mode="import"),
        lambda m: say("Imported 2 Tally invoices, Rs 16,500.50, totals verified."))
    await dispatch(env.app, InboundMessage(wamid="t1", sender=OWNER, kind="document", media_id="MX", filename="daybook.xml"))
    ran = llm.last_tool_result(1)
    assert ran["exit_code"] == 0 and ran["result"] == {"invoices": 2, "total": 16500.5}
    imp = llm.last_tool_result(2)
    assert imp["created"] == 2 and imp["totals_match"] is True and imp["ledger_total_rupees_for_these_invoices"] == 16500.5
    due = await one(env.pool, "select due_date from obligations where invoice_ref = 'S-101'")
    assert due["due_date"] == date(2026, 11, 4)                                 # date + 30 credit days


def test_sandbox_policy():
    debtor = SessionCtx("s", "t", "UNTRUSTED", party_id="p1", files=("whatsapp:a:x.csv",))
    owner_ctx = SessionCtx("s", "t", "TRUSTED", files=("whatsapp:a:x.csv",))
    v = lambda ctx, a: pre_tool_use(ctx, "run_python", a, TOOL_NAMES).verdict
    assert v(debtor, {"code": "print(1)"}) == "deny"                            # no compute for counterparties
    assert v(owner_ctx, {"code": "print(1)", "file_refs": ["whatsapp:a:x.csv"]}) == "allow"
    assert v(owner_ctx, {"code": "print(1)", "file_refs": ["sandbox:deadbeef:other.csv"]}) == "deny"
    assert v(owner_ctx, {"code": ""}) == "deny"


async def test_llm_retries_rate_limits_then_succeeds():
    import httpx
    from src.loop.llm import LLM
    hits = {"n": 0}

    def handler(request):
        hits["n"] += 1
        if hits["n"] < 3:
            return httpx.Response(429, headers={"retry-after": "0"}, json={"error": "slow down"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 5}})
    llm = LLM("https://llm.test", "k", "m", client=httpx.AsyncClient(base_url="https://llm.test",
                                                                   transport=httpx.MockTransport(handler)))
    assert (await llm.chat([], []))["content"] == "ok" and hits["n"] == 3 and llm.last_usage["total_tokens"] == 5
    never = LLM("https://llm.test", "k", "m", max_retries=1, client=httpx.AsyncClient(
        base_url="https://llm.test", transport=httpx.MockTransport(lambda r: httpx.Response(429, headers={"retry-after": "0"}))))
    from src.loop.llm import RateLimited
    with pytest.raises(RateLimited):
        await never.chat([], [])


async def test_llm_falls_back_to_a_second_model_when_rate_limited():
    import httpx
    from src.loop.llm import LLM
    seen = []

    def handler(request):
        model = __import__("json").loads(request.content)["model"]
        seen.append(model)
        if model == "big":
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "from-small"}}]})
    llm = LLM("https://llm.test", "k", "small", agent_model="big", fallback_model="small", max_retries=1,
              client=httpx.AsyncClient(base_url="https://llm.test", transport=httpx.MockTransport(handler)))
    assert (await llm.chat([], []))["content"] == "from-small"
    assert seen == ["big", "big", "small"] and llm.stats["fallbacks"] == 1


async def test_token_bucket_paces_after_the_burst():
    import time
    from src.loop.llm import Bucket
    b = Bucket(rpm=600, burst=2)                    # 10 per second
    t = time.monotonic()
    for _ in range(4):
        await b.take()
    assert 0.15 < time.monotonic() - t < 0.6        # 2 immediate, 2 paced at ~0.1 s each


async def test_rules_fallback_uses_the_invoice_the_debtor_names(env):
    await import_rows(env.pool, env.t.id, [SheetRow("Ravi", DEBTOR, "INV-2", 400_000, date(2026, 11, 2))])
    env.app.state.llm = ScriptLLM(RuntimeError("model down"))
    env.app.state.llm.json = None
    await dispatch(env.app, InboundMessage(wamid="d9", sender=DEBTOR, kind="text", text="INV-2 ka payment kar diya, UTR 412345678901"))
    assert (await one(env.pool, """select o.invoice_ref from proposed_adjustments a
                                   join obligations o on o.id = a.obligation_id"""))["invoice_ref"] == "INV-2"


async def test_business_dates_are_ist(env):
    from datetime import timezone as tz
    from src import clock
    rows = (await query_ledger_sql(env.app, SessionCtx("s", env.t.id, "TRUSTED"), {"sql": "select current_date d"}))["rows"]
    assert rows[0]["d"] == clock.today()                                   # SQL "today" is the shop's today
    assert clock.today() == datetime.now(tz(timedelta(hours=5, minutes=30))).date()


async def test_many_sessions_starting_at_once_never_deadlock_a_tiny_pool(env):
    """Regression: start_session built the prompt (needs a connection) while holding one, so a handful of
    simultaneous owner messages exhausted the pool and every one of them timed out."""
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool
    tiny = AsyncConnectionPool(URL, kwargs={"row_factory": dict_row}, min_size=2, max_size=2, timeout=8, open=False)
    await tiny.open()
    try:
        real, env.app.state.pool = env.app.state.pool, tiny
        env.app.state.llm = ScriptLLM(*[say(f"ok{i}") for i in range(8)])
        await asyncio.wait_for(asyncio.gather(*[dispatch(env.app, owner(f"par{i}", f"parallel {i}")) for i in range(8)]), 30)
        env.app.state.pool = real
    finally:
        await tiny.close()
    assert len([m for m in env.app.state.wa.to(OWNER) if m.get("type") == "text"]) >= 1


async def test_file_request_without_a_file_gets_one_nudge(env):
    llm = env.app.state.llm = ScriptLLM(say("Pehle hi bhej diya hai."), say("Ab bhej raha hoon."))
    await dispatch(env.app, owner("pdfreq", "Aur mujhe pdf statement bhejo"))
    assert "owner asked for a file" in llm.seen[1][-1]["content"]
    assert env.app.state.wa.sent[-1]["text"]["body"] == "Ab bhej raha hoon."          # the nudge is once, not a loop
    llm2 = env.app.state.llm = ScriptLLM(say("Theek hai."))
    await dispatch(env.app, owner("chat1", "Ravi ka kitna baaki hai?"))
    assert len(llm2.seen) == 1                                                          # unrelated messages are untouched


def test_heavy_work_never_uses_the_fallback_model():
    from src.agent_loop import heavy
    sys_, user = {"role": "system", "content": "s"}, lambda t: {"role": "user", "content": t}
    assert not heavy([sys_, user("Ravi ka kitna baaki hai?")])
    assert heavy([sys_, user("Ravi ka statement pdf bhejo")])
    assert heavy([sys_, user("import this\n[The owner attached a file: file_ref=whatsapp:M1:a.xlsx; caption: -]")])
    assert heavy([sys_, user("hi"), {"role": "assistant", "content": "", "tool_calls": [
        {"id": "1", "type": "function", "function": {"name": "run_python", "arguments": "{}"}}]}])


async def test_a_reminder_described_but_never_made_gets_one_nudge_and_then_the_card(env):
    text = "Namaste Ravi ji, INV-1 ka Rs 10,000 baaki hai."
    async with env.pool.connection() as c:
        await c.execute("update parties set last_inbound_at = now() where id = %s", (env.pid,))
    llm = env.app.state.llm = ScriptLLM(say("Reminder ready hai, aapke approval ka intezaar hai."),
                                        lambda m: call("send_whatsapp_message", to=env.pid, text=text), say("Sent."))
    await dispatch(env.app, owner("rem1", "Ravi ko payment ka reminder bhejo"))
    assert "no approval card" in llm.seen[1][-1]["content"]
    assert env.app.state.wa.to(DEBTOR) == []                                          # still waits for the owner
    assert text in env.app.state.wa.to(OWNER)[-1]["interactive"]["body"]["text"]        # the card exists now
    llm2 = env.app.state.llm = ScriptLLM(say("Ravi ka Rs 10,000 baaki hai."))
    await dispatch(env.app, owner("rem2", "Ravi ka kitna baaki hai?"))
    assert len(llm2.seen) == 1                                                          # other questions are untouched


def test_owes_card_only_when_a_card_was_claimed_and_not_made():
    from src.agent_loop import owes_card
    ask = [{"role": "user", "content": "Gupta ko reminder bhejo"}]
    assert owes_card(ask, "Reminder ready hai, approval ka intezaar hai") and owes_card(ask, "Shall I send it?")
    assert not owes_card(ask, "Gupta ka number nahi hai, isliye nahi bhej sakta")
    owner_draft = {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "send_whatsapp_message", "arguments": '{"to": "owner"}'}}]}
    to_customer = {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "send_whatsapp_message", "arguments": '{"to": "p1"}'}}]}
    assert owes_card(ask + [owner_draft], "Draft aapko bhej diya")                      # a draft to the owner is not a card
    assert not owes_card(ask + [to_customer], "Waiting for approval")
    assert not owes_card([{"role": "user", "content": "Gupta ka kitna baaki hai"}], "Shall I send a PDF?")
