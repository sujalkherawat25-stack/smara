"""Phase B against Postgres: delivery receipts, owner 24h window, re-send, Telegram owner channel."""
import os
from datetime import date, datetime, timedelta, timezone
from itertools import count
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src import tenants
from src.config import Settings
from src.db import migrate, open_pool
from src.ingest import telegram as tgmod
from src.ingest.dispatch import dispatch
from src.ingest.importer import import_rows
from src.ingest.payload import InboundMessage, StatusUpdate, parse_statuses
from src.ingest.sheet_mapper import SheetRow
from src.loop.outbound import apply_status
from src.loop.reminders import decide_reminder
from src.loop.scheduler import tick

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")
NOW = datetime.now(timezone.utc) + timedelta(minutes=20)  # ahead of DB now() even after a slow full run
OWNER, DEBTOR = "919000000001", "919876543210"
_ids = count(1)


class FakeWA:
    def __init__(self):
        self.sent = []

    async def send(self, payload, **kw):
        self.sent.append(payload)
        return f"wamid.{next(_ids)}"


class FakeTG:
    def __init__(self):
        self.sent, self.answered = [], []

    async def send(self, payload):
        self.sent.append(payload)

    async def answer(self, cid, text=""):
        self.answered.append(cid)

    async def username(self):
        return "smara_test_bot"


class FakeContinuum:
    async def resolve(self, **kw):
        return {"resolution": {"state_card": ""}}


def make_app(pool, **settings):
    return SimpleNamespace(state=SimpleNamespace(
        pool=pool, settings=Settings(**{"reminder_send_mode": "smara", **settings}), wa=FakeWA(), tg=FakeTG(), continuum=FakeContinuum(), bg=set(),
        llm=SimpleNamespace(enabled=False)))


@pytest.fixture
async def pool():
    p = await open_pool(URL)
    async with p.connection() as c:
        await c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    await migrate(p)
    yield p
    await p.close()


async def seed(pool, inbound_hours_ago=None):
    t = await tenants.create(pool, name="Shop", owner_phone=OWNER, upi_vpa="shop@upi")
    if inbound_hours_ago is not None:
        async with pool.connection() as c:
            await c.execute("UPDATE tenants SET owner_last_inbound_at = now() - %s * interval '1 hour' WHERE id = %s",
                            (inbound_hours_ago, t.id))
    await import_rows(pool, t.id, [SheetRow("Ravi", DEBTOR, "INV-1", 100_000, date(2026, 11, 1))])
    async with pool.connection() as c:
        oid = str((await (await c.execute("SELECT id FROM obligations")).fetchone())["id"])
    return t, oid


async def one(pool, sql, *a):
    async with pool.connection() as c:
        return await (await c.execute(sql, a)).fetchone()


def owner_msg(wamid, text="hi", button=None):
    return InboundMessage(wamid=wamid, sender=OWNER, kind="interactive" if button else "text", text=text, button_id=button)


def test_parse_failed_receipt():
    body = {"entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.X", "status": "failed", "recipient_id": "91 98",
                                                             "errors": [{"code": 131047, "title": "Re-engagement message"}]}]}}]}]}
    (st,) = parse_statuses(body)
    assert (st.msg_id, st.status, st.error_code, st.recipient) == ("wamid.X", "failed", 131047, "9198")


async def test_failed_debtor_reminder_rolls_back_and_tells_owner(pool):
    t, oid = await seed(pool, inbound_hours_ago=1)
    app = make_app(pool)
    app.state.tg = None
    await tick(app, NOW)
    assert "Sent to Ravi" in await decide_reminder(app, t.id, oid, "send", NOW)
    debtor_id = (await one(pool, "SELECT msg_id FROM outbound_messages WHERE kind = 'debtor_reminder'"))["msg_id"]
    await apply_status(app, StatusUpdate(debtor_id, "failed", DEBTOR, 131047, "Re-engagement message"))
    st = await one(pool, "SELECT state, attempt_count FROM obligations WHERE id = %s", oid)
    assert st == {"state": "ACTIVE", "attempt_count": 0}
    note = app.state.wa.sent[-1]
    assert note["to"] == OWNER and "not delivered" in note["text"]["body"] and "template" in note["text"]["body"]
    await apply_status(app, StatusUpdate(debtor_id, "failed", DEBTOR, 131047, "again"))   # duplicate receipt: no-op
    assert len(app.state.wa.sent) == 3


async def test_receipts_never_move_backwards(pool):
    t, _ = await seed(pool, inbound_hours_ago=1)
    app = make_app(pool)
    app.state.tg = None
    await tick(app, NOW)
    mid = (await one(pool, "SELECT msg_id FROM outbound_messages"))["msg_id"]
    await apply_status(app, StatusUpdate(mid, "read", OWNER))
    await apply_status(app, StatusUpdate(mid, "delivered", OWNER))
    assert (await one(pool, "SELECT status FROM outbound_messages"))["status"] == "read"
    await apply_status(app, StatusUpdate("wamid.unknown", "failed", OWNER, 1, "x"))      # unknown id ignored


async def test_closed_window_holds_card_nudges_once_then_resends_on_reply(pool):
    t, oid = await seed(pool, inbound_hours_ago=30)
    app = make_app(pool, wa_owner_template="owner_waiting")
    app.state.tg = None
    await tick(app, NOW)
    (nudge,) = app.state.wa.sent
    assert nudge["type"] == "template" and nudge["template"]["name"] == "owner_waiting"
    assert (await one(pool, "SELECT status FROM outbound_messages"))["status"] == "held"
    await import_rows(pool, t.id, [SheetRow("Ravi", DEBTOR, "INV-2", 50_000, date(2026, 11, 1))])
    await tick(app, NOW)
    assert len(app.state.wa.sent) == 1                                   # second card held, no second nudge
    await dispatch(app, owner_msg("w-hi"))                               # owner replies: window opens
    cards = [m for m in app.state.wa.sent if m.get("type") == "interactive"]
    assert len(cards) == 2 and all(c["to"] == OWNER for c in cards)
    assert (await one(pool, "SELECT count(*) n FROM outbound_messages WHERE status = 'held'"))["n"] == 0


async def test_failed_owner_card_is_resent_when_owner_writes(pool):
    t, oid = await seed(pool, inbound_hours_ago=1)
    app = make_app(pool)
    app.state.tg = None
    await tick(app, NOW)
    mid = (await one(pool, "SELECT msg_id FROM outbound_messages"))["msg_id"]
    await apply_status(app, StatusUpdate(mid, "failed", OWNER, 131047, "Re-engagement message"))
    before = len(app.state.wa.sent)
    await dispatch(app, owner_msg("w-back"))
    resent = [m for m in app.state.wa.sent[before:] if m.get("type") == "interactive"]
    assert len(resent) == 1 and "INV-1" in resent[0]["interactive"]["body"]["text"]
    await dispatch(app, owner_msg("w-again"))                            # not sent twice
    assert len([m for m in app.state.wa.sent if m.get("type") == "interactive"]) == 2


async def test_telegram_link_route_and_buttons(pool):
    t, oid = await seed(pool)
    app = make_app(pool)
    await dispatch(app, owner_msg("w-tg", text="telegram"))
    link = app.state.wa.sent[-1]["text"]["body"]
    code = link.split("start=")[1].strip()
    assert "t.me/smara_test_bot" in link
    await tgmod.handle_update(app, {"message": {"message_id": 1, "chat": {"id": 555, "type": "private"}, "text": f"/start {code}"}})
    assert "Linked to Shop" in app.state.tg.sent[-1]["text"]
    await tgmod.handle_update(app, {"message": {"message_id": 2, "chat": {"id": 777, "type": "private"}, "text": f"/start {code}"}})
    assert "expired" in app.state.tg.sent[-1]["text"]                    # single use
    await tgmod.handle_update(app, {"message": {"message_id": 3, "chat": {"id": 777, "type": "private"}, "text": "digest"}})
    assert "send the word telegram" in app.state.tg.sent[-1]["text"]     # stranger gets nothing

    wa_before = len(app.state.wa.sent)
    await tick(app, NOW)                                                 # owner card now goes to Telegram
    card = app.state.tg.sent[-1]
    assert card["chat_id"] == 555 and card["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == f"send:{oid}"
    assert len(app.state.wa.sent) == wa_before
    await tgmod.handle_update(app, {"callback_query": {"id": "cb1", "data": f"snooze:{oid}", "message": {"chat": {"id": 555}}}})
    assert "snoozed" in app.state.tg.sent[-1]["text"] and app.state.tg.answered == ["cb1"]
    assert (await one(pool, "SELECT state FROM obligations WHERE id = %s", oid))["state"] == "SNOOZED"
    await tgmod.handle_update(app, {"callback_query": {"id": "cb2", "data": f"paid:{oid}", "message": {"chat": {"id": 777}}}})
    assert (await one(pool, "SELECT state FROM obligations WHERE id = %s", oid))["state"] == "SNOOZED"   # unlinked chat ignored


def test_telegram_webhook_requires_secret():
    app = FastAPI()
    app.include_router(tgmod.router)
    app.state.settings = Settings(telegram_webhook_secret="s3")
    c = TestClient(app)
    assert c.post("/telegram/webhook", json={}).status_code == 401
    assert c.post("/telegram/webhook", json={}, headers={"x-telegram-bot-api-secret-token": "nope"}).status_code == 401
    app.state.settings = Settings()                                      # unset secret never accepts
    assert c.post("/telegram/webhook", json={}, headers={"x-telegram-bot-api-secret-token": ""}).status_code == 401
