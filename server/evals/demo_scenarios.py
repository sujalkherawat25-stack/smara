"""Demo readiness run: the scenarios a shop owner would see in a demo, against a realistic shop, scored from ledger state.

    python -m evals.demo_scenarios --db postgresql://.../smara_eval --model glm5.3-flash --sandbox local

Reuses evals.agent_harness (real model, real loop, scratch Postgres, fake WhatsApp). The harness DROPs the public schema, so the
database name must end in _eval. Scenarios marked REVIEW have no automatic pass: read the reply and judge it.
"""
import asyncio
import selectors
import sys
from datetime import date, timedelta
from types import SimpleNamespace

from evals import agent_harness as h
from evals.agent_harness import OWNER, T, Task, has_num, owner, q
from src import clock, tenants
from src.db import migrate
from src.ingest.dispatch import dispatch
from src.ingest.importer import import_rows
from src.ingest.payload import InboundMessage
from src.ingest.sheet_mapper import SheetRow
from src.loop.digest import build_digest

GUPTA, SHARMA = "919811100004", "919811100003"
CUR = {"name": ""}


def d(days_overdue: int) -> date:
    return T - timedelta(days=days_overdue)


def rs(r: float) -> int:
    return int(round(r * 100))


# ---- the shop -------------------------------------------------------------------------------------------------------------
FULL = [  # customer, phone, ref, rupees, days overdue (negative = due in future days)
    ("Gupta Traders", GUPTA, "G-301", 7800, 20), ("Gupta Traders", GUPTA, "G-340", 12000, 46),
    ("Ravi Kirana", "919876543210", "INV-101", 12500, 13), ("Ravi Kirana", "919876543210", "INV-102", 4075, 4),
    ("Sharma Store", SHARMA, "S-201", 20000, -16), ("Mehta Kirana", "919811100005", "M-401", 3300, -3),
    ("Agarwal Agencies", "919811100006", "A-501", 220000, 55), ("Singh Electronics", "919811100007", "E-601", 98750, 31),
    ("Jain Hardware", "919811100008", "J-701", 31000, 8), ("Pandey Sweets", "919811100009", "P-801", 15300, 2),
]
TOTAL_ALL, TOTAL_OVERDUE = 424_725, 401_425          # rupees; checked by hand against FULL above
OVER_45 = 232_000                                       # Agarwal 220,000 + Gupta G-340 12,000

PAYABLES = [  # supplier, gstin, bill_ref, days since bill, rupees, class, written 45-day agreement
    ("Kumar Steel", "07AAAPK1111A1Z1", "K-1", 12, 11800, "micro", False),      # 43B(h) deadline in 3 days
    ("Delhi Pipes", "07AAAPD2222B1Z2", "D-7", 20, 5900, "small", False),       # deadline passed 5 days ago
    ("Rao Bearings", "07AAAPR3333C1Z3", "R-9", 30, 9440, "other", False),      # not an MSME: no 43B(h) pressure
    ("Shah Fasteners", "07AAAPS4444D1Z4", "S-3", 10, 1180, "micro", True),     # deadline 35 days away
]

MESSY = h._xlsx([["Mehta Distributors"], ["Bills Receivable as on today"], [],
                 ["Date", "Ref. No.", "Party's Name", "Mobile", "Pending Amount", "Due on"],
                 ["01-09-2026", "B-11", "Anand Stores", "98100 11111", "8,400.00 Dr", "01-10-2026"],
                 ["03-10-2026", "B-12", "Bhatia & Co", "9810022222", "15,250.50 Dr", "02-11-2026"],
                 ["09-10-2026", "B-13", "Anand Stores", "98100 11111", "1,100.00 Dr", "08-11-2026"],
                 ["12-08-2026", "B-14", "Chopra Sons", None, "42,000.00 Dr", "11-09-2026"],
                 ["20-10-2026", "B-15", "Dutta Agencies", None, "6,500.00 Dr", "19-11-2026"],
                 ["25-09-2026", "B-16", "Ejaz Textiles", "9811133333", "27,300.00 Dr", "25-10-2026"],
                 [None, None, "Total", None, "1,00,550.50 Dr", None]])
BOOKS = h._xlsx([["Purchase Register Oct 2026"], [],
                 ["Date", "Supplier", "Invoice", "Taxable", "CGST", "SGST", "IGST", "Total"],
                 ["02-10-2026", "Kumar Steel", "K-1", 10000, 900, 900, 0, 11800],
                 ["11-10-2026", "Delhi Pipes", "D-7", 5000, 0, 0, 900, 5900],
                 ["20-10-2026", "Kumar Steel", "K-4", 2500, 225, 225, 0, 2950],
                 ["22-10-2026", "Rao Bearings", "R-9", 8000, 720, 720, 0, 9440]])
GSTR2B = h._xlsx([["GSTR-2B for Oct 2026 (B2B invoices)"], [],
                  ["GSTIN of supplier", "Trade name", "Invoice number", "Invoice date", "Taxable value", "Integrated tax", "Central tax", "State/UT tax"],
                  ["07AAAPK1111A1Z1", "Kumar Steel", "K-1", "02-10-2026", 10000, 0, 900, 900],
                  ["07AAAPD2222B1Z2", "Delhi Pipes", "D-7", "11-10-2026", 2500, 450, 0, 0],
                  ["07AAAPR3333C1Z3", "Rao Bearings", "R-9", "22-10-2026", 8000, 0, 720, 720],
                  ["07AAAPS4444D1Z4", "Shah Fasteners", "S-3", "25-10-2026", 1000, 0, 90, 90]])
h.FILES.update({"MX5": ("outstanding_messy.xlsx", MESSY), "MX6": ("purchase_register_oct.xlsx", BOOKS), "MX7": ("GSTR2B_oct.xlsx", GSTR2B)})


# Files go to a throwaway folder (never the real owner-files volume), and the download comes from the in-memory FILES.
import tempfile
from pathlib import Path

from src.box.sandbox import Sandbox
from src.config import Settings as _Settings
from src.ingest import owner_flow

_DIR = tempfile.mkdtemp(prefix="demo-owner-files-")
h.Settings = lambda: _Settings(owner_files_dir=_DIR)


async def _fetch_media(app, msg):
    return h.FILES[msg.media_id][1], msg.mime or "application/octet-stream"


owner_flow.fetch_media = _fetch_media
_orig_read_output = Sandbox.read_output


def _read_output(self, mid, name):
    hits = list(Path(_DIR).glob(f"*/{mid}"))          # a kept owner file: local:<token>:<name>
    return hits[0].read_bytes() if hits else _orig_read_output(self, mid, name)


Sandbox.read_output = _read_output


async def seed(pool):
    async with pool.connection() as c:
        await c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    await migrate(pool)
    t = await tenants.create(pool, name="Mehta Distributors", owner_phone=OWNER, upi_vpa="demo@upi")
    if CUR["name"] not in ("first_file_import", "first_file_then_numbers"):
        await import_rows(pool, t.id, [SheetRow(n, p, r, rs(a), d(o)) for n, p, r, a, o in FULL])
        if CUR["name"].startswith("add_"):
            await import_rows(pool, t.id, [SheetRow("Chopra Sons", "", "C-1", rs(42000), d(60)),
                                           SheetRow("Dutta Agencies", "", "D-1", rs(6500), d(-6))])
        async with pool.connection() as c:
            await c.execute("update obligations set next_followup_at = now() + interval '30 days'")
            await c.execute("update parties set last_inbound_at = now()")
    async with pool.connection() as c:
        await c.execute("update tenants set owner_last_inbound_at = now()")
        if CUR["name"] == "payables_43bh":
            for sup, gst, ref, ago, amt, cls, ag in PAYABLES:
                await c.execute("""INSERT INTO payables (tenant_id, supplier, supplier_gstin, bill_ref, bill_date, amount_paise,
                                   msme_class, agreement_45) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                                (t.id, sup, gst, ref, T - timedelta(days=ago), rs(amt), cls, ag))
    return t


_orig_run_task = h.run_task


async def run_task(env, task):
    CUR["name"] = task.name
    return await _orig_run_task(env, task)


def alltext(env) -> str:
    out = []
    for m in env.wa.to(OWNER):
        out.append(m.get("text", {}).get("body", "") or m.get("interactive", {}).get("body", {}).get("text", ""))
    return "\n".join(out)


async def waiting_sends(env):
    s = await q(env, "select pending from agent_sessions where status = 'WAITING_APPROVAL'")
    return [x["pending"] for x in s if x["pending"].get("name") == "send_whatsapp_message"]


# ---- checks ---------------------------------------------------------------------------------------------------------------
async def c_first_file(env):
    ok, detail = await h.c_import(env, ["B-11", "B-12", "B-13", "B-14", "B-15", "B-16"], 10_055_050)
    t = alltext(env).lower()
    says_debtor = "chopra" in t and has_num(t, "42,000")
    says_gap = any(w in t for w in ("number", "phone", "mobile", "नंबर")) and "chopra" in t and "dutta" in t
    return ok and says_debtor and says_gap, f"import={detail} biggest_debtor_said={says_debtor} missing_numbers_said={says_gap}"


async def c_call_list(env):
    t = alltext(env).lower()
    order = [t.find(n) for n in ("agarwal", "singh")]          # the third place depends on how the agent weighs days overdue
    return all(i >= 0 for i in order) and order == sorted(order), f"positions={order} said={t[:220]!r}"


async def c_new_sale(env):
    rows = await q(env, "select o.invoice_ref, o.amount_paise, o.due_date, o.source from obligations o join parties p on p.id = o.party_id "
                        "where p.canonical_name ilike 'sharma%%' and o.invoice_ref <> 'S-201'")
    said = alltext(env)
    ok = len(rows) == 1 and rows[0]["amount_paise"] == 1_200_000 and rows[0]["due_date"] == clock.today() + timedelta(days=15)
    return ok, f"new_rows={[tuple(r.values()) for r in rows]} said={said[-300:]!r}"


async def c_cash_no_ref(env):
    r = {x["invoice_ref"]: x for x in await q(env, "select invoice_ref, amount_paise, state from obligations where invoice_ref in ('G-301','G-340')")}
    ok = r["G-340"]["amount_paise"] == 700_000 and r["G-340"]["state"] != "PAID" and r["G-301"]["amount_paise"] == 780_000
    return ok, f"G-340={r['G-340']['amount_paise']} G-301={r['G-301']['amount_paise']} said={alltext(env)[-250:]!r}"


async def c_cash_full(env):
    r = await q(env, "select invoice_ref, state from obligations where invoice_ref in ('INV-101','INV-102')")
    return len(r) == 2 and all(x["state"] == "PAID" for x in r), f"states={[tuple(x.values()) for x in r]} said={alltext(env)[-250:]!r}"


async def c_cash_unknown(env):
    r = await q(env, "select count(*) n from obligations where state = 'PAID'")
    t = alltext(env).lower()
    return r[0]["n"] == 0 and "verma" in t, f"paid={r[0]['n']} said={t[-250:]!r}"


async def c_review(env):
    return True, "REVIEW: " + alltext(env)[:400].replace("\n", " ")


async def c_reminder(env):
    s = await waiting_sends(env)
    text = " ".join(x["args"].get("text", "") for x in s)
    amounts = any(n in text.replace(",", "") for n in ("19800", "12000", "7800"))
    upi = "demo@upi" in text.lower() or "upi://" in text.lower()
    ok = bool(s) and amounts and upi and not env.wa.to(GUPTA)
    trail = ""
    if not ok:
        rows = await q(env, """select m->>'role' r, coalesce(m->>'content','') c, m->'tool_calls' t from agent_sessions,
                               jsonb_array_elements(messages) m where m->>'role' in ('assistant','tool')""")
        steps = []
        for r in rows:
            if r["t"]:
                steps.append("CALL " + "; ".join(f"{c['function']['name']}({c['function']['arguments'][:160]})" for c in r["t"]))
            elif r["r"] == "tool":
                steps.append("RESULT " + r["c"][:220])
        trail = " | TRAIL: " + " || ".join(steps)[:2500]
    return ok, f"pending_text={text[:200]!r} amounts={amounts} upi={upi}{trail}"


async def c_promise(env):
    p = await q(env, "select adjustment_type, status from proposed_adjustments")
    hit = [x for x in p if x["adjustment_type"] == "PROMISE_DATE_CHANGE" and x["status"] == "PENDING"]
    return bool(hit) and not env.wa.to(GUPTA), f"proposals={[tuple(x.values()) for x in p]}"


async def ev_gst_two_files(env):
    await dispatch(env.app, owner("Ye mera purchase register hai (books).", "g1", "MX6"))
    await dispatch(env.app, owner("Ye GSTR-2B hai. Dono match karo: kaunsa ITC miss ya kam hai, aur kis supplier ko chase karun?", "g2", "MX7"))


async def c_gst(env):
    t = alltext(env).lower()
    has = {"kumar": "kumar" in t, "delhi": "delhi" in t, "450": "450" in t.replace(",", ""), "shah": "shah" in t}
    n = (await q(env, "select count(*) n from obligations"))[0]["n"]          # purchase bills must never land in the receivables
    return has["kumar"] and has["delhi"] and has["450"] and n == len(FULL), f"found={has} obligations={n} (expected {len(FULL)}) said={t[-300:]!r}"


async def c_43bh(env):
    t = alltext(env).lower()
    ok = "delhi" in t and "kumar" in t
    return ok, f"delhi={'delhi' in t} kumar={'kumar' in t} rao_mentioned={'rao' in t} said={t[:300]!r}"


async def c_pdf_gupta(env):
    if not env.wa.uploads:
        return False, f"no pdf uploaded; said={alltext(env)[:200]!r}"
    name, mime, data = env.wa.uploads[-1]
    import io
    from pypdf import PdfReader
    text = " ".join(pg.extract_text() or "" for pg in PdfReader(io.BytesIO(data)).pages)
    flat = text.replace(",", "")
    return name.endswith(".pdf") and "G-301" in text and "G-340" in text and "19800" in flat, f"{name} {text[:100]!r}"


async def c_summary(env):
    t = alltext(env)
    return has_num(t, "424,725", "401,425"), t[:200]


async def c_devanagari(env):
    return has_num(alltext(env), "19,800"), alltext(env)[:200]


async def c_over45(env):
    return has_num(alltext(env), "232,000"), alltext(env)[:200]


async def c_phone_added(env):
    r = await q(env, "select whatsapp_phone from parties where canonical_name ilike 'chopra%%'")
    return bool(r) and (r[0]["whatsapp_phone"] or "").endswith("9811144444"), f"phone_now={[x['whatsapp_phone'] for x in r]} said={alltext(env)[:200]!r}"


async def c_phone_list(env):
    r = {x["canonical_name"]: x["whatsapp_phone"] for x in await q(env, "select canonical_name, whatsapp_phone from parties where canonical_name in ('Chopra Sons','Dutta Agencies')")}
    return r.get("Chopra Sons") == "919811144444" and r.get("Dutta Agencies") == "919822255555", f"phones={r} said={alltext(env)[-250:]!r}"


async def c_phone_dup(env):
    r = await q(env, "select whatsapp_phone from parties where canonical_name = 'Chopra Sons'")
    return r[0]["whatsapp_phone"] is None and "ravi" in alltext(env).lower(), f"phone={r[0]['whatsapp_phone']} said={alltext(env)[-250:]!r}"


async def ev_file_then_numbers(env):
    await dispatch(env.app, owner("Ye mera outstanding hai.", "n1", "MX5"))
    await dispatch(env.app, owner("Chopra Sons 9811144444 aur Dutta Agencies 98222 55555", "n2"))


async def c_file_then_numbers(env):
    n = (await q(env, "select count(*) n from obligations"))[0]["n"]
    r = {x["canonical_name"]: x["whatsapp_phone"] for x in await q(env, "select canonical_name, whatsapp_phone from parties where canonical_name in ('Chopra Sons','Dutta Agencies')")}
    return n == 6 and r.get("Chopra Sons") == "919811144444" and r.get("Dutta Agencies") == "919822255555", f"obligations={n} phones={r} said={alltext(env)[-250:]!r}"


async def ev_digest(env):
    async with env.pool.connection() as c:
        tid = (await (await c.execute("select id from tenants where owner_phone = %s", (OWNER,))).fetchone())["id"]
    env.digest = await build_digest(env.pool, str(tid), T)


async def c_digest(env):
    t = env.digest
    return has_num(t, "401425") and "agarwal" in t.lower(), t[:300]


TASKS = [
    Task("first_file_import", ("owner", owner("Ye mera outstanding hai, dekh lo.", "d1", "MX5")), c_first_file),
    Task("call_list_top_debtors", ("owner", owner("Aaj kisko call karun? Sabse zyada overdue wale top 3 batao, amount ke saath.", "d2")), c_call_list),
    Task("summary_total_overdue", ("owner", owner("Business ka summary do: kul kitna baaki hai aur usme se kitna overdue hai?", "d3")), c_summary),
    Task("ageing_over_45_days", ("owner", owner("45 din se zyada purana kitna paisa baaki hai?", "d4")), c_over45),
    Task("devanagari_question", ("owner", owner("गुप्ता ट्रेडर्स का कितना बाकी है?", "d5")), c_devanagari),
    Task("khata_new_credit_sale", ("owner", owner("Sharma Store ko aaj 12000 ka maal udhaar diya, 15 din mein dega.", "d6")), c_new_sale, approve=True),
    Task("khata_cash_received_no_invoice_no", ("owner", owner("Gupta Traders ne 5000 cash diya.", "d7")), c_cash_no_ref, approve=True),
    Task("khata_cash_full_payment", ("owner", owner("Ravi Kirana ne poora 16575 cash de diya.", "d7b")), c_cash_full, approve=True),
    Task("khata_cash_unknown_customer", ("owner", owner("Verma ne 5000 cash diya.", "d7c")), c_cash_unknown, approve=True),
    Task("expense_entry", ("owner", owner("Aaj bijli ka bill 2300 bhara, expense mein likh do.", "d8")), c_review),
    Task("stock_question", ("owner", owner("Godown mein abhi kitna stock bacha hai?", "d9")), c_review),
    Task("reminder_with_upi_needs_approval", ("owner", owner("Gupta Traders ko payment ka reminder bhejo, UPI ke saath.", "d10")), c_reminder),
    Task("customer_promise_hinglish", ("debtor", InboundMessage(wamid="d11", sender=GUPTA, kind="text",
         text="Bhaiya parso shaam tak pakka bhej dunga, thoda time do.")), c_promise),
    Task("statement_pdf_customer", ("owner", owner("Gupta Traders ka statement PDF bana ke bhejo.", "d12")), c_pdf_gupta),
    Task("gst_2b_two_files", ("custom", ev_gst_two_files), c_gst),
    Task("payables_43bh", ("owner", owner("Is hafte kin suppliers ko payment karna zaroori hai taaki 43B(h) ka nuksan na ho?", "d14")), c_43bh),
    Task("add_missing_phone", ("owner", owner("Chopra Sons ka number 9811144444 hai, add kar do.", "d15")), c_phone_added),
    Task("add_phone_list", ("owner", owner("Chopra Sons 9811144444, Dutta Agencies 98222 55555", "d16")), c_phone_list),
    Task("add_phone_duplicate", ("owner", owner("Chopra Sons ka number 9876543210 hai.", "d17")), c_phone_dup),
    Task("first_file_then_numbers", ("custom", ev_file_then_numbers), c_file_then_numbers),
    Task("morning_digest", ("custom", ev_digest), c_digest),
]




def _xlsx_cells(data: bytes):
    import io
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    return [str(c.value) for ws in wb for row in ws.iter_rows() for c in row if c.value is not None]


async def c_excel_ledger(env):
    xl = [u for u in env.wa.uploads if u[0].lower().endswith(".xlsx")]
    if not xl:
        return False, f"no xlsx sent; uploads={[u[0] for u in env.wa.uploads]}"
    cells = _xlsx_cells(xl[-1][2])
    joined = " ".join(cells)
    missing = [r[2] for r in FULL if r[2] not in joined]
    nums = []
    for c in cells:
        try:
            nums.append(float(c))
        except ValueError:
            pass
    ok_amounts = all(any(abs(n - r[3]) < 0.01 or abs(n - r[3] * 100) < 0.01 for n in nums) for r in FULL)
    return not missing and ok_amounts, f"xlsx={xl[-1][0]} missing_refs={missing} amounts_ok={ok_amounts}"


async def c_excel_one_customer(env):
    xl = [u for u in env.wa.uploads if u[0].lower().endswith(".xlsx")]
    if not xl:
        return False, f"no xlsx sent; uploads={[u[0] for u in env.wa.uploads]}"
    joined = " ".join(_xlsx_cells(xl[-1][2]))
    return "G-301" in joined and "G-340" in joined and "A-501" not in joined, f"xlsx={xl[-1][0]} cells={joined[:150]}"


TASKS += [
    Task("excel_export_all", ("owner", owner("Saare customers ka outstanding ek Excel sheet mein bhejo.", "x1")), c_excel_ledger),
    Task("excel_export_one_customer", ("owner", owner("Gupta Traders ka hisaab Excel mein bhej do.", "x2")), c_excel_one_customer),
]

def _owner_bodies(env):
    out = []
    for m in env.wa.to(OWNER):
        if m.get("type") == "interactive":
            out.append(("card", m["interactive"]["body"]["text"]))
        elif m.get("type") == "text":
            out.append(("text", m["text"]["body"]))
    return out


def format_issues(env) -> list[str]:
    """Markdown or limits that would look broken in a phone chat."""
    import re as _re
    bad = []
    for kind, body in _owner_bodies(env):
        if len(body) > (1000 if kind == "card" else 1500):
            bad.append(f"{kind} too long ({len(body)})")
        if "**" in body:
            bad.append("double asterisks")
        if "](http" in body:
            bad.append("markdown link")
        for line in body.split("\n"):
            if line.lstrip().startswith("#"):
                bad.append(f"heading: {line[:40]}")
            if line.count(" | ") >= 2 or _re.match(r"^\s*\|.*\|\s*$", line):
                bad.append(f"table: {line[:40]}")
            if "```" not in line:
                marks = [i for i, ch in enumerate(line) if ch == "*" and not (
                    (i == 0 or line[i - 1].isspace()) and (i + 1 >= len(line) or line[i + 1].isspace()))]
                if len(marks) % 2:
                    bad.append(f"lone *: {line[:50]}")
    return bad


async def c_fmt_long(env):
    bodies = _owner_bodies(env)
    issues = format_issues(env)
    text = " ".join(b for _, b in bodies)
    names = all(n.split()[0].lower() in text.lower() for n in ("Gupta", "Ravi", "Sharma", "Agarwal", "Singh", "Jain", "Pandey", "Mehta"))
    return not issues and names, f"messages={len(bodies)} lens={[len(b) for _, b in bodies]} issues={issues[:5]} names_ok={names}"


async def c_fmt_call(env):
    issues = format_issues(env)
    ok, detail = await c_call_list(env)
    return ok and not issues, f"{detail} issues={issues[:5]}"


async def ev_pref_hindi_short(env):
    from src import prefs
    t = await tenants.by_owner_phone(env.pool, OWNER)
    await prefs.put(env.pool, t.id, language="hindi", length="short")
    await dispatch(env.app, owner("Gupta Traders ka kitna baaki hai?", "p1"))


async def c_pref_hindi_short(env):
    import re as _re
    last = env.wa.last_owner_text()
    deva = len(_re.findall(r"[ऀ-ॿ]", last))
    return deva >= 10 and len(last) <= 450 and has_num(last, "19,800") and not format_issues(env), \
        f"devanagari={deva} len={len(last)} issues={format_issues(env)[:3]}"


async def c_pref_by_chat(env):
    from src import prefs
    t = await tenants.by_owner_phone(env.pool, OWNER)
    p = await prefs.get(env.pool, t.id)
    return p["language"] == "english" and p["length"] == "short", f"prefs={p}"


TASKS += [
    Task("fmt_long_list", ("owner", owner("Saare customers ki poori list do: naam, invoice, amount, due date aur kitne din overdue.", "f1")), c_fmt_long),
    Task("fmt_call_list", ("owner", owner("Aaj kisko call karun? Sabse zyada overdue wale top 3 batao, amount ke saath.", "f2")), c_fmt_call),
    Task("pref_hindi_short", ("custom", ev_pref_hindi_short), c_pref_hindi_short),
    Task("pref_set_by_chat", ("owner", owner("Aage se mujhe English mein aur chhote jawab dena.", "f3")), c_pref_by_chat),
]


async def q(env, sql):
    async with env.pool.connection() as conn:
        return await (await conn.execute(sql)).fetchall()


# --- open agent + skills (this branch) ---------------------------------------------------------------------------------
async def c_open_expense_sheet(env):
    xl = [u for u in env.wa.uploads if u[0].lower().endswith(".xlsx")]
    if not xl:
        return False, f"no xlsx sent; uploads={[u[0] for u in env.wa.uploads]}; reply={env.wa.last_owner_text()[:160]!r}"
    cells = _xlsx_cells(xl[-1][2])
    nums = set()
    for c in cells:
        try:
            nums.add(float(c))
        except ValueError:
            pass
    ok = {15000.0, 3200.0, 22000.0} <= nums and 40200.0 in nums
    return ok, f"nums={sorted(nums)[:10]} reply={env.wa.last_owner_text()[:100]!r}"


async def c_open_hindi_draft(env):
    import re as _re
    last = env.wa.last_owner_text()
    deva = len(_re.findall(r"[ऀ-ॿ]", last))
    refused = any(w in last.lower() for w in ("cannot", "can't", "nahi kar sakta", "mere paas ye suvidha"))
    return deva >= 80 and not refused, f"devanagari={deva} refused={refused} reply={last[:140]!r}"


async def ev_skill_flow(env):
    await dispatch(env.app, owner("Gupta Traders, Ravi Kirana Store aur Sharma ka alag-alag statement ek hi Excel mein bana do, har customer ki alag sheet, aur total last mein.", "k1"))
    await dispatch(env.app, owner("Haan, isko skill bana lo.", "k2"))
    env.skill_rows_after_save = await q(env, "select name, description, version from owner_skills")
    async with env.pool.connection() as conn:     # the next request comes another day, as a new conversation
        await conn.execute("UPDATE agent_sessions SET updated_at = now() - interval '2 days', created_at = now() - interval '2 days'")
    await dispatch(env.app, owner("Wahi kaam Singh aur Jain ke liye karo.", "k3"))


async def c_skill_flow(env):
    rows = getattr(env, "skill_rows_after_save", [])
    after = await q(env, "select name, uses from owner_skills")
    xl = [u for u in env.wa.uploads if u[0].lower().endswith(".xlsx")]
    return bool(rows) and bool(after) and after[0]["uses"] >= 1 and len(xl) >= 2, \
        f"saved={[(r['name'], r['version']) for r in rows]} uses={[(r['name'], r['uses']) for r in after]} xlsx={len(xl)}"


TASKS += [
    Task("open_expense_sheet", ("owner", owner("Mere shop ke kharche track karne hain. Ek Excel bana do jisme kiraya 15000, bijli 3200 aur salary 22000 ho, aur total bhi.", "o1")), c_open_expense_sheet),
    Task("open_hindi_draft", ("owner", owner("GST department ko late filing ke liye maafi maangte hue ek chhota letter Hindi mein draft karo.", "o2")), c_open_hindi_draft),
    Task("skill_flow", ("custom", ev_skill_flow), c_skill_flow),
]


async def _clear_ledger(env):
    async with env.pool.connection() as conn:       # a user who has never loaded any books
        await conn.execute("TRUNCATE obligations, parties CASCADE")       # eval database only


async def ev_personal_plan(env):
    await _clear_ledger(env)
    await dispatch(env.app, owner("Mujhe 4 hafte mein SQL seekhni hai, roz 1 ghanta. Ek simple weekly plan bana do.", "pp1"))


async def ev_personal_email(env):
    await _clear_ledger(env)
    await dispatch(env.app, owner("Mere manager ko ek polite email likh do: main Friday ko chhutti par rahunga, kaam Thursday tak khatam kar dunga.", "pp2"))


def _business_context(text):
    import re as _re
    return [text[max(0, m.start() - 30): m.end() + 30] for m in _re.finditer(r"(invoice|invoices|ledger|outstanding|overdue|udhaar|khata)", text.lower())]


async def c_personal_plan(env):
    last = env.wa.last_owner_text()
    ctx = _business_context(last)
    refused = any(w in last.lower() for w in ("cannot help", "can't help", "i can only", "sirf business", "nahi kar sakta"))
    return len(last) >= 150 and not ctx and not refused, f"len={len(last)} business_context={ctx[:3]} refused={refused}"


async def c_personal_write(env):
    last = env.wa.last_owner_text()
    ctx = _business_context(last)
    refused = any(w in last.lower() for w in ("cannot help", "can't help", "i can only", "sirf business", "nahi kar sakta"))
    return len(last) >= 200 and not ctx and not refused, f"len={len(last)} business_context={ctx[:3]} refused={refused} reply={last[:120]!r}"


TASKS += [
    Task("personal_study_plan", ("custom", ev_personal_plan), c_personal_plan),
    Task("personal_write_email", ("custom", ev_personal_email), c_personal_write),
]


if __name__ == "__main__":
    h.TASKS[:] = TASKS
    h.seed = seed
    h.run_task = run_task
    asyncio.run(h.main(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
