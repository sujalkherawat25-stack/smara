"""Invariant 2 against a real Postgres. Set TEST_DATABASE_URL to run (uses a scratch schema)."""
import os
import uuid

import pytest

from src.bridge.coordinator import decide_adjustment, submit_debtor_claim
from src.db import migrate, open_pool

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


class FakeContinuum:
    def __init__(self):
        self.writes = []

    async def write_evidence(self, **kw):
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


async def amount(pool, oid):
    async with pool.connection() as c:
        cur = await c.execute("SELECT amount_paise, state FROM obligations WHERE id=%s", (oid,))
        return await cur.fetchone()


async def test_debtor_claim_never_mutates_until_owner_approves(pool):
    tenant, party, oid = await seed(pool)
    cont = FakeContinuum()
    adj, created = await submit_debtor_claim(
        pool, cont, tenant_id=tenant, party_id=party, obligation_id=oid, wamid="w1",
        adjustment_type="DISCOUNT_CLAIM", evidence_text="10% kam kar do", proposed_amount_paise=90000)
    assert created and cont.writes[0]["trusted"] is False
    assert (await amount(pool, oid))["amount_paise"] == 100000

    # webhook retry: no second row
    _, again = await submit_debtor_claim(
        pool, cont, tenant_id=tenant, party_id=party, obligation_id=oid, wamid="w1",
        adjustment_type="DISCOUNT_CLAIM", evidence_text="10% kam kar do", proposed_amount_paise=90000)
    assert not again

    # another tenant's owner cannot decide it
    assert await decide_adjustment(pool, cont, tenant_id=str(uuid.uuid4()), adjustment_id=adj, approve=True) is None
    assert (await amount(pool, oid))["amount_paise"] == 100000

    assert await decide_adjustment(pool, cont, tenant_id=tenant, adjustment_id=adj, approve=True)
    assert (await amount(pool, oid))["amount_paise"] == 90000
    assert cont.writes[-1]["trusted"] is True
    # double tap is a no-op
    assert await decide_adjustment(pool, cont, tenant_id=tenant, adjustment_id=adj, approve=True) is None


async def test_reject_leaves_obligation_untouched(pool):
    tenant, party, oid = await seed(pool)
    adj, _ = await submit_debtor_claim(
        pool, FakeContinuum(), tenant_id=tenant, party_id=party, obligation_id=oid, wamid="w2",
        adjustment_type="UTR_PROOF", evidence_text="UTR 123456789012")
    await decide_adjustment(pool, FakeContinuum(), tenant_id=tenant, adjustment_id=adj, approve=False)
    assert (await amount(pool, oid))["state"] == "ACTIVE"
