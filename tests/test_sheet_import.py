"""Receivable sheets as owners really send them: Tally exports, odd delimiters and encodings, several sheets."""
import io
from datetime import date

from openpyxl import Workbook

from src.ingest.sheet_mapper import map_sheet, parse_amount_paise

HEAD = "Party,Mobile,Invoice,Due Date,Amount\n"
ROW = "Ravi,9812345678,S1,01-10-2026,5000\n"


def xlsx(*sheets):
    wb = Workbook()
    wb.remove(wb.active)
    for i, rows in enumerate(sheets):
        ws = wb.create_sheet(f"S{i}")
        for r in rows:
            ws.append(r)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def test_delimiters_and_encodings():
    text = HEAD + ROW
    for data in (text.encode(), text.replace(",", ";").encode(), text.replace(",", "\t").encode(),
                 text.encode("utf-16"), ("﻿" + text).encode("utf-8")):
        rows, skipped = map_sheet("a.csv", data)
        assert len(rows) == 1 and not skipped and rows[0].amount_paise == 500000


def test_windows_1252_names_survive():
    rows, _ = map_sheet("a.csv", (HEAD + "Ravi – Sons,9812345678,S1,01-10-2026,5000\n").encode("cp1252"))
    assert rows[0].party == "Ravi – Sons"


def test_tally_bills_receivable_export_uses_customers_on_file_for_phones():
    tally = [["Bills Receivable"], ["Rajesh Enterprises"], [], ["Date", "Ref. No.", "Party's Name", "Pending Amount", "Due on"],
             [date(2026, 9, 1), "INV-77", "Ravi Traders", 50000, date(2026, 10, 1)],
             ["Total", None, None, 50000, None]]
    data = xlsx(tally)
    rows, skipped = map_sheet("t.xlsx", data)
    assert not rows and "Ravi Traders" in skipped[0] and "phone" in skipped[0]
    rows, skipped = map_sheet("t.xlsx", data, {"ravi traders": "9812345678"})
    assert [r.invoice_ref for r in rows] == ["INV-77"] and rows[0].phone == "919812345678" and not skipped


def test_data_on_a_later_sheet_is_found():
    data = xlsx([["Summary"]], [["Party", "Mobile", "Invoice", "Due Date", "Amount"], ["Ravi", "9812345678", "S1", "01-10-2026", 5]])
    assert len(map_sheet("m.xlsx", data)[0]) == 1


def test_hindi_headers():
    data = xlsx([["पार्टी", "मोबाइल", "बिल नं", "देय तिथि", "राशि"], ["रवि", "9812345678", "1", "01-10-2026", 500]])
    assert len(map_sheet("h.xlsx", data)[0]) == 1


def test_rupee_text_amounts():
    for v in ("₹ 5,000/-", "Rs. 5,000/-", "5,000.00 Dr", "INR 5000"):
        assert parse_amount_paise(v) == 500000


def test_unreadable_files_explain_themselves():
    rows, why = map_sheet("e.csv", b"")
    assert rows == [] and "header" in why[0]
    rows, why = map_sheet("x.xlsx", b"not a workbook")
    assert rows == [] and "could not be opened" in why[0]
    rows, why = map_sheet("c.csv", b"Name,Colour\nA,red\n")
    assert "columns I can see: Name, Colour" in why[0]
