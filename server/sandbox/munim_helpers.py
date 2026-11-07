"""Helpers available inside the sandbox as `import munim_helpers as mh`.

Built for messy Indian accounting exports: Tally Prime XML, Excel/CSV reports
with title rows above the header, Indian digit grouping, Dr/Cr suffixes and
day-first dates.
"""
import csv
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path

_HEADER_WORDS = ("party", "particulars", "ledger", "customer", "name", "invoice", "bill", "voucher", "ref",
                 "amount", "balance", "pending", "outstanding", "debit", "credit", "date", "due", "mobile",
                 "phone", "gstin", "gst", "tax", "total", "qty", "rate")


def parse_amount(v) -> float | None:
    """'1,25,000.50 Dr' -> 125000.5 ; '9,999 Cr' -> -9999 ; '(500)' -> -500 ; '-' / '' -> None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if s in ("", "-", "--", "nil", "Nil", "NIL"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    cr = bool(re.search(r"\bcr\.?$", s, re.I))
    s = re.sub(r"(?i)rs\.?|inr|₹|\b(dr|cr)\.?$|[,\s()]", "", s)
    try:
        n = float(s)
    except ValueError:
        return None
    return -abs(n) if (neg or cr) else n


def parse_date(v) -> date | None:
    """Day-first Indian dates, Tally's YYYYMMDD, Excel serial numbers, datetimes."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)) and 20000 < float(v) < 80000:      # Excel serial date
        return date(1899, 12, 30) + timedelta(days=int(v))
    s = str(v).strip()
    for fmt in ("%Y%m%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d-%b-%Y", "%d-%b-%y", "%d/%m/%y", "%d %b %Y",
                "%Y-%m-%d", "%d-%m-%y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def phone(v) -> str:
    s = str(v or "").strip()
    d = re.sub(r"\D", "", s[:-2] if s.endswith(".0") else s)
    if len(d) == 10:
        d = "91" + d
    return d if 11 <= len(d) <= 15 else ""


def read_table(path, sheet=0):
    """Read csv/xlsx/xls into a DataFrame, finding the real header row below any title rows."""
    import pandas as pd
    p = str(path)
    raw = pd.read_csv(p, header=None, dtype=str, keep_default_na=False) if p.lower().endswith(".csv") \
        else pd.read_excel(p, header=None, sheet_name=sheet, dtype=object)
    best, best_score = 0, -1
    for i in range(min(25, len(raw))):
        cells = [str(c).strip().lower() for c in raw.iloc[i].tolist() if str(c).strip() not in ("", "nan", "None")]
        score = sum(any(w in c for w in _HEADER_WORDS) for c in cells)
        if score > best_score:
            best, best_score = i, score
    df = raw.iloc[best + 1:].reset_index(drop=True)
    df.columns = [str(c).strip() or f"col{j}" for j, c in enumerate(raw.iloc[best].tolist())]
    return df.dropna(how="all")


def _clean_xml(data: bytes) -> bytes:
    data = re.sub(rb"&#(?:[0-8]|1[0-9]|2[0-9]|3[01]);", b"", data)          # Tally's illegal char refs
    return re.sub(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]", b"", data)


def _text(el, tag) -> str:
    x = el.find(tag)
    return (x.text or "").strip() if x is not None else ""


def tally_vouchers(path) -> list[dict]:
    """Vouchers from a Tally XML export: date, type, number, party, amount (party's side), credit_days."""
    root = ET.fromstring(_clean_xml(Path(path).read_bytes()))
    out = []
    for v in root.iter("VOUCHER"):
        party = _text(v, "PARTYLEDGERNAME")
        amount, credit_days = None, None
        for entry in list(v.iter("ALLLEDGERENTRIES.LIST")) + list(v.iter("LEDGERENTRIES.LIST")):
            if _text(entry, "LEDGERNAME") == party:
                amount = abs(parse_amount(_text(entry, "AMOUNT")) or 0)
                for bill in entry.iter("BILLALLOCATIONS.LIST"):
                    m = re.search(r"\d+", _text(bill, "BILLCREDITPERIOD"))
                    credit_days = int(m.group()) if m else credit_days
        out.append({"date": parse_date(_text(v, "DATE")), "type": _text(v, "VOUCHERTYPENAME") or v.get("VCHTYPE", ""),
                    "number": _text(v, "VOUCHERNUMBER"), "party": party, "amount": amount, "credit_days": credit_days})
    return out


def tally_ledgers(path) -> list[dict]:
    """Ledger masters from a Tally XML export: name, parent group, mobile, gstin, closing balance."""
    root = ET.fromstring(_clean_xml(Path(path).read_bytes()))
    return [{"name": l.get("NAME") or _text(l, "NAME"), "parent": _text(l, "PARENT"),
             "mobile": phone(_text(l, "LEDGERMOBILE") or _text(l, "LEDGERPHONE")),
             "gstin": _text(l, "PARTYGSTIN") or _text(l, "GSTIN"),
             "closing_balance": parse_amount(_text(l, "CLOSINGBALANCE"))} for l in root.iter("LEDGER")]


def to_rows_csv(records: list[dict], path="out/rows.csv") -> str:
    """Write receivables in the format inspect_and_import_file imports.
    Each record: party, phone, invoice, due_date (date or str), amount (rupees)."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Party Name", "Mobile Number", "Invoice No", "Due Date", "Balance"])
        for r in records:
            due = r["due_date"]
            due = due.strftime("%d/%m/%Y") if isinstance(due, (date, datetime)) else due
            w.writerow([r["party"], r.get("phone", ""), r["invoice"], due, f"{float(r['amount']):.2f}"])
    return path


def tally_receivables(path, default_credit_days: int = 30) -> list[dict]:
    """Sales invoices from a Tally XML export, ready for to_rows_csv: party, phone (from the
    ledger master), invoice, due_date (voucher date + credit period), amount. Receipts are not
    netted here; use tally_vouchers() for those."""
    phones = {l["name"]: l["mobile"] for l in tally_ledgers(path)}
    out = []
    for v in tally_vouchers(path):
        if v["type"].lower().startswith("sales") and v["amount"]:
            days = v["credit_days"] if v["credit_days"] is not None else default_credit_days
            out.append({"party": v["party"], "phone": phones.get(v["party"], ""), "invoice": v["number"],
                        "due_date": v["date"] + timedelta(days=days) if v["date"] else None, "amount": v["amount"]})
    return out


def statement_pdf(path, *, shop: str, party: str, rows: list[dict], as_of=None, note: str = "") -> str:
    """Account statement PDF for one customer. rows: invoice, date, due_date, amount (rupees), paid (rupees, optional).
    Writes `path` (use out/statement.pdf) and returns it. Totals are computed here, so print or RESULT them."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    fmt = lambda n: f"Rs {n:,.2f}"
    d = lambda v: v.strftime("%d %b %Y") if isinstance(v, (date, datetime)) else (str(v) if v else "")
    table = [["Invoice", "Date", "Due", "Amount", "Paid", "Balance"]]
    total = paid_total = 0.0
    for r in rows:
        amt, paid = float(r["amount"]), float(r.get("paid") or 0)
        total, paid_total = total + amt, paid_total + paid
        table.append([r["invoice"], d(r.get("date")), d(r.get("due_date")), fmt(amt), fmt(paid), fmt(amt - paid)])
    table.append(["Total", "", "", fmt(total), fmt(paid_total), fmt(total - paid_total)])
    t = Table(table, repeatRows=1, hAlign="LEFT")
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3a5f")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                           ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"), ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
                           ("GRID", (0, 0), (-1, -1), 0.25, colors.grey), ("FONTSIZE", (0, 0), (-1, -1), 9)]))
    story = [Paragraph(shop, styles["Title"]), Paragraph(f"Statement of account: {party}", styles["Heading2"]),
             Paragraph(f"As of {d(as_of or date.today())}", styles["Normal"]), Spacer(1, 12), t]
    if note:
        story += [Spacer(1, 12), Paragraph(note, styles["Normal"])]
    SimpleDocTemplate(str(path), pagesize=A4, title=f"Statement - {party}").build(story)
    return str(path)


def write_xlsx(sheets: dict, path="out/report.xlsx") -> str:
    """Write {sheet_name: DataFrame} to an Excel file with a frozen header row and auto column widths."""
    import pandas as pd
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, df in sheets.items():
            df.to_excel(xw, sheet_name=str(name)[:31], index=False)
            ws = xw.sheets[str(name)[:31]]
            ws.freeze_panes = "A2"
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = min(48, max(10, max(len(str(c.value or "")) for c in col) + 2))
    return str(path)
