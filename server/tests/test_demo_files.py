"""The demo files must say what the demo script says: they are read by the real importer and the real GST reconcile. No database."""
import importlib.util
from datetime import date
from pathlib import Path

from src.agent import gst
from src.ingest.sheet_mapper import map_sheet

spec = importlib.util.spec_from_file_location("make_demo_files", Path(__file__).resolve().parents[1] / "docs" / "demo" / "make_demo_files.py")
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


def test_the_receivables_file_imports_with_the_figures_the_script_quotes():
    rows, skipped = map_sheet("1_Outstanding_Receivables.xlsx", demo.receivables_xlsx())
    assert len(rows) == 10 and not skipped and all(r.party != "Total" for r in rows)
    assert sum(r.amount_paise for r in rows) == 45_322_500 == demo.FIGURES["total_rupees"] * 100
    today = date.today()
    overdue = [r for r in rows if r.due_date < today]
    assert len(overdue) == demo.FIGURES["overdue_invoices"] == 7 and sum(r.amount_paise for r in overdue) == 42_812_500
    assert sorted(r.party for r in rows if not r.phone) == sorted(demo.FIGURES["without_number"])
    biggest = sorted(overdue, key=lambda r: -r.amount_paise)[:3]
    assert [r.party for r in biggest] == demo.FIGURES["top_overdue"]
    assert sum(r.amount_paise for r in rows if r.party == "Gupta Traders") == demo.FIGURES["gupta_total_rupees"] * 100


def test_the_gst_files_give_the_figures_the_script_quotes():
    books = gst.parse_file("2_Purchase_Register_Oct.xlsx", demo.purchase_register_xlsx())["rows"]
    b2b = gst.parse_file("3_GSTR2B_Oct.xlsx", demo.gstr2b_xlsx())["rows"]
    r, g = gst.reconcile(books, b2b), demo.FIGURES["gst"]
    assert r["counts"]["books_invoices"] == g["books"] and r["counts"]["gstr2b_invoices"] == g["gstr2b"]
    assert (r["counts"]["matched"], r["counts"]["amount_mismatch"], r["counts"]["in_books_not_in_2b"], r["counts"]["in_2b_not_in_books"]) == (
        g["matched"], g["mismatch"], g["missing_in_2b"], g["extra_in_2b"])
    assert r["itc_at_risk_rupees"] == g["itc_at_risk_rupees"] and sorted(c["supplier"] for c in r["chase_suppliers"]) == g["chase"]


def test_the_import_tool_accepts_the_dues_file_and_refuses_the_purchase_files():
    assert gst.not_receivables("1_Outstanding_Receivables.xlsx", demo.receivables_xlsx()) is None
    assert gst.not_receivables("2_Purchase_Register_Oct.xlsx", demo.purchase_register_xlsx()) is not None
    assert gst.not_receivables("3_GSTR2B_Oct.xlsx", demo.gstr2b_xlsx()) is not None
