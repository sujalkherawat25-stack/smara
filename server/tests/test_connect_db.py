"""A shop connects its own WhatsApp number through Meta's Embedded Signup; sends then use the shop's own token."""
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import FastAPI
from test_agent_db import OWNER, env, one, owner  # noqa: F401

from src import tenants, vault
from src.config import Settings
from src.ingest import connect
from src.ingest.dispatch import dispatch
from src.loop.whatsapp import WhatsAppClient

META = dict(meta_app_id="111", meta_es_config_id="222", wa_app_secret="app-secret", public_base_url="https://smara.test/smara")


@pytest.fixture
def master(monkeypatch):
    monkeypatch.setenv("VAULT_MASTER_KEY", vault.new_master_key())
    monkeypatch.setenv("VAULT_MASTER_VERSION", "1")
    monkeypatch.delenv("VAULT_MASTER_KEY_FILE", raising=False)
    connect._TOKENS.clear()


def fake_meta(calls: list, *, refuse: str = "", number="91 98290 00011"):
    def handler(req: httpx.Request):
        calls.append((req.method, req.url.path, dict(req.url.params), req.content.decode() if req.content else ""))
        if refuse and refuse in req.url.path:
            return httpx.Response(400, json={"error": {"message": "permission denied"}})
        if req.url.path.endswith("/oauth/access_token"):
            return httpx.Response(200, json={"access_token": "SHOP-TOKEN-xyz"})
        if req.url.path.endswith("/message_templates"):
            return httpx.Response(200, json={"id": "t1", "status": "PENDING"})
        if req.url.path.endswith("/PHONE1"):
            return httpx.Response(200, json={"display_phone_number": number, "verified_name": "Shop"})
        return httpx.Response(200, json={"success": True, "id": "x"})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def page_app(env, calls, **kw):
    app = FastAPI()
    app.state.pool, app.state.settings = env.pool, Settings(**META)
    app.state.wa, app.state.tg, app.state.graph_http = env.app.state.wa, None, fake_meta(calls, **kw)
    app.include_router(connect.router)
    return app, httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://smara.test")


async def link_for(env):
    url = await connect.new_link(env.pool, Settings(**META), env.t.id)
    return parse_qs(urlparse(url).query)["t"][0], url


# ---------------------------------------------------------------- the owner asks for a link

async def test_owner_gets_a_one_time_link_only_when_meta_is_set_up(env):
    env.app.state.llm.enabled = False
    await dispatch(env.app, owner("c1", "connect whatsapp"))
    assert "abhi chalu nahi" in env.app.state.wa.sent[-1]["text"]["body"]
    env.app.state.settings = Settings(**META)
    await dispatch(env.app, owner("c2", "connect whatsapp"))
    body = env.app.state.wa.sent[-1]["text"]["body"]
    assert "https://smara.test/smara/connect?t=" in body


async def test_the_page_shows_the_shop_and_refuses_bad_links(env):
    t, _ = await link_for(env)
    _, http = page_app(env, [])
    async with http:
        ok = await http.get(f"/connect?t={t}")
        assert ok.status_code == 200 and "Shop" in ok.text and '"config_id": "222"' in ok.text
        assert "existing WhatsApp Business app number" in ok.text
        assert (await http.get("/connect?t=not-a-real-token-123")).status_code == 404


# ---------------------------------------------------------------- Meta's popup finished: connect for real

async def test_ids_that_did_not_come_from_meta_are_refused_and_the_link_stays_usable(env, master):
    t, _ = await link_for(env)
    _, http = page_app(env, [])
    async with http:
        r = await http.post("/connect/complete", json={"t": t, "code": "C" * 20, "mode": "coexistence",
                                                       "waba_id": "777", "phone_number_id": "../../me"})
        assert r.status_code == 400 and "did not tell us which number" in r.text
        assert (await http.get(f"/connect?t={t}")).status_code == 200                  # not consumed by a bad request


async def _connect(env, calls, mode="coexistence", number_id="1001", **kw):
    t, _ = await link_for(env)
    app, http = page_app(env, calls, **kw)

    async with http:
        return await http.post("/connect/complete", json={"t": t, "code": "C" * 20, "mode": mode,
                                                          "waba_id": "777", "phone_number_id": number_id}), t, app


@pytest.fixture
def numbered(monkeypatch):
    """Meta's fake answers the number lookup for /1001."""
    orig = fake_meta

    def fm(calls, *, refuse="", number="91 98290 00011"):
        def handler(req):
            calls.append((req.method, req.url.path, dict(req.url.params), req.content.decode() if req.content else ""))
            if refuse and refuse in req.url.path:
                return httpx.Response(400, json={"error": {"message": "permission denied"}})
            if req.url.path.endswith("/oauth/access_token"):
                return httpx.Response(200, json={"access_token": "SHOP-TOKEN-xyz"})
            if req.url.path.endswith("/message_templates"):
                return httpx.Response(200, json={"id": "t1", "status": "PENDING"})
            if req.url.path.endswith("/1001") or req.url.path.endswith("/1002"):
                return httpx.Response(200, json={"display_phone_number": number, "verified_name": "Shop"})
            return httpx.Response(200, json={"success": True})
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setitem(globals(), "fake_meta", fm)
    yield
    monkeypatch.setitem(globals(), "fake_meta", orig)


async def test_full_connect(env, master, numbered):
    calls: list = []
    r, t, app = await _connect(env, calls)
    assert r.status_code == 200 and r.json()["connected"]
    paths = [c[1] for c in calls]
    assert any(p.endswith("/oauth/access_token") for p in paths)
    assert any(p.endswith("/777/subscribed_apps") for p in paths)                     # their messages reach us
    assert sum(p.endswith("/777/message_templates") for p in paths) == 3               # our templates in their account
    assert not any(p.endswith("/register") for p in paths)                             # existing app number: no re-register
    shop = await tenants.by_id(env.pool, env.t.id)
    assert shop.wa_phone_number_id == "1001"
    assert await vault.get_secret(env.pool, env.t.id, connect.TOKEN_SECRET) == b"SHOP-TOKEN-xyz"
    async with env.pool.connection() as c:
        dump = json.dumps(await (await c.execute("select * from whatsapp_connections")).fetchall(), default=str)
    assert "SHOP-TOKEN" not in dump and '"connected"' in dump
    assert "now connected to Smara" in env.app.state.wa.sent[-1]["text"]["body"]       # the owner is told
    # the same link cannot be used twice
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://smara.test") as http:
        again = await http.post("/connect/complete", json={"t": t, "code": "C" * 20, "mode": "coexistence",
                                                           "waba_id": "777", "phone_number_id": "1001"})
    assert again.status_code == 400 and "expired or was already used" in again.text


async def test_a_new_number_is_registered_with_a_pin_kept_in_the_vault(env, master, numbered):
    calls: list = []
    r, _, _ = await _connect(env, calls, mode="cloud")
    assert r.status_code == 200
    reg = [c for c in calls if c[1].endswith("/1001/register")]
    assert reg and json.loads(reg[0][3])["pin"] == (await vault.get_secret(env.pool, env.t.id, connect.PIN_SECRET)).decode()


async def test_meta_refusing_leaves_the_shop_unchanged_and_is_recorded(env, master, numbered):
    calls: list = []
    r, _, _ = await _connect(env, calls, refuse="subscribed_apps")
    assert r.status_code == 400 and "Meta refused" in r.text
    shop = await tenants.by_id(env.pool, env.t.id)
    assert shop.wa_phone_number_id is None
    assert await vault.get_secret(env.pool, env.t.id, connect.TOKEN_SECRET) is None
    assert (await one(env.pool, "select status from whatsapp_connections"))["status"] == "failed"


async def test_a_number_already_on_another_shop_is_refused(env, master, numbered):
    other = await tenants.create(env.pool, name="Other", owner_phone="919000000066", wa_phone_number_id="1001")
    r, _, _ = await _connect(env, [])
    assert r.status_code == 409 and other


# ---------------------------------------------------------------- sending uses the shop's own token

async def test_messages_from_a_shops_number_use_that_shops_token(env, master):
    await vault.put_secret(env.pool, env.t.id, connect.TOKEN_SECRET, "SHOP-TOKEN-xyz")
    async with env.pool.connection() as c:
        await c.execute("update tenants set wa_phone_number_id = '1001' where id = %s", (env.t.id,))
    seen = []

    def handler(req):
        seen.append((req.url.path, req.headers["authorization"]))
        return httpx.Response(200, json={"messages": [{"id": "wamid.1"}]})
    wa = WhatsAppClient("SMARA-NUMBER", "SMARA-TOKEN", client=httpx.AsyncClient(
        transport=httpx.MockTransport(handler), headers={"Authorization": "Bearer SMARA-TOKEN"}),
        token_for=lambda n: connect.token_for_number(env.pool, n))
    await wa.send({"to": "91"}, number_id="1001")
    await wa.send({"to": "91"})
    assert seen[0] == ("/v20.0/1001/messages", "Bearer SHOP-TOKEN-xyz")
    assert seen[1] == ("/v20.0/SMARA-NUMBER/messages", "Bearer SMARA-TOKEN")
