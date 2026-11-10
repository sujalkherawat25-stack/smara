"""Telegram owner channel (owners only; counterparties stay on WhatsApp).

Linking is started from WhatsApp, where the owner is authenticated by phone:
the owner sends 'telegram', gets a one-hour, single-use t.me link, and opening
it binds that private chat to the tenant. Every update must carry the webhook
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
NOT_LINKED = "To connect this Telegram to your shop, send the word telegram to Smara on WhatsApp and open the link it sends."


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
        await self._call("sendMessage", payload)

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
    if text.startswith("/start"):
        code = text.partition(" ")[2].strip()
        await reply(await _link(pool, code, chat_id) if code else NOT_LINKED)
        return
    tenant = await tenants.by_telegram_chat(pool, chat_id)
    if tenant is None:
        await reply(NOT_LINKED)
        return
    kind, media_id, filename = "text", None, ""
    if doc := m.get("document"):
        kind, media_id, filename = "document", doc["file_id"], doc.get("file_name", "")
    elif photos := m.get("photo"):
        kind, media_id = "image", photos[-1]["file_id"]      # largest size is last
    elif voice := m.get("voice"):
        kind, media_id = "audio", voice["file_id"]
    await _owner(app, tenant, InboundMessage(
        wamid=f"tg:{chat_id}:{m['message_id']}", sender=tenant.owner_phone, kind=kind, text=text,
        media_id=media_id, filename=filename, caption=m.get("caption", ""), channel="telegram", chat_id=chat_id))


async def _owner(app, tenant, msg: InboundMessage) -> None:
    from src.ingest.dispatch import _claim
    from src.ingest.owner_flow import handle_owner
    from src.loop.owner_channel import resend_undelivered

    if not await _claim(app.state.pool, msg.wamid):
        return
    await tenants.touch_owner_inbound(app.state.pool, tenant.id, "telegram")   # approvals now follow the owner here
    await handle_owner(app, tenant, msg)
    await resend_undelivered(app, tenant, channel="telegram")
