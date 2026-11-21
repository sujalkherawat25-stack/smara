"""Fictional client books for a chartered accountant's work, built so every correct answer is known by construction.

Each builder returns (files, truth): files is {name: bytes}, truth is what a careful person would get. Errors are planted
on purpose (a missing invoice, a transposed figure, a wrong tax head) and counted while they are planted, so the answer key
never depends on the agent, and `verify_truth.py` re-derives the main figures from the files with pandas as a second check.
Everything is made up: names, GSTINs (valid checksum, no real registrations) and amounts.
"""
import io
import random
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

CH = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
STATES = {"07": "Delhi", "09": "Uttar Pradesh", "27": "Maharashtra", "29": "Karnataka", "24": "Gujarat", "33": "Tamil Nadu",
          "06": "Haryana", "08": "Rajasthan", "03": "Punjab", "19": "West Bengal"}
FIRST = ["Shree", "Sri", "Maa", "New", "Royal", "Global", "United", "National", "Modern", "Classic", "Star", "Sunrise", "Metro",
         "Prime", "Apex", "Vijay", "Ganga", "Kaveri", "Narmada", "Om"]
SECOND = ["Hardware", "Electricals", "Steel", "Plastics", "Agencies", "Enterprises", "Traders", "Industries", "Distributors", "Pipes",
          "Paints", "Fasteners", "Tools", "Cables", "Packaging", "Chemicals", "Textiles", "Foods", "Logistics", "Sanitary"]
CLIENT = ("Kapoor Hardware Traders", "07", "AABFK4821M")       # the CA's client: Delhi


def d2(x) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def rupee(x) -> int:
    return int(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def gstin(state: str, pan: str) -> str:
    body = f"{state}{pan}1Z"
    total = 0
    for i, ch in enumerate(body):
        v = CH.index(ch) * (1 if i % 2 == 0 else 2)
        total += v // 36 + v % 36
    return body + CH[(36 - total % 36) % 36]


def pan(r: random.Random) -> str:
    L = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    return "".join(r.choice(L) for _ in range(3)) + r.choice("CFPH") + r.choice(L) + f"{r.randint(1000, 9999)}" + r.choice(L)


def names(r: random.Random, n: int) -> list[str]:
    out = set()
    while len(out) < n:
        out.add(f"{r.choice(FIRST)} {r.choice(SECOND)}")
    return sorted(out)


def parties(r: random.Random, n: int, states=None) -> list[dict]:
    st = list(states or STATES)
    return [{"name": nm, "state": (s := r.choice(st)), "gstin": gstin(s, pan(r))} for nm in names(r, n)]


def book(sheets: dict, widths=None, bold_header_row=None) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for title, rows in sheets.items():
        ws = wb.create_sheet(title)
        for row in rows:
            ws.append(row)
        if isinstance(bold_header_row, dict) and title in bold_header_row:
            hr = bold_header_row[title]
            for c in ws[hr]:
                c.font = Font(bold=True)
                c.fill = PatternFill("solid", fgColor="DDEBF7")
                c.alignment = Alignment(wrap_text=True)
            ws.freeze_panes = ws.cell(row=hr + 1, column=1)
        for i, w in enumerate((widths or {}).get(title, ()), start=1):
            ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def split_tax(taxable: Decimal, rate: int, intra: bool):
    t = d2(taxable * rate / 100)
    if intra:
        h = d2(t / 2)
        return h, h, Decimal("0")
    return Decimal("0"), Decimal("0"), t


# ------------------------------------------------------------------------------------------ 1. purchase register vs GSTR-2B
BLOCKED = ["Motor car repairs - director's vehicle", "Diwali gift hampers for customers", "Staff canteen - food and beverages",
           "Cab hire - director's personal use", "Membership club fee"]
ITC_NO_REASON = ["Supplier GSTR-3B not filed", "Supplier registration cancelled", "Return filed after due date"]


def messy(r: random.Random, no: str) -> str:
    """The same supplier invoice number as a clerk might type it into Tally."""
    pre, num = no.split("/")
    return r.choice([f"{pre}-{int(num)}", f"{pre.lower()}/{num}", f"{pre}{num}", f"{pre} {num}", f"{pre}/{int(num)}", f"{pre}-{num}"])


def gst_pair(seed=11):
    r = random.Random(seed)
    sup = parties(r, 30)
    rows = []
    for i in range(380):
        s = r.choice(sup)
        intra = s["state"] == CLIENT[1]
        taxable = d2(r.randrange(2000, 250000) + r.choice([0, 0.25, 0.5, 0.75]))
        rate = r.choice([5, 12, 18, 18, 18, 28])
        c, sg, ig = split_tax(taxable, rate, intra)
        rows.append({"sup": s, "no": f"{s['name'].split()[0][:3].upper()}/{i + 1:04d}", "date": date(2026, 10, r.randint(1, 31)),
                     "taxable": taxable, "c": c, "s": sg, "i": ig, "narr": r.choice(["Goods purchased", "Stock purchase", "Raw material", "Trading goods"]),
                     "kind": "ok", "ITC": "Yes", "reason": ""})
    pick = list(range(len(rows)))
    r.shuffle(pick)
    take = lambda n: [pick.pop() for _ in range(n)]
    kinds = {"missing_in_2b": take(14), "mismatch": take(8), "numfmt": take(11), "duplicate": take(4), "blocked": take(5), "itc_no": take(6)}
    for k, idxs in kinds.items():
        for j in idxs:
            rows[j]["kind"] = k
    for j in kinds["blocked"]:
        rows[j]["narr"] = BLOCKED[kinds["blocked"].index(j) % len(BLOCKED)]
    for j in kinds["itc_no"]:
        rows[j]["ITC"], rows[j]["reason"] = "No", r.choice(ITC_NO_REASON)
    for j in kinds["numfmt"]:
        rows[j]["books_no"] = messy(r, rows[j]["no"])
    # value mismatches: the supplier filed a lower taxable value than the books hold
    for j in kinds["mismatch"]:
        x = rows[j]
        cut = d2(r.choice([2000, 5000, 7500, 10000, 15000]) + 0.0)
        x["b_taxable"] = x["taxable"]
        x["taxable"] = max(d2(1000), x["taxable"] - cut)
        rate = round((x["c"] + x["s"] + x["i"]) / x["b_taxable"] * 100)
        rate = min([5, 12, 18, 28], key=lambda q: abs(q - rate))
        x["c"], x["s"], x["i"] = split_tax(x["taxable"], rate, x["sup"]["state"] == CLIENT[1])
        x["b_c"], x["b_s"], x["b_i"] = split_tax(x["b_taxable"], rate, x["sup"]["state"] == CLIENT[1])
    only = []
    for i in range(9):
        s = r.choice(sup)
        taxable = d2(r.randrange(3000, 90000))
        c, sg, ig = split_tax(taxable, 18, s["state"] == CLIENT[1])
        only.append({"sup": s, "no": f"{s['name'].split()[0][:3].upper()}/{900 + i:04d}", "date": date(2026, 10, r.randint(1, 31)),
                     "taxable": taxable, "c": c, "s": sg, "i": ig})
    tax = lambda x, p="": x.get(p + "c", Decimal(0)) + x.get(p + "s", Decimal(0)) + x.get(p + "i", Decimal(0))

    books = [["Kapoor Hardware Traders"], ["Purchase Register - October 2026"], [f"GSTIN {gstin(CLIENT[1], CLIENT[2])}"], [],
             ["Voucher Date", "Supplier Name", "Supplier GSTIN", "Invoice No", "Invoice Date", "Taxable Value", "CGST", "SGST", "IGST",
              "Invoice Value", "Narration"]]
    b2b = [["Goods and Services Tax - GSTR-2B"], ["Financial Year 2026-27"], ["Tax Period October"], ["GSTIN " + gstin(CLIENT[1], CLIENT[2])], [],
           ["GSTIN of supplier", "Trade/Legal name", "Invoice number", "Invoice Date", "Invoice Value(Rs)", "Taxable Value (Rs)",
            "Integrated Tax(Rs)", "Central Tax(Rs)", "State/UT Tax(Rs)", "ITC Availability", "Reason"]]
    for x in rows:
        bt = x.get("b_taxable", x["taxable"])
        bc, bs, bi = x.get("b_c", x["c"]), x.get("b_s", x["s"]), x.get("b_i", x["i"])
        brow = [x["date"] + timedelta(days=r.randint(0, 3)), x["sup"]["name"], x["sup"]["gstin"], x.get("books_no", x["no"]), x["date"],
                float(bt), float(bc), float(bs), float(bi), float(bt + bc + bs + bi), x["narr"]]
        if x["kind"] != "missing_in_2b" or True:
            books.append(brow)
        if x["kind"] == "duplicate":
            books.append([brow[0] + timedelta(days=1)] + brow[1:])
        if x["kind"] != "missing_in_2b":
            b2b.append([x["sup"]["gstin"], x["sup"]["name"], x["no"], x["date"], float(x["taxable"] + x["c"] + x["s"] + x["i"]), float(x["taxable"]),
                        float(x["i"]), float(x["c"]), float(x["s"]), x["ITC"], x["reason"]])
    for x in only:
        b2b.append([x["sup"]["gstin"], x["sup"]["name"], x["no"], x["date"], float(x["taxable"] + x["c"] + x["s"] + x["i"]), float(x["taxable"]),
                    float(x["i"]), float(x["c"]), float(x["s"]), "Yes", ""])
    r.shuffle(books[5:])
    body = books[5:]
    r.shuffle(body)
    books[5:] = body
    body = b2b[6:]
    r.shuffle(body)
    b2b[6:] = body
    by = lambda k: [rows[j] for j in kinds[k]]
    claim = sum(tax(x) for x in rows if x["kind"] in ("ok", "numfmt", "mismatch", "duplicate"))
    truth = {
        "books_rows": len(books) - 5, "b2b_rows": len(b2b) - 6,
        "missing_in_2b": {"n": 14, "itc": float(sum(tax(x, "b_") if "b_c" in x else tax(x) for x in by("missing_in_2b"))),
                          "invoices": [x["no"] for x in by("missing_in_2b")]},
        "only_in_2b": {"n": 9, "itc": float(sum(tax(x) for x in only)), "invoices": [x["no"] for x in only]},
        "mismatch": {"n": 8, "books_tax": float(sum(tax(x, "b_") for x in by("mismatch"))), "gstr2b_tax": float(sum(tax(x) for x in by("mismatch"))),
                     "invoices": [x["no"] for x in by("mismatch")]},
        "numfmt": {"n": 11, "invoices": [x["no"] for x in by("numfmt")], "books_spelling": [x["books_no"] for x in by("numfmt")]},
        "duplicate": {"n": 4, "invoices": [x["no"] for x in by("duplicate")]},
        "blocked": {"n": 5, "itc": float(sum(tax(x) for x in by("blocked"))), "invoices": [x["no"] for x in by("blocked")]},
        "itc_no": {"n": 6, "itc": float(sum(tax(x) for x in by("itc_no"))), "invoices": [x["no"] for x in by("itc_no")]},
        # claimable in this month's GSTR-3B = the 2B tax of every invoice that is in both files, is not blocked and which 2B allows;
        # a double-booked invoice counts once, a value mismatch counts at the lower (GSTR-2B) figure
        "claimable_itc": float(claim),
    }
    return {"purchase_register_oct.xlsx": book({"Purchase Register": books}, bold_header_row={"Purchase Register": 5}),
            "GSTR2B_102026.xlsx": book({"Read me": [["GSTR-2B"], ["This statement lists invoices reported by your suppliers"]], "B2B": b2b},
                                       bold_header_row={"B2B": 6})}, truth


# ---------------------------------------------------------------------------------------------------------- 2. sales register audit
def sales_register(seed=21):
    r = random.Random(seed)
    cust = parties(r, 60)
    rows, k = [], 0
    for i in range(1150):
        c = r.choice(cust)
        b2c = r.random() < 0.18
        place = c["state"] if not b2c else r.choice(["07", "07", "07", "09", "06"])
        intra = place == CLIENT[1]
        taxable = d2(r.randrange(900, 180000) + r.choice([0, 0.5]))
        rate = r.choice([5, 12, 18, 18, 18, 28])
        cg, sg, ig = split_tax(taxable, rate, intra)
        rows.append({"no": f"KH/26-27/{i + 1:05d}", "date": date(2026, 7, 1) + timedelta(days=r.randrange(0, 92)),
                     "cust": c["name"] if not b2c else "Retail customer", "gstin": "" if b2c else c["gstin"], "type": "B2C" if b2c else "B2B",
                     "place": STATES[place], "taxable": taxable, "c": cg, "s": sg, "i": ig, "rate": rate, "flag": ""})
    idx = list(range(len(rows)))
    r.shuffle(idx)
    take = lambda n: [idx.pop() for _ in range(n)]
    b2b = [j for j in idx if rows[j]["type"] == "B2B"]
    intra_rows = [j for j in b2b if rows[j]["place"] == "Delhi"]
    inter_rows = [j for j in b2b if rows[j]["place"] != "Delhi"]
    r.shuffle(intra_rows)
    r.shuffle(inter_rows)
    wrong_igst = intra_rows[:10]
    wrong_cgst = inter_rows[:7]
    used = set(wrong_igst + wrong_cgst)
    rest = [j for j in b2b if j not in used]
    r.shuffle(rest)
    total_bad, no_gstin, bad_gstin, rate_bad = rest[:6], rest[6:15], rest[15:19], rest[19:24]
    for j in wrong_igst:
        x = rows[j]
        x["c"], x["s"], x["i"] = Decimal(0), Decimal(0), d2(x["taxable"] * x["rate"] / 100)
    for j in wrong_cgst:
        x = rows[j]
        h = d2(x["taxable"] * x["rate"] / 200)
        x["c"], x["s"], x["i"] = h, h, Decimal(0)
    for j in no_gstin:
        rows[j]["gstin"] = ""
    for j in bad_gstin:
        g = rows[j]["gstin"]
        rows[j]["gstin"] = g[:-1] + CH[(CH.index(g[-1]) + 7) % 36]
    for j in rate_bad:        # tax charged at a rate lower than the rate the invoice states
        x = rows[j]
        x["i"], x["c"], x["s"] = (d2(x["taxable"] * 12 / 100), Decimal(0), Decimal(0)) if x["place"] != "Delhi" else (Decimal(0), d2(x["taxable"] * 6 / 100), d2(x["taxable"] * 6 / 100))
        x["rate"] = 18
    total_wrong = {}
    for j in total_bad:
        total_wrong[j] = r.choice([-500, 1000, -1800, 2500, 360, -90])
    notes = []
    planted_all = set(wrong_igst + wrong_cgst + total_bad + no_gstin + bad_gstin + rate_bad)
    clean = [j for j in range(len(rows)) if j not in planted_all]
    r.shuffle(clean)
    for j in clean[:3]:         # credit notes: negative, correct, NOT errors
        x = rows[j]
        for f in ("taxable", "c", "s", "i"):
            x[f] = -x[f]
        x["no"] = x["no"].replace("KH/", "CN/")
        notes.append(x["no"])
    header = ["Invoice No", "Invoice Date", "Customer Name", "Customer GSTIN", "Type", "Place of Supply", "Taxable Value", "CGST", "SGST", "IGST",
              "Invoice Value", "GST Rate %"]
    out = [["Kapoor Hardware Traders"], ["Sales Register - Q2 FY 2026-27 (Jul-Sep 2026)"], [], header]
    dup_rows = []
    for j, x in enumerate(rows):
        val = x["taxable"] + x["c"] + x["s"] + x["i"] + total_wrong.get(j, 0)
        line = [x["no"], x["date"], x["cust"], x["gstin"], x["type"], x["place"], float(x["taxable"]), float(x["c"]), float(x["s"]), float(x["i"]),
                float(val), x["rate"]]
        out.append(line)
    dups = clean[3:8]                                  # exact copies of clean rows
    for j in dups:
        out.append(list(out[4 + j]))
    body = out[4:]
    r.shuffle(body)
    body.sort(key=lambda L: (L[1], L[0]))
    out[4:] = body
    inv = lambda js: sorted(rows[j]["no"] for j in js)
    truth = {"rows": len(out) - 4, "wrong_head_igst_on_intra": inv(wrong_igst), "wrong_head_cgst_sgst_on_inter": inv(wrong_cgst),
             "total_mismatch": inv(total_bad), "duplicates": inv(dups), "b2b_missing_gstin": inv(no_gstin), "invalid_gstin": inv(bad_gstin),
             "rate_mismatch": inv(rate_bad), "credit_notes_not_errors": sorted(notes)}
    # the same invoice can be planted twice only if a duplicated row is also a planted error; keep the key honest
    return {"sales_register_q2.xlsx": book({"Sales": out}, bold_header_row={"Sales": 4})}, truth


# ------------------------------------------------------------------------------------------------------------- 3. bank reconciliation
def bank_recon(seed=31):
    r = random.Random(seed)
    opening = d2(500000)
    ledger, bank = [], []
    for i in range(240):
        dt = date(2026, 10, 1) + timedelta(days=r.randrange(0, 29))            # up to 29 Oct: cleared items
        amt = d2(r.randrange(1500, 90000))
        kind = r.choice(["R", "P"])
        who = r.choice(names(r, 12))
        ref = f"{r.randrange(100000, 999999)}"
        ledger.append({"dt": dt, "who": who, "ref": ref, "dr": amt if kind == "R" else d2(0), "cr": amt if kind == "P" else d2(0), "t": "ok"})
    # planted
    unc = [{"dt": date(2026, 10, r.randint(24, 30)), "who": r.choice(names(r, 12)), "ref": f"{r.randrange(100000, 999999)}", "dr": d2(0),
            "cr": d2(r.randrange(4000, 60000)), "t": "uncleared"} for _ in range(6)]
    dit = [{"dt": date(2026, 10, r.randint(29, 31)), "who": r.choice(names(r, 12)), "ref": f"{r.randrange(100000, 999999)}", "dr": d2(r.randrange(8000, 70000)),
            "cr": d2(0), "t": "transit"} for _ in range(4)]
    trans_true, trans_booked = d2(38610), d2(38160)
    tr = {"dt": date(2026, 10, 12), "who": "Ganga Traders", "ref": "482913", "dr": trans_booked, "cr": d2(0), "t": "transposed"}
    dupe_src = next(x for x in ledger if x["cr"] > 0 and x["t"] == "ok")
    dupe = {**dupe_src, "t": "duplicate"}
    ledger += unc + dit + [tr, dupe]
    charges = [(date(2026, 10, 14), "SMS charges", d2(177.0), "W"), (date(2026, 10, 31), "Cheque book issue charges", d2(236.0), "W"),
               (date(2026, 10, 31), "Quarterly AMC charges", d2(590.0), "W")]
    interest = (date(2026, 10, 31), "Interest on savings account", d2(1843.0), "D")
    direct = (date(2026, 10, 21), "NEFT-Delta Associates-direct credit", d2(26500.0), "D")
    items = []
    for x in ledger:
        if x["t"] in ("uncleared", "duplicate"):
            if x["t"] == "duplicate":
                pass
            continue
        if x["t"] == "transit":
            continue
        amt = x["dr"] if x["dr"] > 0 else x["cr"]
        if x["t"] == "transposed":
            amt = trans_true
        items.append((min(x["dt"] + timedelta(days=r.randint(0, 2)), date(2026, 10, 31)), x["who"], x["ref"], x["cr"] if x["cr"] > 0 and x["t"] != "transposed" else d2(0),
                      amt if (x["dr"] > 0 or x["t"] == "transposed") else d2(0)))
    for dt, narr, amt, k in charges:
        items.append((dt, narr, "", amt, d2(0)))
    items.append((interest[0], interest[1], "", d2(0), interest[2]))
    items.append((direct[0], direct[1], "", d2(0), direct[2]))
    items.sort(key=lambda t: t[0])
    bal = opening
    bank_rows = []
    for dt, narr, ref, w, d in items:
        bal = bal - w + d
        bank_rows.append([dt, narr, ref, float(w) if w else None, float(d) if d else None, float(bal)])
    book_rows, b = [], opening
    ledger.sort(key=lambda x: x["dt"])
    for x in ledger:
        b = b + x["dr"] - x["cr"]
        book_rows.append([x["dt"], x["who"], x["ref"], float(x["dr"]) if x["dr"] else None, float(x["cr"]) if x["cr"] else None])
    books_close = opening + sum(x["dr"] - x["cr"] for x in ledger)
    bank_close = bal
    unc_t, dit_t = sum(x["cr"] for x in unc), sum(x["dr"] for x in dit)
    chg_t = sum(c[2] for c in charges)
    # books after the adjustments only the books can make
    corrected_books = books_close + (trans_true - trans_booked) + dupe["cr"] - chg_t + interest[2] + direct[2]
    bank_side = bank_close + dit_t - unc_t
    assert corrected_books == bank_side, (corrected_books, bank_side)
    truth = {"books_balance": float(books_close), "bank_balance": float(bank_close), "uncleared_cheques": float(unc_t), "uncleared_n": 6,
             "deposits_in_transit": float(dit_t), "deposits_n": 4, "bank_charges": float(chg_t), "interest": float(interest[2]),
             "direct_credit": float(direct[2]), "transposition": {"booked": 38160.0, "bank": 38610.0, "difference": 450.0, "ref": "482913"},
             "duplicate_payment": float(dupe["cr"]), "duplicate_ref": dupe["ref"], "reconciled_balance": float(corrected_books)}
    ledger_hdr = [["Kapoor Hardware Traders"], ["Bank Book - HDFC Current A/c (fictional) - October 2026"], [f"Opening balance as on 01-10-2026: {float(opening):,.2f} Dr"],
                  [], ["Date", "Particulars", "Voucher / Cheque No", "Receipt (Dr)", "Payment (Cr)"]]
    bank_hdr = [["Account Statement - Current Account"], ["Period 01-10-2026 to 31-10-2026"], [f"Opening Balance {float(opening):,.2f}"], [],
                ["Txn Date", "Description", "Chq/Ref No", "Withdrawal", "Deposit", "Closing Balance"]]
    return {"bank_book_oct.xlsx": book({"Bank Book": ledger_hdr + book_rows}, bold_header_row={"Bank Book": 5}),
            "bank_statement_oct.xlsx": book({"Statement": bank_hdr + bank_rows}, bold_header_row={"Statement": 5})}, truth


# ---------------------------------------------------------------------------------------------------------------- 4. trial balance
def trial_balance(seed=41):
    r = random.Random(seed)
    accounts = [("Capital", "Cr"), ("Sales", "Cr"), ("Purchases", "Dr"), ("Rent", "Dr"), ("Salaries", "Dr"), ("Electricity", "Dr"), ("Telephone", "Dr"),
                ("Freight Inward", "Dr"), ("Bank - HDFC", "Dr"), ("Cash in Hand", "Dr"), ("Sundry Debtors", "Dr"), ("Sundry Creditors", "Cr"),
                ("Furniture", "Dr"), ("Computers", "Dr"), ("Loan from Director", "Cr"), ("Interest Paid", "Dr"), ("Repairs", "Dr"),
                ("Printing and Stationery", "Dr"), ("Travelling", "Dr"), ("Discount Received", "Cr"), ("GST Input", "Dr"), ("GST Output", "Cr")]
    names_ = [a for a, _ in accounts]
    lines, vno = [], 0
    for _ in range(300):
        vno += 1
        k = r.choice([2, 2, 3])
        amt = r.randrange(500, 80000)
        picks = r.sample(names_, k)
        dr_acc, cr_accs = picks[0], picks[1:]
        parts = [rupee(amt * w) for w in ([1] if k == 2 else [0.6, 0.4])]
        total = sum(parts)
        d = date(2026, 4, 1) + timedelta(days=r.randrange(0, 200))
        lines.append((f"V{vno:04d}", d, dr_acc, total, 0))
        for a, p in zip(cr_accs, parts):
            lines.append((f"V{vno:04d}", d, a, 0, p))
    bal = {}
    for _, _, a, dr, cr in lines:
        bal[a] = bal.get(a, 0) + dr - cr
    wrong = "Salaries"
    bal.setdefault(wrong, 0)
    # a debit balance with digits that can be swapped: make Salaries large enough
    while abs(bal[wrong]) < 100000 or str(abs(bal[wrong]))[-3:-2] == str(abs(bal[wrong]))[-2:-1]:
        vno += 1
        lines += [(f"V{vno:04d}", date(2026, 9, 30), wrong, 25317, 0), (f"V{vno:04d}", date(2026, 9, 30), "Cash in Hand", 0, 25317)]
        bal[wrong] += 25317
        bal["Cash in Hand"] = bal.get("Cash in Hand", 0) - 25317
    true_v = bal[wrong]
    s = str(abs(true_v))
    swapped = int(s[:-3] + s[-2] + s[-3] + s[-1])          # two middle digits swapped: the classic transposition (diff divisible by 9)
    tb = [["Kapoor Hardware Traders"], ["Trial Balance as on 31-10-2026"], [], ["Account", "Debit", "Credit"]]
    dr_total = cr_total = 0
    for a in names_:
        v = bal.get(a, 0)
        shown = swapped if a == wrong else v
        dr, cr = (shown, None) if v >= 0 else (None, -shown)
        dr_total += dr or 0
        cr_total += cr or 0
        tb.append([a, dr, cr])
    tb.append(["Total", dr_total, cr_total])
    journal = [["Voucher No", "Date", "Account", "Debit", "Credit"]] + [[v, d, a, dr or None, cr or None] for v, d, a, dr, cr in lines]
    truth = {"account": wrong, "tb_shows": swapped, "journal_total": true_v, "difference": abs(swapped - true_v),
             "dr_total": dr_total, "cr_total": cr_total, "tb_gap": abs(dr_total - cr_total)}
    assert truth["tb_gap"] == truth["difference"] and truth["difference"] % 9 == 0, truth
    return {"trial_balance_oct.xlsx": book({"Trial Balance": tb, "Journal": journal}, bold_header_row={"Trial Balance": 4, "Journal": 1})}, truth


# --------------------------------------------------------------------------------------------------------------------- 5. ageing
ASOF = date(2026, 10, 31)


def ageing(seed=51):
    r = random.Random(seed)
    cust = names(r, 60)
    rows = []
    for i in range(900):
        d = date(2026, 1, 1) + timedelta(days=r.randrange(0, 301))
        rows.append((f"INV/{i + 1:05d}", d, r.choice(cust), rupee(r.randrange(2500, 220000) / 10) * 10))
    rows.sort(key=lambda t: t[1])
    bucket = lambda dd: "0-30" if dd <= 30 else "31-60" if dd <= 60 else "61-90" if dd <= 90 else "90+"
    tot, byp = {}, {}
    for _, d, c, a in rows:
        b = bucket((ASOF - d).days)
        tot[b] = tot.get(b, 0) + a
        byp.setdefault(c, {}).setdefault(b, 0)
        byp[c][b] += a
    top90 = sorted(byp, key=lambda c: -byp[c].get("90+", 0))[:3]
    truth = {"asof": str(ASOF), "buckets": tot, "total": sum(tot.values()), "party_buckets": byp, "top90": top90,
             "top90_amounts": [byp[c].get("90+", 0) for c in top90]}
    hdr = [["Kapoor Hardware Traders"], ["Outstanding Receivables - open invoices"], [], ["Invoice No", "Invoice Date", "Party", "Outstanding Amount"]]
    return {"receivables_open.xlsx": book({"Open Items": hdr + [list(t) for t in rows]}, bold_header_row={"Open Items": 4})}, truth


# ----------------------------------------------------------------------------------------------------------------------- 6. TDS
RATES = {"194C": {"Individual": 1, "Company": 2}, "194J": 10, "194I": 10, "194H": 2}


def tds(seed=61):
    r = random.Random(seed)
    ven = [(n, r.choice(["Individual", "Company"]), None if r.random() < 0.12 else pan(r)) for n in names(r, 45)]
    rows, exp_total, ded_total, short, excess, nopan_short = [], 0, 0, [], [], []
    for i in range(320):
        n, vt, p = r.choice(ven)
        sec = r.choice(["194C", "194C", "194J", "194I", "194H"])
        gross = rupee(r.randrange(8000, 400000) / 100) * 100
        rate = RATES[sec][vt] if sec == "194C" else RATES[sec]
        if sec == "194C" and gross <= 30000:
            exp = 0
        else:
            exp = rupee(gross * (max(rate, 20) if not p else rate) / 100)
        roll = r.random()
        if roll < 0.08 and exp:
            ded = rupee(gross * max(rate - 1, 0) / 100) if rate > 1 else rupee(exp * 0.5)       # deducted at the wrong, lower rate
        elif roll < 0.14 and exp and not p:
            ded = rupee(gross * rate / 100)                                                       # no PAN, deducted at the normal rate
        elif roll < 0.18 and exp == 0:
            ded = rupee(gross * 0.01)                                                             # deducted below the threshold (excess)
        else:
            ded = exp
        rows.append([f"PV/{i + 1:04d}", date(2026, 4, 1) + timedelta(days=r.randrange(0, 200)), n, vt, p or "", sec, gross, ded])
        exp_total += exp
        ded_total += ded
        if ded < exp:
            short.append((f"PV/{i + 1:04d}", exp - ded))
        if ded > exp:
            excess.append((f"PV/{i + 1:04d}", ded - exp))
    truth = {"rows": len(rows), "expected_total": exp_total, "deducted_total": ded_total, "short_n": len(short), "short_total": sum(a for _, a in short),
             "short_vouchers": [v for v, _ in short], "excess_n": len(excess), "excess_total": sum(a for _, a in excess),
             "expected": {row[0]: None for row in rows}}
    exp_map = {}
    for row in rows:
        n, vt, p, sec, gross = row[2], row[3], row[4], row[5], row[6]
        rate = RATES[sec][vt] if sec == "194C" else RATES[sec]
        exp_map[row[0]] = 0 if (sec == "194C" and gross <= 30000) else rupee(gross * (max(rate, 20) if not p else rate) / 100)
    truth["expected"] = exp_map
    hdr = [["Kapoor Hardware Traders"], ["Vendor payments with TDS - Apr to Oct 2026"], [],
           ["Voucher", "Date", "Vendor", "Vendor Type", "PAN", "Section", "Gross Amount", "TDS Deducted"]]
    return {"vendor_payments_tds.xlsx": book({"Payments": hdr + rows}, bold_header_row={"Payments": 4})}, truth


# ------------------------------------------------------------------------------------------------------------------------ 7. payroll
def payroll(seed=71):
    r = random.Random(seed)
    rows, truth_rows = [], {}
    for i in range(140):
        basic = rupee(r.choice([9000, 12000, 14000, 15000, 18000, 22000, 30000, 45000, 60000]) * r.choice([1, 1, 1.1, 0.95]))
        hra = rupee(basic * r.choice([0.4, 0.5]))
        spl = rupee(basic * r.choice([0, 0.2, 0.5, 0.8]))
        lop = r.choice([0, 0, 0, 0, 1, 2, 3, 5])
        eid = f"E{1001 + i}"
        f = Decimal(30 - lop) / Decimal(30)
        e_basic, e_gross = d2(basic * f), d2((basic + hra + spl) * f)
        pf = rupee(min(e_basic, Decimal(15000)) * Decimal("0.12"))
        esi_ok = e_gross <= 21000
        esi = rupee(e_gross * Decimal("0.0075")) if esi_ok else 0
        truth_rows[eid] = {"earned_gross": rupee(e_gross), "pf": pf, "esi": esi, "net": rupee(e_gross) - pf - esi}
        rows.append([eid, f"{r.choice(['Amit', 'Ravi', 'Sunita', 'Pooja', 'Deepak', 'Neha', 'Rahul', 'Kiran'])} {r.choice(['Sharma', 'Verma', 'Gupta', 'Yadav', 'Singh', 'Nair'])}",
                     basic, hra, spl, 30, lop])
    totals = {k: sum(v[k] for v in truth_rows.values()) for k in ("earned_gross", "pf", "esi", "net")}
    hdr = [["Kapoor Hardware Traders"], ["Salary sheet - October 2026"], [], ["Emp ID", "Name", "Basic", "HRA", "Special Allowance", "Days in Month", "LOP Days"]]
    return {"salary_oct.xlsx": book({"Salary": hdr + rows}, bold_header_row={"Salary": 4})}, {"rows": truth_rows, "totals": totals, "n": len(rows)}


# ---------------------------------------------------------------------------------------------------------------- 8. depreciation
DEP_RATES = {"Furniture": 10, "Plant & Machinery": 15, "Computers": 40, "Vehicles": 15, "Building": 10}


def depreciation(seed=81):
    r = random.Random(seed)
    rows, per_class, closing = [], {}, 0
    for i in range(38):
        cls = r.choice(list(DEP_RATES))
        added = r.random() < 0.4
        if added:
            d = date(2026, 4, 1) + timedelta(days=r.randrange(0, 360))
            d = min(d, date(2027, 3, 31))
            cost = rupee(r.randrange(20000, 900000) / 100) * 100
            half = d >= date(2026, 10, 4)
            rate = DEP_RATES[cls] / (2 if half else 1)
            dep = rupee(cost * rate / 100)
            base, kind = cost, "Addition"
        else:
            d = date(2022, 1, 1) + timedelta(days=r.randrange(0, 1200))
            base = rupee(r.randrange(40000, 2500000) / 100) * 100
            dep = rupee(base * DEP_RATES[cls] / 100)
            kind = "Opening WDV"
        rows.append([f"FA-{i + 1:03d}", f"{cls} item {i + 1}", cls, d, kind, base])
        per_class.setdefault(cls, {"dep": 0, "closing": 0})
        per_class[cls]["dep"] += dep
        per_class[cls]["closing"] += base - dep
        closing += base - dep
    hdr = [["Kapoor Hardware Traders"], ["Fixed Asset Register - FY 2026-27"], [], ["Asset No", "Description", "Class", "Date Put To Use", "Type", "Amount (Opening WDV or Cost)"]]
    truth = {"per_class": per_class, "dep_total": sum(v["dep"] for v in per_class.values()), "closing_total": closing, "n": len(rows)}
    return {"asset_register.xlsx": book({"Assets": hdr + rows}, bold_header_row={"Assets": 4})}, truth


# ------------------------------------------------------------------------------------------------------- 9. GSTR-3B liability, 2 sheets
def liability(seed=91):
    r = random.Random(seed)
    sales, purch = [], []
    tot = {"c": Decimal(0), "s": Decimal(0), "i": Decimal(0)}
    itc = {"c": Decimal(0), "s": Decimal(0), "i": Decimal(0)}
    for k in range(260):
        intra = r.random() < 0.55
        taxable = d2(r.randrange(5000, 300000))
        rate = r.choice([5, 12, 18, 18, 28])
        c, s, i = split_tax(taxable, rate, intra)
        sales.append([f"S-{k + 1:04d}", date(2026, 10, r.randint(1, 31)), float(taxable), float(c), float(s), float(i)])
        tot["c"] += c
        tot["s"] += s
        tot["i"] += i
    blocked = Decimal(0)
    for k in range(240):
        intra = r.random() < 0.5
        taxable = d2(r.randrange(5000, 280000))
        rate = r.choice([5, 12, 18, 18, 28])
        c, s, i = split_tax(taxable, rate, intra)
        ok = r.random() > 0.07
        purch.append([f"P-{k + 1:04d}", date(2026, 10, r.randint(1, 31)), float(taxable), float(c), float(s), float(i), "Yes" if ok else "No (blocked 17(5))"])
        if ok:
            itc["c"] += c
            itc["s"] += s
            itc["i"] += i
        else:
            blocked += c + s + i
    # set-off: IGST credit first against IGST, then CGST, then SGST; CGST credit against CGST then IGST; SGST credit against SGST then IGST
    liab, cr = dict(tot), dict(itc)
    pay = {"c": Decimal(0), "s": Decimal(0), "i": Decimal(0)}
    use = lambda frm, to: min(cr[frm], liab[to])
    for to in ("i", "c", "s"):
        u = use("i", to)
        cr["i"] -= u
        liab[to] -= u
    for frm in ("c", "s"):
        for to in (frm, "i"):
            u = use(frm, to)
            cr[frm] -= u
            liab[to] -= u
    pay = {k: liab[k] for k in liab}
    parties_ = [["Party", "GSTIN", "State"]] + [[n, gstin("07", "AAAPA1234A"), "Delhi"] for n in names(r, 5)]
    truth = {"output": {k: float(v) for k, v in tot.items()}, "itc_eligible": {k: float(v) for k, v in itc.items()}, "blocked": float(blocked),
             "cash_payable": {k: float(v) for k, v in pay.items()}, "cash_total": float(sum(pay.values())),
             "carry_forward": {k: float(v) for k, v in cr.items()}}
    sh = {"Sales": [["Invoice", "Date", "Taxable Value", "CGST", "SGST", "IGST"]] + sales,
          "Purchases": [["Invoice", "Date", "Taxable Value", "CGST", "SGST", "IGST", "ITC Eligible"]] + purch}
    return {"client_books_oct.xlsx": book(sh, bold_header_row={"Sales": 1, "Purchases": 1})}, truth


# ------------------------------------------------------------------------------------------------------ 10. party names, dirty
def dirty_parties(seed=101):
    r = random.Random(seed)
    base = names(r, 14)
    variants = {}
    for n in base[:8]:                      # same party, written the way different clerks write it: these ARE the same party
        variants[n] = [n, n.upper(), n.lower(), f"M/s {n}", f"{n}.", f"{n}  ", f"Messrs {n}"][: r.randint(3, 6)]
    for n in base[8:]:
        variants[n] = [n]
    ambiguous = [("Zenith Traders", "Zenith Trading Co"), ("Orion Agencies", "Orion Agency")]     # might or might not be one party: a person must say
    rows, truth = [], {}
    for n, vs in variants.items():
        for j in range(r.randint(8, 22)):
            amt = rupee(r.randrange(2000, 90000) / 10) * 10
            rows.append([f"INV-{len(rows) + 1:04d}", date(2026, 10, 1) + timedelta(days=r.randrange(0, 31)), r.choice(vs), amt])
            truth[n] = truth.get(n, 0) + amt
    amb = {}
    for a, b in ambiguous:
        for nm in (a, b):
            for j in range(5):
                amt = rupee(r.randrange(2000, 40000) / 10) * 10
                rows.append([f"INV-{len(rows) + 1:04d}", date(2026, 10, 1) + timedelta(days=r.randrange(0, 31)), nm, amt])
                amb[nm] = amb.get(nm, 0) + amt
    r.shuffle(rows)
    hdr = [["Sales ledger - October 2026"], [], ["Invoice", "Date", "Party", "Amount"]]
    return {"sales_ledger_oct.xlsx": book({"Ledger": hdr + rows}, bold_header_row={"Ledger": 3})}, {"merged": truth, "ambiguous": amb, "rows": len(rows)}
