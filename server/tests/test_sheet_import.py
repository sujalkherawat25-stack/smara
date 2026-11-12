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
    rows, skipped = map_sheet("t.xlsx", data)                 # no number on file: kept, with no phone (never messaged)
    assert [r.invoice_ref for r in rows] == ["INV-77"] and rows[0].phone == "" and not skipped
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


HEADER = ["Party", "Mobile", "Invoice", "Due Date", "Amount"]


def test_every_sheet_with_a_header_is_read_and_counted():
    data = xlsx([HEADER, ["A", "9812345671", "A1", "01-10-2026", 100], ["Total", None, None, None, 100]],
                [HEADER, ["B", "9812345672", "B1", "01-10-2026", 200], ["B", "9812345672", "B2", "02-10-2026", 50]],
                [["Notes"], ["x"]],
                [HEADER, ["C", "9812345673", "C1", "01-10-2026", 300]])
    from src.ingest.sheet_mapper import map_sheet_detail
    rows, skipped, sheets = map_sheet_detail("m.xlsx", data)
    assert sorted(r.invoice_ref for r in rows) == ["A1", "B1", "B2", "C1"]
    assert sum(r.amount_paise for r in rows) == 65000
    assert [s["rows"] for s in sheets] == [1, 2, 1] and not skipped          # the total line and the one-line notes sheet are not noise


def test_a_sheet_with_data_but_no_receivables_columns_is_said_out_loud():
    data = xlsx([HEADER, ["A", "9812345671", "A1", "01-10-2026", 100]],
                [["Item", "Qty", "Rate"], ["nuts", 1, 2], ["bolts", 3, 4], ["screws", 5, 6]])
    rows, skipped = map_sheet("m.xlsx", data)
    assert len(rows) == 1 and any("not read" in s and "S1" in s for s in skipped)


def test_total_lines_are_not_reported_as_skipped_rows():
    csv = HEAD + ROW + "Grand Total,,,,5000\n"
    rows, skipped = map_sheet("t.csv", csv.encode())
    assert len(rows) == 1 and skipped == []


def test_the_same_invoice_twice_counts_once_and_a_reused_number_is_kept_apart():
    data = xlsx([HEADER, ["A", "9812345671", "1001", "01-10-2026", 100]],
                [HEADER, ["A", "9812345671", "1001", "01-10-2026", 100],        # the same invoice again (a summary sheet)
                 ["B", "9812345672", "1001", "01-10-2026", 250]])               # another branch's invoice with the same number
    rows, skipped = map_sheet("m.xlsx", data)
    assert sorted((r.invoice_ref, r.amount_paise) for r in rows) == [("1001", 10000), ("1001-2", 25000)]
    assert any("counted once" in s for s in skipped) and any("reused a number" in s for s in skipped)
    again, _ = map_sheet("m.xlsx", data)                                         # re-sending the file gives the same numbers
    assert [(r.invoice_ref, r.amount_paise) for r in again] == [(r.invoice_ref, r.amount_paise) for r in rows]


def test_a_single_table_file_has_one_sheet_and_no_sheet_prefix_in_reasons():
    from src.ingest.sheet_mapper import map_sheet_detail
    rows, skipped, sheets = map_sheet_detail("t.csv", (HEAD + ROW + "Meena,,S2,,\n").encode())
    assert len(rows) == 1 and len(sheets) == 1 and skipped == ["row 3: no party/amount"]


def test_a_file_over_the_invoice_limit_is_refused_with_a_message_and_imports_nothing(monkeypatch):
    from src.ingest import sheet_mapper
    monkeypatch.setattr(sheet_mapper, "MAX_INVOICES", 3)
    csv_text = "Party,Mobile,Invoice,Amount,Due Date\n" + "".join(f"A{i},9876543210,INV{i},100,01-01-2026\n" for i in range(4))
    rows, skipped, _ = sheet_mapper.map_sheet_detail("big.csv", csv_text.encode())
    assert rows == [] and len(skipped) == 1 and "4 invoices" in skipped[0] and "split it" in skipped[0]
    rows, skipped, _ = sheet_mapper.map_sheet_detail("ok.csv", csv_text.splitlines(True)[0:4] and "".join(csv_text.splitlines(True)[:4]).encode())
    assert len(rows) == 3
