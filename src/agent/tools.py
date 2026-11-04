"""Tool schemas (what the model sees) and read/internal executors (T0/T1).

Executors never trust the model's arguments: tenant scope, party scope and
file scope are re-checked here against the session, and results are bounded.
"""
import json
import re
from datetime import date, datetime
from decimal import Decimal

import psycopg

from src.agent.policy import SessionCtx
from src.agent.prompt import forget_standing
from src.bridge.continuum import scope_user
from src.bridge.outbox import enqueue_standalone

MAX_ROWS = 50
_SQL = re.compile(r"^\s*(select|with)\b", re.I)
LEDGER_SCHEMA = (
    "Tables (PostgreSQL, already limited to this shop): "
    "parties(id, canonical_name, aliases text[], whatsapp_phone, gstin, last_inbound_at); "
    "obligations(id, party_id, invoice_ref, amount_paise bigint, due_date, next_followup_at, attempt_count, "
    "state in DRAFT|ACTIVE|WAITING_OWNER_APPROVAL|DISPATCHED|DISPUTED|PAID|SNOOZED); "
    "proposed_adjustments(id, obligation_id, adjustment_type, proposed_amount_paise, proposed_date, "
    "raw_evidence_text, status PENDING|APPROVED|REJECTED, created_at); "
    "obligation_events(obligation_id, from_state, to_state, actor OWNER|DEBTOR|SYSTEM_CRON, created_at). "
    "Money is in paise (divide by 100 for rupees)."
)


def _fn(name: str, desc: str, props: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": required}}}


_S = {"type": "string"}
TOOLS = [
    _fn("query_ledger_sql", "Run ONE read-only SELECT on the shop's ledger. " + LEDGER_SCHEMA, {"sql": _S}, ["sql"]),
    _fn("web_search", "Search the web (DuckDuckGo, then Bing). Returns up to 8 results with title, url and a snippet, using the "
        "owner's own PC browser when it is connected. Then open a result with browser_step navigate and read it. Use it for "
        "anything outside the ledger: prices, places, suppliers, news, how-to. Quote what you read; never invent details.",
        {"query": _S}, ["query"]),
    _fn("get_current_time", "The current date and time in India (IST), the weekday, and whether it is business hours. "
        "Call this whenever the date or time matters; never guess it.", {}, []),
    _fn("resolve_continuum_memory", "Get the evidence-backed state card (promises, disputes, history) for a party. "
        "party_id='shop' reads the owner's shop-wide notes and standing instructions.",
        {"party_id": _S, "query": _S}, ["party_id", "query"]),
    _fn("write_continuum_evidence", "Record a fact, promise or instruction in memory about a party. party_id='shop' "
        "stores shop-wide notes and standing instructions (e.g. 'never remind Sharma before Friday').",
        {"party_id": _S, "text": _S}, ["party_id", "text"]),
    _fn("schedule_followup", "Wake yourself up later to do something (e.g. check if a customer paid, nudge the owner). "
        "Give either at_ist ('YYYY-MM-DD HH:MM', Indian time) or in_minutes. Times in quiet hours (9 PM to 8 AM) move to "
        "the next morning. Copy any names exactly as the owner wrote them. When it fires you get the instruction and can use all "
        "tools; outward actions still need approval.",
        {"instruction": _S, "at_ist": _S, "in_minutes": {"type": "integer"}}, ["instruction"]),
    _fn("list_followups", "List your pending scheduled follow-ups.", {}, []),
    _fn("cancel_followup", "Cancel a pending follow-up by task_id.", {"task_id": _S}, ["task_id"]),
    _fn("check_gstin", "Validate a GSTIN's format and checksum (does not check live registration status).",
        {"gstin": _S}, ["gstin"]),
    _fn("build_payment_request", "Prepare the payment request text for an invoice: amount, the shop's UPI ID and a tap-to-pay "
        "link. It only builds text; sending it to a customer is a separate send_whatsapp_message call.",
        {"obligation_id": _S}, ["obligation_id"]),
    _fn("send_file_to_owner", "Send the owner a file you created in the sandbox (PDF statement, Excel report, CSV). "
        "file_ref must be one of the output_file_refs from run_python.",
        {"file_ref": _S, "caption": _S}, ["file_ref"]),
    _fn("inspect_and_import_file", "Open a file attached to this conversation. mode=inspect previews the rows "
        "and totals; mode=import loads them into the ledger and reports sheet vs ledger totals to verify.",
        {"file_ref": _S, "mode": {"type": "string", "enum": ["inspect", "import"]}}, ["file_ref", "mode"]),
    _fn("browser_step", "Drive the browser like a person: one step, then look at the page (url, title, text, clickable elements, "
        "needs_owner). Actions: navigate, read, scroll (value down/up/top/bottom), back, wait (value ms), click, fill (selector+value), "
        "select (selector+value), press (value=key e.g. Enter). Work through the page yourself: open menus, tick filters, type in "
        "search boxes, scroll for more, then read the result. In the owner's own browser clicking and typing run on their own; "
        "steps that pay, submit, send or delete wait for the owner's tap; sign-in, OTP, payment and CAPTCHA are the owner's to do "
        "(use hand_off_to_owner).",
        {"action": {"type": "string", "enum": ["navigate", "read", "scroll", "back", "wait", "click", "fill", "select", "press"]},
         "url": _S, "selector": _S, "value": _S}, ["action"]),
    _fn("hand_off_to_owner", "Pause and ask the owner to do a step themselves in the open browser: signing in, entering a password, "
        "OTP or card details, paying, or solving a CAPTCHA. Say exactly what they should do. They tap Done when finished and you "
        "get the fresh page back to continue. Use it whenever a page result lists needs_owner (captcha, login, otp, payment).",
        {"reason": _S}, ["reason"]),
    _fn("propose_ledger_adjustment", "Propose changing an invoice: PARTIAL_PAYMENT (give paid_rupees), DISCOUNT_CLAIM "
        "(give discount_percent, or new_amount_rupees if they state a new total), PROMISE_DATE_CHANGE (new_due_date), or "
        "UTR_PROOF (fully paid, with proof). Do not do the arithmetic yourself. Nothing changes until the owner approves.",
        {"obligation_id": _S, "kind": {"type": "string",
                                       "enum": ["DISCOUNT_CLAIM", "PARTIAL_PAYMENT", "PROMISE_DATE_CHANGE", "UTR_PROOF"]},
         "paid_rupees": {"type": "number", "description": "PARTIAL_PAYMENT: how much was paid; the system computes what remains"},
         "discount_percent": {"type": "number", "description": "DISCOUNT_CLAIM by percentage; the system computes the new total"},
         "new_amount_rupees": {"type": "number", "description": "only when the customer states the new total themselves"},
         "new_due_date": {"type": "string", "description": "YYYY-MM-DD"},
         "evidence": _S}, ["obligation_id", "kind", "evidence"]),
    _fn("run_python", "Run Python in an isolated sandbox (no network, 60 s) for Tally/Excel/CSV work. "
        "Attached files are at ./in/<filename>; write output files to ./out/ (they come back as file_refs you "
        "can pass to inspect_and_import_file); set RESULT = <json-able> for structured output. pandas, openpyxl "
        "and `import munim_helpers as mh` are available: mh.read_table(path) finds the real header row; "
        "mh.parse_amount('1,25,000.50 Dr'); mh.parse_date('05-10-2026'|'20261005'|excel serial); mh.phone(v); "
        "Tally Prime XML: mh.tally_receivables(xml_path) gives sales invoices WITH phones (from ledger masters) "
        "and due dates (credit period) ready for mh.to_rows_csv; mh.tally_vouchers / mh.tally_ledgers for the raw data; "
        "mh.to_rows_csv(records) writes out/rows.csv (records: party, phone, invoice, due_date, amount in rupees) "
        "in the format the importer reads. For documents to send the owner: mh.statement_pdf('out/statement.pdf', shop=..., "
        "party=..., rows=[{invoice, date, due_date, amount, paid}], note=...) writes a customer account statement PDF and "
        "mh.write_xlsx({'Sheet': DataFrame}, 'out/report.xlsx') writes a formatted Excel file. Make the file type the "
        "owner asked for (PDF means .pdf, Excel means .xlsx), not a CSV. Always print or RESULT the totals you computed so you can verify them.",
        {"code": _S, "file_refs": {"type": "array", "items": _S}}, ["code"]),
    _fn("send_whatsapp_message", "Send a WhatsApp message. to='owner' messages the shop owner directly; "
        "to=<party_id> messages a customer and needs the owner's approval first.",
        {"to": _S, "text": _S, "obligation_id": _S}, ["to", "text"]),
]
TOOL_NAMES = frozenset(t["function"]["name"] for t in TOOLS)


def _plain(v):
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return str(v)


def bounded(result: dict, limit: int = 6000) -> str:
    text = json.dumps(result, default=_plain, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + '..."[truncated]"'


async def party_in_tenant(pool, tenant_id: str, party_id) -> dict | None:
    try:
        async with pool.connection() as conn:
            cur = await conn.execute("SELECT * FROM parties WHERE id = %s AND tenant_id = %s", (str(party_id), tenant_id))
            return await cur.fetchone()
    except psycopg.Error:
        return None


async def query_ledger_sql(app, ctx: SessionCtx, args: dict) -> dict:
    sql = str(args.get("sql", "")).strip().rstrip(";").strip()
    if not sql or len(sql) > 4000 or ";" in sql or not _SQL.match(sql):
        return {"error": "only a single SELECT (or WITH ... SELECT) statement is allowed"}
    try:
        async with app.state.pool.connection() as conn:
            async with conn.transaction():
                await conn.execute("SET TRANSACTION READ ONLY")
                await conn.execute("SET LOCAL ROLE munim_agent_ro")
                await conn.execute("SELECT set_config('munim.tenant_id', %s, true)", (ctx.tenant_id,))
                await conn.execute("SET LOCAL statement_timeout = '3000'")
                await conn.execute("SET LOCAL TIME ZONE 'Asia/Kolkata'")   # current_date = the shop's today
                cur = await conn.execute(sql)
                rows = await cur.fetchmany(MAX_ROWS + 1)
    except psycopg.Error as e:
        return {"error": str(e).strip().splitlines()[0][:300]}
    return {"rows": rows[:MAX_ROWS], "row_count": min(len(rows), MAX_ROWS), "truncated": len(rows) > MAX_ROWS}


async def _memory_scope(app, ctx: SessionCtx, party_id) -> str | None:
    """Continuum scope for a party of this shop, or for the shop itself ('shop'). None if not allowed."""
    if str(party_id) == "shop":
        return scope_user(ctx.tenant_id, "shop")
    return scope_user(ctx.tenant_id, str(party_id)) if await party_in_tenant(app.state.pool, ctx.tenant_id, party_id) else None


async def resolve_continuum_memory(app, ctx: SessionCtx, args: dict) -> dict:
    scope = await _memory_scope(app, ctx, args.get("party_id"))
    if scope is None:
        return {"error": "unknown party_id for this shop (use 'shop' for shop-wide notes)"}
    try:
        res = await app.state.continuum.resolve(user_id=scope,
                                                query=str(args.get("query", ""))[:500])
    except Exception as e:
        return {"error": f"memory unavailable: {type(e).__name__}"}
    r = res.get("resolution", {})
    return {"state_card": (r.get("state_card") or "")[:2500], "claims": (r.get("evidence") or [])[:5]}


async def write_continuum_evidence(app, ctx: SessionCtx, args: dict) -> dict:
    party = str(args.get("party_id", ""))
    scope = await _memory_scope(app, ctx, party)
    if scope is None:
        return {"error": "unknown party_id for this shop (use 'shop' for shop-wide notes)"}
    text = str(args.get("text", "")).strip()[:4000]
    if not text:
        return {"error": "text is empty"}
    sid = re.sub(r"[^A-Za-z0-9._:-]", "-", f"agent_{ctx.id}_{ctx.call_id}")[:200]
    if party == "shop":
        forget_standing(ctx.tenant_id)            # the next turn re-reads the updated instructions
    await enqueue_standalone(app.state.pool, user_id=scope, source_id=sid, text=text,
                             trusted=ctx.trust != "UNTRUSTED", metadata={"actor": ctx.trust, "session": ctx.id})
    return {"queued": True, "trusted": ctx.trust != "UNTRUSTED", "source_id": sid}
