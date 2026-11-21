"""Receivables or payables ageing from a list of open invoices, in one call.

Buckets by days from the invoice date (0-30, 31-60, 61-90, over 90) or, when asked, by days past the DUE date (not yet due, 1-30, 31-60,
61-90, over 90). Party-wise table, the biggest parties in the oldest bucket, a PDF and a full Excel. It reads the list and changes nothing.
Credit notes and advances (negative amounts) stay in the bucket of their own date and are named in the summary.
"""
import re
from collections import defaultdict
from datetime import date

from src import clock
from src.agent import ca_reports
from src.agent.ca_reports import rs
from src.ingest.sheet_mapper import _norm, parse_amount_paise, parse_date, read_named_tables

HEADER_SCAN = 40
FIELDS = {
    "party": ("party", "party name", "customer", "customer name", "name", "ledger", "particulars", "supplier", "supplier name", "vendor", "account"),
    "invoice": ("invoice", "invoice no", "invoice number", "bill no", "bill number", "ref no", "voucher no", "document no", "bill", "ref"),
    "date": ("invoice date", "bill date", "date", "document date", "voucher date", "posting date"),
    "due": ("due date", "due on", "due", "payment due date"),
    "amount": ("outstanding amount", "outstanding", "pending amount", "pending", "balance", "balance due", "amount outstanding", "amount due", "balance amount",
               "open amount", "amount", "bill amount"),
}
BUCKETS = {"invoice_date": ["0-30", "31-60", "61-90", "90+"], "due_date": ["Not yet due", "1-30", "31-60", "61-90", "90+"]}


def _map(header: list) -> dict:
    cells = [_norm(h) for h in header]
    found: dict[str, int] = {}
    for exact in (True, False):
        for i, cell in enumerate(cells):
            if not cell or i in found.values():
                continue
            for field, names in FIELDS.items():
                if field in found:
                    continue
                if (cell in names) if exact else any(len(n) >= 5 and cell.startswith(n + " ") for n in names):
                    found[field] = i
                    break
    return found


def parse_file(filename: str, data: bytes) -> dict:
    try:
        tables = read_named_tables(filename, data)
    except Exception as e:
        return {"error": f"could not open {filename}: {str(e)[:120]}"}
    for name, rows in tables:
        for i, row in enumerate(rows[:HEADER_SCAN]):
            cols = _map(row)
            if "party" in cols and "amount" in cols and ("date" in cols or "due" in cols):
                out, skipped = [], 0
                for r in rows[i + 1:]:
                    g = lambda f: r[cols[f]] if f in cols and cols[f] < len(r) else None
                    party = str(g("party") or "").strip()
                    amt = parse_amount_paise(g("amount"))
                    if not party or amt is None or re.search(r"^\s*(grand\s+)?total\b", party, re.I):
                        skipped += 1
                        continue
                    out.append({"party": party, "invoice": str(g("invoice") or "").strip(), "date": parse_date(g("date")), "due": parse_date(g("due")), "amount": amt})
                return {"rows": out, "sheet": name, "filename": filename, "skipped": skipped, "has_date": "date" in cols, "has_due": "due" in cols}
    return {"error": f"{filename}: could not find a header row with a party, an amount and an invoice date or due date"}


def age(rows: list[dict], as_on: date, basis: str) -> dict:
    labels = BUCKETS[basis]
    bucket_of = lambda d: ("0-30" if d <= 30 else "31-60" if d <= 60 else "61-90" if d <= 90 else "90+") if basis == "invoice_date" else \
        ("Not yet due" if d <= 0 else "1-30" if d <= 30 else "31-60" if d <= 60 else "61-90" if d <= 90 else "90+")
    totals, parties, invs, credit, undated = defaultdict(int), defaultdict(lambda: defaultdict(int)), [], 0, 0
    for r in rows:
        d0 = r["date"] if basis == "invoice_date" else r["due"]
        if d0 is None:
            undated += 1
            continue
        b = bucket_of((as_on - d0).days)
        totals[b] += r["amount"]
        parties[r["party"]][b] += r["amount"]
        invs.append({**r, "days": (as_on - d0).days, "bucket": b})
        if r["amount"] < 0:
            credit += r["amount"]
    R = lambda p: round(p / 100, 2)
    table = sorted(({"party": p, **{b: R(v.get(b, 0)) for b in labels}, "total": R(sum(v.values()))} for p, v in parties.items()), key=lambda x: -x["total"])
    oldest = labels[-1]
    top_old = sorted(((p, v.get(oldest, 0)) for p, v in parties.items() if v.get(oldest, 0) > 0), key=lambda t: -t[1])
    return {"as_on": as_on.isoformat(), "basis": basis, "buckets": {b: R(totals.get(b, 0)) for b in labels}, "total_rupees": R(sum(totals.values())),
            "invoices_counted": len(invs), "invoices_without_a_date": undated, "parties": len(parties), "party_wise": table,
            f"top_parties_in_{oldest}": [{"party": p, "rupees": R(a)} for p, a in top_old[:5]], "credit_notes_and_advances_rupees": R(credit),
            "invoice_wise": [{"party": i["party"], "invoice": i["invoice"], "date": (i["date"].isoformat() if i["date"] else ""), "due": (i["due"].isoformat() if i["due"] else ""),
                              "days": i["days"], "bucket": i["bucket"], "rupees": R(i["amount"])} for i in sorted(invs, key=lambda i: -i["days"])]}


def summary_lines(x: dict) -> list[str]:
    way = "from the invoice date" if x["basis"] == "invoice_date" else "past the due date"
    oldest = list(x["buckets"])[-1]
    L = [f"Ageing as on {x['as_on']}, days {way}: total outstanding {rs(x['total_rupees'])} over {x['invoices_counted']} invoices and {x['parties']} parties.",
         "Buckets: " + "; ".join(f"{b} days {rs(v)}" if b[0].isdigit() else f"{b} {rs(v)}" for b, v in x["buckets"].items()) + "."]
    top = x[f"top_parties_in_{oldest}"]
    if top:
        L.append(f"Biggest in {oldest} days: " + "; ".join(f"{t['party']} {rs(t['rupees'])}" for t in top[:3]) + ".")
    if x["credit_notes_and_advances_rupees"]:
        L.append(f"Credit notes / advances of {rs(x['credit_notes_and_advances_rupees'])} are inside these buckets (shown as negatives).")
    if x["invoices_without_a_date"]:
        L.append(f"{x['invoices_without_a_date']} rows had no usable date and are NOT in the buckets.")
    return L


async def ageing_report(app, ctx, args: dict) -> dict:
    from src.agent import actions
    ref = args.get("file_ref")
    if not isinstance(ref, str) or not ref:
        return {"error": "give file_ref: the open invoices list (party, invoice date or due date, outstanding amount) as Excel or CSV"}
    as_on = parse_date(args["as_on"]) if args.get("as_on") else clock.today()
    if as_on is None:
        return {"error": "as_on must be a date like 2026-10-31"}
    data, _, name = await actions._fetch(app, ref, ctx)
    p = parse_file(name, data)
    if "error" in p:
        return {"error": p["error"], "hint": "ask the owner for a list of open invoices as Excel or CSV"}
    basis = "due_date" if str(args.get("basis") or "").lower().startswith("due") else "invoice_date"
    if basis == "invoice_date" and not p["has_date"]:
        basis = "due_date"
    if basis == "due_date" and not p["has_due"]:
        return {"error": "this list has no due date column; ask the owner whether to age it from the invoice date instead"}
    full = age(p["rows"], as_on, basis)
    full["file"] = name
    res = {k: v for k, v in full.items() if k not in ("invoice_wise",)}
    res["party_wise"] = full["party_wise"][:15]
    if len(full["party_wise"]) > 15:
        res["party_wise_note"] = f"{len(full['party_wise'])} parties in all; the Excel has every one"
    res["summary_lines"] = summary_lines(full)
    labels = list(full["buckets"])
    money = lambda cols: [{"label": c, "kind": "money", "align": "right"} if c in labels or c == "Total (Rs)" else {"label": c} for c in cols]
    blocks = [{"type": "stats", "items": [{"label": "Outstanding", "value": rs(full["total_rupees"])}] + [{"label": f"{b}" + ("" if not b[0].isdigit() else " days"), "value": rs(v)} for b, v in full["buckets"].items()]},
              {"type": "table", "caption": f"Party-wise ageing as on {full['as_on']}", "columns": money(["Party"] + labels + ["Total (Rs)"]),
               "rows": [[t["party"]] + [t[b] for b in labels] + [t["total"]] for t in full["party_wise"][:40]]},
              {"type": "note", "text": f"Days counted {'from the invoice date' if basis == 'invoice_date' else 'past the due date'} up to {full['as_on']}. Prepared for the owner to review."}]
    sheets = [("Summary", ["Bucket", "Amount (Rs)"], [[b, v] for b, v in full["buckets"].items()] + [["Total", full["total_rupees"]]]),
              ("Party-wise", ["Party"] + labels + ["Total (Rs)"], [[t["party"]] + [t[b] for b in labels] + [t["total"]] for t in full["party_wise"]]),
              ("Invoice-wise", ["Party", "Invoice", "Invoice date", "Due date", "Days", "Bucket", "Amount (Rs)"],
               [[i["party"], i["invoice"], i["date"], i["due"], i["days"], i["bucket"], i["rupees"]] for i in full["invoice_wise"]])]
    if args.get("report", True):
        out = await ca_reports.deliver(app, ctx, title="Ageing report", subtitle=f"{name} as on {full['as_on']}", blocks=blocks, xl_name="Ageing_report.xlsx", sheets=sheets,
                                       caption="Ageing report", send=bool(args.get("send", True)))
        res.update({"files_made": out["files"], "files_sent_to_owner": out["sent"], "files_not_sent": out["not_sent"], "_new_files": out["_new_files"], "file_refs": out["file_refs"]})
    res["next"] = ("Tell the owner the result in their language using summary_lines (the figures are final: quote them, never recompute them or redo this with code). "
                   + ("The PDF and the full Excel have already been sent. " if res.get("files_sent_to_owner") else "")
                   + f"Say which way the days were counted ({'invoice date' if basis == 'invoice_date' else 'due date'}) and the as-on date used.")
    return ca_reports.compact(res, ("summary_lines", "next", "files_sent_to_owner", "files_not_sent", "as_on", "basis", "buckets", "total_rupees"))
