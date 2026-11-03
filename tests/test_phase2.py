from datetime import date

from src.ingest.extract import _heuristic, parse_promise_date
from src.ingest.sheet_mapper import map_sheet, parse_amount_paise, parse_date, parse_phone
from src.loop.reminders import draft_reminder
from src.loop.upi import rupees, upi_link
from src.loop.whatsapp import owner_approval_card

TALLY = (
    "Outstanding Report,,,,\n"
    "Company: ABC Traders,,,,\n"
    "Date,Particulars,Ref No,Pending Amount,Mobile\n"
    "05-10-2026,Ravi Kirana,INV-101,\"1,25,000.50 Dr\",98765 43210\n"
    "01-11-2026,Sharma Store,INV-102,40000,\n"
    ",,,,\n"
    "12-10-2026,Gupta & Co,INV-103,\"Rs. 9,999\",+91 99999 11111\n"
).encode()


def test_tally_sheet_mapping():
    rows, skipped = map_sheet("out.csv", TALLY)
    assert [r.invoice_ref for r in rows] == ["INV-101", "INV-103"]
    assert rows[0].amount_paise == 12_500_050 and rows[0].phone == "919876543210"
    assert rows[0].due_date == date(2026, 11, 4)  # bill date + 30d fallback
    assert len(skipped) == 1 and "Sharma" in skipped[0]


def test_vyapar_style_headers_and_missing_header():
    csv = b"Party Name,Mobile Number,Invoice No,Due Date,Balance\nAsha,9811122233,V-1,15/11/2026,500\n"
    (r,), _ = map_sheet("v.csv", csv)
    assert (r.party, r.amount_paise, r.due_date) == ("Asha", 50_000, date(2026, 11, 15))
    rows, why = map_sheet("x.csv", b"foo,bar\n1,2\n")
    assert rows == [] and why


def test_parsers():
    assert parse_amount_paise("₹1,000") == 100_000 and parse_amount_paise("0") is None
    assert parse_date("5-Nov-26") == date(2026, 11, 5)
    assert parse_phone("98765 43210") == "919876543210" and parse_phone("123") == ""


def test_promise_dates_and_discount():
    today = date(2026, 11, 3)
    assert parse_promise_date("kal bhej dunga", today) == date(2026, 11, 4)
    assert parse_promise_date("10 tarikh ko", today) == date(2026, 11, 10)
    assert parse_promise_date("2 tarikh ko", today) == date(2026, 12, 2)
    assert parse_promise_date("15 dec tak", today) == date(2026, 12, 15)
    c = _heuristic("10% discount de do", 100_000, today)
    assert (c.type, c.amount_paise) == ("DISCOUNT_CLAIM", 90_000)
    # a "discount" can never raise the amount owed
    assert _heuristic("200% discount", 100_000, today).amount_paise is None


def test_upi_and_reminder():
    link = upi_link("shop@upi", "Ravi Shop", 150_050, "INV 1")
    assert link == "upi://pay?pa=shop@upi&pn=Ravi%20Shop&am=1500.50&cu=INR&tn=INV%201"
    assert rupees(150_050) == "₹1,500.50"
    o = {"canonical_name": "Ravi", "invoice_ref": "INV-1", "amount_paise": 100_000,
         "due_date": date(2026, 11, 1), "attempt_count": 0}
    assert "upi://pay" in draft_reminder(o, {"vpa": "s@upi"}) and "upi://" not in draft_reminder(o, None)


def test_card_shows_proposal():
    card = owner_approval_card("91", adjustment_id="A", party_name="R", invoice_ref="I",
                               adjustment_type="DISCOUNT_CLAIM", evidence="kam kar do", proposal="new amount ₹900")
    assert "Proposed: new amount ₹900" in card["interactive"]["body"]["text"]
