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
            for row in ws.iter_rows(min_row=2):              # a customer name like =HYPERLINK(...) must stay text, never run as a formula
                for c in row:
                    if c.data_type == "f" and isinstance(c.value, str):
                        c.data_type = "s"
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


def compare_workbooks(src: str, dst: str, max_examples: int = 5, allowed=None) -> dict:
    """Compare the owner's original workbook with the edited copy, cell by cell (formulas count as they are written).
    ok is True only when every sheet is still there, no original cell changed or went missing, and no row was lost.
    `added` counts cells that exist only in the copy (the new column). With allowed=['F', 'L'] (the column letters you
    meant to change), changes in those columns are expected and ok means nothing changed anywhere ELSE. Always run this
    before sending an edited file and report the result in plain words."""
    import os
    from openpyxl import load_workbook
    for p in (src, dst):
        if not os.path.exists(p):
            raise FileNotFoundError(f"{p} does not exist. Files written by an earlier run_python are not in ./out any more: "
                                    "pass that run's output_file_ref in file_refs and open it from ./in/")
    allowed = {str(c).upper() for c in (allowed or [])}
    a, b = load_workbook(src), load_workbook(dst)
    out, ok = [], True
    for ws in a.worksheets:
        if ws.title not in b.sheetnames:
            out.append({"sheet": ws.title, "missing_sheet": True})
            ok = False
            continue
        wd = b[ws.title]
        changed, expected, lost, rows_a = [], [], 0, 0
        for row in ws.iter_rows(max_col=min(ws.max_column, 200)):
            if any(c.value is not None for c in row):
                rows_a += 1
            for c in row:
                if c.value is None:
                    continue
                nv = wd.cell(row=c.row, column=c.column).value
                if c.column_letter in allowed:
                    if nv != c.value:
                        expected.append(f"{c.coordinate}: {c.value!r} -> {nv!r}")
                    continue
                if nv is None:
                    lost += 1
                elif nv != c.value:
                    changed.append(f"{c.coordinate}: {c.value!r} -> {nv!r}")
        rows_b = sum(1 for row in wd.iter_rows(max_col=min(wd.max_column, 200)) if any(c.value is not None for c in row))
        new_cells = [c for row in wd.iter_rows(max_col=min(wd.max_column, 200)) for c in row
                     if c.value is not None and ws.cell(row=c.row, column=c.column).value is None]
        added = len(new_cells)
        stray = [c.coordinate for c in new_cells if allowed and c.column_letter not in allowed]
        good = not changed and not lost and rows_b >= rows_a and not stray
        ok = ok and good
        item = {"sheet": ws.title, "rows_original": rows_a, "rows_now": rows_b, "cells_changed": len(changed),
                "cells_lost": lost, "cells_added": added, "examples": changed[:max_examples], "same_apart_from_additions": good}
        if allowed:
            item["changed_in_allowed_columns"] = len(expected)
            item["allowed_examples"] = expected[:max_examples]
            if stray:
                item["added_outside_allowed"] = stray[:max_examples]
        out.append(item)
    return {"ok": ok, "sheets": out, "sheets_in_copy_only": [n for n in b.sheetnames if n not in a.sheetnames]}


# ---------------------------------------------------------------- names, invoice numbers, FIFO, TDS
_NOISE = {"m", "s", "ms", "mrs", "mr", "messrs", "shri", "shree", "the", "pvt", "private", "ltd", "limited", "llp", "co", "company"}


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


def group_names(names, *, cutoff: float = 0.8) -> dict:
    """The spellings of one party gathered together, for a party-wise total over a ledger typed by several people.
    Returns {'groups': [{'name', 'spellings', 'rows'}], 'maybe_same': [{'a', 'b', 'score'}]}. A group holds spellings that only differ in
    case, spaces, punctuation, M/s, Messrs, Pvt/Ltd, '&' or a plural: those ARE one party, total them together. 'maybe_same' pairs
    (Zenith Traders / Zenith Trading Co) may or may not be one party: keep them apart in the totals, list them for the owner and say so."""
    import difflib
    from collections import Counter
    cnt = Counter(str(n).strip() for n in names if str(n or "").strip())
    by: dict[str, list] = {}
    for n, c in cnt.items():
        by.setdefault(name_key(n), []).append((n, c))
    groups = []
    for k, lst in by.items():
        lst.sort(key=lambda x: (-x[1], x[0]))
        canon = re.sub(r"^(m/s|messrs)\s+", "", lst[0][0].strip(), flags=re.I).strip(" .,")
        canon = canon.title() if canon.isupper() or canon.islower() else canon
        groups.append({"name": canon, "key": k, "spellings": [n for n, _ in lst], "rows": sum(c for _, c in lst)})
    keys = sorted(by)
    maybe = []
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            sc = difflib.SequenceMatcher(None, a, b).ratio()
            if sc >= cutoff or (_skeleton(a) == _skeleton(b) and len(a) > 4):
                maybe.append({"a": by[a][0][0], "b": by[b][0][0], "score": round(sc, 2)})
    return {"groups": sorted(groups, key=lambda g: g["name"].lower()), "maybe_same": maybe}


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


def categorise(rows: list[dict], rules: dict, *, text_key: str = "narration", amount_key: str = "amount", default: str = "Others") -> dict:
    """Group rows (bank narrations, expense lines) into categories by keywords. rules = {category: [keywords]}. A keyword matches
    WHOLE WORDS only, so 'RENT' does not match 'TORRENT POWER'; if several categories match, the longest keyword wins.
    Returns {'totals': {cat: sum}, 'counts': {cat: n}, 'items': {cat: [distinct texts]}, 'unmatched': [distinct texts]}.
    ALWAYS print 'items' and read each category's list for misfiled lines before you answer (a category with far more
    entries than the others is a warning sign)."""
    import re
    comp = sorted(((len(kw.strip()), cat, re.compile(r"(?<![A-Za-z0-9])" + re.escape(kw.strip()) + r"(?![A-Za-z0-9])", re.I))
                   for cat, kws in rules.items() for kw in kws), key=lambda x: -x[0])
    totals: dict = {}
    counts: dict = {}
    items: dict = {}
    for r in rows:
        text = str(r.get(text_key) or "")
        cat = next((c for _, c, rx in comp if rx.search(text)), default)
        totals[cat] = totals.get(cat, 0) + float(r.get(amount_key) or 0)
        counts[cat] = counts.get(cat, 0) + 1
        items.setdefault(cat, set()).add(text)
    return {"totals": {k: round(v, 2) for k, v in totals.items()}, "counts": counts,
            "items": {k: sorted(v) for k, v in items.items()}, "unmatched": sorted(items.get(default, []))}


def snapshot_diff(old: list[dict], new: list[dict], *, key: str = "ref", amount: str = "amount") -> dict:
    """Compare two outstanding lists (old = the earlier one) bill by bill. Rows are dicts with an invoice number (`key`) and the
    pending `amount`. Invoice numbers are compared ignoring case and punctuation. Returns:
      cleared (in old, gone now), part_paid (in both, amount lower now: with the reduction), grown (amount higher), new_bills
      (not in old), and totals: old_total, new_total, net_change (new - old), cleared_total, part_paid_total,
      collected (= cleared + part payments), new_bills_total. Use this for 'what changed since last week' instead of
      importing both lists into the ledger."""
    o = {norm_invoice(r[key]): r for r in old}
    n = {norm_invoice(r[key]): r for r in new}
    cleared = [o[k] for k in o if k not in n]
    new_bills = [n[k] for k in n if k not in o]
    part, grown = [], []
    for k in o.keys() & n.keys():
        d = float(o[k][amount]) - float(n[k][amount])
        if d > 0.005:
            part.append({**n[k], "was": float(o[k][amount]), "reduction": round(d, 2)})
        elif d < -0.005:
            grown.append({**n[k], "was": float(o[k][amount]), "increase": round(-d, 2)})
    tot = lambda rows, f=lambda r: float(r[amount]): round(sum(f(r) for r in rows), 2)
    cleared_total, part_total = tot(cleared), tot(part, lambda r: r["reduction"])
    old_total, new_total = tot(list(o.values())), tot(list(n.values()))
    return {"cleared": cleared, "part_paid": part, "grown": grown, "new_bills": new_bills,
            "totals": {"old_total": old_total, "new_total": new_total, "net_change": round(new_total - old_total, 2),
                       "cleared_total": cleared_total, "part_paid_total": part_total, "collected": round(cleared_total + part_total, 2),
                       "new_bills_total": tot(new_bills), "cleared_count": len(cleared), "part_paid_count": len(part), "new_bills_count": len(new_bills)}}


# ---------------------------------------------------------------------------------------------- bank statement PDFs
_BS_DATE = re.compile(r"(?<![\d.])(\d{1,2})[-/ .]((?:\d{1,2})|(?:[A-Za-z]{3,9}))[-/ .,]+(20\d{2}|\d{2}(?!\d))")
_BS_MONEY = re.compile(r"(?<![\w.,])(?:Rs\.?|INR|₹)?\s*(\(?-?\d[\d,]*\.\d{2}\)?)(?:\s*(Cr|Dr|CR|DR)\b)?")
_BS_TIME = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?\b", re.I)
_BS_OPEN = re.compile(r"opening\s+balance[^\d\-]{0,80}(-?\d[\d,]*\.\d{2})", re.I)
_BS_CLOSE = re.compile(r"closing\s+balance[^\d\-]{0,80}(-?\d[\d,]*\.\d{2})", re.I)
_BS_MARK = re.compile(r"\b(CR|DR)\b\s+(?:Rs\.?\s*)?\(?-?\d[\d,]*\.\d{2}", re.I)


def _bs_num(tok: str, suffix: str | None = None) -> float | None:
    """A printed amount as a number. A leading minus, brackets or a Dr suffix on a BALANCE mean overdrawn (negative)."""
    t = tok.strip()
    neg = t.startswith("-") or t.startswith("(")
    v = parse_amount(t.strip("()-"))
    if v is None:
        return None
    v = abs(v)
    return -v if neg or (suffix or "").lower() == "dr" else v


def _bs_header(line: str) -> bool:
    low = line.lower()
    return "balance" in low and any(w in low for w in ("debit", "credit", "withdraw", "deposit", "amount"))


def bank_statement_pdf(path, password: str = "") -> dict:
    """Read a bank statement PDF with code, not by eye: every transaction row, the totals, and the check that opening balance +
    credits - debits = closing balance. Works on the usual layouts (a Debit and a Credit column, or one Amount column with Cr/Dr)
    and decides each row's direction from how the running balance moved, so it does not depend on column positions.
    Returns {"ok": False, "error": ...} for a password-protected PDF (ask the owner for the password and pass password=) or a
    scan (no text). Rows whose balance did not move (failed or rejected transactions) are listed apart and left out of the totals.
    Check result["ties"]: when it is False, say so plainly and show result["notes"] instead of quoting the totals as final."""
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    if reader.is_encrypted and not reader.decrypt(password or ""):
        return {"ok": False, "need_password": True,
                "error": "this PDF is password protected: ask the owner for the password (often the account holder's date of birth "
                         "or the last digits of the mobile number) or an unprotected copy"}
    lines: list[str] = []
    for page in reader.pages[:200]:
        try:
            text = page.extract_text(extraction_mode="layout") or ""
        except Exception:
            text = page.extract_text() or ""
        lines += re.sub(r"SYNTHETIC DATA\s*-\s*MACHINE GENERATED", " ", text).splitlines()
    if sum(len(x.strip()) for x in lines) < 200:
        return {"ok": False, "error": "no text in this PDF: it is a scan. Ask the owner to download the statement from net banking "
                                      "(PDF or Excel), or send a clear photo of each page."}
    whole = "\n".join(lines)
    m_open, m_close = _BS_OPEN.search(whole), _BS_CLOSE.search(whole)
    opening = parse_amount(m_open.group(1)) if m_open else None
    closing = parse_amount(m_close.group(1)) if m_close else None

    # 1. group the lines into transactions: a row starts with a date near the left and carries at least two amounts
    rows: list[dict] = []
    pre_failed: list[dict] = []
    head_cols: dict = {}
    for raw in lines:
        s = raw.strip()
        if not s or re.fullmatch(r"page\s*\d+(\s*of\s*\d+)?", s, re.I):
            continue
        if _bs_header(s):
            low = raw.lower()
            for key, words in (("debit", ("debit", "withdraw")), ("credit", ("credit", "deposit"))):
                for w in words:
                    if w in low:
                        head_cols[key] = low.index(w)
            continue
        lead = raw.lstrip()
        shift = len(raw) - len(lead)
        d = _BS_DATE.search(lead)
        ok_row = bool(d) and d.start() <= 40
        money = [(mm, mm.start()) for mm in _BS_MONEY.finditer(lead[d.end():])] if ok_row else []
        if ok_row and len(money) >= 2:
            first_money = d.end() + money[0][1]
            mk = _BS_MARK.search(lead[d.end():])
            rows.append({"date_text": d.group(0), "text": lead[d.end():first_money],
                         "money": [(_bs_num(mm.group(1), mm.group(2)), mm.group(2), shift + d.end() + pos) for mm, pos in money],
                         "marker": mk.group(1) if mk else None})
        elif ok_row and len(money) == 1 and re.search(r"fail|reject|declin|unsuccess|insufficient", lead, re.I):
            pre_failed.append({"row": None, "date": d.group(0), "description": re.sub(r"\s+", " ", lead[d.end():money[0][1] + d.end()]).strip(), "amount": abs(parse_amount(money[0][0].group(1)) or 0.0), "why": "failed: no balance printed"})
        elif rows and not _BS_MONEY.search(s) and not _BS_DATE.fullmatch(s):
            rows[-1]["text"] += " " + s                      # the description wrapped onto the next line
    if not rows:
        return {"ok": False, "error": "found no transaction rows in this PDF: tell the owner it is not a layout I can read yet and "
                                      "ask for the Excel/CSV download from net banking."}

    # 2. work out each row's amount and direction from the running balance
    out, failed, notes, breaks = [], list(pre_failed), [], []
    prev = opening
    for i, r in enumerate(rows):
        bal = r["money"][-1][0]
        cands = r["money"][:-1]
        amount_tok = cands[0] if len(cands) == 1 else next((t for t in cands if t[0]), cands[0])
        amount = abs(amount_tok[0] or 0.0)
        pos = amount_tok[2]
        desc = _BS_TIME.sub(" ", _BS_DATE.sub(" ", r["text"]))
        desc = re.sub(r"\s+\d{3,5}\s*$", "", re.sub(r"\s+", " ", desc).strip()).strip(" -")
        direction, chain = None, None
        if prev is not None and bal is not None:
            for cand in ((bal, -bal) if bal > 0 else (bal,)):        # a balance printed without its minus sign: overdrawn?
                delta = round(cand - prev, 2)
                if abs(abs(delta) - amount) < 0.011 and amount > 0:
                    direction, chain, bal = ("credit" if delta > 0 else "debit"), True, cand
                    break
            delta = round(bal - prev, 2)
            if chain:
                pass
            elif abs(delta) < 0.011 and amount > 0:
                failed.append({"row": i + 1, "date": r["date_text"], "description": desc, "amount": amount,
                               "why": "balance did not change"})
                continue
            else:
                chain = False
        if direction is None:
            if r["marker"]:
                direction = "credit" if r["marker"].upper() == "CR" else "debit"
            elif head_cols.get("debit") is not None and head_cols.get("credit") is not None:
                direction = "debit" if abs(pos - head_cols["debit"]) <= abs(pos - head_cols["credit"]) else "credit"
            else:
                direction = "credit" if re.search(r"\b(cr|credit|deposit|by|received)\b", desc, re.I) else "debit"
        if chain is False:
            breaks.append(i + 1)
        dt = parse_date(r["date_text"])
        out.append({"row": i + 1, "date": dt.isoformat() if dt else r["date_text"], "description": desc,
                    "debit": amount if direction == "debit" else 0.0, "credit": amount if direction == "credit" else 0.0,
                    "balance": bal, "balance_check": chain})
        prev = bal
    if opening is None and out and out[0]["balance"] is not None:
        opening = round(out[0]["balance"] - out[0]["credit"] + out[0]["debit"], 2)
        notes.append("the statement shows no opening balance; I worked it out from the first row")
    cr = round(sum(x["credit"] for x in out), 2)
    dr = round(sum(x["debit"] for x in out), 2)
    if closing is None and out:
        closing = out[-1]["balance"]
    computed = round((opening or 0.0) + cr - dr, 2) if opening is not None else None
    ties = computed is not None and closing is not None and abs(computed - closing) < 0.02
    last_bal = out[-1]["balance"] if out else None
    rows_consistent = computed is not None and last_bal is not None and abs(computed - last_bal) < 0.02
    if not ties and rows_consistent:
        notes.append(f"the rows add up (opening + credits - debits = {computed} = the last row's balance) but the closing balance printed in the "
                     f"statement header is {closing}: the statement itself is inconsistent, say so")
    elif not ties and last_bal is not None and closing is not None and abs(last_bal - closing) < 0.02 and computed is not None:
        notes.append(f"the closing balance matches the last row, but the credits and debits listed do not add up to it (they are {round(closing - computed, 2)} short): "
                     "transactions may be missing from the PDF or the running balance has gaps; say so instead of quoting the totals as final")
    elif not ties:
        notes.append(f"opening + credits - debits = {computed} but the closing balance printed is {closing}: some rows were not read "
                     "correctly or the statement itself does not add up; do not quote the totals as final")
    if breaks:
        notes.append(f"the running balance did not follow {len(breaks)} row(s): {breaks[:10]}")
    if failed:
        notes.append(f"{len(failed)} row(s) where the balance did not change (failed or rejected): left out of the totals")
    months: dict = {}
    for x in out:
        mth = months.setdefault(x["date"][:7], {"credits": 0.0, "debits": 0.0})
        mth["credits"] = round(mth["credits"] + x["credit"], 2)
        mth["debits"] = round(mth["debits"] + x["debit"], 2)
    dates = [x["date"] for x in out if re.match(r"\d{4}-", x["date"])]
    return {"ok": True, "rows": out, "row_count": len(out),
            "credits": {"total": cr, "count": sum(1 for x in out if x["credit"])},
            "debits": {"total": dr, "count": sum(1 for x in out if x["debit"])},
            "opening": opening, "closing": closing, "computed_closing": computed, "ties": ties, "rows_consistent": rows_consistent,
            "chain_breaks": breaks, "failed": failed, "notes": notes, "by_month": months,
            "period": {"from": min(dates) if dates else None, "to": max(dates) if dates else None},
            "top_credits": sorted((x for x in out if x["credit"]), key=lambda x: -x["credit"])[:5],
            "top_debits": sorted((x for x in out if x["debit"]), key=lambda x: -x["debit"])[:5]}


def _copy_look(c, n) -> None:
    """Copy a cell's number format, font, fill, border and alignment into a cell of ANOTHER workbook (style ids are per workbook)."""
    import copy
    if c.has_style:
        n.number_format = c.number_format
        n.font, n.fill, n.border, n.alignment = copy.copy(c.font), copy.copy(c.fill), copy.copy(c.border), copy.copy(c.alignment)


def split_sheets(src: str, dst: str, by: str, *, how: str = "value", sheet=None, header_row: int | None = None, keep_all_sheet: bool = False) -> dict:
    """Split the owner's sheet into one sheet per value of a column (how='value') or per calendar month of a date column (how='month'),
    in ONE workbook, keeping every cell exactly as it is: dates stay dates, numbers stay numbers, formulas, number formats, fonts, fills
    and column widths are copied. Each new sheet gets the title rows and the header row. Total rows (a row whose text says Total) are
    left out of the pieces. Returns {sheet title: rows}; the row counts add up to the source's data rows. Never go through pandas for
    this: it turns dates into text and drops the formatting."""
    import copy
    from openpyxl import Workbook, load_workbook
    wb = load_workbook(src)
    ws = wb[sheet] if sheet else wb.worksheets[0]
    hdr = header_row
    if hdr is None:
        want = re.sub(r"\s+", "", str(by)).lower()
        for r in range(1, min(ws.max_row, 40) + 1):
            if any(c.value is not None and re.sub(r"\s+", "", str(c.value)).lower() == want for c in ws[r]):
                hdr = r
                break
    if hdr is None:
        raise ValueError(f"no header row with a column called {by!r} in the first 40 rows")
    col = next(c.column for c in ws[hdr] if c.value is not None and re.sub(r"\s+", "", str(c.value)).lower() == re.sub(r"\s+", "", str(by)).lower())
    groups: dict[str, list] = {}
    for r in range(hdr + 1, ws.max_row + 1):
        row = ws[r]
        if all(c.value in (None, "") for c in row) or any(isinstance(c.value, str) and re.match(r"\s*(grand\s+)?total\b", c.value, re.I) for c in row):
            continue
        v = row[col - 1].value
        if how == "month":
            d = parse_date(v)
            key = d.strftime("%Y-%m") if d else "(no date)"
            label = d.strftime("%b %Y") if d else "(no date)"
        else:
            key, label = str(v).strip() if v not in (None, "") else "(blank)", str(v).strip() if v not in (None, "") else "(blank)"
        groups.setdefault(key, [label, []])[1].append(r)
    out = Workbook()
    out.remove(out.active)
    counts = {}
    for key in sorted(groups):
        label, rows = groups[key]
        title = re.sub(r"[\[\]\\*?/:]", "-", label)[:31] or "sheet"
        new = out.create_sheet(title)
        for r in range(1, hdr + 1):                                         # the title rows and the header, with their look
            for c in ws[r]:
                n = new.cell(row=r, column=c.column, value=c.value)
                _copy_look(c, n)
        for i, r in enumerate(rows, start=hdr + 1):
            for c in ws[r]:
                n = new.cell(row=i, column=c.column, value=c.value)
                _copy_look(c, n)
        for letter, dim in ws.column_dimensions.items():
            new.column_dimensions[letter].width = dim.width
        new.freeze_panes = new.cell(row=hdr + 1, column=1)
        counts[title] = len(rows)
    if keep_all_sheet:
        every = out.create_sheet("All")
        for row in ws.iter_rows():
            for c in row:
                _copy_look(c, every.cell(row=c.row, column=c.column, value=c.value))
        for letter, dim in ws.column_dimensions.items():
            every.column_dimensions[letter].width = dim.width
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    out.save(dst)
    return counts
