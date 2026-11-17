"""The agent can put an emoji reaction on the owner's latest Telegram message (never raising, only allowed emoji)."""
import asyncio
from types import SimpleNamespace

import httpx

from src import tenants
from src.agent import extras, policy, tools
from src.agent.policy import SessionCtx
from src.ingest.telegram import TelegramClient

CTX = SessionCtx(id="s", tenant_id="t1", trust="TRUSTED")


class _Tg:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    async def react(self, chat_id, message_id, emoji):
        if self.fail:
            raise httpx.ReadTimeout("slow")
        self.calls.append((chat_id, message_id, emoji))


def _app(tg, last=None):
    return SimpleNamespace(state=SimpleNamespace(pool=None, tg=tg, tg_last=last if last is not None else {555: 42}))


def _run(app, args, monkeypatch, chat_id=555):
    async def by_id(pool, tid):
        return SimpleNamespace(telegram_chat_id=chat_id)
    monkeypatch.setattr(tenants, "by_id", by_id)
    return asyncio.run(extras.react_to_owner(app, CTX, args))


def test_reacts_to_the_latest_message(monkeypatch):
    tg = _Tg()
    out = _run(_app(tg), {"emoji": "🎉"}, monkeypatch)
    assert out["reacted"] is True and tg.calls == [(555, 42, "🎉")]


def test_emoji_outside_the_list_is_refused(monkeypatch):
    tg = _Tg()
    out = _run(_app(tg), {"emoji": "🖕"}, monkeypatch)
    assert out["reacted"] is False and not tg.calls


def test_no_telegram_or_no_message_is_a_note_not_an_error(monkeypatch):
    assert _run(_app(_Tg()), {"emoji": "👍"}, monkeypatch, chat_id=None)["reacted"] is False
    assert _run(_app(_Tg(), last={}), {"emoji": "👍"}, monkeypatch)["reacted"] is False


def test_telegram_failure_never_raises(monkeypatch):
    out = _run(_app(_Tg(fail=True)), {"emoji": "👍"}, monkeypatch)
    assert out["reacted"] is False and "ReadTimeout" in out["note"]


def test_tool_is_registered_and_allowed():
    assert "react_to_message" in tools.TOOL_NAMES
    spec = next(t for t in tools.TOOLS if t["function"]["name"] == "react_to_message")
    assert set(spec["function"]["parameters"]["properties"]["emoji"]["enum"]) == set(policy.REACTIONS)
    assert policy.pre_tool_use(CTX, "react_to_message", {"emoji": "👍"}, tools.TOOL_NAMES).verdict == "allow"
    cp = SessionCtx(id="s", tenant_id="t1", trust="UNTRUSTED", party_id="p")
    assert policy.pre_tool_use(cp, "react_to_message", {"emoji": "👍"}, tools.TOOL_NAMES).verdict == "deny"


def test_client_calls_set_message_reaction():
    seen = {}

    async def go():
        tg = TelegramClient("tok", client=httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r: (seen.update(url=str(r.url), body=r.content), httpx.Response(200, json={"ok": True, "result": True}))[1])))
        await tg.react(555, 42, "🔥")
    asyncio.run(go())
    assert seen["url"].endswith("/setMessageReaction") and b'"message_id": 42' in seen["body"].replace(b":42", b": 42")
