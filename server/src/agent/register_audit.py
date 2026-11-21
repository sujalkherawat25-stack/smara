"""Audit of a GST sales (or purchase) register in one call: the mistakes an accountant looks for, listed invoice by invoice.

  wrong tax head    a supply inside the shop's own state charged IGST, or a supply to another state charged CGST/SGST
  total mismatch    invoice value is not taxable value + the taxes
  entered twice     the same invoice line repeated exactly (invoice number, date, party and every amount)
  GSTIN             a B2B invoice with no GSTIN, or a GSTIN that fails the format and checksum test
  rate              tax does not match the GST rate on the row (or, with no rate column, is not a standard slab)

Credit notes (negative amounts) are checked like any other row and are not errors in themselves. Nothing in the file is changed. It
reads one sheet; the owner's own state is taken from the rows that carry CGST/SGST unless it is given.
"""
import re
from collections import Counter

from src.agent import ca_reports
from src.box.gstin import check_gstin_status
from src.ingest.sheet_mapper import _norm, parse_amount_paise, parse_date, read_named_tables

HEADER_SCAN = 40
LIST_CAP = 6
STATES = {"jammu and kashmir": "01", "himachal pradesh": "02", "punjab": "03", "chandigarh": "04", "uttarakhand": "05", "haryana": "06", "delhi": "07",
          "rajasthan": "08", "uttar pradesh": "09", "bihar": "10", "sikkim": "11", "arunachal pradesh": "12", "nagaland": "13", "manipur": "14",
          "mizoram": "15", "tripura": "16", "meghalaya": "17", "assam": "18", "west bengal": "19", "jharkhand": "20", "odisha": "21", "chhattisgarh": "22",
          "madhya pradesh": "23", "gujarat": "24", "daman and diu": "25", "dadra and nagar haveli": "26", "maharashtra": "27", "karnataka": "29", "goa": "30",
          "lakshadweep": "31", "kerala": "32", "tamil nadu": "33", "puducherry": "34", "andaman and nicobar islands": "35", "telangana": "36",
          "andhra pradesh": "37", "ladakh": "38"}
SLABS = (0, 0.1, 0.25, 1, 1.5, 3, 5, 6, 7.5, 12, 18, 28, 40)
FIELDS = {
    "invoice": ("invoice no", "invoice number", "invoice", "inv no", "bill no", "document number", "voucher no"),
    "date": ("invoice date", "date", "bill date", "document date"),
    "party": ("customer name", "party name", "customer", "party", "buyer", "name of customer", "receiver name"),
    "gstin": ("customer gstin", "gstin", "gstin of customer", "party gstin", "gstin uin", "gstin/uin", "buyer gstin", "recipient gstin"),
    "type": ("type", "supply type", "invoice type", "b2b b2c", "category"),
    "place": ("place of supply", "pos", "state", "place", "state of supply"),
    "taxable": ("taxable value", "taxable", "taxable amount", "basic value"),
    "cgst": ("cgst", "central tax", "cgst amount"),
    "sgst": ("sgst", "state tax", "sgst amount", "sgst utgst"),
    "igst": ("igst", "integrated tax", "igst amount"),
    "total": ("invoice value", "total", "invoice amount", "total amount", "gross total"),
    "rate": ("gst rate", "gst rate %", "rate", "tax rate", "rate %", "gst %"),
}


def _map(header: list) -> dict:
    cells = [_norm(h) for h in header]
    found: dict[str, int] = {}
    for exact in (True, False):
        for i, cell in enumerate(cells):
            if not cell or i in found.values():
                continue
            for field, names in FIELDS.items():
                if field not in found and ((cell in names) if exact else any(len(n) >= 5 and cell.startswith(n + " ") for n in names)):
                    found[field] = i
                    break
    return found


def _state_code(v) -> str:
    s = str(v or "").strip().lower()
    m = re.match(r"^(\d{2})\b", s)
    if m:
        return m.group(1)
    s = re.sub(r"[^a-z ]", " ", s.replace("&", " and "))
    s = re.sub(r"\s+", " ", s).strip()
    return STATES.get(s, "")


def parse_file(filename: str, data: bytes) -> dict:
    try:
        tables = read_named_tables(filename, data)
    except Exception as e:
        return {"error": f"could not open {filename}: {str(e)[:120]}"}
    for name, rows in tables:
        for i, row in enumerate(rows[:HEADER_SCAN]):
            cols = _map(row)
            if "invoice" in cols and "taxable" in cols and ({"cgst", "sgst", "igst"} & set(cols)):
                out = []
                for n, r in enumerate(rows[i + 1:], start=i + 2):
                    g = lambda f: r[cols[f]] if f in cols and cols[f] < len(r) else None
                    inv = str(g("invoice") or "").strip()
                    if not inv or re.match(r"^\s*(grand\s+)?total\b", inv, re.I):
                        continue
                    money = lambda f: parse_amount_paise(g(f)) or 0
                    d = parse_date(g("date"))
                    out.append({"row": n, "invoice": inv, "date": d.isoformat() if d else str(g("date") or ""), "party": str(g("party") or "").strip(),
                                "gstin": re.sub(r"\s", "", str(g("gstin") or "")).upper(), "type": str(g("type") or "").strip().upper(),
                                "place": _state_code(g("place")), "place_text": str(g("place") or "").strip(), "taxable": money("taxable"),
                                "cgst": money("cgst"), "sgst": money("sgst"), "igst": money("igst"), "total": money("total") if "total" in cols else None,
                                "rate": (float(str(g("rate")).replace("%", "")) if "rate" in cols and re.fullmatch(r"\s*-?\d+(\.\d+)?\s*%?\s*", str(g("rate") or "")) else None)})
                return {"rows": out, "cols": set(cols), "sheet": name, "filename": filename}
    return {"error": f"{filename}: could not find a register header (invoice number, taxable value and CGST/SGST/IGST columns)"}


def audit(rows: list[dict], cols: set, own_state: str = "") -> dict:
    own = _state_code(own_state)
    if not own:
        c = Counter(r["place"] for r in rows if r["place"] and (r["cgst"] or r["sgst"]))
        own = c.most_common(1)[0][0] if c else ""
    found: dict[str, list] = {k: [] for k in ("wrong_head", "total_mismatch", "entered_twice", "b2b_missing_gstin", "invalid_gstin", "rate_mismatch")}
    seen = set()
    rp = lambda p: round(p / 100, 2)
    for r in rows:
        tax = r["cgst"] + r["sgst"] + r["igst"]
        key = (r["invoice"], r["date"], r["party"], r["taxable"], r["cgst"], r["sgst"], r["igst"], r["total"])
        if key in seen:
            found["entered_twice"].append((r, "the same line again"))
            continue
        seen.add(key)
        if own and r["place"]:
            if r["place"] == own and r["igst"]:
                found["wrong_head"].append((r, f"supply inside {r['place_text'] or own} charged IGST {rp(r['igst'])}; it should be CGST + SGST"))
            elif r["place"] != own and (r["cgst"] or r["sgst"]):
                found["wrong_head"].append((r, f"supply to {r['place_text'] or r['place']} charged CGST/SGST {rp(r['cgst'] + r['sgst'])}; it should be IGST"))
        if r["total"] is not None and abs(r["total"] - (r["taxable"] + tax)) > 100:
            found["total_mismatch"].append((r, f"invoice value {rp(r['total'])} but taxable + tax = {rp(r['taxable'] + tax)}"))
        if "type" in cols and r["type"].startswith("B2B"):
            if not r["gstin"]:
                found["b2b_missing_gstin"].append((r, "B2B invoice with no customer GSTIN"))
            elif not check_gstin_status(r["gstin"]).get("valid"):
                found["invalid_gstin"].append((r, f"GSTIN {r['gstin']}: {check_gstin_status(r['gstin']).get('reason', 'invalid')}"))
        elif "type" not in cols and r["gstin"] and not check_gstin_status(r["gstin"]).get("valid"):
            found["invalid_gstin"].append((r, f"GSTIN {r['gstin']}: {check_gstin_status(r['gstin']).get('reason', 'invalid')}"))
        if r["taxable"]:
            if r["rate"] is not None:
                if abs(abs(tax) - abs(r["taxable"]) * r["rate"] / 100) > 100:
                    found["rate_mismatch"].append((r, f"tax {rp(tax)} on {rp(r['taxable'])} is {round(abs(tax) / abs(r['taxable']) * 100, 2)}%, the row says {r['rate']}%"))
            elif tax and min(abs(abs(tax) / abs(r["taxable"]) * 100 - s) for s in SLABS) > 0.2:
                found["rate_mismatch"].append((r, f"tax is {round(abs(tax) / abs(r['taxable']) * 100, 2)}% of the taxable value, not a standard GST rate"))
    return {"own_state_code": own, "rows_checked": len(rows), "found": found, "counts": {k: len(v) for k, v in found.items()}}


TITLES = {"wrong_head": "Wrong tax head (IGST vs CGST+SGST)", "total_mismatch": "Invoice value does not match taxable + tax", "entered_twice": "Entered twice",
          "b2b_missing_gstin": "B2B invoice without a GSTIN", "invalid_gstin": "Invalid GSTIN", "rate_mismatch": "Tax does not match the rate"}


def summary_lines(a: dict) -> list[str]:
    c = a["counts"]
    total = sum(c.values())
    L = [f"Checked {a['rows_checked']} invoice rows: {total} problem entries." + (f" Own state taken as code {a['own_state_code']}." if a["own_state_code"] else " Own state could not be told, so the tax-head check was skipped.")]
    for k, title in TITLES.items():
        if c[k]:
            ex = ", ".join(r["invoice"] for r, _ in a["found"][k][:LIST_CAP])
            L.append(f"{title}: {c[k]} (e.g. {ex}{' ...' if c[k] > LIST_CAP else ''}).")
    if not total:
        L.append("No problems found by these checks.")
    L.append("Credit notes (negative amounts) are checked like other rows and are not treated as errors.")
    return L


def excel_sheets(a: dict) -> list:
    rows = [[TITLES[k], r["invoice"], r["date"], r["party"], d] for k, lst in a["found"].items() for r, d in lst]
    return [("Problems", ["Problem", "Invoice", "Date", "Customer", "Detail"], rows),
            ("Summary", ["Check", "Count"], [[TITLES[k], n] for k, n in a["counts"].items()] + [["Rows checked", a["rows_checked"]]])]


async def audit_gst_register(app, ctx, args: dict) -> dict:
    from src.agent import actions
    ref = args.get("file_ref")
    if not isinstance(ref, str) or not ref:
        return {"error": "give file_ref: the sales (or purchase) register as Excel or CSV"}
    data, _, name = await actions._fetch(app, ref, ctx)
    p = parse_file(name, data)
    if "error" in p:
        return {"error": p["error"], "hint": "ask the owner for the register as Excel or CSV with invoice number, taxable value and CGST/SGST/IGST columns"}
    a = audit(p["rows"], p["cols"], str(args.get("own_state") or ""))
    res = {"summary_lines": summary_lines(a), "counts": a["counts"], "rows_checked": a["rows_checked"], "own_state_code": a["own_state_code"], "file": name,
           **{k: [{"invoice": r["invoice"], "detail": d} for r, d in v] for k, v in a["found"].items()}}
    if args.get("report", True):
        blocks = [{"type": "stats", "items": [{"label": TITLES[k][:22], "value": str(n), "tone": "bad" if n else "good"} for k, n in a["counts"].items()]}]
        rows = [[TITLES[k], r["invoice"], r["date"], r["party"][:26], d[:60]] for k, v in a["found"].items() for r, d in v][:60]
        if rows:
            blocks.append({"type": "table", "caption": "Problem entries", "columns": [{"label": c} for c in ("Problem", "Invoice", "Date", "Customer", "Detail")], "rows": rows})
        blocks.append({"type": "note", "text": "Prepared for the owner and the CA to review; nothing in the register was changed."})
        out = await ca_reports.deliver(app, ctx, title="GST register audit", subtitle=name, blocks=blocks, xl_name="Register_audit.xlsx", sheets=excel_sheets(a),
                                       caption="GST register audit: the problem entries only", send=bool(args.get("send", True)))
        res.update({"files_made": out["files"], "files_sent_to_owner": out["sent"], "files_not_sent": out["not_sent"], "_new_files": out["_new_files"], "file_refs": out["file_refs"]})
    res["next"] = ("Tell the owner the result in their language using summary_lines (the lists are final: quote them, never recheck them with code). "
                   + ("The PDF and an Excel with the problem entries only have already been sent. " if res.get("files_sent_to_owner") else "")
                   + "Say what each check means in one line and that nothing in their file was changed.")
    return ca_reports.compact(res, ("summary_lines", "next", "files_sent_to_owner", "files_not_sent", "counts", "rows_checked", "own_state_code"), cap=8)
