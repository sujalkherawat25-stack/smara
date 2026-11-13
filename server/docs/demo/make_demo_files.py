"""The files for the Smara demo: a shop's Tally-style receivables export, a purchase register and a GSTR-2B statement.

    python docs/demo/make_demo_files.py [output_folder]        (run from the server/ folder; default ./demo_files)

Everything is invented. The receivables dates are relative to today, so the same customers are always overdue by about the same
number of days and the figures below hold on any day. Run it on the morning of the demo.

FIGURES is what Smara should say after importing and after each step. The tests (tests/test_demo_files.py) check the files
against the real importer and the real GST reconcile, so this file and the demo script cannot drift apart.
"""
import io
import sys
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook

# customer, mobile (None = no number in the export), invoice, rupees, days overdue (negative = due in future days)
CUSTOMERS = [
    ("Agarwal Agencies", "98000 00001", "B-101", 220000, 63),
    ("Singh Electronics", "9800000002", "B-109", 98750, 50),
    ("Chopra Sons", None, "B-118", 42000, 42),
    ("Gupta Traders", "9800000004", "B-125", 12000, 27),
    ("Gupta Traders", "9800000004", "B-131", 7800, 19),
    ("Jain Hardware", "9800000005", "B-137", 31000, 14),
    ("Ravi Kirana Store", "9800000006", "B-142", 16575, 6),
    ("Dutta Agencies", None, "B-148", 6500, -9),
    ("Pandey Sweets", "9800000007", "B-153", 15300, -14),
    ("Mehta Kirana", "9800000008", "B-160", 3300, -30),
]

FIGURES = {
    "invoices": len(CUSTOMERS),
    "total_rupees": sum(c[3] for c in CUSTOMERS),                                   # 4,53,225
    "overdue_rupees": sum(c[3] for c in CUSTOMERS if c[4] > 0),                     # 4,28,125
    "overdue_invoices": sum(1 for c in CUSTOMERS if c[4] > 0),
    "top_overdue": ["Agarwal Agencies", "Singh Electronics", "Chopra Sons"],
    "without_number": ["Chopra Sons", "Dutta Agencies"],
    "gupta_total_rupees": 19800,
    "gupta_after_5000_cash_rupees": 14800,
    "gst": {"books": 4, "gstr2b": 4, "matched": 2, "mismatch": 1, "missing_in_2b": 1, "extra_in_2b": 1, "itc_at_risk_rupees": 900,
            "chase": ["Delhi Pipes", "Kumar Steel"]},
}


def _xlsx(rows) -> bytes:
    wb = Workbook()
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _dmy(d: date) -> str:
    return d.strftime("%d-%m-%Y")


def receivables_xlsx(today: date | None = None) -> bytes:
    """A TallyPrime 'Bills Receivable' export: title rows, 'Dr' amounts, indian grouping, a total row, two customers without a number."""
    today = today or date.today()
    rows = [["Mehta Distributors"], [f"Bills Receivable as on {_dmy(today)}"], [],
            ["Date", "Ref. No.", "Party's Name", "Mobile", "Pending Amount", "Due on"]]
    for name, phone, ref, rupees, overdue in CUSTOMERS:
        due = today - timedelta(days=overdue)
        rows.append([_dmy(due - timedelta(days=30)), ref, name, phone, f"{rupees:,.2f} Dr", _dmy(due)])
    rows.append([None, None, "Total", None, f"{FIGURES['total_rupees']:,.2f} Dr", None])
    return _xlsx(rows)


def purchase_register_xlsx() -> bytes:
    return _xlsx([["Purchase Register Oct 2026"], [], ["Date", "Supplier", "Invoice", "Taxable", "CGST", "SGST", "IGST", "Total"],
                  ["02-10-2026", "Kumar Steel", "K-1", 10000, 900, 900, 0, 11800],
                  ["11-10-2026", "Delhi Pipes", "D-7", 5000, 0, 0, 900, 5900],
                  ["20-10-2026", "Kumar Steel", "K-4", 2500, 225, 225, 0, 2950],
                  ["22-10-2026", "Rao Bearings", "R-9", 8000, 720, 720, 0, 9440]])


def gstr2b_xlsx() -> bytes:
    return _xlsx([["GSTR-2B for Oct 2026 (B2B invoices)"], [],
                  ["GSTIN of supplier", "Trade name", "Invoice number", "Invoice date", "Taxable value", "Integrated tax", "Central tax", "State/UT tax"],
                  ["07AAAPK1111A1Z1", "Kumar Steel", "K-1", "02-10-2026", 10000, 0, 900, 900],
                  ["07AAAPD2222B1Z2", "Delhi Pipes", "D-7", "11-10-2026", 2500, 450, 0, 0],
                  ["07AAAPR3333C1Z3", "Rao Bearings", "R-9", "22-10-2026", 8000, 0, 720, 720],
                  ["07AAAPS4444D1Z4", "Shah Fasteners", "S-3", "25-10-2026", 1000, 0, 90, 90]])


FILES = {"1_Outstanding_Receivables.xlsx": receivables_xlsx, "2_Purchase_Register_Oct.xlsx": purchase_register_xlsx,
         "3_GSTR2B_Oct.xlsx": gstr2b_xlsx}


def main(out: str = "demo_files") -> None:
    folder = Path(out)
    folder.mkdir(parents=True, exist_ok=True)
    for name, make in FILES.items():
        (folder / name).write_bytes(make())
        print("wrote", folder / name)
    f = FIGURES
    print(f"\nExpected after the import: {f['invoices']} invoices, Rs {f['total_rupees']:,} in all, Rs {f['overdue_rupees']:,} overdue "
          f"({f['overdue_invoices']} invoices); biggest: {', '.join(f['top_overdue'])}; no number: {', '.join(f['without_number'])}.")


if __name__ == "__main__":
    main(*sys.argv[1:2])
