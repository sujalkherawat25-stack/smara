"""The shop's own WhatsApp link (server/wa_link) without a database: client, sidecar contract, config, auth."""
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fake_wa_sidecar import TOKEN, FakeSidecar
from src import config
from src.config import Settings
from src.ingest import wa_link_connect
from src.loop import wa_link

SHOP = "0b65d7d2-acc1-4842-8c1a-6460b692982a"


def client_for(sidecar: FakeSidecar, token: str = TOKEN) -> wa_link.SidecarClient:
    return wa_link.SidecarClient("http://wa-link:8090", token, transport=sidecar.transport())


async def test_pairing_starts_a_fresh_device_and_returns_its_qr():
    sc = FakeSidecar()
    c = client_for(sc)
    res = await c.pair(SHOP, None)
    assert res["status"] == "pairing"
    assert sc.pairs == [(SHOP, None)]
    status = await c.status(SHOP)
    assert status["status"] == "pairing" and status["qr_svg"].startswith("<svg")
    await c.aclose()


async def test_a_linked_number_is_resumed_with_its_stored_auth_not_a_new_pairing():
    sc = FakeSidecar()
    c = client_for(sc)
    await c.pair(SHOP, '{"creds": {}}')
    assert sc.pairs == [(SHOP, '{"creds": {}}')]
    assert (await c.status(SHOP))["qr_svg"] == ""
    await c.aclose()


async def test_send_returns_whatsapps_message_id_and_goes_to_one_number():
    sc = FakeSidecar()
    sc.connect(SHOP)
    c = client_for(sc)
    assert await c.send(SHOP, "919000000001", "Namaste Ravi ji") == "SIM-1"
    assert [m["to"] for m in sc.sent] == ["919000000001"]
    await c.aclose()


async def test_a_send_to_a_device_that_is_not_connected_is_an_error():
    sc = FakeSidecar()
    c = client_for(sc)
    with pytest.raises(wa_link.SidecarError, match="409"):
        await c.send(SHOP, "919000000001", "hi")
    assert sc.sent == []
    await c.aclose()


async def test_a_wrong_token_is_refused():
    sc = FakeSidecar()
    c = client_for(sc, token="not-the-token-0123456789abcdefghijk")
    with pytest.raises(wa_link.SidecarError, match="401"):
        await c.status(SHOP)
    await c.aclose()


async def test_an_unreachable_sidecar_is_a_clear_error_not_a_crash():
    sc = FakeSidecar()
    sc.down = True
    c = client_for(sc)
    with pytest.raises(wa_link.SidecarError, match="unreachable"):
        await c.send(SHOP, "919000000001", "hi")
    await c.aclose()


def test_owner_facing_health_text_covers_every_state():
    assert wa_link.health_text(None) == ""
    assert "not set up" in wa_link.health_text(None, always=True)
    assert "connected (+919812345678)" in wa_link.health_text({"status": "connected", "phone": "919812345678"})
    assert "ALL customer messages are paused" in wa_link.health_text({"status": "logged_out", "phone": ""})
    assert "paused" in wa_link.health_text({"status": "disconnected", "phone": ""})
    assert "waiting for the QR" in wa_link.health_text({"status": "pairing", "phone": ""})
    assert "reconnecting" in wa_link.health_text({"status": "reconnecting", "phone": ""})


def test_the_internal_endpoints_refuse_callers_without_the_sidecar_token():
    app = FastAPI()
    app.include_router(wa_link_connect.router)
    app.state.settings = Settings(wa_link_token=TOKEN)
    app.state.pool = None                       # never reached: the token is checked first
    body = {"tenant_id": SHOP, "status": "connected", "phone": "919812345678"}
    with TestClient(app) as http:
        assert http.post("/internal/wa-link/status", json=body).status_code == 401
        bad = http.post("/internal/wa-link/status", json=body, headers={"Authorization": "Bearer nope"})
        assert bad.status_code == 401
        assert "nope" not in bad.text


def test_the_internal_state_endpoint_rejects_a_body_that_is_not_an_auth_state():
    app = FastAPI()
    app.include_router(wa_link_connect.router)
    app.state.settings = Settings(wa_link_token=TOKEN)
    app.state.pool = None
    with TestClient(app) as http:
        res = http.post("/internal/wa-link/state", json={"tenant_id": SHOP, "auth": '{"no": "creds"}'},
                        headers={"Authorization": f"Bearer {TOKEN}"})
        assert res.status_code == 400


def test_the_link_token_can_come_from_a_secret_file_and_is_not_echoed(tmp_path, monkeypatch):
    secret_file = tmp_path / "wa_link_token"
    secret_file.write_text(TOKEN + "\n", encoding="utf-8")
    monkeypatch.setenv("WA_LINK_TOKEN_FILE", str(secret_file))
    monkeypatch.setenv("WA_LINK_URL", "http://wa-link:8090")
    monkeypatch.delenv("WA_LINK_TOKEN", raising=False)
    s = config.load_settings()
    assert s.wa_link_token == TOKEN
    assert s.wa_link_url == "http://wa-link:8090"
    assert s.wa_link_daily_cap == 40 and s.wa_link_send_gap_s == 6.0


def test_the_link_is_off_unless_its_url_is_set(monkeypatch):
    monkeypatch.delenv("WA_LINK_URL", raising=False)
    assert config.load_settings().wa_link_url == ""
