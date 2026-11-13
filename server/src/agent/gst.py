"""GST purchase reconciliation in one call: the owner's purchase register against the GSTR-2B statement.

The agent used to do this with free-form code: dozens of steps to find the header row of each file, match the rows, and build a
report, taking 1 to 6 minutes and sometimes not finishing. `gst_reconcile` does it deterministically and returns the answer.

  match on   supplier GSTIN (or, when the books have none, the supplier's name) + invoice number with spaces, punctuation and
             leading zeros removed. Never on name alone for rows that have a GSTIN.
  matched    taxable value and total tax each within Rs 1.
  classes    matched, amount mismatch, in books but not in 2B (the ITC at risk), in 2B but not in books.
  never      auto-matches an invoice whose number differs: those are listed as possible matches for a person to confirm.

It reads files; it changes nothing and sends nothing. The PDF report is made with the normal document engine.
"""
import re
from collections import defaultdict

from src.ingest.sheet_mapper import _norm, parse_amount_paise, parse_date, read_named_tables

TOLERANCE = 100                 # paise: Rs 1 of rounding
LIST_CAP = 15                   # rows per class in the answer; the PDF carries up to 40
PDF_CAP = 40
HEADER_SCAN = 40                # rows from the top of a sheet where the header may be

FIELDS = {
    "gstin": ("gstin", "gstin of supplier", "supplier gstin", "gstin uin", "gstin of the supplier", "party gstin", "gst no", "gstin no"),
    "name": ("name", "supplier", "supplier name", "trade name", "trade legal name", "legal name", "party", "party name", "particulars",
             "vendor", "vendor name", "name of supplier"),
    "invoice": ("invoice", "invoice no", "invoice number", "invoice num", "bill no", "bill number", "supplier invoice no", "inv no",
                "ref no", "document number", "voucher ref no", "supplier inv no"),
    "date": ("invoice date", "bill date", "supplier invoice date", "document date", "date"),
    "taxable": ("taxable", "taxable value", "taxable amount", "taxable amt", "basic amount", "assessable value"),
    "igst": ("igst", "integrated tax", "integrated tax paid", "igst amount"),
    "cgst": ("cgst", "central tax", "central tax paid", "cgst amount"),
    "sgst": ("sgst", "state ut tax", "state tax", "sgst utgst", "sgst amount", "state tax paid"),
    "cess": ("cess", "cess amount"),
    "total": ("total", "invoice value", "gross total", "invoice amount", "total amount"),
}


def _map(header: list) -> dict[str, int]:
    cells = [_norm(h) for h in header]
    found: dict[str, int] = {}
    for exact in (True, False):                                      # exact names first, then names that start with one
        for i, cell in enumerate(cells):
            if not cell or i in found.values():
                continue
            for field, names in FIELDS.items():
                if field in found:
                    continue
                if (cell in names) if exact else any(len(n) >= 6 and cell.startswith(n + " ") for n in names):
                    found[field] = i
                    break
    return found


def _find(tables: list[tuple[str, list[list]]]):
    """(sheet name, header row index, columns) of the best table: has an invoice column and an amount column. A sheet called B2B wins."""
    best = None
    for name, rows in tables:
        for i, row in enumerate(rows[:HEADER_SCAN]):
            cols = _map(row)
            if "invoice" in cols and ("taxable" in cols or "total" in cols or {"igst", "cgst"} & set(cols)):
                if best is None or (_norm(name) == "b2b" and _norm(best[0]) != "b2b"):
                    best = (name, i, cols, rows)
                break
    return best


def _cell(row, cols, field):
    i = cols.get(field)
    return row[i] if i is not None and i < len(row) else None


def _inv_key(s) -> str:
    s = re.sub(r"[^A-Z0-9]", "", str(s or "").upper())
    return re.sub(r"(?<=[A-Z])0+(?=\d)|^0+(?=\d)", "", s)


def _gstin(s) -> str:
    g = re.sub(r"\s", "", str(s or "")).upper()
    return g if re.fullmatch(r"[0-9A-Z]{15}", g) else ""


def _name_key(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _paise(v) -> int:
    return parse_amount_paise(v) or 0


def parse_file(filename: str, data: bytes) -> dict:
    """{rows, sheet, filename} or {error}. Each row: supplier, gstin, invoice (as written), key, date, taxable, tax (all paise)."""
    try:
        tables = read_named_tables(filename, data)
    except Exception as e:
        return {"error": f"could not open {filename}: {str(e)[:120]}"}
    found = _find(tables)
    if not found:
        seen = [str(c) for c in (tables[0][1][0] if tables and tables[0][1] else [])][:10]
        return {"error": f"{filename}: could not find a header row with an invoice number column and an amount or tax column "
                         f"(first row: {seen})"}
    sheet, hi, cols, table = found
    rows, skipped = [], 0
    for r in table[hi + 1:]:
        inv = _cell(r, cols, "invoice")
        if inv in (None, "") or "total" in str(_cell(r, cols, "name") or inv).lower():
            skipped += 1
            continue
        tax = sum(_paise(_cell(r, cols, f)) for f in ("igst", "cgst", "sgst", "cess"))
        taxable = _paise(_cell(r, cols, "taxable"))
        if not taxable and _paise(_cell(r, cols, "total")):
            taxable = max(_paise(_cell(r, cols, "total")) - tax, 0)
        d = parse_date(_cell(r, cols, "date"))
        rows.append({"supplier": str(_cell(r, cols, "name") or "").strip(), "gstin": _gstin(_cell(r, cols, "gstin")),
                     "invoice": str(inv).strip(), "inv": _inv_key(inv), "date": d.isoformat() if d else "",
                     "taxable": taxable, "tax": tax})
    return {"rows": rows, "sheet": sheet, "filename": filename, "skipped": skipped}


def _looks_like_2b(filename: str, parsed: dict, data: bytes) -> bool:
    if re.search(r"gstr|2b", filename, re.I) or _norm(parsed.get("sheet")) == "b2b":
        return True
    try:
        head = " ".join(str(c) for _, rows in read_named_tables(filename, data) for row in rows[:4] for c in row if c)
    except Exception:
        return False
    return bool(re.search(r"gstr[\s-]*2b", head, re.I))


def _supplier_id(row: dict, by_name: dict) -> str:
    return row["gstin"] or by_name.get(_name_key(row["supplier"])) or _name_key(row["supplier"])


def reconcile(books: list[dict], b2b: list[dict]) -> dict:
    """Pure matching of two parsed row lists. Amounts in paise in, rupees out."""
    by_name = {_name_key(r["supplier"]): r["gstin"] for r in b2b + books if r["gstin"] and r["supplier"]}

    def index(rows):
        out, dup = {}, 0
        for r in rows:
            k = (_supplier_id(r, by_name), r["inv"])
            if k in out:                                             # the same invoice twice in one file: add them up
                dup += 1
                out[k] = {**out[k], "taxable": out[k]["taxable"] + r["taxable"], "tax": out[k]["tax"] + r["tax"]}
            else:
                out[k] = r
        return out, dup

    bk, bdup = index(books)
    tk, tdup = index(b2b)
    matched, mismatch, missing, extra = [], [], [], []
    for k, b in bk.items():
        t = tk.get(k)
        if t is None:
            missing.append(b)
        elif abs(b["taxable"] - t["taxable"]) <= TOLERANCE and abs(b["tax"] - t["tax"]) <= TOLERANCE:
            matched.append(b)
        else:
            mismatch.append((b, t))
    extra = [t for k, t in tk.items() if k not in bk]

    possible, used = [], set()
    for b in missing:                                                # same supplier and tax, a different invoice number
        for j, t in enumerate(extra):
            if j not in used and _supplier_id(b, by_name) == _supplier_id(t, by_name) and abs(b["tax"] - t["tax"]) <= TOLERANCE:
                possible.append({"supplier": b["supplier"] or t["supplier"], "books_invoice": b["invoice"], "gstr2b_invoice": t["invoice"],
                                 "itc_rupees": b["tax"] / 100})
                used.add(j)
                break

    short = [(b, t, b["tax"] - t["tax"]) for b, t in mismatch if b["tax"] > t["tax"]]
    itc_missing = sum(b["tax"] for b in missing)
    itc_short = sum(d for _, _, d in short)
    chase = defaultdict(lambda: {"invoices": [], "itc": 0})
    for b in missing:
        c = chase[b["supplier"] or b["gstin"]]
        c["invoices"].append(f"{b['invoice']} (not in 2B)")
        c["itc"] += b["tax"]
    for b, t, d in short:
        c = chase[b["supplier"] or b["gstin"]]
        c["invoices"].append(f"{b['invoice']} (2B shows less)")
        c["itc"] += d
    rupees = lambda p: round(p / 100, 2)
    return {
        "counts": {"books_invoices": len(bk), "gstr2b_invoices": len(tk), "matched": len(matched), "amount_mismatch": len(mismatch),
                   "in_books_not_in_2b": len(missing), "in_2b_not_in_books": len(extra), "possible_matches": len(possible)},
        "counts_reconcile": len(bk) == len(matched) + len(mismatch) + len(missing) and len(tk) == len(matched) + len(mismatch) + len(extra),
        "itc_at_risk_rupees": rupees(itc_missing + itc_short),
        "itc_in_books_not_in_2b_rupees": rupees(itc_missing), "itc_short_in_2b_rupees": rupees(itc_short),
        "amount_mismatch": [{"supplier": b["supplier"] or t["supplier"], "invoice": b["invoice"], "itc_books_rupees": rupees(b["tax"]),
                             "itc_2b_rupees": rupees(t["tax"]), "difference_rupees": rupees(b["tax"] - t["tax"]),
                             "taxable_books_rupees": rupees(b["taxable"]), "taxable_2b_rupees": rupees(t["taxable"])} for b, t in mismatch],
        "in_books_not_in_2b": [{"supplier": b["supplier"], "invoice": b["invoice"], "date": b["date"], "itc_rupees": rupees(b["tax"])} for b in missing],
        "in_2b_not_in_books": [{"supplier": t["supplier"], "invoice": t["invoice"], "date": t["date"], "itc_rupees": rupees(t["tax"])} for t in extra],
        "possible_matches": possible,
        "chase_suppliers": sorted(({"supplier": k, "invoices": v["invoices"], "itc_rupees": rupees(v["itc"])} for k, v in chase.items()),
                                  key=lambda x: -x["itc_rupees"]),
        "duplicates_added_up": {"books": bdup, "gstr2b": tdup},
    }


def report_blocks(res: dict) -> list[dict]:
    c = res["counts"]
    money = lambda cols: [{"label": x, "kind": "money", "align": "right"} if x.endswith("(Rs)") else {"label": x} for x in cols]
    blocks = [{"type": "stats", "items": [
        {"label": "In your books", "value": str(c["books_invoices"]), "hint": "invoices"},
        {"label": "In GSTR-2B", "value": str(c["gstr2b_invoices"]), "hint": "invoices"},
        {"label": "Matched", "value": str(c["matched"]), "tone": "good"},
        {"label": "ITC at risk", "value": f"Rs {res['itc_at_risk_rupees']:,.2f}", "tone": "bad" if res["itc_at_risk_rupees"] else "good"}]}]
    if res["in_books_not_in_2b"]:
        blocks.append({"type": "table", "caption": "In your books but not in GSTR-2B (the supplier has not reported it): ITC at risk",
                       "columns": money(["Supplier", "Invoice", "Date", "ITC (Rs)"]),
                       "rows": [[r["supplier"], r["invoice"], r["date"], r["itc_rupees"]] for r in res["in_books_not_in_2b"][:PDF_CAP]]})
    if res["amount_mismatch"]:
        blocks.append({"type": "table", "caption": "Same invoice, different amounts",
                       "columns": money(["Supplier", "Invoice", "ITC in books (Rs)", "ITC in 2B (Rs)", "Difference (Rs)"]),
                       "rows": [[r["supplier"], r["invoice"], r["itc_books_rupees"], r["itc_2b_rupees"], r["difference_rupees"]]
                                for r in res["amount_mismatch"][:PDF_CAP]]})
    if res["in_2b_not_in_books"]:
        blocks.append({"type": "table", "caption": "In GSTR-2B but not in your books (not yet entered, a wrong GSTIN, or not yours)",
                       "columns": money(["Supplier", "Invoice", "Date", "ITC (Rs)"]),
                       "rows": [[r["supplier"], r["invoice"], r["date"], r["itc_rupees"]] for r in res["in_2b_not_in_books"][:PDF_CAP]]})
    if res["possible_matches"]:
        blocks.append({"type": "table", "caption": "Possible matches with a different invoice number (please confirm)",
                       "columns": money(["Supplier", "In books", "In GSTR-2B", "ITC (Rs)"]),
                       "rows": [[r["supplier"], r["books_invoice"], r["gstr2b_invoice"], r["itc_rupees"]] for r in res["possible_matches"][:PDF_CAP]]})
    blocks.append({"type": "note", "text": "Rule: an invoice matches when the supplier GSTIN (or name, if the books have no GSTIN) and the invoice number agree, "
                                           "and the taxable value and the tax are each within Rs 1. Prepared for the owner and the CA to review; it is not advice on "
                                           "what to claim or reverse."})
    return blocks


async def gst_reconcile(app, ctx, args: dict) -> dict:
    from src.agent import actions, documents
    refs = [args.get("books_file_ref"), args.get("gstr2b_file_ref")]
    if not all(isinstance(r, str) and r for r in refs) or refs[0] == refs[1]:
        return {"error": "give books_file_ref (the purchase register) and gstr2b_file_ref (the GSTR-2B statement): two different files"}
    got = []
    for ref in refs:
        data, _, name = await actions._fetch(app, ref, ctx)
        got.append((name, data, parse_file(name, data)))
    for name, _, p in got:
        if "error" in p:
            return {"error": p["error"], "hint": "ask the owner for the purchase register and the GSTR-2B as Excel or CSV"}
    a2b, b2b_ = (_looks_like_2b(n, p, d) for n, d, p in got)
    books, gstr = (got[1], got[0]) if (a2b and not b2b_) else (got[0], got[1])      # the files may have been named the wrong way round
    res = reconcile(books[2]["rows"], gstr[2]["rows"])
    res["files"] = {"books": books[0], "gstr2b": gstr[0]}
    res["skipped_rows"] = {"books": books[2]["skipped"], "gstr2b": gstr[2]["skipped"]}
    for k in ("amount_mismatch", "in_books_not_in_2b", "in_2b_not_in_books", "possible_matches", "chase_suppliers"):
        if len(res[k]) > LIST_CAP:
            res[f"{k}_note"] = f"{len(res[k])} in all; the report has up to {PDF_CAP}"
    full = dict(res)
    res = {k: (v[:LIST_CAP] if isinstance(v, list) else v) for k, v in res.items()}
    if args.get("report", True):
        doc = await documents.make_document(app, ctx, {"kind": "custom", "title": "GSTR-2B reconciliation",
                                                       "subtitle": f"{books[0]} against {gstr[0]}", "blocks": report_blocks(full)})
        if "file_ref" in doc:
            res.update({"report_file_ref": doc["file_ref"], "report_filename": doc["filename"], "_new_files": doc["_new_files"],
                        "next": "Send the report with send_file_to_owner(file_ref=report_file_ref), then tell the owner the counts, the ITC at "
                                "risk and which suppliers to chase. Offer a short message per supplier for the owner's approval."})
        else:
            res["report_error"] = doc.get("error", "the report could not be made")
    return res
