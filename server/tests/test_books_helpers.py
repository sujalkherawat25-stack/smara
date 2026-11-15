"""Sandbox helpers for messy Indian books: names, invoice numbers, first-in-first-out receipts, section 194Q."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sandbox"))
import smara_helpers as mh  # noqa: E402

LEDGER = ["Rathod Traders", "Rathod Cash & Carry", "Rathod Enterprises", "Verma Kirana & General Stores",
          "Vashi Supermarket (Chopra)", "Agarwal Traders", "Maheshwari Cash & Carry"]


def test_name_key_folds_case_ampersand_spaces_plurals():
    assert mh.name_key("M/s Verma  Kirana & General Stores") == mh.name_key("VERMA KIRANA AND GENERAL STORE")


def test_match_names_separates_look_alike_customers():
    m = mh.match_names(["RATHOD CASH AND CARRY", "Rathod  Enterprises", "Rathod Trader", "Agrawal Traders",
                        "Maheswari Cash & Carry", "Vashi  Supermarket (Chopra)"], LEDGER)
    assert m["map"]["RATHOD CASH AND CARRY"] == "Rathod Cash & Carry"
    assert m["map"]["Rathod  Enterprises"] == "Rathod Enterprises"
    assert m["map"]["Rathod Trader"] == "Rathod Traders"
    assert m["map"]["Agrawal Traders"] == "Agarwal Traders"
    assert m["map"]["Maheswari Cash & Carry"] == "Maheshwari Cash & Carry"
    assert m["map"]["Vashi  Supermarket (Chopra)"] == "Vashi Supermarket (Chopra)"
    assert not m["review"]


def test_unclear_names_go_to_review_not_guessed():
    m = mh.match_names(["Rathod", "Sharma Medicals"], LEDGER)
    assert "Sharma Medicals" not in m["map"] and {r["name"] for r in m["review"]} >= {"Sharma Medicals"}


def test_norm_invoice():
    assert mh.norm_invoice("hcp-2604-0333 ") == mh.norm_invoice("HCP/2604/333") == "HCP2604333"
    assert mh.norm_invoice("A/1") != mh.norm_invoice("A/10")


def test_fifo_clears_oldest_first_and_reports_advance():
    inv = [{"party": "A", "ref": "2", "date": date(2026, 5, 1), "balance": 100.0},
           {"party": "A", "ref": "1", "date": date(2026, 4, 1), "balance": 100.0},
           {"party": "B", "ref": "9", "date": date(2026, 4, 1), "balance": 50.0}]
    r = mh.fifo_apply(inv, [{"party": "A", "amount": 130.0}, {"party": "B", "amount": 80.0}, {"party": "C", "amount": 10.0}])
    left = {x["ref"]: x["balance"] for x in r["open"]}
    assert left == {"2": 70.0}                       # A: invoice 1 cleared, 30 off invoice 2; B cleared
    assert r["advances"] == {"B": 30.0, "C": 10.0}   # advances are never moved to another customer


def test_fifo_overpaid_invoice_is_an_advance_not_negative_debt():
    r = mh.fifo_apply([{"party": "A", "ref": "1", "date": date(2026, 4, 1), "balance": -40.0}], [])
    assert r["open"] == [] and r["advances"] == {"A": 40.0}


def test_194q_only_above_the_limit_and_higher_rate_without_pan():
    bills = [{"supplier": "S1", "date": date(2026, 4, 1), "taxable": 4_000_000, "pan": "ABCDE1234F"},
             {"supplier": "S1", "date": date(2026, 5, 1), "taxable": 3_000_000, "pan": "ABCDE1234F"},
             {"supplier": "S2", "date": date(2026, 4, 1), "taxable": 6_000_000, "pan": ""},
             {"supplier": "S3", "date": date(2026, 4, 1), "taxable": 1_000_000, "pan": "ABCDE1234F"}]
    r = mh.tds_194q(bills)
    assert r["S1"]["tds"] == 2000.0 and r["S1"]["above_limit"] == 2_000_000
    assert r["S2"]["tds"] == 50_000.0 and r["S2"]["rate"] == 0.05
    assert r["S3"]["tds"] == 0.0


def test_importer_reads_hinglish_headers_and_skips_credit_balances():
    from src.ingest.sheet_mapper import map_headers, map_sheet_detail
    cols = map_headers(["Party Name", "Bill No", "Baki Rashi", "Due Date"])
    assert {"party", "invoice", "amount", "due"} <= set(cols)
    csv_bytes = "\n".join(['Naam,Bill,O/S,Due Date', 'Zaveri,ZV-1,"12,500/-",15-12-2026', 'Nair,NS-9,"1,500 Cr",10-12-2026']).encode()
    rows, notes, _ = map_sheet_detail("x.csv", csv_bytes)
    assert [r.invoice_ref for r in rows] == ["ZV-1"] and any("credit balance" in n for n in notes)
