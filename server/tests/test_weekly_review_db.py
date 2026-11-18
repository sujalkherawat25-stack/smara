"""Monday's weekly review and the daily alerts: from the ledger, owner only, quiet when there is nothing to say, switchable off."""
from datetime import date

from test_phase_b_db import make_app, one, pool, seed  # noqa: F401

from src import tenants
from src.ingest.importer import import_rows
from src.ingest.sheet_mapper import SheetRow
from src.loop import review

MONDAY = date(2026, 11, 9)


async def ledger(pool):
    t = await tenants.create(pool, name="Pragati", owner_phone="919800000042", upi_vpa="p@upi")
    rows = [("Aman Traders", "INV-A", 1_000_000, date(2026, 11, 20)),      # not due
            ("Bansal Agencies", "INV-B", 2_000_000, date(2026, 10, 25)),   # 15 days late
            ("Chawla Kirana", "INV-C", 3_000_000, date(2026, 9, 30)),      # 40
            ("Dhaniram & Sons", "INV-D", 4_000_000, date(2026, 9, 10)),    # 60 on Monday 9 Nov: crossed 60 this week
            ("Elite Stores", "INV-E", 5_000_000, date(2026, 8, 10)),       # 91: crossed 90 this week
            ("Fatima Store", "INV-F", 6_000_000, date(2026, 6, 1))]        # 161
    await import_rows(pool, t.id, [SheetRow(n, f"9198{i:08d}", inv, amt, due) for i, (n, inv, amt, due) in enumerate(rows)])
    return t


async def test_weekly_review_shows_where_the_money_stands_and_what_aged(pool):
    t = await ledger(pool)
    text = await review.weekly_review(pool, t.id, MONDAY)
    assert "Kul baaki: ₹2,10,000" in text and "6 bills, 6 parties" in text
    assert "Abhi due nahi: ₹10,000" in text and "90 din se upar: ₹1,10,000" in text
    assert "90 din paar: 1 bill" in text and "Elite Stores" in text and "60 din paar: 1 bill" in text and "Dhaniram" in text
    assert "Fatima Store: ₹60,000" in text                                   # biggest overdue first
    assert "purani file" in text and "Tally/Excel file bhej" in text         # no file seen: asks for this week's file
    assert "digest off" in text and "reminder" not in text.lower() and "WhatsApp" not in text


async def test_a_fresh_file_changes_the_nudge(pool):
    t = await ledger(pool)
    async with pool.connection() as c:
        await c.execute("INSERT INTO chat_messages (tenant_id, channel, role, kind, content, created_at) "
                        "VALUES (%s, 'telegram', 'owner', 'file', '[file: x.xlsx]', '2026-11-08 10:00+05:30')", (t.id,))
    text = await review.weekly_review(pool, t.id, MONDAY)
    assert "List 8 Nov tak ki hai" in text and "purani" not in text


async def test_no_ledger_no_review(pool):
    t = await tenants.create(pool, name="Personal", owner_phone="919800000043", upi_vpa=None)
    assert await review.weekly_review(pool, t.id, MONDAY) == "" and await review.daily_alerts(pool, t.id, MONDAY) == ""


async def test_daily_alert_only_when_something_crosses(pool):
    t = await ledger(pool)
    quiet = await review.daily_alerts(pool, t.id, date(2026, 11, 11))
    assert quiet == ""                                                         # nothing crossed 30/60/90 or is a 43B(h) deadline
    day = await review.daily_alerts(pool, t.id, MONDAY)
    assert "aaj 60 din se late" in day and "Dhaniram" in day and "digest off" in day
    ninety = await review.daily_alerts(pool, t.id, date(2026, 11, 9) + (date(2026, 8, 10) - date(2026, 8, 10)))
    assert "90 din" not in ninety or "Elite" in ninety


async def test_supplier_deadline_alert(pool):
    t = await tenants.create(pool, name="Buyer", owner_phone="919800000044", upi_vpa="b@upi")
    async with pool.connection() as c:
        await c.execute("INSERT INTO payables (tenant_id, supplier, bill_ref, bill_date, amount_paise, msme_class, agreement_45) "
                        "VALUES (%s, 'Raj Textiles', 'B-1', '2026-10-26', 2500000, 'micro', false)", (t.id,))   # due 10 Nov
    assert "2 din mein deadline" in await review.daily_alerts(pool, t.id, date(2026, 11, 8))
    assert "aaj deadline" in await review.daily_alerts(pool, t.id, date(2026, 11, 10))
    assert "kal deadline nikal gayi" in await review.daily_alerts(pool, t.id, date(2026, 11, 11))
    assert "Deadline nikal gayi: 1 bill" in await review.weekly_review(pool, t.id, date(2026, 11, 16))


async def test_digest_off_and_on_by_command_and_in_the_scheduler(pool):
    from datetime import datetime

    from src.loop.scheduler import IST, send_digests
    app = make_app(pool)
    t = await ledger(pool)
    await review.set_digest(pool, t.id, False)
    assert await review.opted_out(pool, t.id)
    assert await send_digests(app, datetime(2026, 11, 9, 9, 30, tzinfo=IST)) == 0          # opted out: nothing
    await review.set_digest(pool, t.id, True)
    assert not await review.opted_out(pool, t.id)
    assert await send_digests(app, datetime(2026, 11, 16, 9, 30, tzinfo=IST)) == 1         # the next Monday
