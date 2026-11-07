"""Write assets/sample_outstanding.xlsx: a fake 8-row outstanding list in the shape Smara's importer reads.

Run from the server/ folder:  python scripts/make_sample_sheet.py
All names, phones and amounts are invented. Phones are 9800000001-9800000008 placeholders.
"""
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

ROWS = [
    # customer name, phone, invoice no, amount in rupees, invoice date, due date
    ("Sharma Traders", "9800000001", "INV-2601", 45000.00, date(2026, 10, 3), date(2026, 11, 2)),
    ("Gupta Kirana Store", "9800000002", "INV-2602", 12500.50, date(2026, 10, 9), date(2026, 11, 8)),
    ("Singh Electronics", "9800000003", "INV-2603", 98750.00, date(2026, 9, 21), date(2026, 10, 21)),
    ("Verma & Sons", "9800000004", "INV-2604", 6200.00, date(2026, 10, 15), date(2026, 11, 14)),
    ("Jain Hardware", "9800000005", "INV-2605", 31000.00, date(2026, 10, 1), date(2026, 10, 31)),
    ("Mishra Garments", "9800000006", "INV-2606", 8450.75, date(2026, 10, 20), date(2026, 11, 19)),
    ("Pandey Sweets", "9800000007", "INV-2607", 15300.00, date(2026, 9, 28), date(2026, 10, 28)),
    ("Agarwal Agencies", "9800000008", "INV-2608", 220000.00, date(2026, 8, 30), date(2026, 9, 29)),
]
HEADERS = ["Customer name", "Phone", "Invoice no", "Amount", "Invoice date", "Due date"]


def build(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Outstanding"
    ws.append(HEADERS)
    for c in ws[1]:
        c.font = Font(bold=True)
    for name, phone, inv, amount, inv_date, due in ROWS:
        ws.append([name, phone, inv, amount, inv_date, due])
    for row in ws.iter_rows(min_row=2, min_col=4, max_col=4):
        for c in row:
            c.number_format = "#,##0.00"
    for row in ws.iter_rows(min_row=2, min_col=5, max_col=6):
        for c in row:
            c.number_format = "dd-mm-yyyy"
    for col, width in zip("ABCDEF", (24, 14, 14, 14, 14, 14)):
        ws.column_dimensions[col].width = width
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


if __name__ == "__main__":
    out = Path(__file__).resolve().parents[1] / "assets" / "sample_outstanding.xlsx"
    build(out)
    print(f"wrote {out} with {len(ROWS)} rows, total Rs {sum(r[3] for r in ROWS):,.2f}")
