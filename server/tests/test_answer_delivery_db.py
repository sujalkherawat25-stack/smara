"""When Telegram stops answering, the owner's answer is kept and delivered in the background; the run is not a failure.
(16 Nov: every call to Telegram timed out for a while; the answer was written but lost and the owner was left at 'Still working'.)"""
import httpx
from test_phase_b_db import make_app, one, pool  # noqa: F401

from src import agent_loop, tenants


class FlakyTG:
    def __init__(self, failures):
        self.sent, self.failures = [], failures

    async def send(self, payload):
        if self.failures:
            self.failures -= 1
            raise httpx.ReadTimeout("telegram did not answer")
        self.sent.append(payload)

    async def username(self):
        return "smara_test_bot"


async def _setup(pool, failures):
    app = make_app(pool)
    app.state.tg = FlakyTG(failures)
    t = await tenants.create(pool, name="Shop", owner_phone="tg777")
    async with pool.connection() as c:
        await c.execute("UPDATE tenants SET telegram_chat_id = 777, owner_last_channel = 'telegram' WHERE id = %s", (t.id,))
    return app, {"trust": "TRUSTED", "tenant_id": t.id, "channel": "telegram", "id": "00000000-0000-0000-0000-000000000001"}


async def _drain(app, monkeypatch):
    import asyncio
    real = asyncio.sleep
    monkeypatch.setattr(agent_loop.asyncio, "sleep", lambda s: real(0))
    while app.state.bg:
        await asyncio.gather(*list(app.state.bg))


async def test_an_answer_that_telegram_would_not_take_is_delivered_later(pool, monkeypatch):
    app, row = await _setup(pool, failures=3)
    await agent_loop._finish(app, row, "Distance: 13.4 km")             # does not raise: the answer is kept
    await _drain(app, monkeypatch)
    assert [m["text"] for m in app.state.tg.sent] == ["Distance: 13.4 km"]
    assert (await one(pool, "select count(*) as n from chat_messages where role = 'smara'"))["n"] == 1


async def test_a_refusal_is_not_retried_forever(pool, monkeypatch):
    app, row = await _setup(pool, failures=0)

    async def blocked(payload):
        raise httpx.HTTPStatusError("403", request=httpx.Request("POST", "http://x"), response=httpx.Response(403))
    app.state.tg.send = blocked
    import pytest
    with pytest.raises(httpx.HTTPStatusError):                           # the bot was blocked by the user: a real error, not a blip
        await agent_loop._finish(app, row, "hello")
    assert not app.state.bg
