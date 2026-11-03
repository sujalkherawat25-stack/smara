"""Multi-tenant routing, dedupe, 24h window, durable digest and health, against Postgres."""
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import Response

from src import tenants
from src.config import Settings
from src.db import migrate, open_pool
from src.ingest.dispatch import dispatch
from src.ingest.payload import InboundMessage
from src.loop.reminders import _debtor_payload
from src.loop.scheduler import IST, send_digests
from src.main import app as fastapi_app
from src.main import healthz

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")
NOW = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)


class FakeWA:
    def __init__(self):
        self.sent, self.numbers = [], []

    async def send(self, payload, *, number_id=None):
        self.sent.append(payload)
        self.numbers.append(number_id)


class NoLLM:
    enabled = False


@pytest.fixture
async def app():
    pool = await open_pool(URL)
    async with pool.connection() as c:
        await c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    await migrate(pool)
    yield SimpleNamespace(state=SimpleNamespace(pool=pool, settings=Settings(), wa=FakeWA(), llm=NoLLM(),
                                                continuum=None, bg=set()))
    await pool.close()


async def tenant_with_debtor(app, name, owner, number_id=None, debtor="919876543210"):
    t = await tenants.create(app.state.pool, name=name, owner_phone=owner, upi_vpa=f"{name}@upi",
                             wa_phone_number_id=number_id)
    async with app.state.pool.connection() as c:
        pid = (await (await c.execute(
            "INSERT INTO parties (tenant_id, canonical_name, whatsapp_phone) VALUES (%s,'Ravi',%s) RETURNING id",
            (t.id, debtor))).fetchone())["id"]
        await c.execute(
            "INSERT INTO obligations (tenant_id, party_id, invoice_ref, amount_paise, due_date, state)"
            " VALUES (%s,%s,%s,5000000,'2026-11-01','ACTIVE')", (t.id, pid, f"INV-{name}"))
    return t


def msg(wamid, text="10% discount de do", sender="919876543210", number_id=""):
    return InboundMessage(wamid=wamid, sender=sender, kind="text", text=text, number_id=number_id)


async def one(app, sql, *a):
    async with app.state.pool.connection() as c:
        return await (await c.execute(sql, a)).fetchone()


async def test_duplicate_delivery_is_processed_once(app):
    t = await tenant_with_debtor(app, "alpha", "919000000001")
    await dispatch(app, msg("w1"))
    await dispatch(app, msg("w1"))
    assert (await one(app, "SELECT count(*) n FROM proposed_adjustments"))["n"] == 1
    assert len(app.state.wa.sent) == 1 and app.state.wa.sent[0]["to"] == t.owner_phone


async def test_failed_dispatch_can_be_redelivered(app):
    await tenant_with_debtor(app, "alpha", "919000000001")
    app.state.wa.send = None  # the owner-card send blows up after the claim is stored
    with pytest.raises(TypeError):
        await dispatch(app, msg("w2"))
    assert (await one(app, "SELECT count(*) n FROM processed_messages"))["n"] == 0   # claim released
    app.state.wa = FakeWA()
    await dispatch(app, msg("w2"))                  # redelivery succeeds without a duplicate claim
    assert (await one(app, "SELECT count(*) n FROM proposed_adjustments"))["n"] == 1
    assert len(app.state.wa.sent) == 1


async def test_shared_number_ambiguity_dropped_but_dedicated_number_routes(app):
    await tenant_with_debtor(app, "alpha", "919000000001", number_id="N-A")
    b = await tenant_with_debtor(app, "beta", "919000000002", number_id="N-B")
    await dispatch(app, msg("w3"))                  # same debtor owes both tenants, no number hint
    assert (await one(app, "SELECT count(*) n FROM proposed_adjustments"))["n"] == 0
    await dispatch(app, msg("w4", number_id="N-B"))  # arrives on beta's number
    assert (await one(app, "SELECT count(*) n FROM proposed_adjustments"))["n"] == 1
    assert app.state.wa.sent[0]["to"] == b.owner_phone and app.state.wa.numbers == ["N-B"]
    row = await one(app, "SELECT o.tenant_id FROM proposed_adjustments a JOIN obligations o ON o.id = a.obligation_id")
    assert str(row["tenant_id"]) == b.id


async def test_24h_window_and_template(app):
    t = await tenant_with_debtor(app, "alpha", "919000000001")
    o = {"canonical_name": "Ravi", "invoice_ref": "INV-1", "amount_paise": 5_000_000,
         "due_date": NOW.date(), "attempt_count": 0, "whatsapp_phone": "919876543210", "last_inbound_at": None}
    tpl = Settings(wa_reminder_template="payment_reminder", wa_reminder_lang="en")
    assert _debtor_payload(o, t, Settings(), NOW)["type"] == "text"      # no template configured
    assert _debtor_payload(o, t, tpl, NOW)["type"] == "template"         # never spoke to us
    o["last_inbound_at"] = NOW - timedelta(hours=2)
    assert _debtor_payload(o, t, tpl, NOW)["type"] == "text"             # inside the window
    o["last_inbound_at"] = NOW - timedelta(hours=30)
    p = _debtor_payload(o, t, tpl, NOW)
    assert p["type"] == "template" and p["template"]["name"] == "payment_reminder"
    assert [x["text"] for x in p["template"]["components"][0]["parameters"]][:3] == ["Ravi", "INV-1", "₹50,000"]

    await dispatch(app, msg("w5", text="ok"))                            # chatter still opens the window
    assert (await one(app, "SELECT count(*) n FROM parties WHERE last_inbound_at IS NOT NULL"))["n"] == 1


async def test_digest_once_per_day_per_tenant_across_restarts(app):
    await tenant_with_debtor(app, "alpha", "919000000001")
    await tenant_with_debtor(app, "beta", "919000000002", debtor="919876543211")
    local = NOW.astimezone(IST)
    assert await send_digests(app, local) == 2
    assert await send_digests(app, local) == 0                           # a restart does not resend
    assert await send_digests(app, local + timedelta(days=1)) == 2       # next day does


async def test_healthz_flags_dead_outbox(app):
    fastapi_app.state.pool = app.state.pool
    r = Response()
    assert (await healthz(r))["ok"] is True and r.status_code == 200
    async with app.state.pool.connection() as c:
        await c.execute("INSERT INTO continuum_outbox (user_id, source_id, text, trusted, status) "
                        "VALUES ('u','s1','t',false,'DEAD')")
    r = Response()
    out = await healthz(r)
    assert out["ok"] is False and out["outbox"]["dead"] == 1 and r.status_code == 503
