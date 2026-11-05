"""File import (T0/T1) and gated actions (T2/T3).

Gated tools run in two halves. `open_gate` validates the concrete arguments
and puts the owner's Approve/Reject card up; the session then suspends.
`execute_approved` runs only after the owner tapped Approve, using the exact
arguments that were frozen in the session when the card was shown.
"""
import json
import re
from datetime import date, datetime, timedelta, timezone

from src import clock
from src.agent.policy import SessionCtx
from src.agent.tools import party_in_tenant
from src.bridge.coordinator import submit_debtor_claim
from src.ingest.importer import import_rows
from src.ingest.media import download_media, read_image
from src.ingest.sheet_mapper import map_sheet
from src.loop.outbound import record
from src.loop.owner_channel import send_to_owner
from src.loop.whatsapp import from_shop, reminder_params, owner_approval_card, template_message, text_message

WINDOW = timedelta(hours=24)


async def _fetch(app, file_ref: str) -> tuple[bytes, str, str]:
    channel, media_id, filename = (file_ref.split(":", 2) + ["", ""])[:3]
    if channel == "sandbox":
        return app.state.sandbox.read_output(media_id, filename), "application/octet-stream", filename
    if channel == "telegram":
        return await app.state.tg.download(media_id), "application/octet-stream", filename
    data, mime = await download_media(app.state.settings.wa_access_token, media_id)
    return data, mime, filename


async def inspect_and_import_file(app, ctx: SessionCtx, args: dict) -> dict:
    data, mime, filename = await _fetch(app, args["file_ref"])
    if not filename.lower().endswith((".xlsx", ".xlsm", ".csv")):
        if mime.startswith("image/"):
            return {"kind": "image", "ocr": await read_image(app.state.llm, data, mime)}
        return {"error": f"unsupported file {filename!r}; send .xlsx or .csv"}
    rows, skipped = map_sheet(filename, data)
    total = sum(r.amount_paise for r in rows)
    preview = {"rows_found": len(rows), "sheet_total_rupees": total / 100, "skipped": skipped[:10],
               "sample": [{"party": r.party, "invoice": r.invoice_ref, "rupees": r.amount_paise / 100,
                           "due": r.due_date.isoformat(), "phone": r.phone} for r in rows[:5]]}
    if args.get("mode") != "import" or not rows:
        return preview
    result = await import_rows(app.state.pool, ctx.tenant_id, rows)
    async with app.state.pool.connection() as conn:   # read back for the agent to verify
        cur = await conn.execute(
            "SELECT count(*) n, coalesce(sum(amount_paise), 0) s FROM obligations WHERE tenant_id = %s AND invoice_ref = ANY(%s)",
            (ctx.tenant_id, [r.invoice_ref for r in rows]))
        back = await cur.fetchone()
    return {**preview, **result, "ledger_rows_for_these_invoices": back["n"],
            "ledger_total_rupees_for_these_invoices": int(back["s"]) / 100, "totals_match": int(back["s"]) == total}


async def run_python(app, ctx: SessionCtx, args: dict) -> dict:
    inputs = {}
    for ref in args.get("file_refs") or []:
        data, _, filename = await _fetch(app, ref)
        inputs[filename or "file"] = data
    res = await app.state.sandbox.run(args["code"], inputs)
    refs = [f"sandbox:{res['job_id']}:{n}" for n in res.get("outputs") or []] if res.get("job_id") else []
    return {**{k: res.get(k) for k in ("exit_code", "timed_out", "seconds", "stdout", "stderr", "result", "error")
               if res.get(k) not in (None, "")},
            "output_file_refs": refs, "_new_files": refs}


async def _obligation(pool, ctx: SessionCtx, oid) -> dict | None:
    async with pool.connection() as conn:
        cur = await conn.execute(
            """SELECT o.*, p.canonical_name, p.whatsapp_phone, p.last_inbound_at FROM obligations o
                 JOIN parties p ON p.id = o.party_id WHERE o.id::text = %s AND o.tenant_id = %s""", (str(oid), ctx.tenant_id))
        return await cur.fetchone()


def _compute_amount(args: dict, owed: float) -> str | None:
    """The model states what the customer said; the arithmetic is ours. paid_rupees and discount_percent
    set new_amount_rupees (what is still owed) deterministically, overriding any figure the model computed."""
    try:
        if args.get("kind") == "PARTIAL_PAYMENT" and args.get("paid_rupees") is not None:
            paid = float(args["paid_rupees"])
            if not 0 < paid < owed:
                return f"paid_rupees must be above 0 and below the amount owed (Rs {owed:,.2f})"
            args["new_amount_rupees"] = round(owed - paid, 2)
        elif args.get("kind") == "DISCOUNT_CLAIM" and args.get("discount_percent") is not None:
            pct = float(args["discount_percent"])
            if not 0 < pct < 100:
                return "discount_percent must be between 0 and 100"
            args["new_amount_rupees"] = round(owed * (1 - pct / 100), 2)
    except (TypeError, ValueError):
        return "paid_rupees / discount_percent must be numbers"
    return None


SECRET_FIELD = re.compile(r"pass(word|wd)|\botp\b|\bpin\b|cvv|card.?number|\bupi.?pin\b|secret", re.I)


async def validate_gated(app, ctx: SessionCtx, name: str, args: dict) -> str | None:
    """Concrete checks before any card is shown. Returns an error for the model, or None."""
    pool = app.state.pool
    if name == "send_whatsapp_message":
        p = await party_in_tenant(pool, ctx.tenant_id, args.get("to"))
        if not p:
            return "to must be 'owner' or a party_id of this shop"
        if p.get("opted_out_at"):
            return (f"{p['canonical_name']} replied STOP on {p['opted_out_at']:%d %b} and must not be messaged on WhatsApp. "
                    "Tell the owner; they can call the customer themselves.")
        if not str(args.get("text", "")).strip() or len(str(args["text"])) > 1000:
            return "text must be 1-1000 characters"
        if args.get("obligation_id") and not await _obligation(pool, ctx, args["obligation_id"]):
            return "unknown obligation_id"
        from src import tenants
        t = await tenants.by_id(pool, ctx.tenant_id)
        args["text"] = from_shop(t, str(args["text"]))     # the owner approves exactly what the customer will see
    elif name == "propose_ledger_adjustment":
        o = await _obligation(pool, ctx, args.get("obligation_id"))
        if not o or o["state"] == "PAID":
            return "unknown or already paid obligation_id"
        if args.get("kind") not in ("DISCOUNT_CLAIM", "PARTIAL_PAYMENT", "PROMISE_DATE_CHANGE", "UTR_PROOF"):
            return "kind must be DISCOUNT_CLAIM, PARTIAL_PAYMENT, PROMISE_DATE_CHANGE or UTR_PROOF"
        err = _compute_amount(args, o["amount_paise"] / 100)
        if err:
            return err
        if args["kind"] in ("DISCOUNT_CLAIM", "PARTIAL_PAYMENT") and args.get("new_amount_rupees") is None:
            return "give paid_rupees (part-payment), discount_percent, or new_amount_rupees (the new total owed)"
        if ctx.trust == "UNTRUSTED" and str(o["party_id"]) != ctx.party_id:
            return "a counterparty may only propose changes to its own invoices"
        amt = args.get("new_amount_rupees")
        if amt is not None and not (0 < float(amt) * 100 < o["amount_paise"]):
            return "new_amount_rupees must be positive and below the amount owed"
        if args.get("new_due_date"):
            try:
                if date.fromisoformat(args["new_due_date"]) < clock.today():
                    return "new_due_date is in the past"
            except ValueError:
                return "new_due_date must be YYYY-MM-DD"
    elif name == "browser_step" and not app.state.browsers.alive(ctx.id):
        return "no open page: call browser_step with action=navigate first"
    elif name == "browser_step" and args.get("action") == "fill" and SECRET_FIELD.search(
            f"{args.get('selector', '')} {args.get('value', '')}"):
        return ("I never type passwords, OTPs, PINs or card details. Stop here and ask the owner to enter that "
                "step themselves, or use an official API instead")
    return None


async def open_gate(app, ctx: SessionCtx, tenant, name: str, args: dict, card: str) -> dict:
    """Show the owner the card; returns what must be stored as the session's pending call."""
    if name == "propose_ledger_adjustment":   # reuse the ledger's own PENDING row and Approve/Reject path
        o = await _obligation(app.state.pool, ctx, args["obligation_id"])
        amt = args.get("new_amount_rupees")
        adj_id, _ = await submit_debtor_claim(
            app.state.pool, tenant_id=ctx.tenant_id, party_id=str(o["party_id"]), obligation_id=str(o["id"]),
            wamid=f"agent-{ctx.id}-{ctx.call_id}", adjustment_type=args["kind"],
            evidence_text=str(args.get("evidence") or card)[:2000],
            proposed_amount_paise=round(float(amt) * 100) if amt is not None else None,
            proposed_date=date.fromisoformat(args["new_due_date"]) if args.get("new_due_date") else None)
        async with app.state.pool.connection() as conn:
            await conn.execute("UPDATE proposed_adjustments SET owner_notified_at = now() WHERE id = %s", (adj_id,))
        await send_to_owner(app, tenant, owner_approval_card(
            tenant.owner_phone, adjustment_id=adj_id, party_name=o["canonical_name"], invoice_ref=o["invoice_ref"],
            adjustment_type=args["kind"], evidence=str(args.get("evidence") or ""), proposal=card),
            kind="owner_claim_card", adjustment_id=adj_id)
        return {"adjustment_id": adj_id}
    who = ""
    if name == "send_whatsapp_message":
        p = await party_in_tenant(app.state.pool, ctx.tenant_id, args["to"])
        who = f"To: {p['canonical_name']} (+{p['whatsapp_phone']})\n"
    btn = lambda k, t: {"type": "reply", "reply": {"id": f"{k}:{ctx.id}", "title": t}}
    await send_to_owner(app, tenant, {
        "messaging_product": "whatsapp", "to": tenant.owner_phone, "type": "interactive", "interactive": {
            "type": "button", "body": {"text": (who + card)[:1024]},
            "action": {"buttons": [btn("ag_ok", "Done, continue") if name == "hand_off_to_owner" else btn("ag_ok", "Approve"),
                                   *([btn("ag_site", "Approve + this site")] if name == "browser_step" else []),
                                   btn("ag_no", "Cancel") if name == "hand_off_to_owner" else btn("ag_no", "Reject")]}}},
        kind="owner_agent_card", agent_session_id=ctx.id)
    return {"card": (who + card)[:1024]}


async def execute_approved(app, ctx: SessionCtx, tenant, name: str, args: dict) -> dict:
    if name == "browser_step":
        return await app.state.browsers.step(ctx.id, args, tenant_id=ctx.tenant_id)
    if name == "hand_off_to_owner":
        page = await app.state.browsers.step(ctx.id, {"action": "read"}, tenant_id=ctx.tenant_id) if app.state.browsers.alive(ctx.id) else {}
        return {"owner_done": True, "note": "The owner says they finished their part. Check the page below and continue.", **page}
    if name != "send_whatsapp_message":
        return {"error": f"{name} cannot be executed here"}
    s = app.state.settings
    if clock.is_quiet(s):
        # The owner approved this exact text, but customers are not messaged at night: send it when the shop opens.
        at = clock.next_open(s)
        p = await party_in_tenant(app.state.pool, ctx.tenant_id, args["to"])
        async with app.state.pool.connection() as conn:
            await conn.execute(
                """INSERT INTO agent_tasks (tenant_id, run_at, kind, payload, created_by_session)
                   VALUES (%s, %s, 'send_message', %s, %s)""",
                (ctx.tenant_id, at, json.dumps({**args, "label": p["canonical_name"]}), ctx.id))
        return {"sent": False, "deferred": True, "will_send_at_ist": at.strftime("%Y-%m-%d %H:%M"),
                "note": "Approved. It is quiet hours, so it will be sent automatically when the shop opens."}
    return await deliver_message(app, ctx, tenant, args)


async def deliver_message(app, ctx: SessionCtx, tenant, args: dict) -> dict:
    """Send an owner-approved message to a customer: free text inside the 24h window, the approved template outside it."""
    pool, s, now = app.state.pool, app.state.settings, datetime.now(timezone.utc)
    p = await party_in_tenant(pool, ctx.tenant_id, args["to"])
    if p and p.get("opted_out_at"):              # they said STOP after the owner approved: never send
        return {"sent": False, "error": f"{p['canonical_name']} has opted out of WhatsApp messages (STOP)"}
    o = await _obligation(pool, ctx, args["obligation_id"]) if args.get("obligation_id") else None
    in_window = p["last_inbound_at"] and now - p["last_inbound_at"] < WINDOW
    if in_window:
        payload, via = text_message(p["whatsapp_phone"], from_shop(tenant, str(args["text"]))), "text"
    elif o and s.wa_reminder_template:
        payload, via = template_message(p["whatsapp_phone"], s.wa_reminder_template, s.wa_reminder_lang,
                                        reminder_params(s, {**o, "canonical_name": p["canonical_name"]}, tenant)), \
            "approved template (outside 24h window)"
    else:
        return {"sent": False, "error": "the customer has not messaged in 24 hours; WhatsApp only allows an "
                                        "approved template, and none is configured for this case"}
    msg_id = await app.state.wa.send(payload, number_id=tenant.wa_phone_number_id)
    if msg_id:
        await record(pool, msg_id, tenant_id=ctx.tenant_id, kind="debtor_reminder" if o else "customer_text",
                     to_phone=p["whatsapp_phone"], obligation_id=o["id"] if o else None)
    if o and o["state"] == "WAITING_OWNER_APPROVAL":
        await _transition(pool, o["id"], "WAITING_OWNER_APPROVAL", "DISPATCHED", now + timedelta(days=3), 1)
    return {"sent": True, "via": via, "message_id": msg_id}


async def on_rejected(app, ctx: SessionCtx, name: str, args: dict) -> None:
    """Owner said no to a reminder for a due invoice: snooze it rather than re-asking every minute."""
    if name == "send_whatsapp_message" and args.get("obligation_id"):
        o = await _obligation(app.state.pool, ctx, args["obligation_id"])
        if o and o["state"] == "WAITING_OWNER_APPROVAL":
            await _transition(app.state.pool, o["id"], "WAITING_OWNER_APPROVAL", "SNOOZED",
                              datetime.now(timezone.utc) + timedelta(days=3), 0)


async def _transition(pool, oid, frm: str, to: str, followup, attempts: int) -> None:
    async with pool.connection() as conn:
        async with conn.transaction():
            cur = await conn.execute(
                """UPDATE obligations SET state = %s, next_followup_at = %s, attempt_count = attempt_count + %s
                    WHERE id = %s AND state = %s RETURNING id""", (to, followup, attempts, oid, frm))
            if await cur.fetchone():
                await conn.execute("INSERT INTO obligation_events (obligation_id, from_state, to_state, actor) "
                                   "VALUES (%s, %s, %s, 'OWNER')", (oid, frm, to))
