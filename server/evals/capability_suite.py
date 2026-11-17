"""Capability suite: what can a hosted Smara really do for an Indian SME, wholesaler or CA, on realistic files?

    python -m evals.capability_suite --db postgresql://.../smara_eval --model glm5.3-flash --sandbox container --jobs /jobs [--only a,b]
    CAP_OUT=/out  saves every reply and every file Smara sent, per task, for the report and the demo video.

Same harness as beta_stress (real model, real agent loop and sandbox, scratch Postgres, fake chat channel). Files are
realistic (Tally-style headers, totals rows, Indian number formats, 40-600 rows) and every answer is scored against ground
truth computed here in Python, never against wording. Workflows were picked from what SMEs and CAs say they do by hand
every month: receivables ageing, bank and GST reconciliation, TDS/43B(h), sales and margin analysis, stock, price lists.
Out of scope for the MVP (not tested): messages to customers from Smara's own number.
"""
import asyncio
import io
import os
import random
import re
import selectors
from datetime import timedelta

from evals import agent_harness as h
from evals import demo_scenarios as ds
from evals.agent_harness import OWNER, Task, owner, q
from src.ingest.importer import import_rows
from src.ingest.sheet_mapper import SheetRow

T = h.T
R = random.Random(20261117)
OUT = os.environ.get("CAP_OUT", "")


# ---------------------------------------------------------------------------------------------- helpers
def inr(n: float) -> str:
    s = f"{int(round(n)):d}"
    head, tail = s[:-3], s[-3:]
    head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
    return (head + "," if head else "") + tail


def dmy(d) -> str:
    return d.strftime("%d-%m-%Y")


def xlsx(rows) -> bytes:
    return h._xlsx(rows)


_NUM = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)(?:\s*(lakhs?|lacs?|l|cr|crores?|k)\b)?", re.I)
_MULT = {"": 1, "k": 1e3, "l": 1e5, "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5, "cr": 1e7, "crore": 1e7, "crores": 1e7}


def has_amt(text: str, rupees: float, tol: float = 1.0) -> bool:
    """True if the text holds this amount: plain (any Indian or western grouping, tolerance 1 rupee) or as '4.25 lakh' / '1.2 cr'
    (rounded to two decimals of that unit)."""
    r = float(rupees)
    for m in _NUM.finditer(text.replace(",", "")):
        mult = _MULT[(m.group(2) or "").lower()]
        v = float(m.group(1)) * mult
        if abs(v - r) <= (tol if mult == 1 else mult * 0.006):
            return True
    return False


def file_text(name: str, data: bytes) -> str:
    low = name.lower()
    try:
        if low.endswith(".xlsx"):
            from openpyxl import load_workbook
            wb = load_workbook(io.BytesIO(data))
            return " ".join(str(c) for ws in wb for row in ws.iter_rows(values_only=True) for c in row if c is not None)
        if low.endswith(".pdf"):
            from pypdf import PdfReader
            return " ".join(pg.extract_text() or "" for pg in PdfReader(io.BytesIO(data)).pages)
    except Exception as e:                                           # a broken file is a failed task, not a crashed run
        return f"[unreadable {name}: {e}]"
    return data.decode("utf-8", "replace")


def corpus(env) -> str:
    """Everything the owner received: the chat replies and the text inside every file Smara sent."""
    return "\n".join([ds.alltext(env)] + [file_text(n, d) for n, _, d in env.wa.uploads])


def rows_of(data: bytes) -> list[list]:
    from openpyxl import load_workbook
    ws = load_workbook(io.BytesIO(data), data_only=True).worksheets[0]
    return [list(r) for r in ws.iter_rows(values_only=True)]


def save(env) -> None:
    if not OUT:
        return
    os.makedirs(OUT, exist_ok=True)
    name = ds.CUR["name"]
    open(f"{OUT}/{name}.txt", "w", encoding="utf-8").write(ds.alltext(env))
    for fn, _, data in env.wa.uploads:
        open(f"{OUT}/{name}__{fn}", "wb").write(data)


def cap(fn):
    async def inner(env):
        try:
            return await fn(env)
        finally:
            save(env)
    return inner


def event(*sends):
    """An owner event: one or more messages (text, or file + caption) in order; replies and files of THIS task only."""
    async def run(env):
        from src.ingest.dispatch import dispatch
        env.wa.uploads.clear()
        for i, s in enumerate(sends):
            await dispatch(env.app, owner(s[0], f"{ds.CUR['name']}-{i}", s[1] if len(s) > 1 else None))
    return run


# ---------------------------------------------------------------------------------------------- data: receivables
PARTIES = ["Agarwal Traders", "Bansal Agencies", "Chawla Kirana", "Desai Stores", "Elite Distributors", "Fatima General Store",
           "Gandhi Sons", "Hari Om Traders", "Iyer Provisions", "Jain Brothers", "Kapoor & Co", "Lakshmi Stores", "Mehra Enterprises",
           "Nanda Traders", "Oswal Agencies", "Patel Wholesale", "Qureshi Brothers", "Rathi Traders", "Saini Stores",
           "Thakkar Agencies", "Uppal Traders", "Verma Kirana", "Wadhwa Sons", "Yadav Dairy", "Zaveri Jewellers", "Bhatia & Co",
           "Chopra Sons", "Dutta Agencies"]
PHONE = {p: f"9198{i:02d}{R.randint(100000, 999999)}" for i, p in enumerate(PARTIES)}


def build_receivables(n=172):
    rows = []
    for i in range(n):
        party = PARTIES[min(int(R.paretovariate(1.15)) - 1, len(PARTIES) - 1)]
        bill = T - timedelta(days=R.randint(1, 170))
        credit = R.choice([15, 30, 30, 30, 45, 60])
        amt = min(400_000, max(1500, int(R.lognormvariate(10.2, 0.8) // 10 * 10)))
        rows.append((bill, f"PW/2627/{i + 1:04d}", party, amt, bill + timedelta(days=credit)))
    rows.sort(key=lambda r: r[0])
    return rows


RECV = build_receivables()
RECV_TOTAL = sum(r[3] for r in RECV)
BUCKETS = ("Not due", "1-30", "31-60", "61-90", "90+")


def bucket_of(due) -> str:
    o = (T - due).days
    return "Not due" if o <= 0 else "1-30" if o <= 30 else "31-60" if o <= 60 else "61-90" if o <= 90 else "90+"


def by_party(rows, pred=lambda r: True):
    out = {}
    for r in rows:
        if pred(r):
            out[r[2]] = out.get(r[2], 0) + r[3]
    return out


def tally_receivables(rows, overdue_col=True, title="Pragati Wholesale Traders") -> bytes:
    head = ["Date", "Ref. No.", "Party's Name", "Pending Amount", "Due on"] + (["Overdue by days"] if overdue_col else [])
    body = [[dmy(b), ref, p, f"{inr(a)}.00 Dr", dmy(due)] + ([max(0, (T - due).days)] if overdue_col else []) for b, ref, p, a, due in rows]
    total = [None, None, "Grand Total", f"{inr(sum(r[3] for r in rows))}.00 Dr", None] + ([None] if overdue_col else [])
    return xlsx([[title], [f"Bills Receivable as on {T:%d-%b-%Y}"], [], head, *body, total])


RECV_FILE = tally_receivables(RECV)
RECV_FILE_NO_AGE = tally_receivables(RECV, overdue_col=False)
h.FILES["C01"] = ("Tally_Bills_Receivable.xlsx", RECV_FILE)
h.FILES["C02"] = ("Tally_Bills_Receivable_plain.xlsx", RECV_FILE_NO_AGE)


async def preload_receivables(pool, tenant_id):
    await import_rows(pool, tenant_id, [SheetRow(p, PHONE[p], ref, a * 100, due) for b, ref, p, a, due in RECV])


# last week's snapshot: 12 bills cleared since, 3 part-paid, and the new ones missing
OLD_EXTRA = [(T - timedelta(days=R.randint(30, 60)), f"PW/2627/X{i:03d}", R.choice(PARTIES), R.randint(8, 60) * 1000, T - timedelta(days=R.randint(8, 25)))
             for i in range(12)]
_old_base = [r for r in RECV if r[0] <= T - timedelta(days=7)]
PARTIAL_IDX = R.sample(range(len(_old_base)), 3)
_old_rows = [(b, ref, p, a + (R.randint(6, 20) * 1000 if i in PARTIAL_IDX else 0), due) for i, (b, ref, p, a, due) in enumerate(_old_base)]
OLD_ROWS = sorted(_old_rows + OLD_EXTRA, key=lambda r: r[0])
OLD_TOTAL = sum(r[3] for r in OLD_ROWS)
CLEARED = sum(r[3] for r in OLD_EXTRA)
PARTIAL = sum(o[3] - n[3] for o, n in zip(_old_rows, _old_base))
NEW_BILLS = [r for r in RECV if r[0] > T - timedelta(days=7)]
h.FILES["C03"] = ("receivable_last_week.xlsx", tally_receivables(OLD_ROWS, title="Pragati Wholesale Traders (7 days ago)"))

# ---------------------------------------------------------------------------------------------- data: duplicate parties
VARIANTS = {"Sharma Traders": ["Sharma Traders", "Sharma Trader", "SHARMA TRADERS", "Sharma Tradres"],
            "Gandhi Sons": ["Gandhi Sons", "Gandhi & Sons", "Gandhi sons (Ahd)"],
            "Patel Wholesale": ["Patel Wholesale", "Patel Wholesalers", "Patel Whole Sale"],
            "Iyer Provisions": ["Iyer Provisions", "Iyer Provision Store"]}
SINGLES = ["Nanda Traders", "Oswal Agencies", "Rathi Traders", "Saini Stores", "Thakkar Agencies", "Verma Kirana", "Wadhwa Sons", "Zaveri Jewellers"]
DUP_ROWS, DUP_TRUTH = [], {}
for canon, names in VARIANTS.items():
    for k in range(R.randint(3, 4) * 1):
        a = R.randint(5, 90) * 1000
        DUP_ROWS.append((T - timedelta(days=R.randint(5, 100)), f"DP/{len(DUP_ROWS) + 1:03d}", names[k % len(names)], a, T - timedelta(days=R.randint(-5, 60))))
        DUP_TRUTH[canon] = DUP_TRUTH.get(canon, 0) + a
for s in SINGLES:
    for _ in range(R.randint(1, 3)):
        DUP_ROWS.append((T - timedelta(days=R.randint(5, 100)), f"DP/{len(DUP_ROWS) + 1:03d}", s, R.randint(5, 90) * 1000, T - timedelta(days=R.randint(-5, 60))))
R.shuffle(DUP_ROWS)
h.FILES["C04"] = ("party_outstanding_mixed_spellings.xlsx", tally_receivables(DUP_ROWS, overdue_col=False, title="Outstanding (as typed by 3 staff)"))
DISTINCT_PARTIES = len(VARIANTS) + len(SINGLES)

# ---------------------------------------------------------------------------------------------- data: bank statement
BANK_ROWS, BANK_FULL, BANK_PART, BANK_UNKNOWN, BANK_MATCHED_TOTAL = [], [], [], [], 0
_pick = R.sample(range(len(RECV)), 36)
for idx in _pick[:30]:                                                    # paid in full, party and invoice in the narration
    b, ref, p, a, due = RECV[idx]
    BANK_ROWS.append((T - timedelta(days=R.randint(1, 20)), f"NEFT-HDFC0001234-{p.upper()}-{ref}", a, 0))
    BANK_FULL.append(ref)
    BANK_MATCHED_TOTAL += a
for idx in _pick[30:]:                                                    # part payments
    b, ref, p, a, due = RECV[idx]
    part = int(a * 0.6 // 100 * 100)
    BANK_ROWS.append((T - timedelta(days=R.randint(1, 20)), f"UPI/{R.randint(10**11, 10**12 - 1)}/{p.upper()}/{ref} PART", part, 0))
    BANK_PART.append(ref)
    BANK_MATCHED_TOTAL += part
for name, a in (("RANDOM TRADING CO", 17_350), ("MOHAN LAL GIFT", 5_500), ("UNKNOWN REMITTER", 42_125), ("SELF TRANSFER", 100_000), ("R K ENTERPRISES", 8_880)):
    BANK_ROWS.append((T - timedelta(days=R.randint(1, 20)), f"IMPS/{R.randint(10**11, 10**12 - 1)}/{name}", a, 0))
    BANK_UNKNOWN.append(a)
for narr in ["OFFICE RENT", "SALARY STAFF", "MSEB ELECTRICITY BILL", "VRL FREIGHT", "AIRTEL BROADBAND", "GST PMT CHALLAN"] * 6:
    BANK_ROWS.append((T - timedelta(days=R.randint(1, 20)), narr, 0, R.randint(8, 300) * 100))
BANK_ROWS.sort(key=lambda r: r[0])
_bal, _lines = 500_000, ["Txn Date,Narration,Ref No,Debit,Credit,Balance"]
for d, narr, cr, dr in BANK_ROWS:
    _bal += cr - dr
    _lines.append(f"{d:%d/%m/%Y},{narr},R{R.randint(10**6, 10**7)},{dr or ''},{cr or ''},{_bal}")
h.FILES["C05"] = ("hdfc_statement.csv", ("\r\n".join(_lines) + "\r\n").encode())

# ---------------------------------------------------------------------------------------------- data: GST (books vs 2B)
SUPS = [("Kumar Steel", "27AAAPK1111A1Z1", False), ("Delhi Pipes", "07AAAPD2222B1Z2", True), ("Shah Fasteners", "27AAAPS4444D1Z4", False),
        ("Rao Bearings", "29AAAPR3333C1Z3", True), ("Mehta Packaging", "27AAAPM5555E1Z5", False), ("Gupta Chemicals", "09AAAPG6666F1Z6", True),
        ("Singh Electricals", "27AAAPS7777G1Z7", False), ("Nair Plastics", "32AAAPN8888H1Z8", True), ("Joshi Paper Mills", "27AAAPJ9999J1Z9", False),
        ("Khan Hardware", "27AAAPK1212K1Z0", False), ("Bose Textiles", "19AAAPB3434L1Z2", True), ("Reddy Lubricants", "36AAAPR5656M1Z3", True)]
BOOKS = []
for i in range(60):
    name, gstin, inter = SUPS[i % len(SUPS)]
    taxable = R.randint(8, 300) * 1000
    rate = R.choice([5, 12, 18, 18, 18])
    tax = taxable * rate // 100
    BOOKS.append({"date": T.replace(day=1) - timedelta(days=R.randint(1, 28)), "sup": name, "gstin": gstin,
                  "inv": f"{name.split()[0][:2].upper()}/26-27/{100 + i:04d}", "taxable": taxable,
                  "cgst": 0 if inter else tax // 2, "sgst": 0 if inter else tax // 2, "igst": tax if inter else 0})
_idx = list(range(len(BOOKS)))
R.shuffle(_idx)
GST_MISSING = _idx[:4]
GST_MISMATCH = _idx[4:6]
GST_EXTRA = [{"date": T.replace(day=1) - timedelta(days=R.randint(1, 28)), "sup": SUPS[j][0], "gstin": SUPS[j][1], "inv": f"EX/26-27/{900 + j}",
              "taxable": 40_000 + j * 5000, "cgst": (40_000 + j * 5000) * 9 // 100, "sgst": (40_000 + j * 5000) * 9 // 100, "igst": 0}
             for j in (1, 4, 7)]
for e in GST_EXTRA:
    if SUPS[[s[0] for s in SUPS].index(e["sup"])][2]:
        e["igst"], e["cgst"], e["sgst"] = e["cgst"] * 2, 0, 0
itc = lambda b: b["cgst"] + b["sgst"] + b["igst"]
GST_MISSING_ITC = sum(itc(BOOKS[i]) for i in GST_MISSING)
GST_EXTRA_ITC = sum(itc(e) for e in GST_EXTRA)
_b2 = []
for i, b in enumerate(BOOKS):
    if i in GST_MISSING:
        continue
    b2 = dict(b)
    if i in GST_MISMATCH:
        f = 0.9
        b2["taxable"], b2["cgst"], b2["sgst"], b2["igst"] = (int(b[k] * f) for k in ("taxable", "cgst", "sgst", "igst"))
    _b2.append(b2)
GST_MISMATCH_DIFF = {BOOKS[i]["inv"]: itc(BOOKS[i]) - sum(int(BOOKS[i][k] * 0.9) for k in ("cgst", "sgst", "igst")) for i in GST_MISMATCH}
_b2 += GST_EXTRA
R.shuffle(_b2)
_hdr_b = ["Date", "Supplier", "Supplier GSTIN", "Invoice No", "Taxable Value", "CGST", "SGST", "IGST", "Invoice Value"]
BOOKS_FILE = xlsx([["Purchase Register", f"Month: {(T.replace(day=1) - timedelta(days=1)):%b %Y}"], [], _hdr_b,
                   *[[dmy(b["date"]), b["sup"], b["gstin"], b["inv"], b["taxable"], b["cgst"], b["sgst"], b["igst"], b["taxable"] + itc(b)] for b in BOOKS]])
B2_FILE = xlsx([[f"GSTR-2B (B2B invoices) for {(T.replace(day=1) - timedelta(days=1)):%b %Y}"], [],
                ["GSTIN of supplier", "Trade name", "Invoice number", "Invoice date", "Taxable value", "Integrated tax", "Central tax", "State/UT tax"],
                *[[b["gstin"], b["sup"], b["inv"], dmy(b["date"]), b["taxable"], b["igst"], b["cgst"], b["sgst"]] for b in _b2]])
h.FILES["C06"] = ("purchase_register.xlsx", BOOKS_FILE)
h.FILES["C07"] = ("GSTR2B.xlsx", B2_FILE)

# ---------------------------------------------------------------------------------------------- data: TDS 194Q, 43B(h)
PURCH = [("Kumar Steel", "AAAPK1111A", 6_250_000), ("Delhi Pipes", "AAAPD2222B", 1_840_000), ("Shah Fasteners", "AAAPS4444D", 12_400_000),
         ("Rao Bearings", "AAAPR3333C", 5_100_000), ("Mehta Packaging", "", 7_900_000), ("Gupta Chemicals", "AAAPG6666F", 3_200_000),
         ("Singh Electricals", "AAAPS7777G", 4_999_000), ("Nair Plastics", "AAAPN8888H", 540_000), ("Joshi Paper Mills", "AAAPJ9999J", 8_050_000),
         ("Khan Hardware", "AAAPK1212K", 920_000)]
TDS_TRUTH = {n: round((a - 5_000_000) * (0.001 if pan else 0.05)) for n, pan, a in PURCH if a > 5_000_000}
h.FILES["C08"] = ("supplier_purchases_fy.xlsx", xlsx([["Supplier-wise purchases, FY 2026-27 (1 Apr to date)"], [], ["Supplier", "PAN", "Purchases (excl GST)"],
                                                        *[[n, pan or "", a] for n, pan, a in PURCH]]))
PAY = []
for i, (name, cls) in enumerate([("Kumar Steel", "Micro"), ("Delhi Pipes", "Small"), ("Shah Fasteners", "Micro"), ("Rao Bearings", "Medium"),
                                 ("Mehta Packaging", "Small"), ("Gupta Chemicals", "Large"), ("Singh Electricals", "Micro"), ("Nair Plastics", "Not registered"),
                                 ("Joshi Paper Mills", "Small"), ("Khan Hardware", "Micro"), ("Bose Textiles", "Small"), ("Reddy Lubricants", "Micro")] * 2):
    agree = R.choice([None, None, 45])
    age = R.choice([4, 9, 12, 20, 33, 41, 47, 58, 75])
    PAY.append({"sup": name, "cls": cls, "ref": f"PB/{i + 1:03d}", "date": T - timedelta(days=age), "agree": agree,
                "amt": R.randint(10, 250) * 1000, "paid": (T - timedelta(days=max(1, age - 3))) if R.random() < 0.2 else None})
for p in PAY:
    p["deadline"] = p["date"] + timedelta(days=p["agree"] or 15)
LATE = [p for p in PAY if p["cls"] in ("Micro", "Small") and not p["paid"] and p["deadline"] < T]
SOON = [p for p in PAY if p["cls"] in ("Micro", "Small") and not p["paid"] and T <= p["deadline"] <= T + timedelta(days=7)]
h.FILES["C09"] = ("payables_with_udyam.xlsx", xlsx([["Supplier payables"], [], ["Bill No", "Supplier", "Udyam category", "Bill date", "Written agreement (days)", "Amount", "Paid on"],
                                                      *[[p["ref"], p["sup"], p["cls"], dmy(p["date"]), p["agree"] or "", p["amt"], dmy(p["paid"]) if p["paid"] else ""] for p in PAY]]))

# ---------------------------------------------------------------------------------------------- data: sales, margin, stock, expenses
CUSTS = [p for p in PARTIES[:20]]
ITEMS = [("Basmati Rice 25kg", 1850), ("Toor Dal 30kg", 3300), ("Sugar 50kg", 2150), ("Refined Oil 15L", 2050), ("Atta 50kg", 1650),
         ("Tea 1kg x10", 3900), ("Detergent 20kg", 2350), ("Biscuit carton", 1250), ("Namkeen carton", 1450), ("Soap carton", 1750)]
LOST = ["Zaveri Jewellers", "Yadav Dairy", "Dutta Agencies"]
SALES, MONTH_TOTAL = [], {}
for i in range(480):
    who = R.choice(CUSTS[:14] + LOST[:2] * 2)
    d = T - timedelta(days=R.randint(1, 180))
    if who in LOST[:2]:
        d = T - timedelta(days=R.randint(65, 180))                        # these two stopped buying 60+ days ago
    item, rate = R.choice(ITEMS)
    qty = R.randint(2, 40)
    SALES.append((d, f"S/{i + 1:04d}", who, item, qty, rate, qty * rate))
for _ in range(2):                                                          # a small customer who is also silent (below the 1.5 lakh bar)
    d = T - timedelta(days=R.randint(70, 120))
    SALES.append((d, f"S/{len(SALES) + 1:04d}", LOST[2], ITEMS[0][0], 3, 1850, 5550))
SALES.sort(key=lambda r: r[0])
for r in SALES:
    k = f"{r[0]:%Y-%m}"
    MONTH_TOTAL[k] = MONTH_TOTAL.get(k, 0) + r[6]
CUST_TOTAL = {}
for r in SALES:
    CUST_TOTAL[r[2]] = CUST_TOTAL.get(r[2], 0) + r[6]
TOP5 = sorted(CUST_TOTAL, key=lambda k: -CUST_TOTAL[k])[:5]
RECENT = {r[2] for r in SALES if r[0] > T - timedelta(days=60)}
LOST_TRUTH = sorted(c for c in CUST_TOTAL if c not in RECENT and CUST_TOTAL[c] >= 150_000)
h.FILES["C10"] = ("sales_register_6_months.xlsx", xlsx([["Sales Register (last 6 months)"], [], ["Date", "Invoice", "Customer", "Item", "Qty", "Rate", "Amount"],
                                                          *[[dmy(r[0]), r[1], r[2], r[3], r[4], r[5], r[6]] for r in SALES]]))

COST = {item: int(rate * R.uniform(0.82, 0.9)) for item, rate in ITEMS}
MARGIN_SALES, BELOW = [], {}
for i in range(150):
    item, rate = R.choice(ITEMS)
    qty = R.randint(2, 30)
    MARGIN_SALES.append([dmy(T - timedelta(days=R.randint(1, 60))), f"M/{i + 1:04d}", R.choice(CUSTS), item, qty, int(COST[item] * R.uniform(1.06, 1.25))])
for i in R.sample(range(150), 7):
    item = MARGIN_SALES[i][3]
    MARGIN_SALES[i][5] = int(COST[item] * R.uniform(0.9, 0.97))
    BELOW[MARGIN_SALES[i][1]] = (COST[item] - MARGIN_SALES[i][5]) * MARGIN_SALES[i][4]
h.FILES["C11"] = ("purchase_cost_list.xlsx", xlsx([["Purchase cost per unit"], [], ["Item", "Cost per unit"], *[[k, v] for k, v in COST.items()]]))
h.FILES["C12"] = ("sales_lines_nov.xlsx", xlsx([["Sales lines (Oct-Nov)"], [], ["Date", "Invoice", "Customer", "Item", "Qty", "Sale rate"], *MARGIN_SALES]))

BRANDS = ["Parle", "Britannia", "Haldiram", "Tata", "Fortune", "Aashirvaad", "Patanjali", "Dabur", "Amul", "Maggi", "Surf", "Vim", "Lifebuoy", "Red Label"]
SIZES = ["200g", "500g", "1kg", "5kg", "10kg", "1L"]
STOCK = []
for i, (b, s) in enumerate([(b, s) for b in BRANDS for s in SIZES][:80]):
    STOCK.append((f"{b} {s}", R.randint(5, 400), R.randint(20, 900), T - timedelta(days=R.choice([3, 8, 15, 30, 45, 60, 88, 95, 120, 150, 200, 260]))))
SLOW = [(n, q_ * c) for n, q_, c, last in STOCK if (T - last).days > 90]
SLOW_TOTAL = sum(v for _, v in SLOW)
SLOW_TOP5 = [n for n, _ in sorted(SLOW, key=lambda x: -x[1])[:5]]
h.FILES["C13"] = ("stock_statement.xlsx", xlsx([["Stock Summary"], [], ["Item", "Closing qty", "Cost per unit", "Last sale date"],
                                                  *[[n, q_, c, dmy(last)] for n, q_, c, last in STOCK]]))

EXP_CATS = {"Rent": ["OFFICE RENT NOV", "GODOWN RENT SHARMA"], "Salary": ["SALARY RAMESH K", "SAL SURESH", "SALARY MEENA"],
            "Electricity": ["MSEB ELECTRICITY BILL", "TORRENT POWER"], "Freight": ["VRL LOGISTICS FREIGHT", "TEMPO HIRE LOADING"],
            "Telecom": ["AIRTEL BROADBAND", "JIO POSTPAID"], "Taxes": ["GST PMT CHALLAN", "TDS DEPOSIT"], "Bank charges": ["BANK CHARGES", "SMS ALERT CHG"]}
EXP_ROWS, EXP_TOTAL = [], {k: 0 for k in EXP_CATS}
_used = set()
for i in range(56):
    cat = list(EXP_CATS)[i % 7]
    amt = R.randint(3, 900) * 10 + 7
    while amt in _used:
        amt += 3
    _used.add(amt)
    EXP_ROWS.append((T - timedelta(days=R.randint(1, 60)), R.choice(EXP_CATS[cat]), amt))
    EXP_TOTAL[cat] += amt
EXP_ROWS.sort(key=lambda r: r[0])
h.FILES["C14"] = ("bank_debits_2_months.csv", ("Date,Narration,Debit\r\n" + "\r\n".join(f"{d:%d/%m/%Y},{n},{a}" for d, n, a in EXP_ROWS) + "\r\n").encode())

# ---------------------------------------------------------------------------------------------- data: supplier price lists, trial balance
PL_ITEMS = [f"{b} {s}" for b in BRANDS[:9] for s in SIZES[:4]][:36]
BASE = {i: R.randint(40, 900) for i in PL_ITEMS}
PRICES = {sup: {i: round(BASE[i] * R.uniform(0.92, 1.12), 1) for i in PL_ITEMS} for sup in ("Ankit Traders", "Bharat Suppliers", "City Wholesale")}
CHEAPEST = {i: min(PRICES, key=lambda s: PRICES[s][i]) for i in PL_ITEMS}
SUP_TOTAL = {s: sum(PRICES[s][i] * 100 for i in PL_ITEMS) for s in PRICES}
MIN_TOTAL = sum(min(PRICES[s][i] for s in PRICES) * 100 for i in PL_ITEMS)
BEST_SINGLE = min(SUP_TOTAL.values())
for n, (sup, col) in enumerate((("Ankit Traders", ("Item", "Rate")), ("Bharat Suppliers", ("Product description", "Price/unit")), ("City Wholesale", ("Particulars", "Rate (Rs)")))):
    items = list(PL_ITEMS)
    R.shuffle(items)
    h.FILES[f"C1{5 + n}"] = (f"{sup.split()[0].lower()}_rates.xlsx", xlsx([[sup, "Rate list effective this month"], [], list(col), *[[i, PRICES[sup][i]] for i in items]]))

TB = [("Reserves & Surplus", 0, 1_850_000), ("Bank OD - HDFC", 0, 1_240_000), ("Sundry Creditors", 0, 2_160_000),
      ("Duties & Taxes (GST payable)", 0, 310_000), ("Sales", 0, 14_800_000), ("Other Income", 0, 95_000), ("Sundry Debtors", 3_420_000, 0),
      ("Stock in Trade", 2_950_000, 0), ("Furniture & Fixtures", 480_000, 0), ("Vehicles", 920_000, 0), ("Purchases", 11_250_000, 0),
      ("Salaries", 780_000, 0), ("Rent", 360_000, 0), ("Freight", 240_000, 0), ("Electricity", 96_000, 0), ("Telephone", 38_000, 0),
      ("Interest paid", 142_000, 0), ("Depreciation", 160_000, 0), ("Suspense A/c", 240_000, 0), ("Kapoor & Co (Sundry Debtor)", 0, 85_000),
      ("Cash in hand", 0, 180_000)]
_dr, _cr = sum(r[1] for r in TB), sum(r[2] for r in TB)
TB.insert(0, ("Capital Account", 0, _dr - _cr))             # balances the books, so any red flag is a planted one
h.FILES["C18"] = ("trial_balance.xlsx", xlsx([["Pragati Wholesale Traders", "Trial Balance as on " + dmy(T)], [], ["Particulars", "Debit", "Credit"], *[[a, b or None, c or None] for a, b, c in TB],
                                              ["Grand Total", sum(r[1] for r in TB), sum(r[2] for r in TB)]]))


# ---------------------------------------------------------------------------------------------- seed
PRELOAD = {"cap_top5_concentration", "cap_call_list_top10", "cap_party_statement_pdf", "cap_bank_reconciliation", "cap_collection_forecast",
           "cap_interest_on_overdue"}


async def seed(pool):
    from src import tenants
    from src.db import migrate
    async with pool.connection() as c:
        await c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    await migrate(pool)
    t = await tenants.create(pool, name="Pragati Wholesale Traders", owner_phone=OWNER, upi_vpa="pragati@upi")
    if ds.CUR["name"] in PRELOAD:
        await preload_receivables(pool, t.id)
        async with pool.connection() as c:
            await c.execute("update obligations set next_followup_at = now() + interval '30 days'")
            await c.execute("update parties set last_inbound_at = now()")
    async with pool.connection() as c:
        await c.execute("update tenants set owner_last_inbound_at = now()")
    return t


# ---------------------------------------------------------------------------------------------- checks
async def ledger_state(env):
    r = await q(env, "select count(*) n, coalesce(sum(amount_paise),0) s, count(*) filter (where state = 'PAID') paid from obligations")
    return r[0]


@cap
async def c_ageing(env):
    t, led = corpus(env), await ledger_state(env)
    b = {k: 0 for k in BUCKETS}
    for r in RECV:
        b[bucket_of(r[4])] += r[3]
    miss = [k for k, v in b.items() if v and not has_amt(t, v)]
    imported = led["n"] == len(RECV) and led["s"] == RECV_TOTAL * 100
    return has_amt(t, RECV_TOTAL) and not miss and imported, f"total={has_amt(t, RECV_TOTAL)} buckets_missing={miss} ledger={led['n']}/{len(RECV)} sum_ok={led['s'] == RECV_TOTAL * 100}"


@cap
async def c_top5(env):
    t, low = corpus(env), corpus(env).lower()
    bp = by_party(RECV)
    top = sorted(bp, key=lambda k: -bp[k])[:5]
    pos = [low.find(n.lower().split()[0]) for n in top]
    pct = round(sum(bp[n] for n in top) / RECV_TOTAL * 100, 1)
    pcts = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*%", t)]
    return all(p >= 0 for p in pos) and pos == sorted(pos) and any(abs(p - pct) <= 0.3 for p in pcts), f"top5={top} positions={pos} pct_truth={pct} pcts_seen={pcts[:6]}"


@cap
async def c_call_list(env):
    od = by_party(RECV, lambda r: (T - r[4]).days > 30)
    top = sorted(od, key=lambda k: -od[k])[:10]
    xl = [u for u in env.wa.uploads if u[0].lower().endswith(".xlsx")]
    if not xl:
        return False, f"no xlsx sent; said={ds.alltext(env)[:160]!r}"
    rows = rows_of(xl[-1][2])
    right = 0
    for p in top:
        for r in rows:
            if any(isinstance(c, str) and p.lower() in c.lower() for c in r) and any(isinstance(c, (int, float)) and abs(c - od[p]) <= 1 for c in r):
                right += 1
                break
    return right >= 9, f"top10_with_right_amount={right}/10 truth={[(p, od[p]) for p in top[:3]]}"


@cap
async def c_statement(env):
    bp = by_party(RECV)
    p = max(bp, key=lambda k: bp[k])
    mine = [r for r in RECV if r[2] == p]
    pdf = [u for u in env.wa.uploads if u[0].lower().endswith(".pdf")]
    if not pdf:
        return False, f"no pdf; party={p}; said={ds.alltext(env)[:150]!r}"
    text = file_text(pdf[-1][0], pdf[-1][2])
    missing = [r[1] for r in mine if r[1] not in text]
    return not missing and has_amt(text, bp[p]), f"party={p} bills={len(mine)} missing={missing[:4]} total_ok={has_amt(text, bp[p])}"


@cap
async def c_snapshot(env):
    t = corpus(env)
    new_sum = sum(r[3] for r in NEW_BILLS)
    collected = CLEARED + PARTIAL
    net = abs(RECV_TOTAL - OLD_TOTAL)
    got = {"collected": has_amt(t, collected), "new_bills": has_amt(t, new_sum), "net_change": has_amt(t, net), "cleared_count_12": bool(re.search(r"\b12\b", t))}
    return all(got.values()), f"{got} truth: collected={collected} new={new_sum} net={net}"


@cap
async def c_duplicates(env):
    t, low = corpus(env), corpus(env).lower()
    got = {c: has_amt(t, v) for c, v in DUP_TRUTH.items()}
    return all(got.values()) and bool(re.search(rf"\b{DISTINCT_PARTIES}\b", t)), f"merged_totals_found={got} distinct_parties={DISTINCT_PARTIES}"


@cap
async def c_bank_rec(env):
    t, led = corpus(env), await ledger_state(env)
    full = sum(ref in t or ref.split("/")[-1] in t for ref in BANK_FULL)       # the full number or just its last digits (0003)
    part = sum(ref in t or ref.split("/")[-1] in t for ref in BANK_PART)
    unk = [a for a in BANK_UNKNOWN if not has_amt(t, a)]
    return has_amt(t, BANK_MATCHED_TOTAL) and full >= 27 and part >= 5 and not unk and led["paid"] == 0, \
        f"matched_total={has_amt(t, BANK_MATCHED_TOTAL)} full_refs={full}/30 part_refs={part}/6 unknown_missing={unk} ledger_paid={led['paid']}"


@cap
async def c_gst(env):
    t, led = corpus(env), await ledger_state(env)
    miss_refs = [BOOKS[i]["inv"] for i in GST_MISSING]
    mm_refs = [BOOKS[i]["inv"] for i in GST_MISMATCH]
    ex_refs = [e["inv"] for e in GST_EXTRA]
    got = {"missing_refs": all(r in t for r in miss_refs), "itc_at_risk": has_amt(t, GST_MISSING_ITC), "mismatch_refs": all(r in t for r in mm_refs),
           "extra_in_2b": all(r in t for r in ex_refs), "report_file": bool(env.wa.uploads), "books_not_in_ledger": led["n"] == 0}
    return all(got.values()), f"{got} truth: itc_at_risk={GST_MISSING_ITC} mismatch_diffs={GST_MISMATCH_DIFF}"


@cap
async def c_tds(env):
    t = corpus(env)
    got = {n: has_amt(t, v, tol=2) for n, v in TDS_TRUTH.items()}
    return all(got.values()), f"tds_found={got} truth={TDS_TRUTH}"


@cap
async def c_43bh(env):
    t = corpus(env)
    late, soon = sum(p["amt"] for p in LATE), sum(p["amt"] for p in SOON)
    got = {"late_total": has_amt(t, late), "soon_total": has_amt(t, soon) if soon else True, "late_bills": sum(p["ref"] in t for p in LATE)}
    return got["late_total"] and got["soon_total"] and got["late_bills"] >= len(LATE) - 1, f"{got} truth: late_n={len(LATE)} late={late} soon_n={len(SOON)} soon={soon}"


@cap
async def c_sales(env):
    t, low = corpus(env), corpus(env).lower()
    months = [k for k, v in MONTH_TOTAL.items() if not has_amt(t, v)]
    pos = [low.find(n.lower().split()[0]) for n in TOP5]
    lost = [n for n in LOST_TRUTH if n.lower().split()[0] not in low]
    return not months and all(p >= 0 for p in pos) and pos == sorted(pos) and not lost, f"months_missing={months} top5={TOP5} pos={pos} lost_missing={lost} truth_lost={LOST_TRUTH}"


@cap
async def c_margin(env):
    t = corpus(env)
    found = sum(r in t for r in BELOW)
    return found == len(BELOW) and has_amt(t, sum(BELOW.values())), f"below_cost_found={found}/{len(BELOW)} loss_ok={has_amt(t, sum(BELOW.values()))} loss={sum(BELOW.values())}"


@cap
async def c_stock(env):
    t, low = corpus(env), corpus(env).lower()
    top = [n for n in SLOW_TOP5 if n.lower() in low]
    return has_amt(t, SLOW_TOTAL) and len(top) >= 5, f"slow_total_ok={has_amt(t, SLOW_TOTAL)} total={SLOW_TOTAL} top5_found={len(top)}/5"


@cap
async def c_expenses(env):
    t = corpus(env)
    got = {k: has_amt(t, v) for k, v in EXP_TOTAL.items()}
    return all(got.values()), f"{got} truth={EXP_TOTAL}"


@cap
async def c_forecast(env):
    t = corpus(env)
    wk, overdue, today = [0, 0, 0, 0], 0, 0
    for r in RECV:
        k = (r[4] - T).days
        if k < 0:
            overdue += r[3]
        elif k == 0:
            today += r[3]                       # due today: 'already due' and 'in the next 7 days' are both fair readings
        elif k <= 7:
            wk[0] += r[3]
        elif k <= 14:
            wk[1] += r[3]
        elif k <= 21:
            wk[2] += r[3]
        elif k <= 30:
            wk[3] += r[3]
    got = {"overdue": has_amt(t, overdue) or has_amt(t, overdue + today), "week1": has_amt(t, wk[0]) or has_amt(t, wk[0] + today),
           **{f"week{i + 1}": has_amt(t, v) for i, v in enumerate(wk) if v and i}}
    return all(got.values()), f"{got} truth: overdue={overdue} weeks={wk}"


@cap
async def c_interest(env):
    t = corpus(env)
    per = {}
    for r in RECV:
        o = (T - r[4]).days
        if o > 0:
            per[r[2]] = per.get(r[2], 0) + r[3] * 0.18 * o / 365
    top = sorted(per, key=lambda k: -per[k])[:5]
    got = {n: has_amt(t, per[n], tol=2) for n in top}
    return all(got.values()) and has_amt(t, sum(per.values()), tol=3), f"top5={got} total_ok={has_amt(t, sum(per.values()), tol=3)} total={round(sum(per.values()))}"


@cap
async def c_price_compare(env):
    t = corpus(env)
    got = {"cheapest_total": has_amt(t, MIN_TOTAL, tol=2), "best_single_total": has_amt(t, BEST_SINGLE, tol=2),
           "saving": has_amt(t, BEST_SINGLE - MIN_TOTAL, tol=2)}
    return all(got.values()), f"{got} truth: min={round(MIN_TOTAL)} best_single={round(BEST_SINGLE)} saving={round(BEST_SINGLE - MIN_TOTAL)} suppliers={ {k: round(v) for k, v in SUP_TOTAL.items()} }"


@cap
async def c_trial_balance(env):
    low = corpus(env).lower()
    got = {"cash_negative": "cash" in low and any(w in low for w in ("credit balance", "negative", "overdrawn", "cr balance")), "suspense": "suspense" in low,
           "kapoor_credit": "kapoor" in low}
    return all(got.values()), str(got)


@cap
async def c_edit_workbook(env):
    xl = [u for u in env.wa.uploads if u[0].lower().endswith(".xlsx")]
    if not xl:
        return False, f"no xlsx sent; said={ds.alltext(env)[:160]!r}"
    rows = rows_of(xl[-1][2])
    hdr_i = next((i for i, r in enumerate(rows) if r and any(isinstance(c, str) and "ref" in c.lower() for c in r)), None)
    if hdr_i is None:
        return False, "header row not found"
    hdr = [str(c or "").lower() for c in rows[hdr_i]]
    ci = lambda *keys: next((i for i, c in enumerate(hdr) if all(k in c for k in keys)), None)
    c_ref, c_days, c_bucket = ci("ref"), ci("overdue", "day"), ci("bucket")
    if None in (c_ref, c_days, c_bucket):
        return False, f"new columns missing in header {hdr}"
    truth = {r[1]: ((T - r[4]).days, bucket_of(r[4])) for r in RECV}
    good = 0
    for r in rows[hdr_i + 1:]:
        if r and r[c_ref] in truth:
            days, b = truth[r[c_ref]]
            try:
                ok_days = int(float(r[c_days])) == days or (days < 0 and int(float(r[c_days])) in (0, days))
            except (TypeError, ValueError):
                ok_days = False
            tok = {"Not due": "not", "1-30": "1-30", "31-60": "31-60", "61-90": "61-90", "90+": "90"}[b]
            ok_b = tok in str(r[c_bucket]).lower().replace(" ", "").replace("–", "-").replace("notdue", "not")
            good += ok_days and ok_b
    return good >= len(RECV) * 0.98, f"rows_correct={good}/{len(RECV)}"


def main_tasks():
    P = lambda *s: event(*s)
    return [
        Task("cap_receivables_import_and_ageing", ("custom", P(("Ye Tally se bills receivable nikala hai. Import karke batao: kul outstanding kitna hai, aur ageing: not due, 1-30, 31-60, 61-90, aur 90 din se upar, har bucket mein kitna?", "C01"))), c_ageing),
        Task("cap_top5_concentration", ("custom", P(("Sabse bade 5 parties kaun hain outstanding mein, aur total outstanding ka kitna percent in 5 ka hai?",))), c_top5),
        Task("cap_call_list_top10", ("custom", P(("Kal main 10 parties ko phone karunga. 30 din se zyada overdue amount ke hisaab se top 10 parties ki Excel list bana do: party, 30+ din overdue amount, aur kitne bills.",))), c_call_list),
        Task("cap_party_statement_pdf", ("custom", P(("Sabse zyada outstanding wali party ka poora statement PDF bhejo, saare bills ke saath. Party ka naam batana bhi.",))), c_statement),
        Task("cap_week_over_week", ("custom", P(("Ye pichhle hafte (7 din pehle) ki outstanding list hai.", "C03"), ("Ye aaj ki list hai. Pichhle hafte se kya badla: kitna paisa aaya (poore bhare bills aur partial payments dono), kitne nayi bills aaye aur unka total, aur net outstanding kitna badla? Kitne bills poore clear huye?", "C01"))), c_snapshot),
        Task("cap_duplicate_party_names", ("custom", P(("Is list mein kuch parties ke naam alag-alag spelling mein aaye hain (3 staff ne type kiya). Same party ko ek karke party-wise total do, aur batao asal mein kitni parties hain.", "C04"))), c_duplicates),
        Task("cap_bank_reconciliation", ("custom", P(("Ye mera bank statement hai. Mere ledger ke saath match karo: kin parties ka paisa aaya (kul kitna match hua), kaunse invoices poore bhare, kaunse partial, aur kaunse credits kisi party se match nahi hue.", "C05"))), c_bank_rec),
        Task("cap_gstr2b_vs_purchase_register", ("custom", P(("Ye mera purchase register hai (books).", "C06"), ("Ye GSTR-2B hai. Dono match karo: kaunse invoice 2B mein nahi aaye aur kitna ITC atka hai, kisi mein amount ka fark hai, aur 2B mein kya hai jo books mein nahi. Report PDF ya Excel mein bhi bhejo.", "C07"))), c_gst),
        Task("cap_tds_194q", ("custom", P(("Mera turnover 25 crore hai. Ye supplier-wise is saal ki purchases hain. 194Q ke hisaab se kis supplier pe TDS banta hai aur kitna?", "C08"))), c_tds),
        Task("cap_msme_43bh", ("custom", P(("Ye supplier payables hain. 43B(h) ke liye: kin micro aur small suppliers ke bills ka deadline nikal chuka hai (written agreement ho to 45 din, warna 15 din), aur agle 7 din mein kiska aa raha hai? Total amount bhi batao.", "C09"))), c_43bh),
        Task("cap_sales_analysis", ("custom", P(("Ye pichhle 6 mahine ka sales register hai. Month-wise sales batao, top 5 customers, aur kin customers ne pichhle 60 din mein kuch nahi kharida jinka kul purchase 1.5 lakh se upar tha?", "C10"))), c_sales),
        Task("cap_sales_below_cost", ("custom", P(("Ye mera cost list hai.", "C11"), ("Ye sales lines hain. Kaunse invoices mein maine cost se kam rate pe becha, aur kul kitna nuksan hua?", "C12"))), c_margin),
        Task("cap_slow_moving_stock", ("custom", P(("Ye stock statement hai. Jo items pichhle 90 din se nahi bike, unmein kitna paisa fasa hai (qty x cost), aur top 5 items kaun se hain?", "C13"))), c_stock),
        Task("cap_expense_categories", ("custom", P(("Ye 2 mahine ke bank debits hain. Category-wise kharcha batao: rent, salary, electricity, freight, telecom, taxes, bank charges.", "C14"))), c_expenses),
        Task("cap_collection_forecast", ("custom", P(("Agle 30 din mein due date ke hisaab se kitna paisa aane wala hai? Week-wise (1-7, 8-14, 15-21, 22-30 din) batao, aur jo abhi tak due ho chuka hai woh alag.",))), c_forecast),
        Task("cap_interest_on_overdue", ("custom", P(("Overdue bills pe 18% saalana simple byaaj lagao, due date se aaj tak. Party-wise top 5 byaaj aur kul byaaj batao.",))), c_interest),
        Task("cap_supplier_price_compare", ("custom", P(("Ankit Traders ka rate list.", "C15"), ("Bharat Suppliers ka rate list.", "C16"), ("City Wholesale ka rate list. Teeno compare karo: har item 100 unit lun to har supplier se akela lene pe total kitna, aur har item sabse sasta supplier se lun to total kitna, kitna bachega?", "C17"))), c_price_compare),
        Task("cap_trial_balance_redflags", ("custom", P(("Ye client ka trial balance hai. CA ki nazar se quick check karo: debit-credit barabar hai? Koi red flag ya ghalat balance dikh raha hai?", "C18"))), c_trial_balance),
        Task("cap_edit_my_workbook", ("custom", P(("Is file mein 'Overdue Days' aur 'Bucket' (Not due / 1-30 / 31-60 / 61-90 / 90+) ke do naye column jod do, baaki sab waisa hi rehne do. Nayi file bhejo.", "C02"))), c_edit_workbook),
    ]


TASKS = main_tasks()

if __name__ == "__main__":
    h.TASKS[:] = TASKS
    h.seed = seed
    h.run_task = ds.run_task
    asyncio.run(h.main(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
