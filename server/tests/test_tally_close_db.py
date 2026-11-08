"""Paid invoices drop out of Tally; customers without a number; the owner's 'Name number' command. Against Postgres."""
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src import tenants
from src.db import migrate, open_pool
from src.ingest.customer_number import add_number, parse_number_command
from src.ingest.importer import import_rows
from src.ingest.sheet_mapper import SheetRow
from src.ingest.tally_sync import ReceivableIn, SyncBody, apply_sync
from src.loop.reminders import claim_due, stale_sync_note

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")
TODAY = date.today()


def test_parse_number_command():
    assert parse_number_command("Ravi Traders 98765 43210") == ("Ravi Traders", "919876543210")
    assert parse_number_command("Ravi Traders +91 98765-43210") == ("Ravi Traders", "919876543210")
    assert parse_number_command("Ravi 9876543210") == ("Ravi", "919876543210")
    assert parse_number_command("ravi ka kitna baaki hai") is None      # no number: agent handles it
    assert parse_number_command("Ravi 12345") is None
    assert parse_number_command("") is None


@pytest.fixture
async def pool():
    p = await open_pool(URL)
    await migrate(p)
    yield p
    await p.close()


async def _tenant(pool):
    return await tenants.create(pool, name=f"Close {uuid.uuid4().hex[:6]}", owner_phone=f"9199{uuid.uuid4().int % 10**8:08d}")


async def _state(pool, invoice_ref):
    async with pool.connection() as conn:
        row = await (await conn.execute("SELECT state, source FROM obligations WHERE invoice_ref = %s", (invoice_ref,))).fetchone()
    return row["state"], row["source"]


def _rec(party, phone, ref, amount=1000.0):
    return ReceivableIn(party=party, phone=phone, invoice_ref=ref, amount_rupees=amount, due_date=TODAY - timedelta(days=3))


async def test_invoice_missing_from_newer_tally_snapshot_is_closed(pool):
    t = await _tenant(pool)
    ref = f"A-{uuid.uuid4().hex[:6]}"
    keep = [f"K{i}-{uuid.uuid4().hex[:5]}" for i in range(6)]
    await apply_sync(pool, t.id, None, SyncBody(receivables=[_rec("Ravi", "9812345678", r) for r in keep + [ref]]))
    result = await apply_sync(pool, t.id, None, SyncBody(receivables=[_rec("Ravi", "9812345678", r) for r in keep]))
    assert result["closed"] == 1 and result["closed_parties"] == ["Ravi"] and result["close_skipped"] is None
    assert await _state(pool, ref) == ("PAID", "TALLY")
    states = [await _state(pool, r) for r in keep]
    assert all(s[0] == "ACTIVE" for s in states)


async def test_paid_invoice_stays_paid_when_tally_lists_it_again(pool):
    t = await _tenant(pool)
    ref = f"B-{uuid.uuid4().hex[:6]}"
    body = SyncBody(receivables=[_rec("Asha", "9811111111", ref), _rec("Asha", "9811111111", ref + "x")])
    await apply_sync(pool, t.id, None, body)
    await apply_sync(pool, t.id, None, SyncBody(receivables=[_rec("Asha", "9811111111", ref + "x")]))
    assert (await _state(pool, ref))[0] == "PAID"
    await apply_sync(pool, t.id, None, body)                           # comes back in Tally: still PAID
    assert (await _state(pool, ref))[0] == "PAID"


async def test_empty_snapshot_closes_nothing(pool):
    t = await _tenant(pool)
    ref = f"C-{uuid.uuid4().hex[:6]}"
    await apply_sync(pool, t.id, None, SyncBody(receivables=[_rec("Gita", "9822222222", ref)]))
    result = await apply_sync(pool, t.id, None, SyncBody(receivables=[]))
    assert result["closed"] == 0 and "no receivables" in result["close_skipped"]
    assert (await _state(pool, ref))[0] == "ACTIVE"


async def test_guard_refuses_to_close_most_open_invoices_at_once(pool):
    t = await _tenant(pool)
    refs = [f"G{i}-{uuid.uuid4().hex[:5]}" for i in range(6)]
    await apply_sync(pool, t.id, None, SyncBody(receivables=[_rec("Hari", "9833333333", r) for r in refs]))
    result = await apply_sync(pool, t.id, None, SyncBody(receivables=[_rec("Hari", "9833333333", refs[0])]))
    assert result["closed"] == 0 and "Check the Tally export" in result["close_skipped"]
    states = [await _state(pool, r) for r in refs]
    assert all(s[0] == "ACTIVE" for s in states)


async def test_sheet_invoice_is_never_closed_by_tally(pool):
    t = await _tenant(pool)
    ref = f"S-{uuid.uuid4().hex[:6]}"
    await import_rows(pool, t.id, [SheetRow("Kiran", "919844444444", ref, 50000, TODAY)], source="SHEET")
    keep = [f"T{i}-{uuid.uuid4().hex[:5]}" for i in range(6)]
    await apply_sync(pool, t.id, None, SyncBody(receivables=[_rec("Other", "9855555555", r) for r in keep]))
    await apply_sync(pool, t.id, None, SyncBody(receivables=[_rec("Other", "9855555555", r) for r in keep]))
    assert (await _state(pool, ref))[0] == "ACTIVE"


async def test_sheet_upload_does_not_downgrade_a_tally_invoice(pool):
    t = await _tenant(pool)
    ref = f"D-{uuid.uuid4().hex[:6]}"
    await import_rows(pool, t.id, [SheetRow("Meena", "919866666666", ref, 1000, TODAY)], source="TALLY")
    await import_rows(pool, t.id, [SheetRow("Meena", "919866666666", ref, 2000, TODAY)], source="SHEET")
    assert await _state(pool, ref) == ("ACTIVE", "TALLY")


async def test_customer_without_number_is_kept_then_gets_number_later(pool):
    t = await _tenant(pool)
    ref = f"N-{uuid.uuid4().hex[:6]}"
    await import_rows(pool, t.id, [SheetRow("Sunil Stores", "", ref, 7000, TODAY)])
    await import_rows(pool, t.id, [SheetRow("Sunil Stores", "", ref + "b", 800, TODAY)])
    async with pool.connection() as conn:
        n = (await (await conn.execute("SELECT count(*) AS n FROM parties WHERE tenant_id = %s", (t.id,))).fetchone())["n"]
    assert n == 1                                                      # both invoices hang off one party, found by name
    await import_rows(pool, t.id, [SheetRow("Sunil Stores", "919877777777", ref + "c", 100, TODAY)])
    async with pool.connection() as conn:
        rows = await (await conn.execute("SELECT whatsapp_phone FROM parties WHERE tenant_id = %s", (t.id,))).fetchall()
    assert [r["whatsapp_phone"] for r in rows] == ["919877777777"]     # the number arrived: same party, no duplicate


async def test_number_command_adds_number_and_refuses_duplicates(pool):
    t = await _tenant(pool)
    await import_rows(pool, t.id, [SheetRow("Lalita", "", f"L-{uuid.uuid4().hex[:5]}", 5000, TODAY)])
    await import_rows(pool, t.id, [SheetRow("Mohan", "919888888888", f"M-{uuid.uuid4().hex[:5]}", 5000, TODAY)])
    assert "pehle se Mohan" in await add_number(pool, t.id, "Lalita", "919888888888")   # number already used by Mohan
    assert "save ho gaya" in await add_number(pool, t.id, "lalita", "919811122233")     # free number: saved, name matched case-insensitively
    assert await add_number(pool, t.id, "Lalita", "919811122233") is None              # now has a number: agent takes over
    assert await add_number(pool, t.id, "Nobody", "919800000000") is None              # not a customer without a number


async def test_customer_without_number_is_never_claimed_for_reminder(pool):
    t = await _tenant(pool)
    ref = f"R-{uuid.uuid4().hex[:6]}"
    await import_rows(pool, t.id, [SheetRow("Rekha", "", ref, 9000, TODAY - timedelta(days=5))])
    async with pool.connection() as conn:
        await conn.execute("UPDATE obligations SET next_followup_at = now() - interval '1 hour' WHERE invoice_ref = %s", (ref,))
    claimed = await claim_due(pool, datetime.now(timezone.utc))
    assert ref not in [c["invoice_ref"] for c in claimed]
    assert (await _state(pool, ref))[0] == "ACTIVE"


async def test_stale_sync_note(pool):
    t = await _tenant(pool)
    now = datetime.now(timezone.utc)
    assert "nahi hua" in await stale_sync_note(pool, t.id, now)          # never synced
    await apply_sync(pool, t.id, None, SyncBody(receivables=[_rec("Tara", "9899999999", f"Q-{uuid.uuid4().hex[:5]}")]))
    assert await stale_sync_note(pool, t.id, now) == ""                 # synced just now
    assert "ghante pehle" in await stale_sync_note(pool, t.id, now + timedelta(hours=30))
