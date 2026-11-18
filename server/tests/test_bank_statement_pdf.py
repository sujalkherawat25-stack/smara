"""mh.bank_statement_pdf reads a bank statement PDF with code: rows, totals, direction from the running balance, failed rows,
overdrawn balances, a tie-out against the closing balance, passwords and scans."""
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sandbox"))
import smara_helpers as mh  # noqa: E402

reportlab = pytest.importorskip("reportlab")
from reportlab.lib.pagesizes import A4, landscape  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402


def money(v: float) -> str:
    s = f"{abs(v):,.2f}"
    head, _, tail = s.partition(".")
    digits = head.replace(",", "")
    out = digits[-3:]
    digits = digits[:-3]
    while digits:
        out = digits[-2:] + "," + out
        digits = digits[:-2]
    return f"{'-' if v < 0 else ''}{out}.{tail}"


def make_pdf(tmp_path: Path, txns, opening, closing, style="columns", name="stmt.pdf", failed_row=None) -> Path:
    """txns: [(date 'dd-mm-yyyy', description, debit, credit)]. Writes each row on its own line at fixed columns, over two pages."""
    path = tmp_path / name
    c = canvas.Canvas(str(path), pagesize=landscape(A4))
    c.setFont("Helvetica", 9)
    c.drawString(40, 560, "Account Statement  SAMPLE TRADERS")
    c.drawString(40, 545, f"Opening Balance   Rs. {money(opening)}")
    c.drawString(300, 545, f"Closing Balance   Rs. {money(closing)}")

    def header(y):
        if style == "columns":
            for x, t in ((40, "Txn Date"), (130, "Description"), (470, "Debit"), (560, "Credit"), (660, "Balance")):
                c.drawString(x, y, t)
        else:
            for x, t in ((40, "Date"), (130, "Description"), (470, "Cr/Dr"), (520, "Amount"), (640, "Balance")):
                c.drawString(x, y, t)
    y = 520
    header(y)
    bal = opening
    for i, (d, desc, dr, cr) in enumerate(txns):
        y -= 18
        if y < 60:
            c.showPage()
            c.setFont("Helvetica", 9)
            y = 540
            header(y)
            y -= 18
        c.drawString(40, y, d)
        c.drawString(130, y, desc)
        if failed_row == i:                                    # a failed cash withdrawal: amount shown, balance unchanged
            amt, cd, shown = dr, "DR", bal
        else:
            bal = round(bal + cr - dr, 2)
            amt, cd, shown = (dr or cr), ("DR" if dr else "CR"), bal
        if style == "columns":
            if dr:
                c.drawRightString(520, y, f"Rs. {money(amt)}")
            else:
                c.drawRightString(610, y, f"Rs. {money(amt)}")
            c.drawRightString(720, y, f"Rs. {money(shown)}")
        else:
            c.drawString(470, y, cd)
            c.drawRightString(590, y, money(amt))
            c.drawRightString(700, y, money(shown))
    c.save()
    return path


TXNS = [
    ("01-04-2026", "NEFT Cr-GUPTA TRADERS", 0, 50000.00),
    ("02-04-2026", "UPI/DR/ELECTRICITY BOARD", 4250.50, 0),
    ("03-04-2026", "CHQ PAID 000281 SHARMA TEXTILES", 20000.00, 0),
    ("05-04-2026", "RTGS Cr-VERMA AGENCIES", 0, 125000.00),
    ("07-04-2026", "Service Charges SMS Alerts", 118.00, 0),
    ("09-04-2026", "NEFT Cr-GUPTA TRADERS", 0, 30000.00),
]


@pytest.mark.parametrize("style", ["columns", "cr_dr"])
def test_totals_rows_and_the_tie_out(tmp_path, style):
    opening = 10000.00
    closing = round(opening + 50000 - 4250.50 - 20000 + 125000 - 118 + 30000, 2)
    r = mh.bank_statement_pdf(make_pdf(tmp_path, TXNS, opening, closing, style))
    assert r["ok"] and r["row_count"] == 6
    assert r["credits"] == {"total": 205000.0, "count": 3} and r["debits"] == {"total": 24368.5, "count": 3}
    assert r["opening"] == opening and r["closing"] == closing and r["ties"] is True and r["chain_breaks"] == []
    assert r["top_credits"][0]["credit"] == 125000.0 and r["top_debits"][0]["debit"] == 20000.0
    assert r["rows"][0]["date"] == "2026-04-01" and r["by_month"]["2026-04"]["credits"] == 205000.0


def test_multi_page_statement(tmp_path):
    txns = [(f"{1 + i % 28:02d}-05-2026", f"UPI/DR/SHOP {i}", 100.0 + i, 0) for i in range(80)]
    closing = round(500000 - sum(t[2] for t in txns), 2)
    r = mh.bank_statement_pdf(make_pdf(tmp_path, txns, 500000.0, closing))
    assert r["ok"] and r["row_count"] == 80 and r["ties"] and r["debits"]["total"] == round(sum(t[2] for t in txns), 2)


def test_a_failed_row_is_left_out_and_reported(tmp_path):
    opening = 10000.00
    closing = round(opening + 50000 - 4250.50 - 20000 + 125000 - 118 + 30000, 2)
    r = mh.bank_statement_pdf(make_pdf(tmp_path, TXNS, opening, closing, failed_row=2))   # the 20,000 cheque did not clear
    assert r["row_count"] == 5 and len(r["failed"]) == 1 and r["failed"][0]["amount"] == 20000.0
    assert r["debits"]["total"] == 4368.5


def test_an_overdrawn_balance_printed_without_its_sign_still_reads(tmp_path):
    txns = [("01-04-2026", "CHQ PAID 1", 5000.00, 0), ("02-04-2026", "NEFT Cr-ABC", 0, 7000.00)]   # opening 2,000 -> -3,000 -> 4,000
    r = mh.bank_statement_pdf(make_pdf(tmp_path, txns, 2000.00, 4000.00))
    assert r["ok"] and r["debits"]["total"] == 5000.0 and r["credits"]["total"] == 7000.0 and r["ties"]
    assert r["rows"][0]["balance"] == -3000.0


def test_a_statement_that_does_not_add_up_says_so(tmp_path):
    r = mh.bank_statement_pdf(make_pdf(tmp_path, TXNS, 10000.00, 999999.99))
    assert r["ties"] is False and any("closing balance" in n for n in r["notes"])


def test_a_scan_and_a_non_statement_are_told_apart(tmp_path):
    p = tmp_path / "blank.pdf"
    c = canvas.Canvas(str(p))
    c.showPage()
    c.save()
    assert "scan" in mh.bank_statement_pdf(p)["error"]
    q = tmp_path / "letter.pdf"
    c = canvas.Canvas(str(q))
    for i in range(40):
        c.drawString(40, 800 - i * 15, "This is a letter about something else entirely and has no transactions in it at all.")
    c.save()
    assert "no transaction rows" in mh.bank_statement_pdf(q)["error"]


def test_password_protected_pdf_asks_for_the_password(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    plain = make_pdf(tmp_path, TXNS, 10000.00, 205000 - 24368.5 + 10000, name="plain.pdf")
    w = pypdf.PdfWriter(clone_from=str(plain))
    try:
        w.encrypt("s3cret")
    except Exception as e:                                   # no crypto library here: not a failure of the reader
        pytest.skip(f"cannot encrypt in this environment: {e}")
    locked = tmp_path / "locked.pdf"
    with open(locked, "wb") as f:
        w.write(f)
    bad = mh.bank_statement_pdf(locked)
    assert bad["ok"] is False and bad["need_password"] is True
    good = mh.bank_statement_pdf(locked, password="s3cret")
    assert good["ok"] and good["row_count"] == 6


def test_read_attachment_hands_the_agent_a_program_read_statement(tmp_path):
    from src.agent import reader
    p = make_pdf(tmp_path, TXNS, 10000.00, round(10000 + 205000 - 24368.5, 2))
    data = p.read_bytes()
    text = reader.pdf_text(data)
    import re
    assert re.search(r"opening balance|closing balance", text["text"], re.I)           # the gate in read_attachment
    out = reader._bank_statement(data)
    assert out["statement"]["ties"] is True and out["statement"]["credits"]["total"] == 205000.0
    assert len(out["rows_preview"]) == 5 and "do not re-add" in out["next"]


def test_a_non_statement_pdf_is_not_taken_for_one(tmp_path):
    from src.agent import reader
    q = tmp_path / "letter.pdf"
    c = canvas.Canvas(str(q))
    for i in range(40):
        c.drawString(40, 800 - i * 15, "A letter about an unrelated matter with the balance of the argument still open.")
    c.save()
    assert reader._bank_statement(q.read_bytes()) is None
