"""Thread B: debtor messages are UNTRUSTED. Evidence + PENDING proposal + owner card only."""
import logging
from datetime import date

from src.bridge.continuum import make_source_id, scope_user
from src.bridge.coordinator import submit_debtor_claim
from src.bridge.outbox import enqueue_standalone
from src.agent_loop import start_session
from src.ingest.extract import extract_claim
from src.ingest.media import download_media, ocr_to_evidence, read_image, transcribe
from src.ingest.payload import InboundMessage
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


async def handle_debtor(app, msg: InboundMessage) -> None:
    pool = app.state.pool
    receiving = await tenants.by_number_id(pool, msg.number_id) if msg.number_id else None
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
    if len({r["tenant_id"] for r in rows}) > 1:  # same debtor owes several tenants, number gives no hint
        log.warning("ambiguous tenant for sender on shared number, dropped: %s", msg.wamid)
        return
    row = rows[0]
    tenant_id, party_id = str(row["tenant_id"]), str(row["party_id"])
    async with pool.connection() as conn:  # opens the 24h reply window
        await conn.execute("UPDATE parties SET last_inbound_at = now() WHERE id = %s", (party_id,))
    text = await _evidence(app, msg)
    # The raw message is always stored as untrusted evidence, whatever happens next.
    await enqueue_standalone(
        pool, user_id=scope_user(tenant_id, party_id), source_id=make_source_id(tenant_id, msg.wamid, text),
        text=text, trusted=False, metadata={"party_id": party_id, "actor": "DEBTOR"})

    async def rules() -> None:   # deterministic path: no LLM, or the agent failed before acting
        claim = await extract_claim(app.state.llm, text, outstanding_paise=row["amount_paise"], today=date.today())
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
