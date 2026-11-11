"""Thread A: owner messages are trusted. Buttons, sheet/bill uploads, digest requests, voice notes and files."""
import asyncio
from src import clock
import logging
from dataclasses import replace
from datetime import date, datetime, timezone

from src.bridge.coordinator import decide_adjustment
from src.ingest.box_commands import decide_browser_task, handle_text
from src.ingest.importer import import_rows
from src.ingest.media import MediaError, clean_phone_caption, download_media, media_token, read_image, transcribe
from src.ingest import owner_files
from src.ingest.payload import InboundMessage
from src.ingest.customer_number import add_number, parse_number_command
from src.ingest.reopen import parse_reopen_command, reopen_invoice
from src.ingest.guides import EXPORT_GUIDE
from src.ingest.telegram import link_message, unlink
from src.ingest.sheet_mapper import SheetRow, map_sheet, parse_amount_paise, parse_date, parse_phone
from src.agent_loop import attach_to_running, resume, start_session
from src.loop import wa_link
from src.loop.digest import build_digest
from src.loop.reminders import decide_reminder
from src.loop.owner_channel import send_to_owner
from src.loop.whatsapp import text_message
from src.tenants import Tenant

log = logging.getLogger(__name__)
HELP = ("Commands: digest, telegram (link Telegram), gstin <no>, quotes <product>, browse <task>. Send me your outstanding sheet (.xlsx/.csv), a photo of a bill with the customer's "
        "number as caption, or say 'digest'.\nTally/Busy se list kaise nikalein: 'export'. Sample file: 'sample'.")
EXPORT_CMDS = ("export", "tally export", "kaise bheju", "export guide")
SAMPLE_CMDS = ("sample", "sample file")
FILE_KINDS = ("document", "image", "video", "sticker")
VOICE_OFF = "Voice notes are not enabled on Smara yet. Please type your message instead."
VOICE_FAILED = "I could not turn that voice note into text right now. Please type it, or send it again."
VOICE_UNCLEAR = "Voice note samajh nahi aaya. Kripya type karke bhejiye ya dobara bolkar bhejiye."
FILE_FAILED = "I could not download that file. Please send it again."
DEFAULT_MIME = {"image": "image/jpeg", "audio": "audio/ogg", "video": "video/mp4"}


async def handle_owner(app, tenant: Tenant, msg: InboundMessage) -> None:
    reply = await _route(app, tenant.id, msg)
    if reply:
        await send_to_owner(app, tenant, text_message(tenant.owner_phone, reply), kind="owner_text",
                            reply_channel=msg.channel, number_id=msg.number_id or None)


async def _route(app, tenant_id: str, msg: InboundMessage) -> str | None:
    if msg.size > owner_files.MAX_BYTES:            # a file or voice note over the limit: said plainly, nothing is kept
        return owner_files.too_large_text(msg.size)
    if msg.kind == "audio" and msg.media_id:        # a voice note is read as if the owner had typed it
        spoken, reply = await _transcribe(app, msg)
        if reply:
            return reply
        msg = replace(msg, kind="text", text=spoken)
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
        if action in ("send", "own", "snooze", "paid"):
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
    cmd = " ".join(msg.text.strip().lower().replace("'", "").split())
    if len(cmd) <= 30 and "unpair" not in cmd and any(k in cmd for k in ("pair browser", "pair pc", "connect browser", "browser pair", "pair new browser")):
        from src.bridge.device import online_device, paired_device, start_pairing
        if "new" not in cmd:
            if d := await online_device(app.state.pool, tenant_id):
                return f"Your PC browser ({d['name']}) is already connected. Nothing to do: just ask me what to look up or do."
            if d := await paired_device(app.state.pool, tenant_id):
                return (f"Your PC ({d['name']}) is already paired. Its helper starts by itself when the PC is on and when Smara "
                        "Desktop is open, so you do not need to type anything. Just switch the PC on. "
                        "(To connect a different PC, send 'pair new browser'.)")
        code = await start_pairing(app.state.pool, tenant_id)
        return (f"Browser pairing code: {code}\n(valid 1 hour, works once)\n\n"
                "On your PC open Smara Desktop > Settings > Browser, paste the code and press Pair. That is all: after this it "
                "starts by itself whenever the PC is on, and I will use your own browser (your logins stay on your PC).\n\n"
                f"No Smara Desktop? Run once: python smara_bridge.py pair --code {code} && python smara_bridge.py install-startup\n"
                "Send 'unpair browser' to disconnect.")
    if cmd in ("unpair browser", "disconnect browser"):
        from src.bridge.device import revoke_all
        n = await revoke_all(app.state.pool, tenant_id)
        return "Your PC browser is disconnected." if n else "No PC browser was connected."
    if cmd in ("browser status", "browser"):
        from src.bridge.device import online_device
        d = await online_device(app.state.pool, tenant_id)
        return f"Your PC browser is online ({d['name']})." if d else "Your PC browser is not connected. Send 'pair browser' to connect it; until then I use the server browser."
    if cmd in ("delete my data", "mera data delete karo", "data delete karo", "delete my account"):
        async with app.state.pool.connection() as conn:
            open_ = await (await conn.execute("SELECT 1 FROM data_requests WHERE tenant_id = %s AND kind = 'delete' AND status = 'OPEN'",
                                              (tenant_id,))).fetchone()
            if not open_:
                await conn.execute("INSERT INTO data_requests (tenant_id, kind) VALUES (%s, 'delete')", (tenant_id,))
        return ("Aapka data delete karne ka request mil gaya. 7 din ke andar aapka shop, customers, baaki rakam aur messages "
                "hata diye jayenge, aur hum yahin bata denge. Rokna ho to 'cancel delete' likhiye.")
    if cmd in ("cancel delete", "delete cancel"):
        async with app.state.pool.connection() as conn:
            n = (await conn.execute("DELETE FROM data_requests WHERE tenant_id = %s AND kind = 'delete' AND status = 'OPEN'",
                                    (tenant_id,))).rowcount
        return "Delete request cancel kar diya." if n else "Koi delete request khula nahi tha."
    if cmd in ("connect whatsapp", "whatsapp connect", "connect my whatsapp", "apna number jodo", "number connect karo"):
        from src.ingest.connect import configured, new_link
        if app.state.settings.wa_link_url:            # the shop's own SIM, through the link sidecar (QR code page)
            link = await new_link(app.state.pool, app.state.settings, tenant_id, path="/connect/wa-link")
            return (f"To link your WhatsApp SIM number to Smara, open this link (valid 24 hours, one time) and scan the QR "
                    f"code with that SIM's WhatsApp:\n{link}\n\n"
                    "Customer messages will go out from that number. Nothing is sent without your approval.")
        if not configured(app.state.settings):
            return ("Apna khud ka WhatsApp number jodna abhi chalu nahi hua hai (Meta ki manzoori baaki hai). "
                    "Jaise hi chalu hoga, main aapko bataunga.")
        link = await new_link(app.state.pool, app.state.settings, tenant_id)
        return ("Apna WhatsApp Business number Smara se jodne ke liye ye link kholiye (24 ghante, ek baar):\n"
                f"{link}\n\nAapke customers ko message aapke apne number se jayenge. Kuch bhi bina aapke tap ke nahi jata.")
    reopen_ref = parse_reopen_command(msg.text)
    if reopen_ref:
        reply = await reopen_invoice(app.state.pool, tenant_id, reopen_ref)
        if reply:
            return reply
    number_cmd = parse_number_command(msg.text)
    if number_cmd:
        reply = await add_number(app.state.pool, tenant_id, *number_cmd)
        if reply:
            return reply
    if cmd in EXPORT_CMDS:
        return EXPORT_GUIDE
    if cmd in SAMPLE_CMDS:
        return await _send_sample(app, tenant_id, msg.channel)
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
        digest = await build_digest(app.state.pool, tenant_id, clock.today())
        line = await wa_link.health_line(app.state.pool, tenant_id)
        return f"{line}\n\n{digest}" if line else digest
    if cmd in ("whatsapp status", "wa status", "link status"):
        return await wa_link.health_line(app.state.pool, tenant_id, always=True)
    if cmd in ("disconnect whatsapp", "whatsapp disconnect"):
        if not wa_link.configured(app):
            return "No WhatsApp SIM link is set up on this server."
        await wa_link.disconnect(app, tenant_id)
        return "The SIM link is disconnected. Customer messages are paused until you send 'connect whatsapp' again."
    return HELP


async def _send_sample(app, tenant_id: str, channel: str) -> str | None:
    """Send the fake sample outstanding list as a file. None means it was sent (no text reply needed)."""
    from src import tenants
    from src.ingest.guides import SAMPLE_CAPTION, SAMPLE_NAME, XLSX_MIME, sample_sheet_bytes
    from src.loop.owner_channel import send_document_to_owner
    data = sample_sheet_bytes()
    if data is None:
        return "Sample file abhi mere paas nahi hai. Apni Excel file bhejiye, ya 'export' likhiye."
    tenant = await tenants.by_id(app.state.pool, tenant_id)
    how = await send_document_to_owner(app, tenant, data, SAMPLE_NAME, XLSX_MIME, SAMPLE_CAPTION, reply_channel=channel)
    if how == "window_closed":
        return "Sample file bhejne ke liye pehle koi message likhiye, phir 'sample' bhejiye."
    return None


async def _transcribe(app, msg: InboundMessage) -> tuple[str, str | None]:
    """The spoken words, or ('', the reply to send) when the voice note cannot be used. Off until a Sarvam key is set."""
    s = app.state.settings
    if not s.sarvam_api_key:
        return "", VOICE_OFF
    try:
        data, mime = await fetch_media(app, msg)
    except MediaError as e:
        return "", str(e)
    except Exception:
        log.exception("owner voice note download failed")
        return "", VOICE_FAILED
    try:
        spoken = (await transcribe(s.sarvam_api_key, data, mime or "audio/ogg", url=s.sarvam_stt_url)).strip()
    except Exception:
        log.exception("owner voice transcription failed")
        return "", VOICE_FAILED
    return (spoken, None) if spoken else ("", VOICE_UNCLEAR)


async def _agent(app, tenant_id: str, msg: InboundMessage) -> str | None:
    """Owner's free text or file starts (or continues) a trusted agent session; it replies itself.
    A message that arrives while a run is working on a task joins that run instead, and is read at its next step."""
    from src import tenants

    files, content = (), msg.text or ""
    if msg.media_id and msg.kind in FILE_KINDS:
        try:
            data, _ = await fetch_media(app, msg)
        except MediaError as e:
            return str(e)
        except Exception:
            log.exception("owner file download failed")
            return FILE_FAILED
        try:
            ref = await asyncio.to_thread(owner_files.save, app.state.settings.owner_files_dir, tenant_id, file_name(msg), data)
        except OSError:
            log.exception("could not keep an owner file")
            return FILE_FAILED
        files = (ref,)
        content = f"{content}\n[The owner attached a file: file_ref={ref}; caption: {msg.caption or '-'}]".strip()
    if not content:
        return None
    if await attach_to_running(app, tenant_id, content, files):
        return None
    tenant = await tenants.by_id(app.state.pool, tenant_id)
    await start_session(app, tenant=tenant, trust="TRUSTED", trigger="owner_message", content=content,
                        files=files, channel=msg.channel, background=getattr(app.state, "agent_background", True))
    return None


_EXT = {"image": ".jpg", "video": ".mp4", "sticker": ".webp", "audio": ".ogg"}


def file_name(msg: InboundMessage) -> str:
    """A safe name with an extension, so the agent and the sandbox can tell what kind of file it is."""
    import mimetypes
    from src.box.sandbox import safe_name
    name = safe_name(msg.filename) if msg.filename else ""
    if not name or "." not in name:
        ext = mimetypes.guess_extension((msg.mime or "").split(";")[0]) or _EXT.get(msg.kind, "")
        name = (name or {"image": "photo", "video": "video", "sticker": "sticker"}.get(msg.kind, "file")) + ext
    return name


async def fetch_media(app, msg: InboundMessage) -> tuple[bytes, str]:
    """An owner's file or voice note, from whichever channel it came by. Refused above the 20 MB limit on every channel."""
    limit = owner_files.MAX_BYTES
    if msg.size > limit:
        raise MediaError(owner_files.too_large_text(msg.size))
    mime = msg.mime or DEFAULT_MIME.get(msg.kind, "application/octet-stream")
    if msg.channel == "telegram":
        data = await app.state.tg.download(msg.media_id)          # Telegram itself refuses files over 20 MB
    elif msg.channel == "wa_link":
        data = await wa_link.media(app, msg.link_tenant_id, msg.media_id)
    else:
        return await download_media(await media_token(app, msg.number_id), msg.media_id, max_bytes=limit)
    if len(data) > limit:
        raise MediaError(owner_files.too_large_text(len(data)))
    return data, mime


async def _sheet(app, tenant_id: str, msg: InboundMessage) -> str:
    if not msg.filename.lower().endswith((".xlsx", ".xlsm", ".xls", ".csv", ".tsv", ".txt")):
        return "I can read .xlsx, .xls or .csv sheets. Please export your outstanding report in one of those."
    try:
        data, _ = await fetch_media(app, msg)
    except MediaError as e:
        return str(e)
    async with app.state.pool.connection() as conn:       # customers already on file supply missing mobile numbers
        known = {r["canonical_name"]: r["whatsapp_phone"] for r in await (await conn.execute(
            "SELECT canonical_name, whatsapp_phone FROM parties WHERE tenant_id = %s", (tenant_id,))).fetchall()}
    rows, skipped = map_sheet(msg.filename, data, known)
    result = await import_rows(app.state.pool, tenant_id, rows) if rows else {"created": 0, "updated": 0}
    out = f"Imported {result['created']} new, {result['updated']} updated invoices."
    if skipped:
        out += f"\nSkipped {len(skipped)}: " + "; ".join(skipped[:5])
    return out


async def _bill(app, tenant_id: str, msg: InboundMessage) -> str:
    try:
        data, mime = await fetch_media(app, msg)
    except MediaError as e:
        return str(e)
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
