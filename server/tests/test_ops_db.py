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
NOW = datetime.now(timezone.utc) + timedelta(minutes=20)  # ahead of DB now() even after a slow full run


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


async def test_shared_number_ambiguity_asks_which_shop_and_dedicated_number_routes(app):
    await tenant_with_debtor(app, "alpha", "919000000001", number_id="N-A")
    b = await tenant_with_debtor(app, "beta", "919000000002", number_id="N-B")
    await dispatch(app, msg("w3"))                  # same debtor owes both tenants, no number hint
    assert (await one(app, "SELECT count(*) n FROM proposed_adjustments"))["n"] == 0
    ask = app.state.wa.sent[-1]                     # not dropped: the customer is asked which shop
    assert ask["to"] == "919876543210" and [x["reply"]["title"] for x in ask["interactive"]["action"]["buttons"]] == ["alpha", "beta"]
    await dispatch(app, msg("w4", number_id="N-B"))  # arrives on beta's number
    assert (await one(app, "SELECT count(*) n FROM proposed_adjustments"))["n"] == 1
    assert app.state.wa.sent[-1]["to"] == b.owner_phone and app.state.wa.numbers[-1] is None   # the owner hears from Smara's number
    row = await one(app, "SELECT o.tenant_id FROM proposed_adjustments a JOIN obligations o ON o.id = a.obligation_id")
    assert str(row["tenant_id"]) == b.id


async def test_customer_picks_the_shop_and_their_waiting_message_is_processed_for_it(app):
    a = await tenant_with_debtor(app, "alpha", "919000000001")
    b = await tenant_with_debtor(app, "beta", "919000000002")
    await dispatch(app, msg("p1", text="10% discount de do"))
    buttons = app.state.wa.sent[-1]["interactive"]["action"]["buttons"]
    beta = next(x["reply"]["id"] for x in buttons if x["reply"]["title"] == "beta")
    await dispatch(app, InboundMessage(wamid="p2", sender="919876543210", kind="interactive", button_id=beta))
    row = await one(app, "SELECT o.tenant_id, a.raw_evidence_text FROM proposed_adjustments a JOIN obligations o ON o.id = a.obligation_id")
    assert str(row["tenant_id"]) == b.id and "discount" in row["raw_evidence_text"]
    assert app.state.wa.sent[-1]["to"] == b.owner_phone                                  # beta's owner gets the card
    await dispatch(app, InboundMessage(wamid="p3", sender="919876543210", kind="interactive", button_id=beta))
    assert (await one(app, "SELECT count(*) n FROM proposed_adjustments"))["n"] == 1     # a second tap does nothing
    forged = f"shop:{a.id}:" + beta.rsplit(":", 1)[1]
    await dispatch(app, InboundMessage(wamid="p4", sender="919000000099", kind="interactive", button_id=forged))
    assert (await one(app, "SELECT count(*) n FROM proposed_adjustments"))["n"] == 1     # someone else's choice is ignored


async def test_a_reply_to_one_shops_message_goes_to_that_shop(app):
    from src.loop.outbound import record
    a = await tenant_with_debtor(app, "alpha", "919000000001")
    b = await tenant_with_debtor(app, "beta", "919000000002")
    await record(app.state.pool, "wamid.FROM-ALPHA", tenant_id=a.id, kind="debtor_reminder", to_phone="919876543210")
    await record(app.state.pool, "wamid.FROM-BETA", tenant_id=b.id, kind="debtor_reminder", to_phone="919876543210")
    await dispatch(app, InboundMessage(wamid="c1", sender="919876543210", kind="text", text="10% discount de do",
                                       reply_to="wamid.FROM-ALPHA"))
    row = await one(app, "SELECT o.tenant_id FROM proposed_adjustments a JOIN obligations o ON o.id = a.obligation_id")
    assert str(row["tenant_id"]) == a.id                       # both wrote recently, but they replied to alpha's


async def test_the_only_shop_that_wrote_recently_gets_the_reply(app):
    from src.loop.outbound import record
    await tenant_with_debtor(app, "alpha", "919000000001")
    b = await tenant_with_debtor(app, "beta", "919000000002")
    await record(app.state.pool, "wamid.B1", tenant_id=b.id, kind="debtor_reminder", to_phone="919876543210")
    await dispatch(app, msg("r1"))
    row = await one(app, "SELECT o.tenant_id FROM proposed_adjustments a JOIN obligations o ON o.id = a.obligation_id")
    assert str(row["tenant_id"]) == b.id


def test_customer_messages_on_the_shared_number_name_the_shop():
    from types import SimpleNamespace
    from src.loop.whatsapp import from_shop
    shared = SimpleNamespace(name="Ravi Traders", wa_phone_number_id=None)
    own = SimpleNamespace(name="Ravi Traders", wa_phone_number_id="N-1")
    assert from_shop(shared, "Namaste, INV-1 baaki hai") == "*Ravi Traders*" + chr(10) + "Namaste, INV-1 baaki hai"
    assert from_shop(shared, from_shop(shared, "x")) == from_shop(shared, "x")            # never doubled
    assert from_shop(own, "Namaste") == "Namaste"                                          # its own number is the shop


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
    from src.box.sandbox import Sandbox
    fastapi_app.state.pool = app.state.pool
    fastapi_app.state.sandbox = Sandbox("data/test-sandbox", "local")
    fastapi_app.state.settings = Settings(quiet_start_hour=21, quiet_end_hour=8)
    r = Response()
    first = await healthz(r)
    assert first["ok"] is True and r.status_code == 200
    assert set(first["ops"]) == {"tasks_pending", "cards_held", "failed_sessions_1h", "quiet_hours_now"}
    async with app.state.pool.connection() as c:
        await c.execute("INSERT INTO continuum_outbox (user_id, source_id, text, trusted, status) "
                        "VALUES ('u','s1','t',false,'DEAD')")
    r = Response()
    out = await healthz(r)
    assert out["ok"] is False and out["outbox"]["dead"] == 1 and r.status_code == 503


def test_reminder_template_variables_follow_the_configured_order():
    from types import SimpleNamespace
    from src.loop.whatsapp import reminder_params
    o = {"canonical_name": "Ravi", "invoice_ref": "INV-1", "amount_paise": 1_250_000, "due_date": NOW.date()}
    shop = SimpleNamespace(name="Sharma Traders")
    old = Settings(wa_reminder_template="t")
    assert reminder_params(old, o, shop)[:3] == ["Ravi", "INV-1", "₹12,500"]                    # v1/v2 order unchanged
    v3 = Settings(wa_reminder_template="smara_account_update", wa_reminder_params="customer,shop,invoice,amount,due")
    assert reminder_params(v3, o, shop)[:3] == ["Ravi", "Sharma Traders", "INV-1"] and len(reminder_params(v3, o, shop)) == 5
