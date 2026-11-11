"""make_document: print-quality PDF / Word documents whose numbers come from the ledger, not from the model.

The server reads the database, builds a plain JSON spec (see sandbox/smara_docs.py) and the isolated sandbox renders it.
The model only chooses which document to make and, for a letter or a custom report, supplies the words.
"""
import json
import re
from datetime import date, timedelta

from src import clock, tenants
from src.agent.money import inr
from src.agent.policy import SessionCtx
from src.agent.tools import party_in_tenant
from src.box.sandbox import safe_name

KINDS = ("statement", "payables_schedule", "owner_report", "reminder_letter", "custom")
FORMATS = ("pdf", "docx")
MAX_BLOCKS, MAX_ROWS, MAX_COLS, MAX_TEXT = 60, 500, 12, 3000
BLOCK_TYPES = frozenset({"heading", "paragraph", "table", "stats", "keyvalues", "bar_chart", "note"})
AGING = (("Not yet due", None, 0), ("1–30 days", 1, 30), ("31–60 days", 31, 60), ("61–90 days", 61, 90), ("Over 90 days", 91, 10 ** 6))

RENDER = '''import json, smara_docs
spec = json.load(open("in/spec.json", encoding="utf-8"))
fmt = spec.pop("_format")
name = spec.pop("_filename")
RESULT = smara_docs.render(spec, "out/" + name, fmt)
'''


def rs(paise) -> float:
    return int(paise or 0) / 100


def money(paise) -> str:
    return inr(rs(paise))


def bills(n: int) -> str:
    return f"{n} bill{'' if n == 1 else 's'}"


def dmy(d: date) -> str:
    return d.strftime("%d %b %Y")


def _frame(tenant, title: str, subtitle: str, meta: list, today: date) -> dict:
    return {"title": title, "subtitle": subtitle, "shop": {"name": tenant.name, "line": ""}, "meta": meta, "blocks": [],
            "footer": f"Prepared by Smara for {tenant.name} · {dmy(today)}"}


def _bucket(days_overdue: int) -> str:
    for label, lo, hi in AGING:
        if (lo is None and days_overdue <= 0) or (lo is not None and lo <= days_overdue <= hi):
            return label
    return AGING[-1][0]


async def _open_invoices(pool, tenant_id: str, party_id: str | None = None) -> list[dict]:
    sql = ("SELECT p.id party_id, p.canonical_name, o.invoice_ref, o.amount_paise, o.due_date, o.state "
           "FROM obligations o JOIN parties p ON p.id = o.party_id "
           "WHERE o.tenant_id = %s AND o.state NOT IN ('PAID','DRAFT')" + (" AND o.party_id = %s" if party_id else "") +
           " ORDER BY o.due_date, o.invoice_ref")
    async with pool.connection() as conn:
        return await (await conn.execute(sql, (tenant_id, party_id) if party_id else (tenant_id,))).fetchall()


def _pay_line(tenant) -> str:
    return f"Pay by UPI to {tenant.upi_vpa}" + (f" ({tenant.upi_name})" if tenant.upi_name else "") if tenant.upi_vpa else ""


INVOICE_COLUMNS = [{"label": "Invoice", "align": "left"}, {"label": "Due date", "align": "left"},
                   {"label": "Days overdue", "align": "right"}, {"label": "Amount", "kind": "money"}]


def _invoice_rows(rows: list[dict], today: date) -> tuple[list, int]:
    out, total = [], 0
    for r in rows:
        late = (today - r["due_date"]).days
        out.append([r["invoice_ref"], dmy(r["due_date"]), str(late) if late > 0 else "–", rs(r["amount_paise"])])
        total += r["amount_paise"]
    return out, total


async def statement(app, tenant, args: dict, today: date) -> tuple[dict, dict]:
    party = await party_in_tenant(app.state.pool, tenant.id, args.get("party_id"))
    if not party:
        raise ValueError("party_id must be a customer of this shop")
    rows = await _open_invoices(app.state.pool, tenant.id, str(party["id"]))
    body, total = _invoice_rows(rows, today)
    overdue = sum(r["amount_paise"] for r in rows if r["due_date"] < today)
    buckets = {label: 0 for label, *_ in AGING}
    for r in rows:
        buckets[_bucket((today - r["due_date"]).days)] += r["amount_paise"]
    spec = _frame(tenant, "Statement of account", party["canonical_name"],
                  [["Statement date", dmy(today)], ["Customer", party["canonical_name"]],
                   ["Open invoices", str(len(rows))]], today)
    spec["blocks"] = [
        {"type": "stats", "items": [{"label": "Total outstanding", "value": money(total)},
                                     {"label": "Overdue", "value": money(overdue), "tone": "bad" if overdue else "good"},
                                     {"label": "Open invoices", "value": str(len(rows))}]},
        {"type": "table", "caption": "Open invoices", "columns": INVOICE_COLUMNS, "rows": body,
         "total_row": ["Total outstanding", "", "", rs(total)] if rows else None, "empty": "No open invoices. This account is clear."},
    ]
    if rows:
        spec["blocks"] += [{"type": "bar_chart", "title": "How old is the outstanding amount", "items": [
            {"label": k, "value": rs(v)} for k, v in buckets.items()]}]
    notes = ["Amounts are in Indian rupees. If you have already paid, please share the UTR / payment reference so we can update the account."]
    if _pay_line(tenant):
        notes.insert(0, _pay_line(tenant) + ". Please quote the invoice number.")
    spec["blocks"] += [{"type": "note", "text": " ".join(notes)}]
    return spec, {"customer": party["canonical_name"], "open_invoices": len(rows), "outstanding_rupees": rs(total), "overdue_rupees": rs(overdue)}


async def reminder_letter(app, tenant, args: dict, today: date) -> tuple[dict, dict]:
    party = await party_in_tenant(app.state.pool, tenant.id, args.get("party_id"))
    if not party:
        raise ValueError("party_id must be a customer of this shop")
    text = str(args.get("body") or "").strip()
    if not text:
        raise ValueError("body is required: the main paragraph(s) of the letter, without a greeting or sign-off")
    rows = [r for r in await _open_invoices(app.state.pool, tenant.id, str(party["id"])) if r["due_date"] < today]
    body, total = _invoice_rows(rows, today)
    spec = _frame(tenant, "Payment reminder", f"{money(total)} overdue",
                  [["Date", dmy(today)], ["To", party["canonical_name"]]], today)
    spec["blocks"] = [{"type": "letter", "text": f"Dear {party['canonical_name']},\n\n{text[:1500]}"},
                      {"type": "table", "caption": "Overdue invoices", "columns": INVOICE_COLUMNS, "rows": body,
                       "total_row": ["Total overdue", "", "", rs(total)] if rows else None, "empty": "No overdue invoices."}]
    if _pay_line(tenant):
        spec["blocks"].append({"type": "note", "text": _pay_line(tenant) + ". Please share the UTR after payment."})
    spec["blocks"] += [{"type": "letter", "text": f"Thank you,\n{tenant.name}"}]
    return spec, {"customer": party["canonical_name"], "overdue_invoices": len(rows), "overdue_rupees": rs(total)}


async def _payables(pool, tenant_id: str) -> list[dict]:
    async with pool.connection() as conn:
        return await (await conn.execute(
            """SELECT supplier, bill_ref, bill_date, amount_paise, msme_class, agreement_45,
                      CASE WHEN msme_class IN ('micro','small') THEN bill_date + CASE WHEN agreement_45 THEN 45 ELSE 15 END END AS deadline
                 FROM payables WHERE tenant_id = %s AND paid_at IS NULL ORDER BY deadline NULLS LAST, bill_date""", (tenant_id,))).fetchall()


async def payables_schedule(app, tenant, args: dict, today: date) -> tuple[dict, dict]:
    rows = await _payables(app.state.pool, tenant.id)
    tracked = [r for r in rows if r["deadline"]]
    unknown = [r for r in rows if not r["deadline"] and r["msme_class"] == "unknown"]
    late = [r for r in tracked if r["deadline"] < today]
    soon = [r for r in tracked if today <= r["deadline"] <= today + timedelta(days=7)]

    def status(r):
        d = (r["deadline"] - today).days
        return f"{-d} day{'' if d == -1 else 's'} past deadline" if d < 0 else "Due today" if d == 0 else f"{d} day{'' if d == 1 else 's'} left"
    spec = _frame(tenant, "Supplier payment schedule", "Section 43B(h) deadlines", [["As on", dmy(today)]], today)
    cols = [{"label": "Supplier"}, {"label": "Bill"}, {"label": "Bill date"}, {"label": "Pay by"}, {"label": "Status"}, {"label": "Amount", "kind": "money"}]
    spec["blocks"] = [
        {"type": "stats", "items": [
            {"label": "Past deadline", "value": money(sum(r['amount_paise'] for r in late)), "tone": "bad" if late else "good", "hint": bills(len(late))},
            {"label": "Due in 7 days", "value": money(sum(r['amount_paise'] for r in soon)), "tone": "warn" if soon else "neutral", "hint": bills(len(soon))},
            {"label": "All tracked bills", "value": money(sum(r['amount_paise'] for r in tracked)), "hint": bills(len(tracked))}]},
        {"type": "table", "caption": "Micro and small suppliers, earliest deadline first", "columns": cols,
         "rows": [[r["supplier"], r["bill_ref"], dmy(r["bill_date"]), dmy(r["deadline"]), status(r), rs(r["amount_paise"])] for r in tracked],
         "total_row": ["Total", "", "", "", "", rs(sum(r["amount_paise"] for r in tracked))] if tracked else None,
         "empty": "No open bills from micro or small suppliers."}]
    if unknown:
        spec["blocks"] += [{"type": "heading", "text": "Suppliers not classified yet"},
                           {"type": "paragraph", "text": "These bills cannot get a deadline until you tell Smara whether the supplier is a micro, small or other enterprise (look at the supplier's Udyam certificate)."},
                           {"type": "table", "columns": cols[:3] + [cols[5]], "rows": [[r["supplier"], r["bill_ref"], dmy(r["bill_date"]), rs(r["amount_paise"])] for r in unknown[:100]]}]
    spec["blocks"] += [{"type": "note", "text": "Section 43B(h): a payment owed to a micro or small enterprise must be made within 15 days of acceptance of the goods or services (up to 45 days if there is a written agreement), or the expense is deductible only in the year it is actually paid. Deadlines here are counted from the bill date; confirm the exact date and each supplier's status with your CA."}]
    return spec, {"tracked_bills": len(tracked), "past_deadline_bills": len(late), "past_deadline_rupees": rs(sum(r["amount_paise"] for r in late)),
                  "due_7_days_bills": len(soon), "unclassified_bills": len(unknown)}


async def owner_report(app, tenant, args: dict, today: date) -> tuple[dict, dict]:
    days = max(1, min(int(args.get("days") or 30), 365))
    rows = await _open_invoices(app.state.pool, tenant.id)
    total = sum(r["amount_paise"] for r in rows)
    overdue_rows = [r for r in rows if r["due_date"] < today]
    overdue = sum(r["amount_paise"] for r in overdue_rows)
    buckets = {label: 0 for label, *_ in AGING}
    for r in rows:
        buckets[_bucket((today - r["due_date"]).days)] += r["amount_paise"]
    async with app.state.pool.connection() as conn:
        got = await (await conn.execute(
            """SELECT count(DISTINCT e.obligation_id) n, coalesce(sum(o.amount_paise), 0) s FROM obligation_events e
                 JOIN obligations o ON o.id = e.obligation_id
                WHERE o.tenant_id = %s AND e.to_state = 'PAID' AND e.created_at >= %s""", (tenant.id, today - timedelta(days=days)))).fetchone()
    per_party: dict[str, dict] = {}
    for r in overdue_rows:
        p = per_party.setdefault(r["canonical_name"], {"n": 0, "amt": 0, "oldest": 0})
        p["n"] += 1
        p["amt"] += r["amount_paise"]
        p["oldest"] = max(p["oldest"], (today - r["due_date"]).days)
    top = sorted(per_party.items(), key=lambda kv: -kv[1]["amt"])[:10]
    pay = await _payables(app.state.pool, tenant.id)
    late = [r for r in pay if r["deadline"] and r["deadline"] < today]
    spec = _frame(tenant, "Business report", f"Last {days} days", [["As on", dmy(today)], ["Period", f"{dmy(today - timedelta(days=days))} to {dmy(today)}"]], today)
    spec["blocks"] = [
        {"type": "stats", "items": [{"label": "Customers owe you", "value": money(total), "hint": f"{len(rows)} open invoice{'' if len(rows) == 1 else 's'}"},
                                     {"label": "Overdue", "value": money(overdue), "tone": "bad" if overdue else "good", "hint": f"{len(overdue_rows)} invoice{'' if len(overdue_rows) == 1 else 's'}"},
                                     {"label": f"Collected ({days} days)", "value": money(got['s']), "tone": "good", "hint": f"{got['n']} invoice{'' if got['n'] == 1 else 's'} cleared"}]},
        {"type": "heading", "text": "Who owes you, by age"},
        {"type": "bar_chart", "items": [{"label": k, "value": rs(v)} for k, v in buckets.items()]},
        {"type": "heading", "text": "Largest overdue customers"},
        {"type": "table", "columns": [{"label": "Customer"}, {"label": "Invoices", "align": "right"}, {"label": "Oldest (days)", "align": "right"}, {"label": "Overdue", "kind": "money"}],
         "rows": [[k, str(v["n"]), str(v["oldest"]), rs(v["amt"])] for k, v in top], "total_row": ["Total shown", "", "", rs(sum(v["amt"] for _, v in top))] if top else None,
         "empty": "Nothing is overdue."}]
    if pay:
        spec["blocks"] += [{"type": "heading", "text": "What you owe suppliers"},
                           {"type": "stats", "items": [{"label": "Open supplier bills", "value": money(sum(r['amount_paise'] for r in pay)), "hint": bills(len(pay))},
                                                        {"label": "Past 43B(h) deadline", "value": money(sum(r['amount_paise'] for r in late)), "tone": "bad" if late else "good", "hint": bills(len(late))}]}]
    spec["blocks"] += [{"type": "note", "text": "Figures come from the receivables and payables Smara has been given (Tally sync or uploaded sheets). Items not entered there are not included."}]
    return spec, {"outstanding_rupees": rs(total), "overdue_rupees": rs(overdue), "collected_rupees": rs(got["s"]), "collected_invoices": got["n"],
                  "open_supplier_bills": len(pay), "past_deadline_supplier_bills": len(late)}


def clean_custom(args: dict) -> list[dict]:
    """The model's own blocks, trimmed to a safe size. Only the whitelisted block types are kept."""
    blocks = args.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        raise ValueError("blocks must be a non-empty list for a custom document")
    out = []
    for b in blocks[:MAX_BLOCKS]:
        if not isinstance(b, dict) or b.get("type") not in BLOCK_TYPES:
            raise ValueError(f"unknown block type {b.get('type') if isinstance(b, dict) else b!r}; use one of {sorted(BLOCK_TYPES)}")
        c = {"type": b["type"]}
        for k in ("text", "title", "caption", "empty"):
            if k in b:
                c[k] = str(b[k])[:MAX_TEXT]
        if b["type"] == "table":
            cols = [x if isinstance(x, dict) else {"label": str(x)} for x in (b.get("columns") or [])][:MAX_COLS]
            if not cols:
                raise ValueError("a table needs columns")
            c["columns"] = [{"label": str(x.get("label", ""))[:80], "kind": "money" if x.get("kind") == "money" else "text",
                             **({"align": x["align"]} if x.get("align") in ("left", "right", "center") else {})} for x in cols]
            c["rows"] = [list(r)[:len(cols)] for r in (b.get("rows") or [])[:MAX_ROWS] if isinstance(r, (list, tuple))]
            if isinstance(b.get("total_row"), list):
                c["total_row"] = b["total_row"][:len(cols)]
        elif b["type"] in ("stats", "keyvalues", "bar_chart"):
            c["items"] = (b.get("items") or [])[:60]
            if b["type"] == "bar_chart":
                c["money"] = bool(b.get("money", True))
        out.append(c)
    return out


async def custom(app, tenant, args: dict, today: date) -> tuple[dict, dict]:
    spec = _frame(tenant, str(args.get("title") or "Report")[:120], str(args.get("subtitle") or "")[:160], [["Date", dmy(today)]], today)
    spec["blocks"] = clean_custom(args)
    return spec, {"blocks": len(spec["blocks"])}


BUILDERS = {"statement": statement, "reminder_letter": reminder_letter, "payables_schedule": payables_schedule,
            "owner_report": owner_report, "custom": custom}


async def make_document(app, ctx: SessionCtx, args: dict) -> dict:
    kind, fmt = args.get("kind"), args.get("format") or "pdf"
    if kind not in BUILDERS or fmt not in FORMATS:
        return {"error": f"kind must be one of {list(KINDS)} and format one of {list(FORMATS)}"}
    tenant = await tenants.by_id(app.state.pool, ctx.tenant_id)
    today = clock.today()
    try:
        spec, summary = await BUILDERS[kind](app, tenant, args, today)
    except ValueError as e:
        return {"error": str(e)}
    who = re.sub(r"\W+", "_", spec["meta"][1][1] if kind in ("statement", "reminder_letter") else "")[:30].strip("_")
    name = safe_name(f"{kind.replace('_', ' ').title().replace(' ', '_')}{'_' + who if who else ''}_{today.isoformat()}.{fmt}")
    spec["_format"], spec["_filename"] = fmt, name
    res = await app.state.sandbox.run(RENDER, {"spec.json": json.dumps(spec, ensure_ascii=False).encode("utf-8")}, timeout_s=60)
    if res.get("exit_code") != 0 or name not in (res.get("outputs") or []):
        return {"error": "the document could not be made", "detail": (res.get("stderr") or res.get("error") or "")[-400:]}
    ref = f"sandbox:{res['job_id']}:{name}"
    pages = (res.get("result") or {}).get("pages")
    return {"file_ref": ref, "filename": name, "pages": pages, "figures": summary, "output_file_refs": [ref], "_new_files": [ref],
            "next": "Check the figures above against what the owner asked, then call send_file_to_owner with this file_ref."}
