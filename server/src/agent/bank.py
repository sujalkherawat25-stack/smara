"""Bank reconciliation statement (BRS) in one call: the cash/bank book against the bank statement.

  match      1. same reference (cheque / UTR / voucher number, spaces and leading zeros ignored), same direction, same amount
             2. same reference and direction but a different amount: a recording error (a transposition when the digits are the same)
             3. no reference to go by: same direction and amount within 10 days, listed as matched WITHOUT a reference
  left over  books only: payments not yet presented (deduct from the bank balance) and receipts in transit (add to it)
             bank only: charges, interest and direct credits the books do not have yet (the books must be corrected)
             the same entry twice in the books: reversed in the books
  proof      balance per books, corrected for what only the books can fix, equals the balance per bank, adjusted for what only the
             bank can fix. `reconciles` is that check; a person reads the lists, the tool never changes either file.

Both files may be Excel or CSV with title rows above the header. The opening balance is read from a line such as "Opening balance
... 5,00,000.00 Dr"; without one the closing balances cannot be worked out and the tool says what it needs.
"""
import re
from collections import defaultdict
from datetime import date, timedelta

from src.agent import ca_reports
from src.agent.ca_reports import rs
from src.ingest.sheet_mapper import _norm, parse_amount_paise, parse_date, read_named_tables

HEADER_SCAN = 40
WINDOW = timedelta(days=10)
LIST_CAP = 15

COMMON = {
    "date": ("date", "txn date", "transaction date", "voucher date", "vch date", "value date", "posting date"),
    "ref": ("voucher cheque no", "cheque no", "chq no", "chq ref no", "cheque ref no", "instrument no", "ref no", "reference no", "voucher no", "vch no",
            "voucher number", "cheque number", "utr", "utr no", "reference", "ref", "chq", "cheque"),
    "desc": ("particulars", "narration", "description", "party", "party name", "details", "remarks", "transaction remarks"),
    "balance": ("balance", "closing balance", "running balance", "balance amount"),
}
IN_OUT = {
    "books": {"in": ("receipt", "receipts", "receipt dr", "debit", "dr", "debit amount", "deposit", "amount received"),
              "out": ("payment", "payments", "payment cr", "credit", "cr", "credit amount", "withdrawal", "amount paid")},
    "bank": {"in": ("deposit", "deposits", "deposit amt", "credit", "cr", "credit amount", "receipt", "amount credited"),
             "out": ("withdrawal", "withdrawals", "withdrawal amt", "debit", "dr", "debit amount", "payment", "amount debited")},
}


def _map(header: list, role: str) -> dict:
    fields = {**COMMON, **IN_OUT[role]}
    cells = [_norm(h) for h in header]
    found: dict[str, int] = {}
    for exact in (True, False):
        for i, cell in enumerate(cells):
            if not cell or i in found.values():
                continue
            for field, names in fields.items():
                if field in found:
                    continue
                if (cell in names) if exact else any(len(n) >= 5 and cell.startswith(n + " ") for n in names):
                    found[field] = i
                    break
    return found


def _ref_key(v) -> str:
    s = re.sub(r"[^A-Z0-9]", "", str(v or "").upper())
    return re.sub(r"^0+(?=\d)", "", s)


def _amount_after(text: str):
    """The amount in a line like 'Opening balance as on 01-10-2026: 5,00,000.00 Dr' (decimals first, so a date is not an amount); signed."""
    m = re.search(r"(\d[\d,]*\.\d{1,2})\s*(dr|cr)?\b", text, re.I) or re.search(r"(?:balance|b/f)[^\d]*(\d[\d,]{3,})\s*(dr|cr)?\b", text, re.I)
    if not m:
        return None
    v = parse_amount_paise(m.group(1))
    if v is None:
        return None
    return -v if (m.group(2) or "").lower() == "cr" else v


def parse_file(filename: str, data: bytes, role: str) -> dict:
    """{rows, opening (paise or None), closing (paise or None), sheet} or {error}. Each row: date, ref, key, desc, amount (paise, positive), dir in|out."""
    try:
        tables = read_named_tables(filename, data)
    except Exception as e:
        return {"error": f"could not open {filename}: {str(e)[:120]}"}
    best = None
    for name, rows in tables:
        for i, row in enumerate(rows[:HEADER_SCAN]):
            cols = _map(row, role)
            if "date" in cols and ("in" in cols or "out" in cols) and ("in" in cols and "out" in cols or "balance" in cols):
                best = (name, i, cols, rows)
                break
        if best:
            break
    if not best:
        return {"error": f"{filename}: could not find a header row with a date column and receipt/payment (or deposit/withdrawal) columns"}
    sheet, hi, cols, table = best
    opening = None
    for r in table:
        line = " ".join(str(c) for c in r if c not in (None, ""))
        if re.search(r"opening\s+bal|brought\s+forward|b/f", line, re.I) and opening is None:
            opening = _amount_after(line)
            if opening is None:
                nums = [parse_amount_paise(c) for c in r if isinstance(c, (int, float))]
                opening = nums[-1] if nums else None
    out, last_balance = [], None
    get = lambda r, f: r[cols[f]] if f in cols and cols[f] < len(r) else None
    for r in table[hi + 1:]:
        desc, ref = str(get(r, "desc") or "").strip(), get(r, "ref")
        line = f"{desc} {ref or ''}"
        if re.search(r"\btotal\b|opening\s+bal|closing\s+bal|brought\s+forward|carried\s+forward", line, re.I):
            continue
        d = parse_date(get(r, "date"))
        if d is None:
            continue
        if "balance" in cols and parse_amount_paise(get(r, "balance")) is not None:
            last_balance = parse_amount_paise(get(r, "balance"))
        for direction in ("in", "out"):
            amt = parse_amount_paise(get(r, direction)) or 0
            if amt:
                out.append({"date": d, "ref": str(ref or "").strip(), "key": _ref_key(ref), "desc": desc, "amount": abs(amt), "dir": direction})
    return {"rows": out, "opening": opening, "closing": last_balance, "sheet": sheet, "filename": filename}


def _transposed(a: int, b: int) -> bool:
    return a != b and sorted(str(a)) == sorted(str(b)) and abs(a - b) % 9 == 0


def reconcile(books: dict, bank: dict, as_on: date | None = None) -> dict:
    """Pure matching of two parsed files (see module doc). Amounts in paise in, rupees out."""
    B = [dict(r, i=i) for i, r in enumerate(books["rows"]) if not as_on or r["date"] <= as_on]
    S = [dict(r, i=i) for i, r in enumerate(bank["rows"]) if not as_on or r["date"] <= as_on]
    mb, ms, pairs = set(), set(), []

    def pair(b, s, kind):
        mb.add(b["i"]); ms.add(s["i"]); pairs.append((b, s, kind))
    by_key = defaultdict(list)
    for s in S:
        if s["key"]:
            by_key[(s["key"], s["dir"])].append(s)
    for b in B:                                                        # 1. reference, direction and amount
        if not b["key"]:
            continue
        for s in by_key.get((b["key"], b["dir"]), []):
            if s["i"] not in ms and s["amount"] == b["amount"]:
                pair(b, s, "ref")
                break
    mismatches = []
    for b in B:                                                        # 2. reference and direction, other amount
        if b["i"] in mb or not b["key"]:
            continue
        for s in by_key.get((b["key"], b["dir"]), []):
            if s["i"] not in ms:
                mb.add(b["i"]); ms.add(s["i"])
                mismatches.append({"books": b, "bank": s})
                break
    dups = []
    seen = {(b["key"], b["dir"], b["amount"]) for b, _, _ in pairs}
    for b in B:                                                        # the same entry again in the books
        if b["i"] not in mb and b["key"] and (b["key"], b["dir"], b["amount"]) in seen:
            mb.add(b["i"])
            dups.append(b)
    for b in B:                                                        # 3. nothing to go by but direction, amount and date
        if b["i"] in mb:
            continue
        for s in S:
            if s["i"] not in ms and s["dir"] == b["dir"] and s["amount"] == b["amount"] and abs(s["date"] - b["date"]) <= WINDOW \
                    and not (b["key"] and s["key"] and b["key"] != s["key"] and len(s["key"]) > 6 and len(b["key"]) > 6):
                pair(b, s, "no reference")
                break
    left_b = [b for b in B if b["i"] not in mb]
    left_s = [s for s in S if s["i"] not in ms]
    sums = lambda rows, d: sum(r["amount"] for r in rows if r["dir"] == d)
    uncleared, transit = [b for b in left_b if b["dir"] == "out"], [b for b in left_b if b["dir"] == "in"]
    bank_out, bank_in = [s for s in left_s if s["dir"] == "out"], [s for s in left_s if s["dir"] == "in"]
    flow = lambda rows: sum(r["amount"] if r["dir"] == "in" else -r["amount"] for r in rows)
    books_bal = (books["opening"] + flow(B)) if books["opening"] is not None else None
    bank_bal = bank["closing"] if (bank["closing"] is not None and not as_on) else ((bank["opening"] + flow(S)) if bank["opening"] is not None else bank["closing"])
    fix_mismatch = sum((m["bank"]["amount"] - m["books"]["amount"]) * (1 if m["books"]["dir"] == "in" else -1) for m in mismatches)
    fix_dups = sum(d["amount"] * (1 if d["dir"] == "out" else -1) for d in dups)
    books_fix = sums(bank_in, "in") - sums(bank_out, "out") + fix_mismatch + fix_dups
    bank_fix = sums(transit, "in") - sums(uncleared, "out")
    corrected = books_bal + books_fix if books_bal is not None else None
    adjusted = bank_bal + bank_fix if bank_bal is not None else None
    r = lambda p: round(p / 100, 2)
    row = lambda x: {"date": x["date"].isoformat(), "ref": x["ref"], "narration": x["desc"], "amount_rupees": r(x["amount"])}
    return {
        "counts": {"books_entries": len(B), "bank_entries": len(S), "matched": len(pairs), "matched_without_reference": sum(1 for p in pairs if p[2] != "ref"),
                   "not_presented_or_in_transit": len(left_b), "only_in_bank": len(left_s), "amount_differs": len(mismatches), "entered_twice": len(dups)},
        "balance_per_books_rupees": r(books_bal) if books_bal is not None else None,
        "balance_per_bank_rupees": r(bank_bal) if bank_bal is not None else None,
        "payments_not_yet_presented": [row(x) for x in uncleared], "payments_not_yet_presented_rupees": r(sums(uncleared, "out")),
        "receipts_in_transit": [row(x) for x in transit], "receipts_in_transit_rupees": r(sums(transit, "in")),
        "bank_debits_not_in_books": [row(x) for x in bank_out], "bank_debits_not_in_books_rupees": r(sums(bank_out, "out")),
        "bank_credits_not_in_books": [row(x) for x in bank_in], "bank_credits_not_in_books_rupees": r(sums(bank_in, "in")),
        "amount_recorded_differently": [{**{k: v for k, v in row(m["books"]).items() if k != "amount_rupees"}, "in_books_rupees": r(m["books"]["amount"]),
                                         "in_bank_rupees": r(m["bank"]["amount"]), "difference_rupees": r(m["bank"]["amount"] - m["books"]["amount"]),
                                         "direction": "receipt" if m["books"]["dir"] == "in" else "payment",
                                         "looks_like_transposed_digits": _transposed(m["books"]["amount"], m["bank"]["amount"])} for m in mismatches],
        "entered_twice_in_books": [row(x) for x in dups], "entered_twice_rupees": r(sum(d["amount"] for d in dups)),
        "matched_without_reference": [{**row(b), "bank_date": s["date"].isoformat()} for b, s, k in pairs if k != "ref"],
        "books_corrections_rupees": r(books_fix) if books_bal is not None else None,
        "corrected_balance_per_books_rupees": r(corrected) if corrected is not None else None,
        "adjusted_balance_per_bank_rupees": r(adjusted) if adjusted is not None else None,
        "reconciles": (corrected is not None and adjusted is not None and abs(corrected - adjusted) <= 1),
        "difference_rupees": r(corrected - adjusted) if corrected is not None and adjusted is not None else None,
        "needs": "" if books_bal is not None and bank_bal is not None else
                 "the opening balance of the " + ("cash book" if books_bal is None else "bank statement") + " (it was not in the file): ask the owner for it",
    }


def summary_lines(x: dict) -> list[str]:
    if x["needs"]:
        return [f"I matched the entries but cannot finish the statement without {x['needs']}.",
                f"Not yet presented: {len(x['payments_not_yet_presented'])} payments {rs(x['payments_not_yet_presented_rupees'])}; in transit: {len(x['receipts_in_transit'])} receipts {rs(x['receipts_in_transit_rupees'])}."]
    c = x["counts"]
    L = [f"Balance as per cash book: {rs(x['balance_per_books_rupees'])}. Balance as per bank statement: {rs(x['balance_per_bank_rupees'])}.",
         f"{c['matched']} entries match" + (f" ({c['matched_without_reference']} of them only by amount and date, no reference: please confirm)" if c["matched_without_reference"] else "") + "."]
    if x["payments_not_yet_presented"]:
        L.append(f"Payments in the books not yet presented to the bank (cheques issued): {len(x['payments_not_yet_presented'])}, {rs(x['payments_not_yet_presented_rupees'])} (deduct from the bank balance).")
    if x["receipts_in_transit"]:
        L.append(f"Receipts in the books not yet credited by the bank (deposits in transit): {len(x['receipts_in_transit'])}, {rs(x['receipts_in_transit_rupees'])} (add to the bank balance).")
    if x["bank_debits_not_in_books"]:
        L.append(f"Bank debits not in the books (charges etc.): {len(x['bank_debits_not_in_books'])}, {rs(x['bank_debits_not_in_books_rupees'])}: " +
                 "; ".join(f"{d['narration'] or d['ref']} {rs(d['amount_rupees'])}" for d in x["bank_debits_not_in_books"][:6]) + ".")
    if x["bank_credits_not_in_books"]:
        L.append(f"Bank credits not in the books (interest, direct credits): {len(x['bank_credits_not_in_books'])}, {rs(x['bank_credits_not_in_books_rupees'])}: " +
                 "; ".join(f"{d['narration'] or d['ref']} {rs(d['amount_rupees'])}" for d in x["bank_credits_not_in_books"][:6]) + ".")
    for m in x["amount_recorded_differently"]:
        L.append(f"Recorded differently: {m['direction']} ref {m['ref']} ({m['narration']}) is {rs(m['in_books_rupees'])} in the books but {rs(m['in_bank_rupees'])} in the bank"
                 + (" (two digits swapped)" if m["looks_like_transposed_digits"] else "") + ".")
    if x["entered_twice_in_books"]:
        L.append("Entered twice in the books: " + "; ".join(f"{d['narration']} ref {d['ref']} {rs(d['amount_rupees'])}" for d in x["entered_twice_in_books"]) + " (reverse one).")
    L.append(f"Corrected balance as per books: {rs(x['corrected_balance_per_books_rupees'])}. Adjusted balance as per bank: {rs(x['adjusted_balance_per_bank_rupees'])}. "
             + ("They agree: the statement reconciles." if x["reconciles"] else f"They differ by {rs(abs(x['difference_rupees']))}: something is still unexplained, look at the lists."))
    return L


def blocks(x: dict) -> list[dict]:
    money = lambda cols: [{"label": c, "kind": "money", "align": "right"} if c.endswith("(Rs)") else {"label": c} for c in cols]
    b = [{"type": "stats", "items": [{"label": "Per cash book", "value": rs(x["balance_per_books_rupees"] or 0)}, {"label": "Per bank", "value": rs(x["balance_per_bank_rupees"] or 0)},
                                     {"label": "Reconciled", "value": rs(x["adjusted_balance_per_bank_rupees"] or 0), "tone": "good" if x["reconciles"] else "bad"}]}]
    rows = [["Balance as per bank statement", x["balance_per_bank_rupees"]], ["Add: receipts in the books not yet credited", x["receipts_in_transit_rupees"]],
            ["Less: payments in the books not yet presented", -x["payments_not_yet_presented_rupees"]], ["Adjusted balance as per bank", x["adjusted_balance_per_bank_rupees"]],
            ["Balance as per cash book", x["balance_per_books_rupees"]], ["Add: bank credits not in the books", x["bank_credits_not_in_books_rupees"]],
            ["Less: bank debits not in the books", -x["bank_debits_not_in_books_rupees"]], ["Other corrections (recorded differently, entered twice)",
             round(x["books_corrections_rupees"] - x["bank_credits_not_in_books_rupees"] + x["bank_debits_not_in_books_rupees"], 2)],
            ["Corrected balance as per cash book", x["corrected_balance_per_books_rupees"]]]
    b.append({"type": "table", "caption": "Bank reconciliation statement", "columns": money(["Item", "Amount (Rs)"]), "rows": rows})
    for key, cap in (("payments_not_yet_presented", "Payments not yet presented"), ("receipts_in_transit", "Receipts in transit"),
                     ("bank_debits_not_in_books", "Bank debits not in the books"), ("bank_credits_not_in_books", "Bank credits not in the books"),
                     ("entered_twice_in_books", "Entered twice in the books")):
        if x[key]:
            b.append({"type": "table", "caption": cap, "columns": money(["Date", "Ref", "Narration", "Amount (Rs)"]),
                      "rows": [[d["date"], d["ref"], d["narration"][:40], d["amount_rupees"]] for d in x[key][:40]]})
    if x["amount_recorded_differently"]:
        b.append({"type": "table", "caption": "Recorded at a different amount", "columns": money(["Ref", "Narration", "In books (Rs)", "In bank (Rs)", "Difference (Rs)"]),
                  "rows": [[m["ref"], m["narration"][:30], m["in_books_rupees"], m["in_bank_rupees"], m["difference_rupees"]] for m in x["amount_recorded_differently"]]})
    b.append({"type": "note", "text": "Matched on reference, direction and amount; entries with no reference are matched on amount and date within 10 days and listed for confirmation. "
                                      "Prepared for the owner and the CA to review: nothing in either file was changed."})
    return b


def excel_sheets(x: dict) -> list:
    rows = lambda key: [[d["date"], d["ref"], d["narration"], d["amount_rupees"]] for d in x[key]]
    brs = [["Balance as per bank statement", x["balance_per_bank_rupees"]], ["Add: receipts in transit", x["receipts_in_transit_rupees"]],
           ["Less: payments not yet presented", -x["payments_not_yet_presented_rupees"]], ["Adjusted balance as per bank", x["adjusted_balance_per_bank_rupees"]],
           ["Balance as per cash book", x["balance_per_books_rupees"]], ["Add: bank credits not in books", x["bank_credits_not_in_books_rupees"]],
           ["Less: bank debits not in books", -x["bank_debits_not_in_books_rupees"]], ["Corrected balance as per cash book", x["corrected_balance_per_books_rupees"]],
           ["Reconciles", "Yes" if x["reconciles"] else f"No, differs by {x['difference_rupees']}"]]
    cols = ["Date", "Ref", "Narration", "Amount (Rs)"]
    return [("BRS", ["Item", "Amount (Rs)"], brs), ("Payments not presented", cols, rows("payments_not_yet_presented")), ("Receipts in transit", cols, rows("receipts_in_transit")),
            ("Bank debits not in books", cols, rows("bank_debits_not_in_books")), ("Bank credits not in books", cols, rows("bank_credits_not_in_books")),
            ("Recorded differently", ["Ref", "Narration", "Direction", "In books (Rs)", "In bank (Rs)", "Difference (Rs)"],
             [[m["ref"], m["narration"], m["direction"], m["in_books_rupees"], m["in_bank_rupees"], m["difference_rupees"]] for m in x["amount_recorded_differently"]]),
            ("Entered twice", cols, rows("entered_twice_in_books")), ("Matched without reference", cols + ["Bank date"],
             [[d["date"], d["ref"], d["narration"], d["amount_rupees"], d["bank_date"]] for d in x["matched_without_reference"]])]


async def bank_reconcile(app, ctx, args: dict) -> dict:
    from src.agent import actions
    refs = [args.get("book_file_ref"), args.get("bank_file_ref")]
    if not all(isinstance(r, str) and r for r in refs) or refs[0] == refs[1]:
        return {"error": "give book_file_ref (the cash/bank book from the books or Tally) and bank_file_ref (the bank statement): two different files"}
    as_on = None
    if args.get("as_on"):
        as_on = parse_date(args["as_on"])
        if as_on is None:
            return {"error": "as_on must be a date like 2026-10-31"}
    parsed = []
    for ref, role in zip(refs, ("books", "bank")):
        data, _, name = await actions._fetch(app, ref, ctx)
        p = parse_file(name, data, role)
        if "error" in p:
            return {"error": p["error"], "hint": "ask the owner for the cash/bank book and the bank statement as Excel or CSV"}
        parsed.append(p)
    res = reconcile(parsed[0], parsed[1], as_on)
    res["files"] = {"book": parsed[0]["filename"], "bank": parsed[1]["filename"]}
    res["summary_lines"] = summary_lines(res)
    full = dict(res)
    if not full["needs"] and args.get("report", True):
        out = await ca_reports.deliver(app, ctx, title="Bank reconciliation statement", subtitle=f"{parsed[0]['filename']} against {parsed[1]['filename']}", blocks=blocks(full),
                                       xl_name="Bank_reconciliation.xlsx", sheets=excel_sheets(full), caption="Bank reconciliation statement", send=bool(args.get("send", True)))
        res.update({"files_made": out["files"], "files_sent_to_owner": out["sent"], "files_not_sent": out["not_sent"], "_new_files": out["_new_files"], "file_refs": out["file_refs"]})
    res["next"] = ("Tell the owner the result in their language using summary_lines (the figures are final: quote them, never recompute them or redo this with code). "
                   + ("The statement (PDF) and the full Excel have already been sent. " if res.get("files_sent_to_owner") else "")
                   + ("Ask the owner for what 'needs' says, then call this again. " if full["needs"] else "")
                   + "The books must be corrected by the owner or their accountant for the bank-only items, the differing amount and the double entry: say so, do not do it.")
    return ca_reports.compact(res, ("summary_lines", "next", "files_sent_to_owner", "files_not_sent", "needs", "reconciles", "balance_per_books_rupees",
                                    "balance_per_bank_rupees", "corrected_balance_per_books_rupees", "adjusted_balance_per_bank_rupees", "counts"))
