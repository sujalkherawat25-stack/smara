"""An ops alert reaches the Smara team once an hour when owner messages cannot be sent, and never breaks a run."""
from types import SimpleNamespace

import httpx
import pytest

from src import ops_alert as oa
from src.loop import owner_channel


class FakeTG:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    async def send(self, payload):
        if self.fail:
            raise RuntimeError("telegram down")
        self.sent.append(payload)


class RefusingWA:
    """Meta says the token is no good."""

    async def send(self, payload, *, number_id=None):
        req = httpx.Request("POST", "https://graph.example/messages")
        raise httpx.HTTPStatusError("401 Unauthorized", request=req, response=httpx.Response(401, request=req))


def _app(chat=99, tg=None, wa=None):
    settings = SimpleNamespace(ops_telegram_chat_id=chat, wa_owner_template="")
    return SimpleNamespace(state=SimpleNamespace(settings=settings, tg=tg, wa=wa, wa_link=None, pool=None))


@pytest.fixture(autouse=True)
def _fresh_alert_memory():
    oa._last_sent.clear()
    yield
    oa._last_sent.clear()


async def test_an_alert_goes_to_the_ops_chat_once_an_hour(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(oa.time, "monotonic", lambda: now[0])
    tg = FakeTG()
    app = _app(tg=tg)
    assert await oa.ops_alert(app, "k", "first") is True
    assert await oa.ops_alert(app, "k", "again soon") is False
    assert await oa.ops_alert(app, "other", "different problem") is True
    now[0] += oa.ALERT_EVERY_S
    assert await oa.ops_alert(app, "k", "an hour later") is True
    assert [m["text"] for m in tg.sent] == ["first", "different problem", "an hour later"]
    assert all(m["chat_id"] == 99 for m in tg.sent)


async def test_without_an_ops_chat_or_a_bot_the_alert_is_only_logged(caplog):
    assert await oa.ops_alert(_app(chat=None, tg=FakeTG()), "k", "x") is False
    assert await oa.ops_alert(_app(chat=99, tg=None), "k", "x") is False
    assert "ops alert not sent" in caplog.text


async def test_a_failed_alert_does_not_raise_and_is_sent_next_time():
    tg = FakeTG(fail=True)
    app = _app(tg=tg)
    assert await oa.ops_alert(app, "k", "x") is False
    tg.fail = False
    assert await oa.ops_alert(app, "k", "x") is True


async def test_an_official_401_tells_ops_once_and_the_error_still_reaches_the_run():
    tg = FakeTG()
    app = _app(tg=tg, wa=RefusingWA())
    tenant = SimpleNamespace(id="t1", owner_phone="919000000001", telegram_chat_id=None,
                             owner_last_channel="whatsapp", owner_last_inbound_at=None)
    payload = {"type": "text", "text": {"body": "hi"}}
    with pytest.raises(httpx.HTTPStatusError):
        await owner_channel.send_to_owner(app, tenant, payload, kind="owner_text")
    with pytest.raises(httpx.HTTPStatusError):
        await owner_channel.send_to_owner(app, tenant, payload, kind="owner_text")
    assert len(tg.sent) == 1
    assert tg.sent[0]["chat_id"] == 99 and "HTTP 401" in tg.sent[0]["text"]
