"""Telegram owner channel (owners only; counterparties stay on WhatsApp).

An existing shop links Telegram from WhatsApp, where the owner is authenticated by phone:
the owner sends 'telegram', gets a one-hour, single-use t.me link, and opening
it binds that private chat to the tenant. A new owner can also sign up here
directly with an invite code (see telegram_onboarding.py). Every update must carry the webhook
secret Telegram was given at setWebhook time.
"""
import hmac
import logging
import secrets

import httpx
from fastapi import APIRouter, HTTPException, Request

from src import tenants
from src.ingest.payload import InboundMessage

log = logging.getLogger(__name__)
router = APIRouter()
API = "https://api.telegram.org"


class TelegramClient:
    def __init__(self, token: str, client: httpx.AsyncClient | None = None):
        self._bot, self._file = f"{API}/bot{token}", f"{API}/file/bot{token}"
        self._http = client or httpx.AsyncClient(timeout=30)
        self._username: str | None = None

    async def _call(self, method: str, payload: dict) -> dict:
        resp = await self._http.post(f"{self._bot}/{method}", json=payload)
        resp.raise_for_status()
        return resp.json().get("result") or {}

    async def send(self, payload: dict) -> None:
        """sendMessage. If Telegram refuses the HTML (a tag it cannot parse), the same words go again as plain text, so a
        formatting slip never loses the message. A 'too many requests' answer waits as asked (at most 5 s) and retries once."""
        try:
            await self._call("sendMessage", payload)
            return
        except httpx.HTTPStatusError as err:
            code = err.response.status_code
            if code == 429:
                import asyncio
                try:
                    wait = int(((err.response.json() or {}).get("parameters") or {}).get("retry_after", 1))
                except Exception:
                    wait = 1
                await asyncio.sleep(min(max(wait, 1), 5))
                await self._call("sendMessage", payload)
                return
            if code != 400 or not payload.get("parse_mode"):
                raise
            log.warning("telegram refused the formatted text; sending it plain")
        from src.loop.wa_format import html_to_plain
        plain = {k: v for k, v in payload.items() if k != "parse_mode"}
        plain["text"] = html_to_plain(payload.get("text", ""))
        await self._call("sendMessage", plain)

    async def typing(self, chat_id: int) -> None:
        """Shows 'typing…' in the owner's chat for about five seconds."""
        await self._call("sendChatAction", {"chat_id": chat_id, "action": "typing"})

    async def set_commands(self, commands: list[tuple[str, str]]) -> None:
        """The menu Telegram shows when the owner types '/'."""
        await self._call("setMyCommands", {"commands": [{"command": c, "description": d} for c, d in commands]})

    async def send_document(self, chat_id: int, data: bytes, filename: str, caption: str = "") -> None:
        resp = await self._http.post(f"{self._bot}/sendDocument", data={"chat_id": chat_id, "caption": caption[:1000]},
                                     files={"document": (filename, data)})
        resp.raise_for_status()

    async def answer(self, callback_id: str, text: str = "") -> None:
        await self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})

    async def download(self, file_id: str) -> bytes:
        path = (await self._call("getFile", {"file_id": file_id}))["file_path"]
        resp = await self._http.get(f"{self._file}/{path}")
        resp.raise_for_status()
        return resp.content

    async def username(self) -> str:
        if self._username is None:
            self._username = (await self._call("getMe", {}))["username"]
        return self._username

    async def aclose(self) -> None:
        await self._http.aclose()


async def link_message(app, tenant_id: str) -> str:
    """Owner asked on WhatsApp to link Telegram: issue a one-hour single-use code."""
    tg = getattr(app.state, "tg", None)
    if tg is None:
        return "Telegram is not set up on this server yet."
    code = secrets.token_urlsafe(12)
    async with app.state.pool.connection() as conn:
        await conn.execute(
            """UPDATE tenants SET telegram_link_code = %s, telegram_link_expires_at = now() + interval '1 hour'
                WHERE id = %s""", (code, tenant_id))
    return f"Open this link on the phone you use Telegram on (valid 1 hour):\nhttps://t.me/{await tg.username()}?start={code}"


async def unlink(app, tenant_id: str) -> str:
    async with app.state.pool.connection() as conn:
        await conn.execute("UPDATE tenants SET telegram_chat_id = NULL, owner_last_channel = 'whatsapp' WHERE id = %s", (tenant_id,))
    return "Telegram disconnected. I'll message you on WhatsApp."


async def _link(pool, code: str, chat_id: int) -> str:
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT id FROM tenants WHERE telegram_chat_id = %s", (chat_id,))
        if await cur.fetchone():
            return "This Telegram is already linked to a shop. Send 'telegram off' on WhatsApp there first."
        cur = await conn.execute(
            """UPDATE tenants SET telegram_chat_id = %s, owner_last_channel = 'telegram', telegram_link_code = NULL, telegram_link_expires_at = NULL
                WHERE telegram_link_code = %s AND telegram_link_expires_at > now() RETURNING name""", (chat_id, code))
        row = await cur.fetchone()
    return f"Linked to {row['name']}. Approval cards and your digest will now come here." if row \
        else "That link has expired or was already used. Send 'telegram' on WhatsApp for a new one."


async def _is_link_code(pool, code: str) -> bool:
    """True if this /start code is a shop's Telegram link code (used or expired ones included, so they get the right reply)."""
    from src.ingest.onboarding import _code_from, _valid_invite
    invite = _code_from(f"join {code}")
    return not (invite and await _valid_invite(pool, invite))


@router.post("/telegram/webhook")
async def webhook(request: Request):
    secret = request.app.state.settings.telegram_webhook_secret
    got = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not secret or not hmac.compare_digest(got, secret):
        raise HTTPException(status_code=401, detail="bad secret")
    try:
        await handle_update(request.app, await request.json())
    except Exception:
        log.exception("telegram update failed")   # 200 anyway: Telegram retries otherwise
    return {"ok": True}


async def handle_update(app, upd: dict) -> None:
    pool, tg = app.state.pool, app.state.tg
    if cq := upd.get("callback_query"):
        await tg.answer(cq["id"])
        chat_id = ((cq.get("message") or {}).get("chat") or {}).get("id")
        tenant = await tenants.by_telegram_chat(pool, chat_id) if chat_id else None
        if tenant:
            await _owner(app, tenant, InboundMessage(
                wamid=f"tg:cq:{cq['id']}", sender=tenant.owner_phone, kind="interactive",
                button_id=cq.get("data"), channel="telegram", chat_id=chat_id))
        return
    m = upd.get("message")
    if not m or (m.get("chat") or {}).get("type") != "private":
        return
    chat_id, text = m["chat"]["id"], (m.get("text") or "").strip()
    from src.loop.wa_format import to_telegram_html
    reply = lambda t: tg.send({"chat_id": chat_id, "text": to_telegram_html(t), "parse_mode": "HTML"})
    from src.ingest import telegram_onboarding
    tenant = await tenants.by_telegram_chat(pool, chat_id)
    if text.startswith("/start"):
        code = text.partition(" ")[2].strip()
        if tenant:
            await reply(f"This Telegram is linked to {tenant.name}. Just write to me here.")
        elif code and await _is_link_code(pool, code):
            await reply(await _link(pool, code, chat_id))
        else:                                   # an invite link (t.me/<bot>?start=<invite code>), or a bare /start
            await telegram_onboarding.handle(app, m, chat_id, f"join {code}" if code else "")
        return
    if tenant is None:
        await telegram_onboarding.handle(app, m, chat_id, text)   # a new owner signing up, or the waitlist
        return
    kind, media_id, filename, mime, size = "text", None, "", "", 0
    if doc := m.get("document"):
        kind, media_id, filename = "document", doc["file_id"], doc.get("file_name", "")
        mime, size = doc.get("mime_type", ""), doc.get("file_size", 0)
    elif photos := m.get("photo"):
        kind, media_id = "image", photos[-1]["file_id"]      # largest size is last
        size = photos[-1].get("file_size", 0)
    elif voice := m.get("voice"):
        kind, media_id, mime, size = "audio", voice["file_id"], voice.get("mime_type", "audio/ogg"), voice.get("file_size", 0)
    elif video := m.get("video"):
        kind, media_id, filename = "video", video["file_id"], video.get("file_name", "")
        mime, size = video.get("mime_type", ""), video.get("file_size", 0)
    elif audio := m.get("audio"):                             # a music or audio file, not a voice note: a file
        kind, media_id, filename = "document", audio["file_id"], audio.get("file_name", "")
        mime, size = audio.get("mime_type", ""), audio.get("file_size", 0)
    await _owner(app, tenant, InboundMessage(
        wamid=f"tg:{chat_id}:{m['message_id']}", sender=tenant.owner_phone, kind=kind, text=text,
        media_id=media_id, filename=filename, caption=m.get("caption", ""), channel="telegram", chat_id=chat_id,
        mime=mime, size=int(size or 0)))


async def _owner(app, tenant, msg: InboundMessage) -> None:
    from src.ingest.dispatch import _claim
    from src.ingest.owner_flow import handle_owner
    from src.loop.owner_channel import resend_undelivered

    if not await _claim(app.state.pool, msg.wamid):
        return
    from src import limits
    from src.loop.owner_channel import send_to_owner
    from src.loop.whatsapp import text_message

    await tenants.touch_owner_inbound(app.state.pool, tenant.id, "telegram")   # approvals now follow the owner here
    if not msg.button_id:                       # the same daily limits as on WhatsApp; button taps are never limited
        await limits.record(app.state.pool, tenant.id, limits.kind_of(msg))
    if (stop := await limits.check(app, tenant, msg)):
        await send_to_owner(app, tenant, text_message(tenant.owner_phone, stop), kind="owner_text", reply_channel="telegram")
    else:
        await handle_owner(app, tenant, msg)
    await resend_undelivered(app, tenant, channel="telegram")
