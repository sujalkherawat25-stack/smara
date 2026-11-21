"""Bank reconciliation statement: matching on reference and amount, the items only one side has, a transposed amount, a double entry, and
the proof that the corrected book balance equals the adjusted bank balance. The planted-error files come from evals/ca_data.py, whose
answer key was derived twice (evals/verify_truth.py)."""
import asyncio
import io
from datetime import date
from types import SimpleNamespace

from openpyxl import Workbook

from evals import ca_data as cd
from src.agent import actions, bank, ca_reports, documents, extras
from src.agent.policy import SessionCtx, pre_tool_use
from src.agent.tools import TOOL_NAMES


def xlsx(rows) -> bytes:
    wb = Workbook()
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_the_planted_files_reconcile_to_the_independent_key():
    f, t = cd.bank_recon()
    b = bank.parse_file("book.xlsx", f["bank_book_oct.xlsx"], "books")
    s = bank.parse_file("stmt.xlsx", f["bank_statement_oct.xlsx"], "bank")
    r = bank.reconcile(b, s)
    assert r["balance_per_books_rupees"] == t["books_balance"] and r["balance_per_bank_rupees"] == t["bank_balance"]
    assert r["payments_not_yet_presented_rupees"] == t["uncleared_cheques"] and len(r["payments_not_yet_presented"]) == t["uncleared_n"]
    assert r["receipts_in_transit_rupees"] == t["deposits_in_transit"] and len(r["receipts_in_transit"]) == t["deposits_n"]
    assert r["bank_debits_not_in_books_rupees"] == t["bank_charges"] and r["bank_credits_not_in_books_rupees"] == t["interest"] + t["direct_credit"]
    assert r["entered_twice_rupees"] == t["duplicate_payment"] and r["entered_twice_in_books"][0]["ref"] == t["duplicate_ref"]
    [m] = r["amount_recorded_differently"]
    assert (m["in_books_rupees"], m["in_bank_rupees"], m["ref"], m["looks_like_transposed_digits"]) == (38160.0, 38610.0, "482913", True)
    assert r["corrected_balance_per_books_rupees"] == r["adjusted_balance_per_bank_rupees"] == t["reconciled_balance"] and r["reconciles"]


def test_the_summary_sentences_carry_every_figure_the_owner_asked_for():
    f, t = cd.bank_recon()
    r = bank.reconcile(bank.parse_file("b.xlsx", f["bank_book_oct.xlsx"], "books"), bank.parse_file("s.xlsx", f["bank_statement_oct.xlsx"], "bank"))
    text = " ".join(bank.summary_lines(r))
    for needle in ("8,17,808.00", "9,60,135.00", "1,95,242.00", "1,26,246.00", "1,003.00", "1,843.00", "26,500.00", "38,160.00", "38,610.00", "45,541.00", "8,91,139.00", "reconciles"):
        assert needle in text, needle


def test_without_an_opening_balance_it_says_what_it_needs_instead_of_guessing():
    book = xlsx([["Date", "Particulars", "Cheque No", "Receipt", "Payment"], ["01-10-2026", "A", "1001", None, 500]])
    stmt = xlsx([["Date", "Description", "Chq/Ref No", "Withdrawal", "Deposit", "Balance"], ["02-10-2026", "A", "1001", 500, None, 9500]])
    r = bank.reconcile(bank.parse_file("b.xlsx", book, "books"), bank.parse_file("s.xlsx", stmt, "bank"))
    assert r["needs"].startswith("the opening balance of the cash book") and r["corrected_balance_per_books_rupees"] is None and r["counts"]["matched"] == 1
    assert "cannot finish" in bank.summary_lines(r)[0]


def test_entries_with_no_reference_match_on_amount_and_date_and_are_flagged_for_a_person():
    book = xlsx([["Opening balance 10,000.00 Dr"], ["Date", "Particulars", "Cheque No", "Receipt", "Payment"], ["01-10-2026", "Cash deposit", None, 2000, None]])
    stmt = xlsx([["Opening Balance 10,000.00"], ["Date", "Description", "Chq/Ref No", "Withdrawal", "Deposit", "Balance"], ["03-10-2026", "CASH DEP", None, None, 2000, 12000]])
    r = bank.reconcile(bank.parse_file("b.xlsx", book, "books"), bank.parse_file("s.xlsx", stmt, "bank"))
    assert r["counts"]["matched"] == 1 and r["counts"]["matched_without_reference"] == 1 and r["reconciles"]
    assert "only by amount and date" in " ".join(bank.summary_lines(r))


def test_a_payment_and_a_receipt_of_the_same_amount_are_never_matched_to_each_other():
    book = xlsx([["Opening balance 0.00"], ["Date", "Particulars", "Cheque No", "Receipt", "Payment"], ["01-10-2026", "Paid", "7001", None, 700]])
    stmt = xlsx([["Opening Balance 0.00"], ["Date", "Description", "Chq/Ref No", "Withdrawal", "Deposit", "Balance"], ["01-10-2026", "Received", "7001", None, 700, 700]])
    r = bank.reconcile(bank.parse_file("b.xlsx", book, "books"), bank.parse_file("s.xlsx", stmt, "bank"))
    assert r["counts"]["matched"] == 0 and len(r["payments_not_yet_presented"]) == 1 and len(r["bank_credits_not_in_books"]) == 1


def test_as_on_leaves_out_what_happened_after_the_date():
    f, t = cd.bank_recon()
    b = bank.parse_file("b.xlsx", f["bank_book_oct.xlsx"], "books")
    s = bank.parse_file("s.xlsx", f["bank_statement_oct.xlsx"], "bank")
    early = bank.reconcile(b, s, date(2026, 10, 15))
    assert early["counts"]["books_entries"] < len(b["rows"]) and early["balance_per_books_rupees"] != t["books_balance"]


def test_the_tool_makes_the_files_sends_them_and_refuses_files_that_are_not_in_the_conversation(monkeypatch):
    f, t = cd.bank_recon()
    files = {"local:b:book.xlsx": ("book.xlsx", f["bank_book_oct.xlsx"]), "local:s:stmt.xlsx": ("stmt.xlsx", f["bank_statement_oct.xlsx"])}

    async def fetch(app, ref, ctx=None):
        n, d = files[ref]
        return d, "", n
    sent = []

    async def send(app, ctx, args):
        sent.append(args["file_ref"])
        return {"sent": True}

    class Docs:
        async def make_document(self, app, ctx, args):
            return {"file_ref": "sandbox:j:report.pdf", "filename": "report.pdf", "_new_files": ["sandbox:j:report.pdf"]}

    async def excel(app, name, sheets):
        assert [s[0] for s in sheets][:2] == ["BRS", "Payments not presented"]
        return f"sandbox:j:{name}", name
    monkeypatch.setattr(actions, "_fetch", fetch)
    monkeypatch.setattr(extras, "send_file_to_owner", send)
    monkeypatch.setattr(documents, "make_document", Docs().make_document)
    monkeypatch.setattr(ca_reports, "excel_file", excel)
    ctx = SessionCtx("s", "t", "TRUSTED", files=tuple(files))
    r = asyncio.run(bank.bank_reconcile(SimpleNamespace(), ctx, {"book_file_ref": "local:b:book.xlsx", "bank_file_ref": "local:s:stmt.xlsx"}))
    assert r["reconciles"] and sorted(r["files_sent_to_owner"]) == ["Bank_reconciliation.xlsx", "report.pdf"] and len(sent) == 2
    assert "never recompute" in r["next"] and any("Rs 8,91,139.00" in x for x in r["summary_lines"])
    assert "error" in asyncio.run(bank.bank_reconcile(SimpleNamespace(), ctx, {"book_file_ref": "x", "bank_file_ref": "x"}))
    ok = {"book_file_ref": "local:b:book.xlsx", "bank_file_ref": "local:s:stmt.xlsx"}
    assert "bank_reconcile" in TOOL_NAMES and pre_tool_use(ctx, "bank_reconcile", ok, TOOL_NAMES).verdict == "allow"
    assert pre_tool_use(SessionCtx("s", "t", "TRUSTED", files=("local:b:book.xlsx",)), "bank_reconcile", ok, TOOL_NAMES).verdict == "deny"
