"""Invariants 2 + outbox against a real Postgres. Set TEST_DATABASE_URL to run (uses a scratch schema)."""
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.bridge.coordinator import decide_adjustment, submit_debtor_claim
from src.bridge.outbox import drain
from src.db import migrate, open_pool

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")
NOW = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)


class FakeContinuum:
    def __init__(self, down=False):
        self.writes, self.down = [], down

    async def write_evidence(self, **kw):
        if self.down:
            raise ConnectionError("continuum unreachable")
        self.writes.append(kw)


@pytest.fixture
async def pool():
    p = await open_pool(URL)
    async with p.connection() as c:
        await c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    await migrate(p)
    yield p
    await p.close()


async def seed(pool):
    tenant = str(uuid.uuid4())
    async with pool.connection() as c:
        cur = await c.execute(
            "INSERT INTO parties (tenant_id, canonical_name, whatsapp_phone) VALUES (%s,'Ravi','91')"
            " RETURNING id", (tenant,))
        party = (await cur.fetchone())["id"]
        cur = await c.execute(
            "INSERT INTO obligations (tenant_id, party_id, invoice_ref, amount_paise, due_date, state)"
            " VALUES (%s,%s,'INV-1',100000,'2026-11-01','ACTIVE') RETURNING id", (tenant, party))
        return tenant, str(party), str((await cur.fetchone())["id"])


async def row(pool, sql, *a):
    async with pool.connection() as c:
        return await (await c.execute(sql, a)).fetchone()


async def claim(pool, tenant, party, oid, wamid="w1", **kw):
    return await submit_debtor_claim(
        pool, tenant_id=tenant, party_id=party, obligation_id=oid, wamid=wamid,
        adjustment_type="DISCOUNT_CLAIM", evidence_text="10% kam kar do", proposed_amount_paise=90000, **kw)


async def test_debtor_claim_never_mutates_until_owner_approves(pool):
    tenant, party, oid = await seed(pool)
    cont = FakeContinuum()
    adj, created = await claim(pool, tenant, party, oid)
    assert created
    await drain(pool, cont, NOW)
    assert cont.writes[0]["trusted"] is False
    assert (await row(pool, "SELECT amount_paise FROM obligations WHERE id=%s", oid))["amount_paise"] == 100000

    _, again = await claim(pool, tenant, party, oid)  # webhook retry: no second row
    assert not again

    # another tenant's owner cannot decide it
    assert await decide_adjustment(pool, tenant_id=str(uuid.uuid4()), adjustment_id=adj, approve=True) is None
    assert (await row(pool, "SELECT amount_paise FROM obligations WHERE id=%s", oid))["amount_paise"] == 100000

    assert await decide_adjustment(pool, tenant_id=tenant, adjustment_id=adj, approve=True)
    assert (await row(pool, "SELECT amount_paise FROM obligations WHERE id=%s", oid))["amount_paise"] == 90000
    await drain(pool, cont, NOW)
    assert cont.writes[-1]["trusted"] is True
    assert (await row(pool, "SELECT last_continuum_source_id s FROM obligations WHERE id=%s", oid))["s"].endswith("_owner_ok")
    # double tap is a no-op
    assert await decide_adjustment(pool, tenant_id=tenant, adjustment_id=adj, approve=True) is None


async def test_reject_leaves_obligation_untouched(pool):
    tenant, party, oid = await seed(pool)
    adj, _ = await claim(pool, tenant, party, oid, wamid="w2")
    await decide_adjustment(pool, tenant_id=tenant, adjustment_id=adj, approve=False)
    assert (await row(pool, "SELECT state FROM obligations WHERE id=%s", oid))["state"] == "ACTIVE"


async def test_continuum_outage_loses_nothing_and_retries(pool):
    tenant, party, oid = await seed(pool)
    adj, created = await claim(pool, tenant, party, oid, wamid="w3")   # Continuum is down: still saved
    assert created
    down = FakeContinuum(down=True)
    assert await drain(pool, down, NOW) == {"delivered": 0, "failed": 1}
    r = await row(pool, "SELECT status, attempts, last_error FROM continuum_outbox")
    assert r["status"] == "PENDING" and r["attempts"] == 1 and "unreachable" in r["last_error"]

    # owner can still decide during the outage
    assert await decide_adjustment(pool, tenant_id=tenant, adjustment_id=adj, approve=True)

    # the owner's decision must not overtake the claim it refers to, which is still backing off
    assert await drain(pool, FakeContinuum(), NOW + timedelta(seconds=10)) == {"delivered": 0, "failed": 0}
    up = FakeContinuum()
    assert await drain(pool, up, NOW + timedelta(minutes=10)) == {"delivered": 1, "failed": 0}
    assert await drain(pool, up, NOW + timedelta(minutes=10)) == {"delivered": 1, "failed": 0}
    assert [w["trusted"] for w in up.writes] == [False, True]          # evidence first, then owner decision
    assert (await row(pool, "SELECT count(*) n FROM continuum_outbox WHERE status='DONE'"))["n"] == 2
    assert await drain(pool, up, NOW + timedelta(hours=2)) == {"delivered": 0, "failed": 0}  # nothing re-sent


async def test_overlapping_drains_do_not_double_send(pool):
    tenant, party, oid = await seed(pool)
    await claim(pool, tenant, party, oid, wamid="w4")
    a, b = FakeContinuum(), FakeContinuum()
    await drain(pool, a, NOW)
    await drain(pool, b, NOW)
    assert len(a.writes) + len(b.writes) == 1


async def test_dead_after_max_attempts(pool):
    tenant, party, oid = await seed(pool)
    await claim(pool, tenant, party, oid, wamid="w5")
    t = NOW
    for _ in range(20):
        await drain(pool, FakeContinuum(down=True), t)
        t += timedelta(hours=2)
    assert (await row(pool, "SELECT status, attempts FROM continuum_outbox")) == {"status": "DEAD", "attempts": 20}
