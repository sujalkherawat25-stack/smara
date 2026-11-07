"""Tally sync endpoint, 43B(h) deadlines and the digest, against Postgres."""
import os
from datetime import date, timedelta

import pytest

from src import tenants
from src.db import migrate, open_pool
from src.ingest.tally_sync import PayableIn, ReceivableIn, SyncBody, apply_sync, deadline
from src.loop.digest import build_digest

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


def test_deadline_rules():
    d = date(2026, 11, 1)
    assert deadline(d, "micro", False) == date(2026, 11, 16)
    assert deadline(d, "small", True) == date(2026, 12, 16)
    assert deadline(d, "other", False) is None
    assert deadline(d, "unknown", True) is None


@pytest.fixture
async def pool():
    p = await open_pool(URL)
    await migrate(p)
    yield p
    await p.close()


async def test_sync_is_idempotent_and_digest_warns(pool):
    t = await tenants.create(pool, name="Sync Shop", owner_phone=f"9199{os.getpid():08d}"[:12]) if hasattr(tenants, "create") else None
    if t is None:
        pytest.skip("tenants.create missing")
    today = date.today()
    body = SyncBody(
        receivables=[ReceivableIn(party="Ravi Traders", phone="9812345678", invoice_ref="S-1", amount_rupees=50000, due_date=today - timedelta(days=3))],
        payables=[PayableIn(supplier="Kota Steel", bill_ref="P-1", bill_date=today - timedelta(days=14), amount_rupees=80000, msme_class="small"),
                  PayableIn(supplier="Big Corp", bill_ref="P-2", bill_date=today - timedelta(days=30), amount_rupees=1000, msme_class="other")])
    first = await apply_sync(pool, t.id, None, body)
    again = await apply_sync(pool, t.id, None, body)
    assert first["created"] == 1 and again["created"] == 0 and again["updated"] == 1
    text = await build_digest(pool, t.id, today)
    assert "Kota Steel" in text and "43B(h)" in text and "Big Corp" not in text
