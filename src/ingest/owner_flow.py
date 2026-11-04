"""Thread A: owner messages are trusted. Buttons, sheet/bill uploads, digest requests."""
from src import clock
import logging
from datetime import date, datetime, timezone

from src.bridge.coordinator import decide_adjustment
from src.ingest.box_commands import decide_browser_task, handle_text
from src.ingest.importer import import_rows
from src.ingest.media import clean_phone_caption, download_media, read_image, transcribe
from src.ingest.payload import InboundMessage
from src.ingest.telegram import link_message, unlink
from src.ingest.sheet_mapper import SheetRow, map_sheet, parse_amount_paise, parse_date, parse_phone
from src.agent_loop import resume, start_session
from src.loop.digest import build_digest
from src.loop.reminders import decide_reminder
from src.loop.owner_channel import send_to_owner
from src.loop.whatsapp import text_message
from src.tenants import Tenant

log = logging.getLogger(__name__)
HELP = ("Commands: digest, telegram (link Telegram), gstin <no>, quotes <product>, browse <task>. Send me your outstanding sheet (.xlsx/.csv), a photo of a bill with the customer's "
        "number as caption, or say 'digest'.")


async def handle_owner(app, tenant: Tenant, msg: InboundMessage) -> None:
    reply = await _route(app, tenant.id, msg)
    if reply:
        await send_to_owner(app, tenant, text_message(tenant.owner_phone, reply), kind="owner_text",
                            reply_channel=msg.channel, number_id=msg.number_id or None)


async def _route(app, tenant_id: str, msg: InboundMessage) -> str | None:
    if msg.button_id and ":" in msg.button_id:
        action, ref = msg.button_id.split(":", 1)
        if action in ("approve", "reject"):
            r = await decide_adjustment(app.state.pool,
                                        tenant_id=tenant_id, adjustment_id=ref, approve=action == "approve")
            if r is None:
                return "That request was already handled or no longer exists."
            await resume(app, tenant_id, adjustment_id=ref, approved=r["approved"],   # agent waiting on it, if any
                         outcome={"owner_decision": "approved" if r["approved"] else "rejected",
                                  "invoice_ref": r["invoice_ref"], "ledger_updated": r["approved"]},
                         background=getattr(app.state, "agent_background", True))
            return f"{'Approved' if r['approved'] else 'Rejected'}: invoice {r['invoice_ref']}."
        if action in ("send", "snooze", "paid"):
            return await decide_reminder(app, tenant_id, ref, action, datetime.now(timezone.utc)) \
                or "That reminder was already handled."
        if action in ("ag_ok", "ag_no", "ag_site"):
            if action == "ag_site":     # approve this step and let the agent keep working on this one site for 15 minutes
                from src.agent_loop import grant_site
                await grant_site(app, tenant_id, ref)
            ok = await resume(app, tenant_id, sid=ref, approved=action != "ag_no",
                              background=getattr(app.state, "agent_background", True))
            return None if ok else "That was already handled."   # the resumed agent reports the outcome itself
        if action in ("bt_approve", "bt_reject"):
            return await decide_browser_task(app, tenant_id, msg.sender, ref, action == "bt_approve", number_id=msg.number_id,
                                             channel=msg.channel)
        return None
    cmd = msg.text.strip().lower()
    if cmd in ("pair browser", "pair pc", "connect browser", "browser pair"):
        from src.bridge.device import start_pairing
        code = await start_pairing(app.state.pool, tenant_id)
        return (f"Browser pairing code: {code}\nValid for 1 hour, works once. On your PC, in the Smara Browser Bridge folder run:\n"
                f"python munim_bridge.py pair --code {code}\nThen start it with: python munim_bridge.py run\n"
                "From then on I will use your own browser (your IP and logins stay on your PC). Send 'unpair browser' to disconnect.")
    if cmd in ("unpair browser", "disconnect browser"):
        from src.bridge.device import revoke_all
        n = await revoke_all(app.state.pool, tenant_id)
        return "Your PC browser is disconnected." if n else "No PC browser was connected."
    if cmd in ("browser status", "browser"):
        from src.bridge.device import online_device
        d = await online_device(app.state.pool, tenant_id)
        return f"Your PC browser is online ({d['name']})." if d else "Your PC browser is not connected. Send 'pair browser' to connect it; until then I use the server browser."
    if msg.text.strip().lower() == "telegram":
        return await link_message(app, tenant_id)
    if msg.text.strip().lower() == "telegram off":
        return await unlink(app, tenant_id)
    if getattr(getattr(app.state, "llm", None), "enabled", False):
        return await _agent(app, tenant_id, msg)
    if msg.kind == "document":
        return await _sheet(app, tenant_id, msg)
    if msg.kind == "image":
        return await _bill(app, tenant_id, msg)
    if msg.kind == "text" and (reply := await handle_text(app, tenant_id, msg.sender, msg.text, number_id=msg.number_id,
                                                                    channel=msg.channel)):
        return reply
    if msg.kind == "text" and msg.text.lower().startswith("browse "):
        return None  # plan card already sent
    if msg.text.strip().lower() in ("digest", "summary", "overdue", "status"):
        return await build_digest(app.state.pool, tenant_id, clock.today())
    return HELP


async def _agent(app, tenant_id: str, msg: InboundMessage) -> None:
    """Owner's free text or file starts (or continues) a trusted agent session; it replies itself."""
    from src import tenants

    files, content = (), msg.text or ""
    if msg.kind == "audio" and msg.media_id:               # voice note: transcribe, then treat as text
        spoken = ""
        try:
            data, mime = await fetch_media(app, msg)
            spoken = await transcribe(app.state.settings.sarvam_api_key, data, mime)
        except Exception:
            log.exception("owner voice transcription failed")
        if not spoken.strip():
            return "Voice note samajh nahi aaya. Kripya type karke bhejiye ya dobara bolkar bhejiye."
        content = f"[voice note, transcribed] {spoken.strip()}"
    elif msg.media_id and msg.kind in ("document", "image"):
        ref = f"{msg.channel}:{msg.media_id}:{msg.filename or ('photo.jpg' if msg.kind == 'image' else 'file')}"
        files = (ref,)
        content = f"{content}\n[The owner attached a file: file_ref={ref}; caption: {msg.caption or '-'}]".strip()
    if not content:
        return None
    tenant = await tenants.by_id(app.state.pool, tenant_id)
    await start_session(app, tenant=tenant, trust="TRUSTED", trigger="owner_message", content=content,
                        files=files, channel=msg.channel, background=getattr(app.state, "agent_background", True))
    return None


async def fetch_media(app, msg: InboundMessage) -> tuple[bytes, str]:
    if msg.channel == "telegram":
        mime = "image/jpeg" if msg.kind == "image" else "application/octet-stream"
        return await app.state.tg.download(msg.media_id), mime
    return await download_media(app.state.settings.wa_access_token, msg.media_id)


async def _sheet(app, tenant_id: str, msg: InboundMessage) -> str:
    if not msg.filename.lower().endswith((".xlsx", ".xlsm", ".csv")):
        return "I can read .xlsx or .csv sheets. Please export your outstanding report in one of those."
    data, _ = await fetch_media(app, msg)
    rows, skipped = map_sheet(msg.filename, data)
    result = await import_rows(app.state.pool, tenant_id, rows) if rows else {"created": 0, "updated": 0}
    out = f"Imported {result['created']} new, {result['updated']} updated invoices."
    if skipped:
        out += f"\nSkipped {len(skipped)}: " + "; ".join(skipped[:5])
    return out


async def _bill(app, tenant_id: str, msg: InboundMessage) -> str:
    data, mime = await fetch_media(app, msg)
    ocr = await read_image(app.state.llm, data, mime)
    if not ocr:
        return "Bill reading is not configured yet. Please send the sheet instead."
    phone = parse_phone(clean_phone_caption(msg.caption)) or parse_phone(ocr.get("phone"))
    amount = parse_amount_paise(ocr.get("amount_rupees"))
    due = parse_date(ocr.get("due_date")) or clock.today()
    if ocr.get("kind") != "bill" or not (ocr.get("party") and amount and phone):
        return "I could not read a complete bill. Send it again with the customer's number as caption."
    ref = ocr.get("invoice_ref") or f"bill-{msg.wamid[-8:]}"
    r = await import_rows(app.state.pool, tenant_id, [SheetRow(ocr["party"], phone, ref, amount, due)])
    return f"Added invoice {ref} for {ocr['party']}." if r["created"] else f"Updated invoice {ref}."
