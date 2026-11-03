"""Universal .xlsx/.csv receivable mapper for Tally / Vyapar / hand-made sheets.

Pure parsing: bytes -> normalized rows. No DB, no network.
"""
import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

DEFAULT_CREDIT_DAYS = 30

# canonical field -> header synonyms (lowercased, punctuation stripped)
SYNONYMS: dict[str, tuple[str, ...]] = {
    "party": ("party", "party name", "customer", "customer name", "particulars", "ledger",
              "ledger name", "debtor", "account name", "name"),
    "phone": ("mobile", "mobile no", "mobile number", "phone", "phone no", "contact",
              "contact no", "whatsapp", "whatsapp no"),
    "invoice": ("invoice", "invoice no", "invoice number", "bill no", "bill number",
                "voucher no", "vch no", "ref no", "reference", "ref"),
    "amount": ("pending", "pending amount", "outstanding", "outstanding amount", "balance",
               "due amount", "closing balance", "amount due", "amount", "debit"),
    "due": ("due date", "due on", "due", "payment due"),
    "bill_date": ("date", "invoice date", "bill date", "voucher date"),
    "gstin": ("gstin", "gst no", "gst number", "gstin uin"),
}


@dataclass(frozen=True)
class SheetRow:
    party: str
    phone: str
    invoice_ref: str
    amount_paise: int
    due_date: date
    gstin: str | None = None


def _norm(h) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", str(h or "").lower())).strip()


def read_table(filename: str, data: bytes) -> list[list]:
    if filename.lower().endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        return [list(r) for r in wb.active.iter_rows(values_only=True)]
    text = data.decode("utf-8-sig", errors="replace")
    return [r for r in csv.reader(io.StringIO(text))]


def map_headers(header: list) -> dict[str, int]:
    """First-match wins, scanning synonyms in priority order (exact only)."""
    cols = [_norm(h) for h in header]
    found: dict[str, int] = {}
    for field, names in SYNONYMS.items():
        for name in names:
            if name in cols:
                found[field] = cols.index(name)
                break
    return found


def parse_amount_paise(v) -> int | None:
    if v is None or v == "":
        return None
    s = re.sub(r"(?i)[₹,\s]|rs\.?|inr|\b(dr|cr)\b", "", str(v))
    try:
        paise = int((Decimal(s) * 100).to_integral_value())
    except InvalidOperation:
        return None
    return paise if paise > 0 else None


_FORMATS = ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y", "%d-%b-%y", "%d/%m/%y", "%d.%m.%Y", "%d %b %Y")


def parse_date(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v or "").strip()
    for fmt in _FORMATS:  # Indian sheets are day-first
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_phone(v) -> str:
    digits = re.sub(r"\D", "", str(v or "").removesuffix(".0"))
    if len(digits) == 10:
        digits = "91" + digits
    return digits if 11 <= len(digits) <= 15 else ""


def map_sheet(filename: str, data: bytes) -> tuple[list[SheetRow], list[str]]:
    """Returns (rows, skipped_reasons). Header row = first of top 15 with >=2 fields."""
    table = read_table(filename, data)
    for i, header in enumerate(table[:15]):
        cols = map_headers(header)
        if "party" in cols and "amount" in cols:
            break
    else:
        return [], ["could not find a header row with party and amount columns"]

    rows: list[SheetRow] = []
    skipped: list[str] = []
    for n, r in enumerate(table[i + 1:], start=i + 2):
        get = lambda f: r[cols[f]] if f in cols and cols[f] < len(r) else None
        party = str(get("party") or "").strip()
        amount = parse_amount_paise(get("amount"))
        if not party or amount is None:
            if any(c not in (None, "") for c in r):
                skipped.append(f"row {n}: no party/amount")
            continue
        phone = parse_phone(get("phone"))
        due = parse_date(get("due"))
        if due is None and (billed := parse_date(get("bill_date"))):
            due = billed + timedelta(days=DEFAULT_CREDIT_DAYS)
        if not phone or due is None:
            skipped.append(f"row {n} ({party}): missing {'phone' if not phone else 'due date'}")
            continue
        ref = str(get("invoice") or "").strip() or f"{party[:12]}-{due.isoformat()}-{amount}"
        gstin = str(get("gstin") or "").strip().upper() or None
        rows.append(SheetRow(party, phone, ref, amount, due, gstin))
    return rows, skipped
