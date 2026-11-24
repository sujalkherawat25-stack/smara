"""Smara starts nothing by itself unless the owner turned it on; read marks, typing dots and moderated reactions on WhatsApp."""
from datetime import date, datetime, timezone
from uuid import uuid4
from types import SimpleNamespace

from test_agent_db import OWNER, env, one  # noqa: F401

from src import proactive
from src.agent import extras, policy, tools
from src.agent.policy import SessionCtx
from src.config import Settings
from src.ingest.dispatch import dispatch
from src.ingest.importer import import_rows
from src.ingest.payload import InboundMessage
from src.ingest.sheet_mapper import SheetRow
from src.loop import scheduler
from src.loop.reminders import claim_due

NOW = datetime.now(timezone.utc)


def settings(env, **kw):
    env.app.state.settings = Settings(**{**env.app.state.settings.__dict__, **kw})


# ------------------------------------------------------------------ nothing unasked

async def make_due(env):
    async with env.pool.connection() as c:
        await c.execute("update obligations set next_followup_at = now() - interval '1 day', state = 'ACTIVE'")


async def test_a_shop_that_never_turned_the_cards_on_is_not_asked_about_its_overdue_invoices(env):
    await make_due(env)
    assert await claim_due(env.pool, NOW, default_on=False) == []                      # production default
    await proactive.set_on(env.pool, env.t.id, True)
    got = await claim_due(env.pool, NOW, default_on=False)
    assert [str(r["tenant_id"]) for r in got] == [str(env.t.id)]                        # only the shop that asked


async def test_one_shop_never_gets_more_than_the_daily_cap_of_cards(env):
    rows = [SheetRow(f"Cust{i}", f"9198000000{i:02d}", f"INV-{i}", 100_000 + i, date(2026, 10, 1)) for i in range(2, 14)]
    await import_rows(env.pool, env.t.id, rows)                                         # 13 overdue invoices in all
    await make_due(env)
    first = await claim_due(env.pool, NOW, limit=50, cards_per_day=8)
    assert len([r for r in first if str(r["tenant_id"]) == str(env.t.id)]) == 8        # the cap is per shop
    assert await claim_due(env.pool, NOW, limit=50, cards_per_day=8) == []              # the rest wait for tomorrow


async def test_the_morning_summary_goes_only_to_owners_who_turned_it_on(env):
    settings(env, proactive_default=False)
    local = datetime(2026, 11, 23, 9, 0, tzinfo=scheduler.IST)                          # a Monday: the weekly review day
    assert await scheduler.send_digests(env.app, local) == 0
    assert (await one(env.pool, "select count(*) n from digest_log"))["n"] == 0            # not even marked as done
    await proactive.set_on(env.pool, env.t.id, True)
    assert await scheduler.send_digests(env.app, local) >= 1


async def test_only_the_owner_can_switch_it_on_and_it_is_a_light_tool(env):
    assert "set_proactive_messages" in tools.TOOL_NAMES
    ok = policy.pre_tool_use(SessionCtx("s", env.t.id, "TRUSTED"), "set_proactive_messages", {"on": True}, tools.TOOL_NAMES)
    assert ok.verdict == "allow"
    for trust in ("UNTRUSTED", "SYSTEM"):
        assert policy.pre_tool_use(SessionCtx("s", env.t.id, trust), "set_proactive_messages", {"on": True}, tools.TOOL_NAMES).verdict == "deny"
    ctx = SessionCtx("s", env.t.id, "TRUSTED")
    assert "error" in await proactive.set_proactive_messages(env.app, ctx, {"on": "yes"})
    await proactive.set_proactive_messages(env.app, ctx, {"on": True})
    assert await proactive.enabled(env.pool, env.t.id)
    await proactive.set_proactive_messages(env.app, ctx, {"on": False})
    assert not await proactive.enabled(env.pool, env.t.id)


async def test_the_same_reminder_asked_twice_is_one_task(env):
    ctx = SessionCtx(str(uuid4()), env.t.id, "TRUSTED")
    from datetime import timedelta
    from src import clock
    when = (clock.now().astimezone(clock.IST) + timedelta(days=1)).strftime("%Y-%m-%d") + " 10:00"   # always in the future
    a = await extras.schedule_followup(env.app, ctx, {"instruction": "Ravi se Rs 5000 collect karna yaad dilana", "at_ist": when})
    b = await extras.schedule_followup(env.app, ctx, {"instruction": "Ravi Traders se Rs 5,000 collect karne hain", "at_ist": when})
    assert b.get("already_scheduled") and b["task_id"] == a["task_id"]
    c = await extras.schedule_followup(env.app, ctx, {"instruction": "call the bank about the loan", "at_ist": when})
    assert "already_scheduled" not in c                                                 # a different reminder at the same time is fine
    assert (await one(env.pool, "select count(*) n from agent_tasks where status = 'PENDING'"))["n"] == 2


# ------------------------------------------------------------------ blue ticks, reactions

class WA:
    def __init__(self):
        self.sent, self.read, self.reacted = [], [], []

    async def send(self, payload, **kw):
        self.sent.append(payload)
        return "wamid.out"

    async def mark_read(self, wamid, **kw):
        self.read.append(wamid)

    async def react(self, to, wamid, emoji, **kw):
        self.reacted.append((to, wamid, emoji))


async def test_a_whatsapp_message_gets_the_blue_ticks_and_a_reaction_gets_nothing(env):
    import asyncio
    env.app.state.wa = WA()
    await dispatch(env.app, InboundMessage(wamid="w-hello", sender=OWNER, kind="text", text="/admin"))
    await asyncio.sleep(0.05)
    assert env.app.state.wa.read == ["w-hello"]
    before = len(env.app.state.wa.sent)
    await dispatch(env.app, InboundMessage(wamid="w-react", sender=OWNER, kind="reaction"))
    await asyncio.sleep(0.05)
    assert env.app.state.wa.read == ["w-hello"] and len(env.app.state.wa.sent) == before   # no reply, no read mark


async def test_the_agent_reacts_on_whatsapp_once_in_a_while_with_an_allowed_emoji(env):
    env.app.state.wa = WA()
    env.app.state.wa_last = {str(env.t.id): (OWNER, "w-last", "numid")}
    async with env.pool.connection() as c:
        await c.execute("update tenants set owner_last_channel = 'whatsapp' where id = %s", (env.t.id,))
    ctx = SessionCtx("s", env.t.id, "TRUSTED")
    assert (await extras.react_to_owner(env.app, ctx, {"emoji": "🖕"}))["reacted"] is False
    assert (await extras.react_to_owner(env.app, ctx, {"emoji": "👍"}))["reacted"] is True
    assert env.app.state.wa.reacted == [(OWNER, "w-last", "👍")]
    again = await extras.react_to_owner(env.app, ctx, {"emoji": "🎉"})
    assert again["reacted"] is False and len(env.app.state.wa.reacted) == 1             # not twice in a row
