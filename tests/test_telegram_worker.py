import asyncio
import json

from smara.telegram_worker import TelegramClient, chunks


class FakeResponse:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {"ok": True, "result": {"message": "Hello from Smara"}}


class FakeHttp:
    def __init__(self):
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse()


class FakeAccounts:
    def __init__(self):
        self.linked = {42: {"id": "acct_owner", "chat_id": "9"}}

    def telegram_account(self, user_id):
        return self.linked.get(int(user_id))

    def redeem_telegram_code(self, code, user_id, chat_id):
        return "acct_owner" if code == "123456" else None

    def unlink_telegram(self, account_id):
        self.linked.clear()


def test_chunks_respects_telegram_limit():
    parts = chunks("x" * 9000)
    assert [len(part) for part in parts] == [4096, 4096, 808]


def test_link_command_and_chat_use_native_smara_headers(monkeypatch):
    http = FakeHttp()
    client = TelegramClient("bot-token", "http://api:8080", FakeAccounts(), http)
    asyncio.run(client.handle_update({"update_id": 1, "message": {"chat": {"id": 9}, "from": {"id": 99}, "text": "/link 123456"}}))
    assert any("sendMessage" in call[0] for call in http.calls)

    # The linked account is represented by the fake store under user 42.
    asyncio.run(client.handle_update({"update_id": 2, "message": {"chat": {"id": 9}, "from": {"id": 42}, "text": "hello"}}))
    chat_calls = [call for call in http.calls if call[0].endswith("/v1/chat")]
    assert chat_calls
    assert chat_calls[-1][1]["headers"]["X-Smara-Account-Id"] == "acct_owner"
    assert "X-Smara-Internal-Token" in chat_calls[-1][1]["headers"]

