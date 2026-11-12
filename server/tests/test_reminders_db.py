"""Invariant 3 against Postgres: nothing reaches a debtor until the owner taps Send."""
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src import tenants
from src.config import Settings
from src.db import migrate, open_pool
from src.ingest.importer import import_rows
from src.ingest.sheet_mapper import SheetRow
from src.loop.digest import build_digest
from src.loop.reminders import decide_reminder
from src.loop.scheduler import tick

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")
NOW = datetime.now(timezone.utc) + timedelta(minutes=20)  # ahead of DB now() even after a slow full run


class FakeWA:
    def __init__(self):
        self.sent = []

    async def send(self, payload, **kw):
        self.sent.append(payload)


class FakeContinuum:
    async def resolve(self, **kw):
        return {"resolution": {"state_card": "Promised to pay on 1 Nov."}}

    async def write_evidence(self, **kw):
        pass


@pytest.fixture
async def app():
    pool = await open_pool(URL)
    async with pool.connection() as c:
        await c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    await migrate(pool)
    tenant = str(uuid.uuid4())
    await tenants.create(pool, name="Shop", owner_phone="919000000001", upi_vpa="shop@upi", tenant_id=tenant)
    s = Settings()
    yield SimpleNamespace(state=SimpleNamespace(pool=pool, settings=s, wa=FakeWA(), continuum=FakeContinuum(),
                                                tenant=tenant))
    await pool.close()


async def seed(app):
    t = app.state.tenant
    await import_rows(app.state.pool, t, [SheetRow("Ravi", "919876543210", "INV-1", 100_000, date(2026, 11, 1))])
    async with app.state.pool.connection() as c:
        return str((await (await c.execute("SELECT id FROM obligations")).fetchone())["id"])


async def state(app, oid):
    async with app.state.pool.connection() as c:
        return await (await c.execute("SELECT state, attempt_count FROM obligations WHERE id=%s", (oid,))).fetchone()


async def test_reminder_waits_for_owner_then_sends(app):
    oid = await seed(app)
    assert await tick(app, NOW) == 1
    (card,) = app.state.wa.sent
    assert card["to"] == "919000000001"           # owner, not debtor
    assert (await state(app, oid))["state"] == "WAITING_OWNER_APPROVAL"
    assert await tick(app, NOW) == 0              # not re-claimed while waiting

    reply = await decide_reminder(app, app.state.tenant, oid, "send", NOW)
    assert "Sent to Ravi" in reply
    debtor_msg = app.state.wa.sent[-1]
    assert debtor_msg["to"] == "919876543210" and "INV-1" in debtor_msg["text"]["body"]
    assert await state(app, oid) == {"state": "DISPATCHED", "attempt_count": 1}
    assert await decide_reminder(app, app.state.tenant, oid, "send", NOW) is None  # double tap


async def test_snooze_and_paid_send_nothing_to_debtor(app):
    oid = await seed(app)
    await tick(app, NOW)
    await decide_reminder(app, app.state.tenant, oid, "snooze", NOW)
    assert all(m["to"] == "919000000001" for m in app.state.wa.sent)
    assert (await state(app, oid))["state"] == "SNOOZED"
    await tick(app, NOW + timedelta(days=4))
    await decide_reminder(app, app.state.tenant, oid, "paid", NOW)
    assert (await state(app, oid))["state"] == "PAID"


async def test_foreign_tenant_cannot_decide(app):
    oid = await seed(app)
    await tick(app, NOW)
    assert await decide_reminder(app, str(uuid.uuid4()), oid, "send", NOW) is None
    assert (await state(app, oid))["state"] == "WAITING_OWNER_APPROVAL"


async def test_digest_and_reimport_idempotent(app):
    await seed(app)
    again = await import_rows(app.state.pool, app.state.tenant,
                              [SheetRow("Ravi", "919876543210", "INV-1", 90_000, date(2026, 11, 1))])
    assert again == {"created": 0, "updated": 1}
    text = await build_digest(app.state.pool, app.state.tenant, date(2026, 11, 3))
    assert "Overdue: 1 invoices, ₹900" in text and "Ravi" in text


async def test_failed_owner_card_releases_obligation_for_retry(app):
    oid = await seed(app)

    async def boom(payload, **kw):
        raise ConnectionError("whatsapp down")

    app.state.wa.send = boom
    assert await tick(app, NOW) == 0
    st = await state(app, oid)
    assert st["state"] == "ACTIVE"                       # not stuck in WAITING_OWNER_APPROVAL
    assert await tick(app, NOW + timedelta(minutes=1)) == 0   # backing off for 10 minutes
    app.state.wa = FakeWA()
    assert await tick(app, NOW + timedelta(minutes=11)) == 1  # retried and delivered
    assert (await state(app, oid))["state"] == "WAITING_OWNER_APPROVAL"


async def test_mondays_digest_adds_what_last_week_brought_in(app):
    from src.loop.digest import weekly_result
    oid = await seed(app)
    assert await weekly_result(app.state.pool, app.state.tenant) == []      # a quiet week shows nothing
    async with app.state.pool.connection() as c:
        await c.execute("UPDATE obligations SET state = 'PAID', attempt_count = 1 WHERE id = %s", (oid,))
        await c.execute("""INSERT INTO obligation_events (obligation_id, from_state, to_state, actor)
                           VALUES (%s, 'ACTIVE', 'DISPATCHED', 'OWNER'), (%s, 'DISPATCHED', 'PAID', 'OWNER')""", (oid, oid))
    monday = await build_digest(app.state.pool, app.state.tenant, date(2026, 11, 9))
    assert "Last 7 days: 1 invoice(s) paid," in monday and "after a reminder" in monday
    assert "1 reminder(s) sent, 0 promise(s) recorded." in monday
    assert "Last 7 days" not in await build_digest(app.state.pool, app.state.tenant, date(2026, 11, 10))


async def test_an_unanswered_card_is_asked_again_after_two_days_and_the_old_tap_still_works_once(app):
    from src.loop.reminders import ASK_AGAIN_AFTER
    oid = await seed(app)
    assert await tick(app, NOW) == 1
    assert await tick(app, NOW + timedelta(hours=47)) == 0          # still waiting: not asked twice too soon
    later = NOW + ASK_AGAIN_AFTER + timedelta(minutes=5)
    assert await tick(app, later) == 1                               # the lost card is sent again
    assert len([m for m in app.state.wa.sent if m["to"] == "919000000001"]) == 2
    assert (await state(app, oid)) == {"state": "WAITING_OWNER_APPROVAL", "attempt_count": 0}
    assert "Sent to Ravi" in await decide_reminder(app, app.state.tenant, oid, "send", later)   # the first card's button
    assert await decide_reminder(app, app.state.tenant, oid, "send", later) is None             # the second: already done
    assert len([m for m in app.state.wa.sent if m["to"] == "919876543210"]) == 1


async def test_an_invoice_an_agent_is_waiting_on_is_left_alone_until_that_approval_expires(app):
    from src.loop.reminders import APPROVAL_EXPIRES, reopen_unanswered
    oid = await seed(app)
    async with app.state.pool.connection() as c:
        await c.execute("UPDATE obligations SET state = 'WAITING_OWNER_APPROVAL' WHERE id = %s", (oid,))
        await c.execute("""INSERT INTO obligation_events (obligation_id, from_state, to_state, actor, created_at)
                           VALUES (%s, 'ACTIVE', 'WAITING_OWNER_APPROVAL', 'SYSTEM_CRON', now() - interval '3 days')""", (oid,))
        sid = (await (await c.execute(
            """INSERT INTO agent_sessions (tenant_id, trust, trigger, status, pending)
               VALUES (%s, 'SYSTEM', 'cron_reminder', 'WAITING_APPROVAL', %s::jsonb) RETURNING id""",
            (app.state.tenant, '{"name": "send_whatsapp_message", "args": {"obligation_id": "%s"}}' % oid))).fetchone())["id"]
    now = datetime.now(timezone.utc)
    assert await reopen_unanswered(app.state.pool, now) == {"reopened": 0, "expired": 0}
    assert (await state(app, oid))["state"] == "WAITING_OWNER_APPROVAL"
    gone = now + APPROVAL_EXPIRES + timedelta(minutes=1)
    assert await reopen_unanswered(app.state.pool, gone) == {"reopened": 1, "expired": 1}
    assert (await state(app, oid))["state"] == "ACTIVE"
    async with app.state.pool.connection() as c:
        row = await (await c.execute("SELECT status, pending FROM agent_sessions WHERE id = %s", (sid,))).fetchone()
    assert row == {"status": "DONE", "pending": None}             # closed, not FAILED: no health alert
