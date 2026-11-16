"""A brief network blip to api.telegram.org must not lose a reply (seen 16 Nov: admin command failed with ConnectError)."""
import httpx
import pytest

from src.ingest.telegram import TelegramClient


def client(handler):
    return TelegramClient("TOKEN", httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_send_retries_a_failed_connection_and_succeeds(monkeypatch):
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) < 3:
            raise httpx.ConnectError("blip", request=req)
        return httpx.Response(200, json={"ok": True, "result": {}})
    monkeypatch.setattr("asyncio.sleep", lambda s: _noop())
    await client(handler).send({"chat_id": 1, "text": "hi"})
    assert len(calls) == 3


async def test_send_gives_up_after_three_connection_failures(monkeypatch):
    calls = []

    def handler(req):
        calls.append(1)
        raise httpx.ConnectError("down", request=req)
    monkeypatch.setattr("asyncio.sleep", lambda s: _noop())
    with pytest.raises(httpx.ConnectError):
        await client(handler).send({"chat_id": 1, "text": "hi"})
    assert len(calls) == 3


async def test_a_refusal_from_telegram_is_not_retried_as_a_connection_problem():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(403, json={"ok": False})
    with pytest.raises(httpx.HTTPStatusError):
        await client(handler).send({"chat_id": 1, "text": "hi"})
    assert len(calls) == 1


async def test_a_failed_button_spinner_does_not_stop_the_tap():
    from types import SimpleNamespace
    from src.ingest import telegram as tgmod

    class Down:
        async def answer(self, cid, text=""):
            raise httpx.ConnectError("blip")
    app = SimpleNamespace(state=SimpleNamespace(tg=Down(), pool=None, settings=SimpleNamespace(admin_telegram_ids=(), ops_telegram_chat_id=None)))
    upd = {"callback_query": {"id": "c1", "data": "x", "message": {"chat": {"id": 5, "type": "private"}}}}
    with pytest.raises(AttributeError):          # it got past answer(): the next step needs a pool we did not give it
        await tgmod.handle_update(app, upd)


async def _noop():
    return None
