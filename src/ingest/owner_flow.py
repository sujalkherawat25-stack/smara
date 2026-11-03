"""Thread A: owner messages are trusted. Buttons, sheet/bill uploads, digest requests."""
import logging
from datetime import date, datetime, timezone

from src.bridge.coordinator import decide_adjustment
from src.ingest.box_commands import decide_browser_task, handle_text
from src.ingest.importer import import_rows
from src.ingest.media import clean_phone_caption, download_media, read_image
from src.ingest.payload import InboundMessage
from src.ingest.sheet_mapper import SheetRow, map_sheet, parse_amount_paise, parse_date, parse_phone
from src.loop.digest import build_digest
from src.loop.reminders import decide_reminder
from src.loop.whatsapp import text_message
from src.tenants import Tenant

log = logging.getLogger(__name__)
HELP = ("Commands: gstin <no>, quotes <product>, browse <task>. Send me your outstanding sheet (.xlsx/.csv), a photo of a bill with the customer's "
        "number as caption, or say 'digest'.")


async def handle_owner(app, tenant: Tenant, msg: InboundMessage) -> None:
    reply = await _route(app, tenant.id, msg)
    if reply:
        await app.state.wa.send(text_message(msg.sender, reply), number_id=msg.number_id or tenant.wa_phone_number_id)


async def _route(app, tenant_id: str, msg: InboundMessage) -> str | None:
    if msg.button_id and ":" in msg.button_id:
        action, ref = msg.button_id.split(":", 1)
        if action in ("approve", "reject"):
            r = await decide_adjustment(app.state.pool,
                                        tenant_id=tenant_id, adjustment_id=ref, approve=action == "approve")
            if r is None:
                return "That request was already handled or no longer exists."
            return f"{'Approved' if r['approved'] else 'Rejected'}: invoice {r['invoice_ref']}."
        if action in ("send", "snooze", "paid"):
            return await decide_reminder(app, tenant_id, ref, action, datetime.now(timezone.utc)) \
                or "That reminder was already handled."
        if action in ("bt_approve", "bt_reject"):
            return await decide_browser_task(app, tenant_id, msg.sender, ref, action == "bt_approve", number_id=msg.number_id)
        return None
    if msg.kind == "document":
        return await _sheet(app, tenant_id, msg)
    if msg.kind == "image":
        return await _bill(app, tenant_id, msg)
    if msg.kind == "text" and (reply := await handle_text(app, tenant_id, msg.sender, msg.text, number_id=msg.number_id)):
        return reply
    if msg.kind == "text" and msg.text.lower().startswith("browse "):
        return None  # plan card already sent
    if msg.text.strip().lower() in ("digest", "summary", "overdue", "status"):
        return await build_digest(app.state.pool, tenant_id, date.today())
    return HELP


async def _sheet(app, tenant_id: str, msg: InboundMessage) -> str:
    if not msg.filename.lower().endswith((".xlsx", ".xlsm", ".csv")):
        return "I can read .xlsx or .csv sheets. Please export your outstanding report in one of those."
    data, _ = await download_media(app.state.settings.wa_access_token, msg.media_id)
    rows, skipped = map_sheet(msg.filename, data)
    result = await import_rows(app.state.pool, tenant_id, rows) if rows else {"created": 0, "updated": 0}
    out = f"Imported {result['created']} new, {result['updated']} updated invoices."
    if skipped:
        out += f"\nSkipped {len(skipped)}: " + "; ".join(skipped[:5])
    return out


async def _bill(app, tenant_id: str, msg: InboundMessage) -> str:
    data, mime = await download_media(app.state.settings.wa_access_token, msg.media_id)
    ocr = await read_image(app.state.llm, data, mime)
    if not ocr:
        return "Bill reading is not configured yet. Please send the sheet instead."
    phone = parse_phone(clean_phone_caption(msg.caption)) or parse_phone(ocr.get("phone"))
    amount = parse_amount_paise(ocr.get("amount_rupees"))
    due = parse_date(ocr.get("due_date")) or date.today()
    if ocr.get("kind") != "bill" or not (ocr.get("party") and amount and phone):
        return "I could not read a complete bill. Send it again with the customer's number as caption."
    ref = ocr.get("invoice_ref") or f"bill-{msg.wamid[-8:]}"
    r = await import_rows(app.state.pool, tenant_id, [SheetRow(ocr["party"], phone, ref, amount, due)])
    return f"Added invoice {ref} for {ocr['party']}." if r["created"] else f"Updated invoice {ref}."
