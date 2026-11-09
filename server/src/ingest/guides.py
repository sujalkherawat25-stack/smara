"""How an owner gets their outstanding list out of Tally / Busy, plus a sample sheet to copy the shape from."""
from pathlib import Path

SAMPLE_NAME = "sample_outstanding.xlsx"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

EXPORT_GUIDE = (
    "Tally/Busy se outstanding list kaise nikalein:\n\n"
    "*Tally Prime*\n"
    "Gateway of Tally > Display More Reports > Outstandings > Receivables. "
    "Phir Alt+E dabayein, Format: Excel (XLSX), Export. File yahin bhej dijiye.\n\n"
    "*Tally ERP 9*\n"
    "Display > Statements of Accounts > Outstandings > Receivables. Phir Alt+E, Excel.\n\n"
    "*Busy*\n"
    "Display > Outstanding Analysis > Bills Receivable. Bill-wise option chunein, phir Export > Excel.\n\n"
    "*Koi software nahi?*\n"
    "Excel mein ye columns banaiye: Customer name, Phone, Invoice no, Amount, Invoice date, Due date. "
    "Ya bahi/khaate ke page ki saaf photo bhej dijiye.\n\n"
    "Sample file chahiye to 'sample' likhiye."
)

SAMPLE_CAPTION = ("Ye sample file hai, sab data nakli hai. Apni list isi tarah ke columns mein bhejiye "
                  "(Tally/Busy export bhi chalega).")


def sample_sheet_path() -> Path | None:
    """assets/ sits next to src/ in the repo and in the Docker image (/app/assets)."""
    for base in (Path(__file__).resolve().parents[2], Path.cwd()):
        p = base / "assets" / SAMPLE_NAME
        if p.is_file():
            return p
    return None


def sample_sheet_bytes() -> bytes | None:
    p = sample_sheet_path()
    return p.read_bytes() if p else None
