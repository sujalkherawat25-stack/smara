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
              "ledger name", "debtor", "account name", "name", "party s name", "partys name", "particular",
              "customer s name", "पार्टी", "पार्टी का नाम", "ग्राहक", "नाम"),
    "phone": ("mobile", "mobile no", "mobile number", "phone", "phone no", "contact",
              "contact no", "whatsapp", "whatsapp no", "mobile phone", "ledger mobile", "phone number", "mob", "mob no",
              "मोबाइल", "फोन", "फ़ोन"),
    "invoice": ("invoice", "invoice no", "invoice number", "bill no", "bill number",
                "voucher no", "vch no", "ref no", "reference", "ref", "ref no", "bill ref", "बिल नं", "बिल नंबर"),
    "amount": ("pending", "pending amount", "outstanding", "outstanding amount", "balance",
               "due amount", "closing balance", "amount due", "amount", "debit", "pending amt", "balance amount",
               "राशि", "बकाया"),
    "due": ("due date", "due on", "due", "payment due", "देय तिथि", "due dt"),
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
    return re.sub(r"\s+", " ", re.sub(r"[^\wऀ-ॿ ]|_", " ", str(h or "").lower())).strip()


def decode_text(data: bytes) -> str:
    """Tally and Excel save CSVs as UTF-8 (with or without BOM), UTF-16 or Windows-1252; try them in that order."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def read_tables(filename: str, data: bytes) -> list[list[list]]:
    """Every sheet of a workbook, or the single table of a text file, as lists of rows."""
    name = filename.lower()
    if name.endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        return [[list(r) for r in ws.iter_rows(values_only=True)] for ws in wb.worksheets]
    if name.endswith(".xls"):
        try:
            import xlrd
        except ImportError:
            raise ValueError("old .xls files are not supported here; save the sheet as .xlsx or .csv and send it again") from None
        book = xlrd.open_workbook(file_contents=data)
        return [[[c.value if c.ctype != xlrd.XL_CELL_DATE else xlrd.xldate.xldate_as_datetime(c.value, book.datemode)
                  for c in sh.row(i)] for i in range(sh.nrows)] for sh in book.sheets()]
    text = decode_text(data)
    sample = "\n".join(text.splitlines()[:20])
    delimiter = max(",;\t|", key=sample.count) if sample.strip() else ","
    return [[r for r in csv.reader(io.StringIO(text), delimiter=delimiter)]]


def read_table(filename: str, data: bytes) -> list[list]:
    return read_tables(filename, data)[0]


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
    s = re.sub(r"(?i)[₹,\s]|rs\.?|inr|\b(dr|cr)\b|/-|-$", "", str(v))
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


def _find_header(table: list[list]):
    for i, header in enumerate(table[:15]):
        cols = map_headers(header)
        if "party" in cols and "amount" in cols:
            return i, cols
    return None


def map_sheet(filename: str, data: bytes, known_phones: dict[str, str] | None = None) -> tuple[list[SheetRow], list[str]]:
    """Returns (rows, skipped_reasons). Header row = first of top 15 with party and amount. Every sheet is tried;
    known_phones (customer name lower-cased -> number) fills a missing mobile column from customers already on file."""
    try:
        tables = read_tables(filename, data)
    except ValueError as e:
        return [], [str(e)]
    except Exception:
        return [], ["the file could not be opened; it may be damaged or password protected"]
    found = next(((t, *h) for t in tables if (h := _find_header(t))), None)
    if found is None:
        seen = next((r for t in tables for r in t[:15] if sum(1 for c in r if c not in (None, "")) >= 2), None)
        hint = f" The columns I can see: {', '.join(str(c) for c in seen if c not in (None, ''))[:200]}." if seen else ""
        return [], ["could not find a header row with a party/customer column and an amount/pending column." + hint]
    table, i, cols = found
    known = {k.strip().lower(): v for k, v in (known_phones or {}).items()}

    rows: list[SheetRow] = []
    skipped: list[str] = []
    for n, r in enumerate(table[i + 1:], start=i + 2):
        get = lambda f: r[cols[f]] if f in cols and cols[f] < len(r) else None
        party = str(get("party") or "").strip()
        amount = parse_amount_paise(get("amount"))
        if not party or amount is None:
            is_total = any(str(c or "").strip().lower() in ("total", "grand total", "totals") for c in r)
            if any(c not in (None, "") for c in r) and not is_total:
                skipped.append(f"row {n}: no party/amount")
            continue
        phone = parse_phone(get("phone")) or parse_phone(known.get(party.lower()))
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
