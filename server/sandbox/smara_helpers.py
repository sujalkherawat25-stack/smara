"""Helpers available inside the sandbox as `import smara_helpers as mh`.

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


# ---- changing a workbook the owner sent, without rebuilding it -------------------------------------------------------
def _find_header(ws, text: str, max_rows: int = 30, max_cols: int = 80):
    """(row, column) of the first cell in the top of the sheet whose text equals `text` (ignoring case and spaces)."""
    want = re.sub(r"\s+", "", str(text)).lower()
    for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row, max_rows), max_col=min(ws.max_column, max_cols)):
        for cell in row:
            if cell.value is not None and re.sub(r"\s+", "", str(cell.value)).lower() == want:
                return cell.row, cell.column
    return None


def add_column(src: str, dst: str, *, source_header: str, new_header: str, formula: str, sheets=None) -> list[dict]:
    """Add ONE new column to a copy of the owner's own workbook and change nothing else.

    The file is opened with openpyxl (formulas, formatting, widths and every row stay as they are). On each sheet the
    header row is found by looking for `source_header` (it can be on a different row on every sheet). The new column goes
    right after the last used header cell, with the header's look copied, and `formula` is written for each data row that
    has a value under `source_header`; use {c} for that column's letter and {r} for the row, e.g. '={c}{r}*0.4'.
    Returns one dict per sheet: sheet, header_row, column, rows_filled, or skipped with the reason. A sheet where the
    header is not found is left untouched and reported, never dropped. Tell the owner about every skipped sheet."""
    from copy import copy

    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter
    wb = load_workbook(src)
    report = []
    for ws in wb.worksheets:
        if sheets and ws.title not in sheets:
            report.append({"sheet": ws.title, "skipped": "not in the list of sheets to change"})
            continue
        found = _find_header(ws, source_header)
        if not found:
            report.append({"sheet": ws.title, "skipped": f"no '{source_header}' header in the first rows", "rows": ws.max_row})
            continue
        hrow, scol = found
        last = max((c.column for c in ws[hrow] if c.value is not None), default=scol)
        ncol = last + 1
        head_src = ws.cell(row=hrow, column=last)
        head = ws.cell(row=hrow, column=ncol, value=new_header)
        if head_src.has_style:
            head._style = copy(head_src._style)
        ws.column_dimensions[get_column_letter(ncol)].width = max(12, len(new_header) + 4)
        letter, filled = get_column_letter(scol), 0
        for r in range(hrow + 1, ws.max_row + 1):
            v = ws.cell(row=r, column=scol).value
            if v is None or (isinstance(v, str) and not v.strip()):
                continue
            cell = ws.cell(row=r, column=ncol, value=formula.format(c=letter, r=r))
            src_cell = ws.cell(row=r, column=scol)
            if src_cell.has_style:
                cell._style = copy(src_cell._style)
            filled += 1
        report.append({"sheet": ws.title, "header_row": hrow, "column": get_column_letter(ncol), "rows_filled": filled})
    if not any("rows_filled" in x for x in report):
        raise ValueError(f"'{source_header}' was not found on any sheet: {report}")
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    wb.save(dst)
    return report


def compare_workbooks(src: str, dst: str, max_examples: int = 5) -> dict:
    """Compare the owner's original workbook with the edited copy, cell by cell (formulas count as they are written).
    ok is True only when every sheet is still there, no original cell changed or went missing, and no row was lost.
    `added` counts cells that exist only in the copy (the new column). Always run this before sending an edited file
    and report the result in plain words."""
    from openpyxl import load_workbook
    a, b = load_workbook(src), load_workbook(dst)
    out, ok = [], True
    for ws in a.worksheets:
        if ws.title not in b.sheetnames:
            out.append({"sheet": ws.title, "missing_sheet": True})
            ok = False
            continue
        wd = b[ws.title]
        changed, lost, rows_a = [], 0, 0
        for row in ws.iter_rows(max_col=min(ws.max_column, 200)):
            if any(c.value is not None for c in row):
                rows_a += 1
            for c in row:
                if c.value is None:
                    continue
                nv = wd.cell(row=c.row, column=c.column).value
                if nv is None:
                    lost += 1
                elif nv != c.value:
                    changed.append(f"{c.coordinate}: {c.value!r} -> {nv!r}")
        rows_b = sum(1 for row in wd.iter_rows(max_col=min(wd.max_column, 200)) if any(c.value is not None for c in row))
        added = sum(1 for row in wd.iter_rows(max_col=min(wd.max_column, 200)) for c in row
                    if c.value is not None and ws.cell(row=c.row, column=c.column).value is None)
        good = not changed and not lost and rows_b >= rows_a
        ok = ok and good
        out.append({"sheet": ws.title, "rows_original": rows_a, "rows_now": rows_b, "cells_changed": len(changed),
                    "cells_lost": lost, "cells_added": added, "examples": changed[:max_examples], "same_apart_from_additions": good})
    return {"ok": ok, "sheets": out, "sheets_in_copy_only": [n for n in b.sheetnames if n not in a.sheetnames]}


# ---------------------------------------------------------------- names, invoice numbers, FIFO, TDS
_NOISE = {"m", "s", "ms", "mrs", "mr", "shri", "shree", "the", "pvt", "private", "ltd", "limited", "llp", "co", "company"}


def name_key(s) -> str:
    """Spelling-proof key: lower case, '&'=='and', no punctuation or double spaces, plurals folded
    ('Stores'=='Store', 'Traders'=='Trader'). 'M/s Verma  Kirana & General Stores' == 'verma kirana and general store'."""
    t = str(s or "").lower().replace("&", " and ")
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    toks = []
    for w in t.split():
        if w in _NOISE:
            continue
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        toks.append(w)
    return " ".join(toks)


def _skeleton(k: str) -> str:
    """Agarwal / Agrawal, Maheshwari / Maheswari, Shah / Saha: drop vowels and doubled letters."""
    return re.sub(r"(.)\1+", r"\1", re.sub(r"[aeiouhy ]", "", k))


def match_names(names, master, *, cutoff: float = 0.88, margin: float = 0.04) -> dict:
    """Match every spelling in `names` to one name in `master`.
    Returns {'map': {spelling: master_name}, 'review': [{'name', 'best', 'second', 'score'}], 'exact': n, 'fuzzy': n}.
    Exact key first; then the consonant skeleton; then difflib similarity. A name whose two best candidates are within
    `margin`, or below `cutoff`, goes to 'review' (not guessed): ask the owner or use the invoices it is allocated to.
    Look at `review` and at every fuzzy pair before you rely on the result."""
    import difflib
    master = list(dict.fromkeys(str(m) for m in master))
    by_key, by_skel = {}, {}
    for m in master:
        by_key.setdefault(name_key(m), []).append(m)
        by_skel.setdefault(_skeleton(name_key(m)), []).append(m)
    out, review, exact, fuzzy = {}, [], 0, 0
    for n in dict.fromkeys(str(x) for x in names):
        k = name_key(n)
        if len(by_key.get(k, [])) == 1:
            out[n] = by_key[k][0]; exact += 1; continue
        s = by_skel.get(_skeleton(k), [])
        if len(s) == 1:
            out[n] = s[0]; fuzzy += 1; continue
        scored = sorted(((difflib.SequenceMatcher(None, k, name_key(m)).ratio(), m) for m in master), reverse=True)
        best = scored[0] if scored else (0, None)
        second = scored[1] if len(scored) > 1 else (0, None)
        if best[0] >= cutoff and best[0] - second[0] >= margin:
            out[n] = best[1]; fuzzy += 1
        else:
            review.append({"name": n, "best": best[1], "second": second[1], "score": round(best[0], 3)})
    return {"map": out, "review": review, "exact": exact, "fuzzy": fuzzy}


def norm_invoice(s) -> str:
    """'hcp/2604/333', 'HCP-2604-333 ', 'HCP 2604 333' -> 'HCP2604333'. Leading zeros inside numbers are dropped."""
    t = re.sub(r"(?<![0-9])0+(?=[0-9])", "", str(s or "").upper())
    return re.sub(r"[^A-Z0-9]", "", t)


def fifo_apply(invoices: list[dict], payments: list[dict]) -> dict:
    """First-in-first-out for lump-sum ('On Account') receipts.
    invoices: [{'party','ref','date','balance'}] balance already net of credit notes and receipts allocated to that invoice.
    payments: [{'party','amount'}] unallocated money. Per party the oldest invoice (by date, then ref) with a balance is
    cleared first. Money left over after every invoice is cleared is an ADVANCE; it is reported, never netted off
    against anything. Returns {'open': [{'party','ref','date','balance'}...], 'advances': {party: rupees}}."""
    by_party: dict = {}
    for inv in invoices:
        by_party.setdefault(inv["party"], []).append(dict(inv))
    pay: dict = {}
    for p in payments:
        pay[p["party"]] = pay.get(p["party"], 0.0) + float(p["amount"])
    open_rows, advances = [], {}
    for party in set(by_party) | set(pay):
        rows = sorted(by_party.get(party, []), key=lambda r: (r["date"], str(r["ref"])))
        left = pay.get(party, 0.0)
        for r in rows:
            bal = float(r["balance"])
            if bal <= 0:
                if bal < 0:                       # an invoice paid more than its value
                    advances[party] = advances.get(party, 0.0) - bal
                continue
            take = min(left, bal)
            left -= take
            r["balance"] = round(bal - take, 2)
            if r["balance"] > 0.004:
                open_rows.append(r)
        if left > 0.004:
            advances[party] = advances.get(party, 0.0) + left
    return {"open": open_rows, "advances": {k: round(v, 2) for k, v in advances.items()}}


def tds_194q(bills: list[dict], *, limit: float = 5_000_000.0, rate: float = 0.001, no_pan_rate: float = 0.05) -> dict:
    """Section 194Q, per supplier. bills: [{'supplier','date','taxable','pan'(optional),'ref'(optional)}].
    Count each supplier invoice once (pass de-duplicated bills). Bills are taken in date order; tax is due only on the
    part of the running total ABOVE `limit` (the bill that crosses the limit is taxed on its excess only).
    Rate is `no_pan_rate` when the supplier has no PAN. Returns {supplier: {'tds','above_limit','total','rate'}}."""
    by: dict = {}
    for b in bills:
        by.setdefault(b["supplier"], []).append(b)
    out = {}
    for sup, rows in by.items():
        rows = sorted(rows, key=lambda r: (r["date"], str(r.get("ref", ""))))
        total = sum(float(r["taxable"]) for r in rows)
        above = max(0.0, total - limit)
        has_pan = any(str(r.get("pan") or "").strip() not in ("", "nan", "None") for r in rows)
        rt = rate if has_pan else no_pan_rate
        out[sup] = {"tds": round(above * rt, 2), "above_limit": round(above, 2), "total": round(total, 2), "rate": rt}
    return out
