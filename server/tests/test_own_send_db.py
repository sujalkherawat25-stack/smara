"""Send-from-your-own-WhatsApp (REMINDER_SEND_MODE=own): the owner taps 'Send myself', gets a wa.me link with the
approved text typed in, and taps Send there. Smara never messages the customer in this mode."""
import os
import uuid
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from src import clock, tenants
from src.agent import actions
from src.agent.policy import SessionCtx
from src.config import Settings, reminder_mode
from src.db import migrate, open_pool
from src.ingest.importer import import_rows
from src.ingest.sheet_mapper import SheetRow
from src.loop.reminders import decide_reminder, reminder_card
from src.loop.scheduler import tick
from src.loop.whatsapp import own_whatsapp_link

URL = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")
NOW = datetime.now(timezone.utc) + timedelta(minutes=20)  # ahead of DB now() even after a slow full run
NIGHT = datetime(2026, 11, 8, 3, 0, tzinfo=clock.IST)     # 03:00 IST: inside the default 21-8 quiet hours


# ---------- pure unit tests -------------------------------------------------------------------------

def test_link_adds_country_code_and_encodes_hindi_rupee_and_newline():
    text = "Namaste Ravi ji,\n₹1,000 baaki hai"
    link = own_whatsapp_link("+91 98765-43210", text)
    assert link.startswith("https://wa.me/919876543210?text=")
    assert parse_qs(urlsplit(link).query)["text"] == [text]        # round-trips exactly
    assert "\n" not in link and "₹" not in link                    # encoded, safe to paste into WhatsApp


def test_link_keeps_full_international_number_and_strips_leading_zero():
    assert own_whatsapp_link("919876543210", "hi").startswith("https://wa.me/919876543210?text=")
    assert own_whatsapp_link("09876543210", "hi").startswith("https://wa.me/919876543210?text=")


def test_reminder_mode_defaults_to_own_and_rejects_unknown_values():
    assert reminder_mode("") == "own"
    assert reminder_mode("OWN") == "own"
    assert reminder_mode("smara") == "smara"
    assert reminder_mode("whatever") == "own"
    assert Settings().reminder_send_mode == "own"


def test_card_buttons_follow_mode():
    o = {"id": uuid.uuid4(), "canonical_name": "Ravi"}
    own = reminder_card("919000000001", o, "draft", "", mode="own")["interactive"]["action"]["buttons"]
    smara = reminder_card("919000000001", o, "draft", "", mode="smara")["interactive"]["action"]["buttons"]
    assert [b["reply"]["title"] for b in own] == ["Send myself", "Snooze 3 days", "Mark paid"]
    assert own[0]["reply"]["id"].startswith("own:")
    assert smara[0]["reply"]["title"] == "Send" and smara[0]["reply"]["id"].startswith("send:")
    assert all(len(b["reply"]["title"]) <= 20 for b in own + smara)


# ---------- database tests --------------------------------------------------------------------------

class FakeWA:
    def __init__(self):
        self.sent = []

    async def send(self, payload, **kw):
        self.sent.append(payload)


class FakeContinuum:
    async def resolve(self, **kw):
        return {"resolution": {"state_card": ""}}


@pytest.fixture
async def app():
    pool = await open_pool(URL)
    async with pool.connection() as c:
        await c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    await migrate(pool)
    tenant = str(uuid.uuid4())
    await tenants.create(pool, name="Shop", owner_phone="919000000001", upi_vpa="shop@upi", tenant_id=tenant)
    s = replace(Settings(), reminder_send_mode="own")        # 0/0 = never quiet, so these tests do not depend on the clock
    yield SimpleNamespace(state=SimpleNamespace(pool=pool, settings=s, wa=FakeWA(), continuum=FakeContinuum(),
                                                tenant=tenant))
    await pool.close()


async def seed(app):
    t = app.state.tenant
    await import_rows(app.state.pool, t, [SheetRow("Ravi", "919876543210", "INV-1", 100_000, date(2026, 11, 1))])
    async with app.state.pool.connection() as c:
        row = await (await c.execute("SELECT id, party_id FROM obligations")).fetchone()
        return str(row["id"]), str(row["party_id"])


async def state(app, oid):
    async with app.state.pool.connection() as c:
        return await (await c.execute("SELECT state, attempt_count FROM obligations WHERE id=%s", (oid,))).fetchone()


def customer_sends(app):
    return [m for m in app.state.wa.sent if m["to"] != "919000000001"]


@needs_db
async def test_own_tap_returns_link_and_sends_nothing_to_customer(app):
    oid, _ = await seed(app)
    assert await tick(app, NOW) == 1
    reply = await decide_reminder(app, app.state.tenant, oid, "own", NOW)
    link = next(line for line in reply.splitlines() if line.startswith("https://wa.me/"))
    assert link.startswith("https://wa.me/919876543210?text=")
    typed = parse_qs(urlsplit(link).query)["text"][0]
    assert "Ravi" in typed and "INV-1" in typed and "₹" in typed
    assert "shop@upi" in typed                                  # the approved draft carries the UPI line
    assert "ko apne WhatsApp se bhejiye" in reply
    assert customer_sends(app) == []                            # Smara messaged nobody but the owner
    assert await state(app, oid) == {"state": "DISPATCHED", "attempt_count": 1}
    async with app.state.pool.connection() as c:
        n = (await (await c.execute("SELECT count(*) AS n FROM outbound_messages WHERE kind = 'debtor_reminder'")).fetchone())["n"]
    assert n == 0                                               # no customer row either
    assert await decide_reminder(app, app.state.tenant, oid, "own", NOW) is None   # second tap is stale


@needs_db
async def test_own_tap_refused_for_opted_out_party(app):
    oid, party = await seed(app)
    await tick(app, NOW)
    async with app.state.pool.connection() as c:
        await c.execute("UPDATE parties SET opted_out_at = now() WHERE id = %s", (party,))
    reply = await decide_reminder(app, app.state.tenant, oid, "own", NOW)
    assert "STOP" in reply and "https://wa.me/" not in reply
    assert (await state(app, oid))["state"] == "SNOOZED"
    assert customer_sends(app) == []


@needs_db
async def test_own_tap_refused_in_quiet_hours_and_state_unchanged(app):
    oid, _ = await seed(app)
    await tick(app, NOW)
    app.state.settings = replace(app.state.settings, quiet_start_hour=21, quiet_end_hour=8)
    reply = await decide_reminder(app, app.state.tenant, oid, "own", NIGHT.astimezone(timezone.utc))
    assert "raat" in reply and "https://wa.me/" not in reply
    assert (await state(app, oid))["state"] == "WAITING_OWNER_APPROVAL"


@needs_db
async def test_own_tap_without_saved_number_returns_draft_to_copy(app):
    oid, party = await seed(app)
    await tick(app, NOW)
    async with app.state.pool.connection() as c:
        await c.execute("UPDATE parties SET whatsapp_phone = '' WHERE id = %s", (party,))   # column is NOT NULL
    reply = await decide_reminder(app, app.state.tenant, oid, "own", NOW)
    assert "saved nahi hai" in reply and "INV-1" in reply and "https://wa.me/" not in reply
    assert customer_sends(app) == []


@needs_db
async def test_smara_mode_send_still_sends_from_shared_number(app):
    oid, _ = await seed(app)
    await tick(app, NOW)
    reply = await decide_reminder(app, app.state.tenant, oid, "send", NOW)
    assert "Sent to Ravi" in reply
    assert customer_sends(app)[-1]["to"] == "919876543210"


@needs_db
async def test_agent_path_in_own_mode_gives_owner_link_not_customer_send(app):
    oid, party = await seed(app)
    await tick(app, NOW)
    ctx = SessionCtx(id=str(uuid.uuid4()), tenant_id=app.state.tenant, trust="TRUSTED")
    tenant = await tenants.by_id(app.state.pool, app.state.tenant)
    result = await actions.deliver_message(app, ctx, tenant, {
        "to": party, "text": "Namaste Ravi ji, INV-1 ka payment kar dein.", "obligation_id": oid})
    assert result["sent"] is False and result["via"] == "owner_own_whatsapp"
    assert customer_sends(app) == []
    owner_msgs = [m for m in app.state.wa.sent if m["to"] == "919000000001" and m.get("type") == "text"]
    assert owner_msgs and "https://wa.me/919876543210?text=" in owner_msgs[-1]["text"]["body"]
    assert (await state(app, oid))["state"] == "DISPATCHED"
