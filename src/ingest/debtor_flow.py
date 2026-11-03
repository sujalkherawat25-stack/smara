"""Thread B: debtor messages are UNTRUSTED. Evidence + PENDING proposal + owner card only."""
import logging
from datetime import date

from src.bridge.continuum import make_source_id, scope_user
from src.bridge.coordinator import submit_debtor_claim
from src.bridge.outbox import enqueue_standalone
from src.ingest.extract import extract_claim
from src.ingest.media import download_media, ocr_to_evidence, read_image, transcribe
from src.ingest.payload import InboundMessage
from src.loop.upi import rupees
from src.loop.scheduler import owner_phone
from src.loop.whatsapp import owner_approval_card

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


async def handle_debtor(app, msg: InboundMessage) -> None:
    async with app.state.pool.connection() as conn:
        cur = await conn.execute(
            """SELECT p.id AS party_id, p.tenant_id, p.canonical_name, o.id AS obligation_id,
                      o.invoice_ref, o.amount_paise
                 FROM parties p JOIN obligations o ON o.party_id = p.id
                WHERE p.whatsapp_phone = %s AND o.state = ANY(%s)
                ORDER BY o.due_date LIMIT 1""", (msg.sender, list(OPEN_STATES)))
        row = await cur.fetchone()
    if row is None:
        log.info("message from unknown/no-open-obligation sender dropped: %s", msg.wamid)
        return
    tenant_id, party_id = str(row["tenant_id"]), str(row["party_id"])
    text = await _evidence(app, msg)
    claim = await extract_claim(app.state.llm, text, outstanding_paise=row["amount_paise"], today=date.today())
    if claim is None:  # chatter is still evidence, just never a proposal
        await enqueue_standalone(
            app.state.pool, user_id=scope_user(tenant_id, party_id),
            source_id=make_source_id(tenant_id, msg.wamid, text),
            text=text, trusted=False, metadata={"party_id": party_id, "actor": "DEBTOR"})
        return
    adj_id, created = await submit_debtor_claim(
        app.state.pool, tenant_id=tenant_id, party_id=party_id,
        obligation_id=str(row["obligation_id"]), wamid=msg.wamid, adjustment_type=claim.type,
        evidence_text=text, proposed_amount_paise=claim.amount_paise, proposed_date=claim.date)
    phone = owner_phone(app.state.settings, tenant_id)
    if created and phone:
        bits = ([f"new amount {rupees(claim.amount_paise)}"] if claim.amount_paise else []) + \
               ([f"pay by {claim.date:%d %b}"] if claim.date else [])
        await app.state.wa.send(owner_approval_card(
            phone, adjustment_id=adj_id, party_name=row["canonical_name"], invoice_ref=row["invoice_ref"],
            adjustment_type=claim.type, evidence=text, proposal=", ".join(bits)))
