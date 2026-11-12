"""A first import of many overdue invoices must not ask the owner about all of them at once."""
import os
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from src import tenants
from src.db import migrate, open_pool
from src.ingest.importer import HORIZON_DAYS, PACE_PER_DAY, import_rows, paced_followups
from src.ingest.sheet_mapper import SheetRow

URL = os.environ.get("TEST_DATABASE_URL")
NOW = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)


def rows(n, due=date(2026, 9, 1)):
    return [SheetRow(f"Cust{i}", f"9198{i:08d}", f"INV-{i:04d}", (i + 1) * 100_00, due) for i in range(n)]


def test_late_invoices_are_paced_largest_first_and_future_ones_keep_their_due_date():
    late = rows(70)
    future = [SheetRow("Later", "919800000001", "F-1", 5_00, date(2026, 12, 25))]
    when, days_needed, held = paced_followups(late + future, NOW)
    days = [(when[i] - NOW).days for i in range(70)]
    assert days_needed == 3 and held == 0
    assert [days.count(d) for d in (0, 1, 2)] == [PACE_PER_DAY, PACE_PER_DAY, 70 - 2 * PACE_PER_DAY]
    assert all(days[i] == 0 for i in range(40, 70))                  # the biggest amounts are the last rows: they go first
    assert when[70] == datetime(2026, 12, 25, tzinfo=timezone.utc)   # not yet due: untouched


def test_a_small_import_is_unchanged():
    when, days, held = paced_followups(rows(PACE_PER_DAY), NOW)
    assert set(when.values()) == {NOW} and days == 1 and held == 0


def test_a_huge_import_schedules_only_a_month_of_cards_and_says_how_many_are_left_out():
    n = PACE_PER_DAY * HORIZON_DAYS + 250
    when, days, held = paced_followups(rows(n), NOW)
    assert days == HORIZON_DAYS and held == 250
    assert sum(1 for t in when.values() if t is None) == 250
    assert max(t for t in when.values() if t is not None) == NOW + timedelta(days=HORIZON_DAYS - 1)
    assert [i for i, t in when.items() if t is None] == list(range(250))      # the smallest amounts are the ones left out


@pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")
async def test_a_big_import_is_spread_over_days_and_says_so():
    pool = await open_pool(URL)
    await migrate(pool)
    try:
        t = await tenants.create(pool, name=f"Pace {uuid.uuid4().hex[:6]}", owner_phone=f"9199{uuid.uuid4().int % 10**8:08d}")
        res = await import_rows(pool, t.id, rows(65))
        assert res["created"] == 65 and res["first_reminders_over_days"] == 3
        async with pool.connection() as conn:
            due = await (await conn.execute(
                "SELECT count(*) n FROM obligations WHERE tenant_id = %s AND next_followup_at <= now() + interval '1 minute'", (t.id,))).fetchone()
        assert due["n"] == PACE_PER_DAY
        small = await import_rows(pool, t.id, [SheetRow("Solo", "919811112222", "SOLO-1", 100, date(2026, 9, 1))])
        assert "first_reminders_over_days" not in small
    finally:
        await pool.close()
