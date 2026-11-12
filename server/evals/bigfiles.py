"""Large-file test: does Smara read and import big, messy owner files correctly and in reasonable time?

No model is called. Every file is generated with a known truth (invoices, rupees, customers), so each result is checked
against the truth, not against what the importer says about itself. Per case it measures, in a fresh process:

  parse   map_sheet() (or the Tally helper) on the bytes: seconds, peak memory, rows kept, rupees kept, skipped reasons
  import  import_rows() into a throwaway tenant of an evaluation database: seconds, rows created, rupees in the ledger
  again   the same file imported a second time: must change nothing
  due     how many of the imported invoices the reminder loop would pick up at once (the owner gets a card for each)

Run from server/ (the database name must end in _eval; its tenants are deleted at the end, nothing else is touched):

    python -m evals.bigfiles --db postgresql://user:pw@127.0.0.1:25432/smara_bigfiles_eval --out <dir> [--only csv_cap ...]
"""
import argparse
import asyncio
import csv
import io
import json
import random
import re
import subprocess
import sys
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

CAP = 20 * 1024 * 1024
TODAY = date(2026, 11, 12)
FIRST = ["Sri", "Shree", "New", "Royal", "Maa", "Jai", "Om", "Sai", "National", "Modern", "Gupta", "Sharma", "Patel", "Singh", "Khan"]
KIND = ["Traders", "Enterprises", "Stores", "Agencies", "Hardware", "Electricals", "Sweets", "Textiles", "Motors", "Pharma", "Foods", "Steel"]
CITY = ["Pune", "Indore", "Surat", "Jaipur", "Kanpur", "Nagpur", "Agra", "Rajkot", "Ludhiana", "Patna"]


def make_rows(n: int, seed: int = 7, customers: int | None = None) -> list[dict]:
    """n invoices. Names are unique per customer; invoice refs are unique; amounts are whole paise."""
    rng = random.Random(seed)
    k = customers or max(10, n // 8)
    names = []
    for i in range(k):
        names.append(f"{rng.choice(FIRST)} {rng.choice(KIND)} {rng.choice(CITY)} {i}")
    phones = {nm: "9" + "".join(rng.choice("0123456789") for _ in range(9)) for nm in names}
    out = []
    for i in range(n):
        nm = names[rng.randrange(k)]
        billed = TODAY - timedelta(days=rng.randrange(1, 400))
        out.append({"party": nm, "phone": phones[nm], "invoice": f"INV/{2026 - (i % 3)}/{i:06d}", "billed": billed,
                    "due": billed + timedelta(days=rng.choice((15, 30, 45, 60))),
                    "paise": rng.randrange(50_000, 80_000_000)})        # Rs 500 to Rs 8 lakh
    return out


def indian(paise: int) -> str:
    """1,23,456.78"""
    rupees, p = divmod(paise, 100)
    s = str(rupees)
    if len(s) > 3:
        head, groups = s[:-3], []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups) + "," + s[-3:]
    return f"{s}.{p:02d}"


def csv_bytes(rows, *, encoding="utf-8", delim=",", style="plain", title=False, total=True, blanks=False) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=delim, lineterminator="\r\n")
    if title:
        w.writerow(["Sri Test Traders - Outstanding Receivables"]); w.writerow(["As on 12-Nov-2026"]); w.writerow([])
    w.writerow(["Party Name", "Mobile", "Invoice No", "Date", "Due Date", "Pending Amount"])
    for i, r in enumerate(rows):
        amt = indian(r["paise"]) + (" Dr" if style == "tally" else "") if style != "plain" else f"{r['paise'] / 100:.2f}"
        w.writerow([r["party"], r["phone"], r["invoice"], r["billed"].strftime("%d-%m-%Y"),
                    r["due"].strftime("%d-%b-%Y" if style == "tally" else "%d/%m/%Y"), amt])
        if blanks and i % 997 == 0:
            w.writerow([])
    if total:
        w.writerow(["Grand Total", "", "", "", "", indian(sum(r["paise"] for r in rows))])
    return buf.getvalue().encode(encoding)


def xlsx_bytes(rows, *, sheets=1, title=True, total=True, real_dates=True, phone_as_number=False) -> bytes:
    from openpyxl import Workbook
    wb = Workbook(write_only=True)
    per = -(-len(rows) // sheets)
    for s in range(sheets):
        ws = wb.create_sheet(f"Sheet{s + 1}" if s else "Outstanding")
        chunk = rows[s * per:(s + 1) * per]
        if title:
            ws.append(["Sri Test Traders"]); ws.append(["Receivables as on 12-Nov-2026"]); ws.append([])
        ws.append(["Customer Name", "Mobile No", "Bill No", "Bill Date", "Due Date", "Outstanding"])
        for r in chunk:
            ws.append([r["party"], int(r["phone"]) if phone_as_number else r["phone"], r["invoice"],
                       datetime.combine(r["billed"], datetime.min.time()) if real_dates else r["billed"].strftime("%d-%m-%Y"),
                       datetime.combine(r["due"], datetime.min.time()) if real_dates else r["due"].strftime("%d-%m-%Y"),
                       r["paise"] / 100])
        if total:
            ws.append(["Total", None, None, None, None, sum(r["paise"] for r in chunk) / 100])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def tally_xml(rows, receipts: float = 0.0, seed: int = 3) -> bytes:
    """A Tally Prime daybook export: a ledger master per customer, a Sales voucher per invoice, and some Receipt vouchers."""
    rng = random.Random(seed)
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;")
    parts = ["<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER><BODY><IMPORTDATA><REQUESTDATA>"]
    seen = {}
    for r in rows:
        seen.setdefault(r["party"], r["phone"])
    for nm, ph in seen.items():
        parts.append(f'<TALLYMESSAGE xmlns:UDF="TallyUDF"><LEDGER NAME="{esc(nm)}"><PARENT>Sundry Debtors</PARENT>'
                     f"<LEDGERMOBILE>{ph[:5]} {ph[5:]}</LEDGERMOBILE></LEDGER></TALLYMESSAGE>")
    for r in rows:
        amt = f"{r['paise'] / 100:.2f}"
        days = (r["due"] - r["billed"]).days
        parts.append(
            f'<TALLYMESSAGE xmlns:UDF="TallyUDF"><VOUCHER VCHTYPE="Sales" ACTION="Create"><DATE>{r["billed"]:%Y%m%d}</DATE>'
            f'<VOUCHERTYPENAME>Sales</VOUCHERTYPENAME><VOUCHERNUMBER>{r["invoice"]}</VOUCHERNUMBER>'
            f'<PARTYLEDGERNAME>{esc(r["party"])}</PARTYLEDGERNAME><ALLLEDGERENTRIES.LIST><LEDGERNAME>{esc(r["party"])}</LEDGERNAME>'
            f'<AMOUNT>-{amt}</AMOUNT><BILLALLOCATIONS.LIST><NAME>{r["invoice"]}</NAME><BILLCREDITPERIOD>{days} Days</BILLCREDITPERIOD>'
            f'</BILLALLOCATIONS.LIST></ALLLEDGERENTRIES.LIST><ALLLEDGERENTRIES.LIST><LEDGERNAME>Sales</LEDGERNAME><AMOUNT>{amt}</AMOUNT>'
            f"</ALLLEDGERENTRIES.LIST></VOUCHER></TALLYMESSAGE>")
    for j, r in enumerate(rng.sample(rows, int(len(rows) * receipts))):
        amt = f"{r['paise'] / 100:.2f}"
        parts.append(
            f'<TALLYMESSAGE xmlns:UDF="TallyUDF"><VOUCHER VCHTYPE="Receipt" ACTION="Create"><DATE>{TODAY:%Y%m%d}</DATE>'
            f"<VOUCHERTYPENAME>Receipt</VOUCHERTYPENAME><VOUCHERNUMBER>R-{j}</VOUCHERNUMBER><PARTYLEDGERNAME>{esc(r['party'])}"
            f"</PARTYLEDGERNAME><ALLLEDGERENTRIES.LIST><LEDGERNAME>{esc(r['party'])}</LEDGERNAME><AMOUNT>{amt}</AMOUNT>"
            f"</ALLLEDGERENTRIES.LIST></VOUCHER></TALLYMESSAGE>")
    parts.append("</REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>")
    return "".join(parts).encode("utf-8")


def sized(make, lo: int, hi: int, target: int = CAP - 200_000) -> tuple[list[dict], bytes]:
    """The most invoices whose file still fits under target bytes (a real owner's biggest file the app will accept)."""
    best = None
    while lo <= hi:
        mid = (lo + hi) // 2
        rows = make_rows(mid)
        data = make(rows)
        if len(data) <= target:
            best, lo = (rows, data), mid + 1
        else:
            hi = mid - 1
    return best


# name -> (filename, builder returning (rows_with_truth, bytes), notes)
def build(case: str):
    if case == "csv_1k":
        r = make_rows(1_000); return "dues.csv", r, csv_bytes(r)
    if case == "csv_20k":
        r = make_rows(20_000); return "dues.csv", r, csv_bytes(r)
    if case == "csv_cap":
        r, d = sized(lambda rows: csv_bytes(rows, style="tally"), 50_000, 400_000); return "tally_outstanding.csv", r, d
    if case == "csv_utf16":
        r = make_rows(20_000); return "dues.csv", r, csv_bytes(r, encoding="utf-16", delim="\t", style="tally", title=True)
    if case == "csv_cp1252_semicolon":
        r = make_rows(20_000); return "dues.csv", r, csv_bytes(r, encoding="cp1252", delim=";", title=True, blanks=True)
    if case == "xlsx_1k":
        r = make_rows(1_000); return "dues.xlsx", r, xlsx_bytes(r)
    if case == "xlsx_20k":
        r = make_rows(20_000); return "dues.xlsx", r, xlsx_bytes(r)
    if case == "xlsx_cap":
        r, d = sized(lambda rows: xlsx_bytes(rows), 20_000, 400_000); return "outstanding.xlsx", r, d
    if case == "xlsx_multisheet_text_dates":
        r = make_rows(30_000); return "branches.xlsx", r, xlsx_bytes(r, sheets=4, real_dates=False, phone_as_number=True)
    if case == "tally_xml_1k":
        r = make_rows(1_000); return "daybook.xml", r, tally_xml(r)
    if case == "tally_xml_cap":
        r, d = sized(lambda rows: tally_xml(rows), 5_000, 80_000); return "daybook.xml", r, d
    if case == "tally_xml_with_receipts":
        r = make_rows(5_000); return "daybook.xml", r, tally_xml(r, receipts=0.4)
    raise SystemExit(f"unknown case {case}")


CASES = ["csv_1k", "csv_20k", "csv_cap", "csv_utf16", "csv_cp1252_semicolon", "xlsx_1k", "xlsx_20k", "xlsx_cap",
         "xlsx_multisheet_text_dates", "tally_xml_1k", "tally_xml_cap", "tally_xml_with_receipts"]


def peak_mb() -> float:
    """Highest memory this process has used so far (peak working set on Windows, peak RSS elsewhere)."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
        return round(pmc.PeakWorkingSetSize / 1e6, 1)
    import resource
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e3, 1)


def parse(filename: str, data: bytes, workdir: Path):
    from src.ingest.sheet_mapper import SheetRow, map_sheet
    if filename.endswith(".xml"):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sandbox"))
        import smara_helpers as mh
        p = workdir / filename
        p.write_bytes(data)
        recs = mh.tally_receivables(str(p))
        rows = [SheetRow(r["party"], re.sub(r"\D", "", r["phone"]), str(r["invoice"]), round(float(r["amount"]) * 100), r["due_date"])
                for r in recs if r["due_date"]]
        return rows, []
    from src.ingest.sheet_mapper import map_sheet_detail
    rows, skipped, sheets = map_sheet_detail(filename, data, {})
    return rows, skipped


async def run_case(case: str, db: str, out: Path) -> dict:
    from src.db import migrate, open_pool
    from src.ingest.importer import import_rows
    filename, truth, data = build(case)
    out.mkdir(parents=True, exist_ok=True)
    (out / filename.replace(".", f"_{case}.", 1)).write_bytes(data)
    res = {"case": case, "file": filename, "bytes": len(data), "invoices_in_file": len(truth),
           "rupees_in_file": sum(r["paise"] for r in truth) / 100}
    if len(data) > CAP:
        res["note"] = "over the 20 MB limit: the app would refuse this file before reading it"
    t0 = time.perf_counter()
    rows, skipped = parse(filename, data, out)
    res["parse_s"] = round(time.perf_counter() - t0, 2)
    res["parse_peak_mb"] = peak_mb()
    res["rows_kept"] = len(rows)
    res["rupees_kept"] = sum(r.amount_paise for r in rows) / 100
    res["skipped"] = len(skipped)
    res["skipped_sample"] = skipped[:3]
    refs = {r.invoice_ref for r in rows}
    res["unique_refs_kept"] = len(refs)
    pool = await open_pool(db)
    await migrate(pool)
    tid = str(uuid.uuid4())
    try:
        async with pool.connection() as conn:
            await conn.execute("INSERT INTO tenants (id, name, owner_phone) VALUES (%s, %s, %s)", (tid, f"bigfiles {case}", f"9199{uuid.uuid4().int % 10**8:08d}"))
        t1 = time.perf_counter()
        r1 = await import_rows(pool, tid, rows, source="SHEET")
        res["import_s"] = round(time.perf_counter() - t1, 2)
        res["import_peak_mb"] = peak_mb()
        res["created"], res["updated"] = r1["created"], r1["updated"]
        async with pool.connection() as conn:
            row = await (await conn.execute("SELECT count(*) n, coalesce(sum(amount_paise),0) s, count(DISTINCT party_id) p FROM obligations WHERE tenant_id=%s", (tid,))).fetchone()
            parties = await (await conn.execute("SELECT count(*) n FROM parties WHERE tenant_id=%s", (tid,))).fetchone()
        res["ledger_rows"], res["ledger_rupees"], res["ledger_customers"] = row["n"], int(row["s"]) / 100, parties["n"]
        t2 = time.perf_counter()
        r2 = await import_rows(pool, tid, rows, source="SHEET")
        res["again_s"] = round(time.perf_counter() - t2, 2)
        res["again_created"] = r2["created"]
        async with pool.connection() as conn:
            row2 = await (await conn.execute("SELECT count(*) n, coalesce(sum(amount_paise),0) s FROM obligations WHERE tenant_id=%s", (tid,))).fetchone()
            due = await (await conn.execute("SELECT count(*) n FROM obligations o JOIN parties p ON p.id=o.party_id WHERE o.tenant_id=%s AND o.state='ACTIVE' AND o.next_followup_at <= now() + interval '1 minute' AND p.whatsapp_phone IS NOT NULL", (tid,))).fetchone()
        res["again_changed_ledger"] = (row2["n"], int(row2["s"]) / 100) != (row["n"], int(row["s"]) / 100)
        res["due_now_with_a_number"] = due["n"]
        res["first_reminders_over_days"] = r1.get("first_reminders_over_days", 1)
        res["not_scheduled"] = r1.get("not_scheduled", 0)
        res["customers_in_file"] = len({r["party"] for r in truth})
        res["ok_rows"] = res["ledger_rows"] == res["invoices_in_file"]
        res["ok_rupees"] = abs(res["ledger_rupees"] - res["rupees_in_file"]) < 0.01
    finally:
        async with pool.connection() as conn:
            await conn.execute("DELETE FROM obligation_events WHERE obligation_id IN (SELECT id FROM obligations WHERE tenant_id=%s)", (tid,))
            await conn.execute("DELETE FROM obligations WHERE tenant_id=%s", (tid,))
            await conn.execute("DELETE FROM parties WHERE tenant_id=%s", (tid,))
            await conn.execute("DELETE FROM tenants WHERE id=%s", (tid,))
        await pool.close()
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--case")        # internal: run one case in this process and print its result as JSON
    a = ap.parse_args()
    name = re.sub(r"\?.*$", "", a.db).rsplit("/", 1)[-1]
    if not name.endswith("_eval"):
        sys.exit(f"refusing to run: the database must end in _eval (got {name!r})")
    out = Path(a.out)
    if a.case:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        print("RESULT " + json.dumps(asyncio.run(run_case(a.case, a.db, out)), default=str))
        return
    results = []
    for case in a.only or CASES:
        p = subprocess.run([sys.executable, "-m", "evals.bigfiles", "--db", a.db, "--out", str(out), "--case", case],
                           capture_output=True, text=True, timeout=3600)
        line = next((l for l in p.stdout.splitlines() if l.startswith("RESULT ")), None)
        results.append(json.loads(line[7:]) if line else {"case": case, "error": (p.stderr or p.stdout)[-600:]})
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    (out / "bigfiles_results.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
