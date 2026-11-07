"""The PC-side Tally reader: parse bills, join phone numbers, build the server payload. No Tally or network needed."""
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
import tally_sender as ts  # noqa: E402

BILLS = b"""<ENVELOPE>
<BILLFIXED><BILLDATE>1-Nov-26</BILLDATE><BILLREF>S-101</BILLREF><BILLPARTY>Ravi Traders</BILLPARTY></BILLFIXED>
<BILLCL>-50,000.00</BILLCL><BILLDUE>16-Nov-26</BILLDUE>
<BILLFIXED><BILLDATE>3-Nov-26</BILLDATE><BILLREF>S-102</BILLREF><BILLPARTY>No Phone Co</BILLPARTY></BILLFIXED>
<BILLCL>-1,200.00</BILLCL><BILLDUE></BILLDUE>
<BILLFIXED><BILLDATE>3-Nov-26</BILLDATE><BILLREF>Z-0</BILLREF><BILLPARTY>Zero Co</BILLPARTY></BILLFIXED>
<BILLCL>0.00</BILLCL>
</ENVELOPE>"""
PARTIES = b"""<ENVELOPE><LEDGER NAME="Ravi Traders"><LEDGERMOBILE>+91 98123-45678</LEDGERMOBILE><PARTYGSTIN>08ABCDE1234F1Z5</PARTYGSTIN></LEDGER>
<LEDGER NAME="No Phone Co"><PARTYGSTIN></PARTYGSTIN></LEDGER></ENVELOPE>"""


def test_parse_bills_drops_zero_and_reads_signed_amounts():
    bills = ts.parse_bills(BILLS)
    assert [b["ref"] for b in bills] == ["S-101", "S-102"]
    assert bills[0]["amount"] == 50000.0 and bills[0]["due"] == date(2026, 11, 16)
    assert bills[1]["due"] is None


def test_payload_skips_parties_without_a_phone_and_defaults_due_date():
    parties = ts.parse_parties(PARTIES)
    assert parties["Ravi Traders"]["phone"] == "9812345678"
    p = ts.build_payload(ts.parse_bills(BILLS), ts.parse_bills(BILLS), parties, msme={"RAVI TRADERS": "small"})
    assert [r["invoice_ref"] for r in p["receivables"]] == ["S-101"]
    assert p["_skipped_no_phone"] == ["No Phone Co"]
    assert p["payables"][0]["msme_class"] == "small" and p["payables"][1]["msme_class"] == "unknown"


def test_due_date_falls_back_to_credit_days():
    bills = ts.parse_bills(BILLS)
    p = ts.build_payload(bills[1:], [], {"No Phone Co": {"phone": "9000000000", "gstin": None}})
    assert p["receivables"][0]["due_date"] == "2026-12-03"


def test_runs_once_a_day_after_two_am():
    assert not ts.due_now(None, datetime(2026, 11, 7, 1, 0))
    assert ts.due_now(None, datetime(2026, 11, 7, 2, 5))
    ran = datetime(2026, 11, 7, 2, 5).timestamp()
    assert not ts.due_now(ran, datetime(2026, 11, 7, 23, 0))
    assert ts.due_now(ran, datetime(2026, 11, 8, 3, 0))


def test_only_loopback_is_ever_contacted():
    import pytest
    with pytest.raises(ValueError):
        ts.post_tally("<x/>", port=0)
