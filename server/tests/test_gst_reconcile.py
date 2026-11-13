"""GST reconciliation in one call: header detection for books and GSTR-2B files, matching on GSTIN or name plus a normalised invoice
number, the four classes, ITC at risk, possible typo matches, a swapped file order, and the policy rules. No database needed."""
import asyncio
import io
from types import SimpleNamespace

import pytest
from openpyxl import Workbook

from src.agent import gst
from src.agent.policy import SessionCtx, pre_tool_use
from src.agent.tools import TOOL_NAMES


def xlsx(rows, sheet="Sheet1") -> bytes:
    wb = Workbook()
    wb.active.title = sheet
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


BOOKS = xlsx([["Purchase Register Oct 2026"], [], ["Date", "Supplier", "Invoice", "Taxable", "CGST", "SGST", "IGST", "Total"],
              ["02-10-2026", "Kumar Steel", "K-1", 10000, 900, 900, 0, 11800],
              ["11-10-2026", "Delhi Pipes", "D-7", 5000, 0, 0, 900, 5900],
              ["20-10-2026", "Kumar Steel", "K-4", 2500, 225, 225, 0, 2950],
              ["22-10-2026", "Rao Bearings", "R-0009", 8000, 720, 720, 0, 9440],
              [None, "Total", None, 25500, None, None, None, 30090]])
B2B = xlsx([["GSTR-2B for Oct 2026"], [], ["GSTIN of supplier", "Trade name", "Invoice number", "Invoice date", "Taxable value",
                                           "Integrated tax", "Central tax", "State/UT tax"],
            ["07AAAPK1111A1Z1", "Kumar Steel", "K1", "02-10-2026", 10000, 0, 900, 900],
            ["07AAAPD2222B1Z2", "Delhi Pipes", "D-7", "11-10-2026", 2500, 450, 0, 0],
            ["07AAAPR3333C1Z3", "Rao Bearings", "R-9", "22-10-2026", 8000, 0, 720, 720],
            ["07AAAPS4444D1Z4", "Shah Fasteners", "S-3", "25-10-2026", 1000, 0, 90, 90]])


def run(books=BOOKS, b2b=B2B):
    return gst.reconcile(gst.parse_file("books.xlsx", books)["rows"], gst.parse_file("GSTR2B.xlsx", b2b)["rows"])


def test_headers_are_found_below_title_rows_and_totals_are_not_invoices():
    p = gst.parse_file("books.xlsx", BOOKS)
    assert [r["invoice"] for r in p["rows"]] == ["K-1", "D-7", "K-4", "R-0009"] and p["skipped"] == 1
    q = gst.parse_file("GSTR2B.xlsx", B2B)
    assert q["rows"][0]["gstin"] == "07AAAPK1111A1Z1" and q["rows"][1]["tax"] == 45000      # Rs 450 in paise


def test_the_four_classes_and_the_itc_at_risk():
    r = run()
    assert r["counts"] == {"books_invoices": 4, "gstr2b_invoices": 4, "matched": 2, "amount_mismatch": 1, "in_books_not_in_2b": 1,
                           "in_2b_not_in_books": 1, "possible_matches": 0}
    assert r["counts_reconcile"] is True
    assert [x["invoice"] for x in r["in_books_not_in_2b"]] == ["K-4"] and r["in_books_not_in_2b"][0]["itc_rupees"] == 450.0
    m = r["amount_mismatch"][0]
    assert (m["supplier"], m["itc_books_rupees"], m["itc_2b_rupees"], m["difference_rupees"]) == ("Delhi Pipes", 900.0, 450.0, 450.0)
    assert r["in_2b_not_in_books"][0]["supplier"] == "Shah Fasteners"
    assert r["itc_at_risk_rupees"] == 900.0 and r["itc_in_books_not_in_2b_rupees"] == 450.0 and r["itc_short_in_2b_rupees"] == 450.0
    assert [c["supplier"] for c in r["chase_suppliers"]] == ["Delhi Pipes", "Kumar Steel"] or {c["supplier"] for c in r["chase_suppliers"]} == {"Delhi Pipes", "Kumar Steel"}


def test_invoice_numbers_match_despite_spaces_slashes_and_leading_zeros_but_a_typo_is_only_a_possible_match():
    assert gst._inv_key("R-0009") == gst._inv_key("r 9") == "R9" and gst._inv_key("INV/00012") == "INV12"
    books = xlsx([["Supplier", "Invoice", "Taxable", "CGST", "SGST"], ["Kumar Steel", "K-77", 1000, 90, 90]])
    b2b = xlsx([["GSTIN", "Name", "Invoice No", "Taxable Value", "Central Tax", "State Tax"], ["07AAAPK1111A1Z1", "Kumar Steel", "K-78", 1000, 90, 90]])
    r = run(books, b2b)
    assert r["counts"]["matched"] == 0 and r["counts"]["possible_matches"] == 1
    assert r["possible_matches"][0]["books_invoice"] == "K-77" and r["possible_matches"][0]["gstr2b_invoice"] == "K-78"


def test_rounding_up_to_one_rupee_still_matches():
    books = xlsx([["Supplier", "Invoice", "Taxable", "IGST"], ["Delhi Pipes", "D-1", 5000.4, 900.4]])
    b2b = xlsx([["GSTIN of supplier", "Trade name", "Invoice number", "Taxable value", "Integrated tax"], ["07AAAPD2222B1Z2", "Delhi Pipes", "D-1", 5000, 900]])
    assert run(books, b2b)["counts"]["matched"] == 1


def test_a_gstin_in_both_files_beats_a_different_spelling_of_the_name():
    books = xlsx([["Supplier", "GSTIN", "Invoice", "Taxable", "IGST"], ["Kumar Steels Pvt Ltd", "07AAAPK1111A1Z1", "K-1", 1000, 180]])
    b2b = xlsx([["GSTIN of supplier", "Trade name", "Invoice number", "Taxable value", "Integrated tax"], ["07AAAPK1111A1Z1", "KUMAR STEEL", "K-1", 1000, 180]])
    assert run(books, b2b)["counts"]["matched"] == 1


def test_a_file_without_an_invoice_column_is_a_clear_error():
    p = gst.parse_file("x.xlsx", xlsx([["Name", "Phone"], ["a", "1"]]))
    assert "could not find a header row" in p["error"]


class Docs:
    """Stands in for the document engine, which needs the sandbox."""
    made = None

    async def make_document(self, app, ctx, args):
        Docs.made = args
        return {"file_ref": "sandbox:abc:report.pdf", "filename": "report.pdf", "_new_files": ["sandbox:abc:report.pdf"]}


def test_the_tool_reads_two_files_in_either_order_and_makes_the_report(monkeypatch):
    from src.agent import actions, documents
    files = {"local:t1:books.xlsx": ("books.xlsx", BOOKS), "local:t2:GSTR2B_oct.xlsx": ("GSTR2B_oct.xlsx", B2B)}

    async def fetch(app, ref, ctx=None):
        name, data = files[ref]
        return data, "", name
    monkeypatch.setattr(actions, "_fetch", fetch)
    monkeypatch.setattr(documents, "make_document", Docs().make_document)
    ctx = SessionCtx("s", "t", "TRUSTED", files=tuple(files))
    for args in ({"books_file_ref": "local:t1:books.xlsx", "gstr2b_file_ref": "local:t2:GSTR2B_oct.xlsx"},
                 {"books_file_ref": "local:t2:GSTR2B_oct.xlsx", "gstr2b_file_ref": "local:t1:books.xlsx"}):       # named the wrong way round
        r = asyncio.run(gst.gst_reconcile(SimpleNamespace(), ctx, args))
        assert r["counts"]["matched"] == 2 and r["itc_at_risk_rupees"] == 900.0 and r["counts_reconcile"]
        assert r["report_file_ref"] == "sandbox:abc:report.pdf" and r["_new_files"] == ["sandbox:abc:report.pdf"]
        assert r["files"] == {"books": "books.xlsx", "gstr2b": "GSTR2B_oct.xlsx"}
    kinds = [b["type"] for b in Docs.made["blocks"]]
    assert kinds[0] == "stats" and kinds[-1] == "note" and kinds.count("table") == 3
    assert "error" in asyncio.run(gst.gst_reconcile(SimpleNamespace(), ctx, {"books_file_ref": "x", "gstr2b_file_ref": "x"}))


def test_only_attached_files_and_the_tool_is_read_only():
    assert "gst_reconcile" in TOOL_NAMES
    ok = {"books_file_ref": "local:a:b.xlsx", "gstr2b_file_ref": "local:c:d.xlsx"}
    assert pre_tool_use(SessionCtx("s", "t", "TRUSTED", files=("local:a:b.xlsx", "local:c:d.xlsx")), "gst_reconcile", ok, TOOL_NAMES).verdict == "allow"
    assert pre_tool_use(SessionCtx("s", "t", "TRUSTED", files=("local:a:b.xlsx",)), "gst_reconcile", ok, TOOL_NAMES).verdict == "deny"
    assert pre_tool_use(SessionCtx("s", "t", "UNTRUSTED", party_id="p", files=tuple(ok.values())), "gst_reconcile", ok, TOOL_NAMES).verdict == "deny"
