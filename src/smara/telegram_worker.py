"""Native Smara Telegram edge.

The worker is intentionally a small raw-HTTP long poller. It resolves a
Telegram user to the shared account database, calls Smara's own chat API over
the private Docker network, and sends the answer back. No Memento imports or
public control tokens are involved.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .auth_store import AccountStore
from .config import settings

LOG = logging.getLogger("smara.telegram")
MAX_MESSAGE_CHARS = 4096


def chunks(text: str, size: int = MAX_MESSAGE_CHARS) -> list[str]:
    text = text.strip() or "Smara completed the request without a message."
    return [text[i : i + size] for i in range(0, len(text), size)]


class TelegramClient:
    def __init__(self, token: str, chat_url: str, account_store: AccountStore, http: httpx.AsyncClient):
        self.token = token
        self.chat_url = chat_url.rstrip("/")
        self.accounts = account_store
        self.http = http
        self.base = f"https://api.telegram.org/bot{token}"

    async def api(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self.http.post(f"{self.base}/{method}", json=payload)
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} failed")
        return data

    async def send(self, chat_id: int | str, text: str) -> None:
        for part in chunks(text):
            await self.api("sendMessage", {"chat_id": chat_id, "text": part, "disable_web_page_preview": True})

    async def typing(self, chat_id: int | str) -> None:
        try:
            await self.api("sendChatAction", {"chat_id": chat_id, "action": "typing"})
        except Exception:
            LOG.debug("telegram typing indicator failed", exc_info=True)

    async def handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message") or update.get("edited_message")
        if not isinstance(message, dict) or not isinstance(message.get("chat"), dict):
            return
        text = message.get("text")
        if not isinstance(text, str) or not text.strip():
            return
        chat_id = message["chat"].get("id")
        sender = message.get("from") or {}
        telegram_user_id = sender.get("id", chat_id)
        if chat_id is None or telegram_user_id is None:
            return
        command, _, argument = text.strip().partition(" ")
        command = command.lower().split("@", 1)[0]
        if command == "/start":
            await self.send(chat_id, "Hi, I’m Smara. Sign in on ai.syntarus.com, open Settings → Telegram, then send /link followed by the one-time code.")
            return
        if command == "/help":
            await self.send(chat_id, "/link 123456 — connect this chat to your Smara account\n/unlink — remove the connection\nThen send any message to chat with Smara.")
            return
        if command == "/link":
            if not argument.strip().isdigit() or len(argument.strip()) != 6:
                await self.send(chat_id, "Use the six-digit code exactly as shown in Smara Settings → Telegram.")
                return
            account_id = self.accounts.redeem_telegram_code(argument.strip(), telegram_user_id, chat_id)
            await self.send(chat_id, "✅ Telegram is connected to your Smara account." if account_id else "That code is invalid or expired. Generate a new one in Smara Settings.")
            return
        if command == "/unlink":
            account = self.accounts.telegram_account(telegram_user_id)
            if not account:
                await self.send(chat_id, "This Telegram chat is not linked.")
                return
            self.accounts.unlink_telegram(str(account["id"]))
            await self.send(chat_id, "Telegram has been disconnected from Smara.")
            return

        account = self.accounts.telegram_account(telegram_user_id)
        if not account:
            await self.send(chat_id, "Connect this chat first: sign in at ai.syntarus.com, open Settings → Telegram, then send /link <code>.")
            return
        await self.typing(chat_id)
        response = await self.http.post(
            f"{self.chat_url}/v1/chat",
            json={"message": text.strip(), "workspace_id": "telegram", "conversation_id": f"tg-{chat_id}"},
            headers={"X-Smara-Internal-Token": settings.internal_token, "X-Smara-Account-Id": str(account["id"])},
        )
        if response.status_code >= 400:
            LOG.warning("telegram chat failed status=%s", response.status_code)
            await self.send(chat_id, "Smara could not answer right now. Please try again in a moment.")
            return
        try:
            body = response.json()
            answer = str(body.get("message") or body.get("content") or "")
        except Exception:
            answer = ""
        await self.send(chat_id, answer or "Smara completed the request without a message.")


async def run() -> None:
    if not settings.telegram_bot_token:
        LOG.error("SMARA_TELEGRAM_BOT_TOKEN is not configured; worker will stay idle")
        while True:
            await asyncio.sleep(3600)
    accounts = AccountStore(settings.accounts_database_url, settings.database_path)
    accounts.ensure_schema()
    timeout = httpx.Timeout(65.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as http:
        client = TelegramClient(settings.telegram_bot_token, settings.telegram_chat_url, accounts, http)
        offset = 0
        LOG.info("native Smara Telegram worker started")
        while True:
            try:
                payload = {"timeout": 50, "allowed_updates": ["message"]}
                if offset:
                    payload["offset"] = offset
                data = await client.api("getUpdates", payload)
                for update in data.get("result", []):
                    if isinstance(update, dict):
                        offset = max(offset, int(update.get("update_id", 0)) + 1)
                        try:
                            await client.handle_update(update)
                        except Exception:
                            LOG.exception("telegram update handling failed")
            except asyncio.CancelledError:
                raise
            except Exception:
                LOG.exception("telegram polling failed; retrying")
                await asyncio.sleep(5)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()

