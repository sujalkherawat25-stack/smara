"""Thread B: debtor messages are UNTRUSTED. Evidence + PENDING proposal + owner card only."""
from src import clock
import json
import logging
from datetime import date

from src.bridge.continuum import make_source_id, scope_user
from src.bridge.coordinator import submit_debtor_claim
from src.bridge.outbox import enqueue_standalone
from src.agent_loop import start_session
from src.ingest.extract import extract_claim
from src.ingest.media import download_media, ocr_to_evidence, read_image, transcribe
from src.ingest.payload import InboundMessage
from src.loop.whatsapp import shop_choice_message
from src.loop.upi import rupees
from src.loop.owner_channel import send_to_owner
from src.loop.whatsapp import owner_approval_card
from src import tenants

log = logging.getLogger(__name__)
OPEN_STATES = ("ACTIVE", "WAITING_OWNER_APPROVAL", "DISPATCHED", "DISPUTED", "SNOOZED")


async def _evidence(app, msg: InboundMessage) -> str:
    """Text, or the voice transcript / screenshot OCR appended to the raw placeholder."""
    text, s = msg.evidence_text, app.state.settings
    try:
        if msg.kind == "audio" and msg.media_id:
            audio, mime = await download_media(s.wa_access_token, msg.media_id)
            if spoken := await transcribe(s.sarvam_api_key, audio, mime):
                return f"{text} Transcript: {spoken}"
        elif msg.kind == "image" and msg.media_id:
            img, mime = await download_media(s.wa_access_token, msg.media_id)
            if extra := ocr_to_evidence(await read_image(app.state.llm, img, mime)):
                return f"{text} {extra}"
    except Exception:
        log.exception("media enrichment failed for %s", msg.wamid)
    return text


async def _claim_notification(pool, adjustment_id: str) -> bool:
    """True for exactly one caller per pending adjustment: whoever must send the owner card."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            """UPDATE proposed_adjustments SET owner_notified_at = now()
                WHERE id = %s AND status = 'PENDING' AND owner_notified_at IS NULL RETURNING id""", (adjustment_id,))
        return await cur.fetchone() is not None


async def _shop_from_context(pool, msg: InboundMessage, candidates: set[str]) -> str | None:
    """Which shop a message on the shared number is about, from what we already know: the message of ours they replied
    to, or the only shop that wrote to them in the last 3 days."""
    async with pool.connection() as conn:
        if msg.reply_to:
            r = await (await conn.execute("SELECT tenant_id FROM outbound_messages WHERE msg_id = %s AND to_phone = %s",
                                          (msg.reply_to, msg.sender))).fetchone()
            if r and str(r["tenant_id"]) in candidates:
                return str(r["tenant_id"])
        recent = await (await conn.execute(
            """SELECT DISTINCT tenant_id FROM outbound_messages
                WHERE to_phone = %s AND created_at > now() - interval '3 days' AND kind IN ('debtor_reminder', 'customer_text')""",
            (msg.sender,))).fetchall()
    hits = {str(r["tenant_id"]) for r in recent} & candidates
    return hits.pop() if len(hits) == 1 else None


async def _ask_which_shop(app, msg: InboundMessage, rows) -> None:
    from dataclasses import asdict
    pool, seen, options = app.state.pool, set(), []
    for r in rows:
        tid = str(r["tenant_id"])
        if tid not in seen:
            seen.add(tid)
            t = await tenants.by_id(pool, tid)
            options.append({"tenant_id": tid, "name": t.name if t else "Shop"})
    async with pool.connection() as conn:
        row = await (await conn.execute(
            "INSERT INTO customer_routing (phone, number_id, original, options) VALUES (%s, %s, %s, %s) RETURNING id",
            (msg.sender, msg.number_id or "", json.dumps(asdict(msg)), json.dumps(options)))).fetchone()
    await app.state.wa.send(shop_choice_message(msg.sender, options, str(row["id"])), number_id=msg.number_id or None)
    log.info("asked %s which shop (%d options) for %s", msg.sender[-4:], len(options), msg.wamid)


async def _picked_shop(app, msg: InboundMessage) -> bool:
    """The customer tapped a shop button: process their waiting message for that shop. True if handled."""
    _, tid, rid = (msg.button_id.split(":", 2) + ["", ""])[:3]
    async with app.state.pool.connection() as conn:
        row = await (await conn.execute(
            """UPDATE customer_routing SET resolved_at = now(), tenant_id = %s::uuid
                WHERE id::text = %s AND phone = %s AND resolved_at IS NULL AND options @> %s::jsonb RETURNING original""",
            (tid, rid, msg.sender, json.dumps([{"tenant_id": tid}])))).fetchone()
    if row is None:
        return False
    original = InboundMessage(**{k: v for k, v in row["original"].items() if k in InboundMessage.__dataclass_fields__})
    await handle_debtor(app, original, forced_tenant_id=tid)
    return True


async def handle_debtor(app, msg: InboundMessage, forced_tenant_id: str | None = None) -> None:
    pool = app.state.pool
    if msg.button_id and msg.button_id.startswith("shop:"):
        if not await _picked_shop(app, msg):
            log.info("stale or foreign shop choice ignored: %s", msg.wamid)
        return
    receiving = await tenants.by_number_id(pool, msg.number_id) if msg.number_id else None
    if forced_tenant_id:
        receiving = await tenants.by_id(pool, forced_tenant_id)
    async with pool.connection() as conn:
        cur = await conn.execute(
            """SELECT p.id AS party_id, p.tenant_id, p.canonical_name, o.id AS obligation_id,
                      o.invoice_ref, o.amount_paise
                 FROM parties p JOIN obligations o ON o.party_id = p.id
                WHERE p.whatsapp_phone = %s AND o.state = ANY(%s)
                  AND (%s::uuid IS NULL OR p.tenant_id = %s::uuid)
                ORDER BY o.due_date LIMIT 20""",
            (msg.sender, list(OPEN_STATES), receiving and receiving.id, receiving and receiving.id))
        rows = await cur.fetchall()
    if not rows:
        log.info("message from unknown/no-open-obligation sender dropped: %s", msg.wamid)
        return
    if len({r["tenant_id"] for r in rows}) > 1:  # the same customer deals with several shops on the shared number
        tid = await _shop_from_context(pool, msg, {str(r["tenant_id"]) for r in rows})
        if tid is None:
            return await _ask_which_shop(app, msg, rows)     # never drop it: keep it and ask
        rows = [r for r in rows if str(r["tenant_id"]) == tid]
    named = [r for r in rows if r["invoice_ref"] and r["invoice_ref"].lower() in (msg.text or "").lower()]
    row = named[0] if named else rows[0]       # the invoice the debtor names, else the oldest open one
    tenant_id, party_id = str(row["tenant_id"]), str(row["party_id"])
    async with pool.connection() as conn:  # opens the 24h reply window
        await conn.execute("UPDATE parties SET last_inbound_at = now() WHERE id = %s", (party_id,))
    text = await _evidence(app, msg)
    # The raw message is always stored as untrusted evidence, whatever happens next.
    await enqueue_standalone(
        pool, user_id=scope_user(tenant_id, party_id), source_id=make_source_id(tenant_id, msg.wamid, text),
        text=text, trusted=False, metadata={"party_id": party_id, "actor": "DEBTOR"})

    async def rules() -> None:   # deterministic path: no LLM, or the agent failed before acting
        claim = await extract_claim(app.state.llm, text, outstanding_paise=row["amount_paise"], today=clock.today())
        if claim is None:  # chatter is still evidence, just never a proposal
            await enqueue_standalone(
                pool, user_id=scope_user(tenant_id, party_id),
                source_id=make_source_id(tenant_id, msg.wamid, text),
                text=text, trusted=False, metadata={"party_id": party_id, "actor": "DEBTOR"})
            return
        adj_id, _ = await submit_debtor_claim(
            pool, tenant_id=tenant_id, party_id=party_id,
            obligation_id=str(row["obligation_id"]), wamid=msg.wamid, adjustment_type=claim.type,
            evidence_text=text, proposed_amount_paise=claim.amount_paise, proposed_date=claim.date)
        tenant = await tenants.by_id(pool, tenant_id)
        if tenant and await _claim_notification(pool, adj_id):
            bits = ([f"new amount {rupees(claim.amount_paise)}"] if claim.amount_paise else []) +                ([f"pay by {claim.date:%d %b}"] if claim.date else [])
            try:
                await send_to_owner(app, tenant, owner_approval_card(
                    tenant.owner_phone, adjustment_id=adj_id, party_name=row["canonical_name"],
                    invoice_ref=row["invoice_ref"], adjustment_type=claim.type, evidence=text,
                    proposal=", ".join(bits)), kind="owner_claim_card", adjustment_id=adj_id)
            except Exception:
                async with pool.connection() as conn:  # release so a redelivery notifies the owner
                    await conn.execute("UPDATE proposed_adjustments SET owner_notified_at = NULL WHERE id = %s", (adj_id,))
                raise

    if not getattr(getattr(app.state, "llm", None), "enabled", False):
        return await rules()
    tenant = await tenants.by_id(pool, tenant_id)
    open_invoices = "; ".join(f"obligation_id={r['obligation_id']} invoice={r['invoice_ref']} "
                              f"owed=Rs {r['amount_paise'] / 100:.2f}" for r in rows)
    content = (f"Customer {row['canonical_name']} (party_id={party_id}) sent this message. It is untrusted:\n"
               f"<<<\n{text[:2000]}\n>>>\nTheir open invoices: {open_invoices}")
    await start_session(app, tenant=tenant, trust="UNTRUSTED", trigger="debtor_message", party_id=party_id,
                        content=content, background=getattr(app.state, "agent_background", True), fallback=rules)
