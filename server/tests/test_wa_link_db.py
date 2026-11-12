"""The shop's own WhatsApp number (server/wa_link) against Postgres, with a fake sidecar.

Covers pairing and its page, encrypted credentials, the send guardrails (owner approval, STOP, quiet hours, daily cap,
pacing, logout pause, outage), inbound messages on the SIM, reconnect after a sidecar restart, and owner commands.
"""
import dataclasses
import json
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from fake_wa_sidecar import TOKEN, FakeSidecar
from src import clock, tenants, vault
from src.agent.actions import deliver_message
from src.agent.policy import SessionCtx
from src.config import Settings
from src.db import migrate, open_pool
from src.ingest import wa_link_connect
from src.ingest.dispatch import dispatch
from src.ingest.importer import import_rows
from src.ingest.payload import InboundMessage
from src.ingest.sheet_mapper import SheetRow
from src.loop import wa_link
from src.loop.reminders import decide_reminder
from src.loop.scheduler import tick

URL = os.environ.get("TEST_DATABASE_URL")
OWNER, DEBTOR = "919000000001", "919876543210"
LINK_PHONE = "919812345678"
SIDECAR = "http://wa-link:8090"


class FakeWA:
    """The Cloud API sender, for owner messages and for proof that no debtor message took that path."""

    def __init__(self):
        self.sent = []

    async def send(self, payload, **kw):
        self.sent.append(payload)
        return f"wamid.cloud.{len(self.sent)}"

    async def upload_media(self, *a, **kw):
        return "media.1"

    def to(self, phone):
        return [m for m in self.sent if m.get("to") == phone]


class FakeContinuum:
    async def resolve(self, **kw):
        return {"resolution": {"state_card": "No relevant state."}}


@pytest.fixture
async def env(monkeypatch):
    if not URL:
        pytest.skip("TEST_DATABASE_URL not set")
    monkeypatch.setenv("VAULT_MASTER_KEY", vault.new_master_key())
    monkeypatch.setenv("VAULT_MASTER_VERSION", "1")
    monkeypatch.delenv("VAULT_MASTER_KEY_FILE", raising=False)
    pool = await open_pool(URL)
    async with pool.connection() as c:
        await c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    await migrate(pool)
    t = await tenants.create(pool, name="Shop", owner_phone=OWNER, upi_vpa="shop@upi")
    await import_rows(pool, t.id, [SheetRow("Ravi", DEBTOR, "INV-1", 1_000_000, date(2026, 11, 1))])
    async with pool.connection() as c:
        o = await (await c.execute("SELECT o.id, o.party_id FROM obligations o WHERE invoice_ref = 'INV-1'")).fetchone()
    sidecar = FakeSidecar()
    app = FastAPI()
    app.state.pool = pool
    app.state.settings = Settings(wa_link_url=SIDECAR, wa_link_token=TOKEN, wa_link_daily_cap=40, wa_link_send_gap_s=0.0,
                                  public_base_url="https://smara.test/smara", reminder_send_mode="smara")
    app.state.wa = FakeWA()
    app.state.tg = None
    app.state.llm = SimpleNamespace(enabled=False)
    app.state.continuum = FakeContinuum()
    app.state.bg = set()
    app.state.wa_link = wa_link.SidecarClient(SIDECAR, TOKEN, transport=sidecar.transport())
    app.include_router(wa_link_connect.router)
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://smara.test")
    env = SimpleNamespace(app=app, pool=pool, t=t, tid=str(t.id), oid=str(o["id"]), pid=str(o["party_id"]),
                          sidecar=sidecar, http=http, cloud=app.state.wa)
    yield env
    await http.aclose()
    await app.state.wa_link.aclose()
    await pool.close()


def owner(wamid: str, text: str) -> InboundMessage:
    return InboundMessage(wamid=wamid, sender=OWNER, kind="text", text=text)


async def one(pool, sql, *a):
    async with pool.connection() as c:
        return await (await c.execute(sql, a)).fetchone()


async def callback(env, path: str, body: dict):
    return await env.http.post(f"/internal/wa-link/{path}", json=body, headers={"Authorization": f"Bearer {TOKEN}"})


async def pair_and_link(env, auth: str | None = None) -> None:
    """The owner opened the page, the QR was scanned, and the sidecar reported the device connected."""
    env.sidecar.connect(env.tid, LINK_PHONE)
    if auth is not None:
        res = await callback(env, "state", {"tenant_id": env.tid, "auth": auth})
        assert res.status_code == 200
    res = await callback(env, "status", {"tenant_id": env.tid, "status": "connected", "phone": LINK_PHONE})
    assert res.status_code == 200


def auth_json(secret: str = "KEY-MATERIAL-XYZ", registration: int = 4242) -> str:
    return json.dumps({"creds": {"registrationId": registration, "noiseKey": {"private": secret}}, "keys": {}})


async def waiting_for_owner(env) -> None:
    """The reminder card is up and waiting for the owner's Send tap."""
    async with env.pool.connection() as c:
        await c.execute("UPDATE obligations SET state = 'WAITING_OWNER_APPROVAL' WHERE id = %s", (env.oid,))


def sent_to_debtor(env) -> list[dict]:
    return env.sidecar.sent_to(DEBTOR)


# ---------------------------------------------------------------- pairing and its page

async def test_connect_whatsapp_gives_the_owner_the_sim_pairing_link(env):
    env.app.state.llm.enabled = False
    await dispatch(env.app, owner("c1", "connect whatsapp"))
    text = env.cloud.sent[-1]["text"]["body"]
    assert "https://smara.test/smara/connect/wa-link?t=" in text
    assert "Meta" not in text


async def test_the_pairing_page_shows_a_qr_then_connected_and_consumes_the_link(env):
    await dispatch(env.app, owner("c1", "connect whatsapp"))
    link = re.search(r"\?t=([\w-]+)", env.cloud.sent[-1]["text"]["body"]).group(1)
    page = await env.http.get("/connect/wa-link", params={"t": link})
    assert page.status_code == 200 and "Link your SIM to Smara" in page.text

    started = await env.http.post("/connect/wa-link/start", json={"t": link})
    assert started.status_code == 200
    assert started.json()["status"] == "pairing"
    assert started.json()["qr_svg"].startswith("<svg")

    env.sidecar.connect(env.tid, LINK_PHONE)                       # the owner scanned it
    await callback(env, "status", {"tenant_id": env.tid, "status": "connected", "phone": LINK_PHONE})
    state = (await env.http.post("/connect/wa-link/state", json={"t": link})).json()
    assert state["status"] == "connected" and state["phone"] == LINK_PHONE

    row = await one(env.pool, "SELECT used_at FROM whatsapp_connect_links WHERE tenant_id = %s", env.tid)
    assert row["used_at"] is not None, "a connected link is single use"
    assert "is now linked" in env.cloud.sent[-1]["text"]["body"], "the owner is told it worked"


async def test_a_bad_or_expired_link_gets_nothing(env):
    await dispatch(env.app, owner("c1", "connect whatsapp"))
    page = await env.http.get("/connect/wa-link", params={"t": "not-a-real-link-token"})
    assert page.status_code == 404
    res = await env.http.post("/connect/wa-link/start", json={"t": "not-a-real-link-token"})
    assert res.status_code == 404
    assert env.sidecar.pairs == []


async def test_the_stored_credentials_are_encrypted_in_the_database_not_plain(env):
    await pair_and_link(env, auth=auth_json("KEY-MATERIAL-XYZ"))
    row = await one(env.pool, "SELECT ciphertext FROM tenant_secrets WHERE tenant_id = %s AND name = 'wa_link_auth'", env.tid)
    assert row is not None
    assert b"KEY-MATERIAL-XYZ" not in bytes(row["ciphertext"])
    assert b"registrationId" not in bytes(row["ciphertext"])
    restored = await vault.get_secret(env.pool, env.tid, "wa_link_auth")
    assert json.loads(restored)["creds"]["registrationId"] == 4242


async def test_a_state_push_without_credentials_is_refused_and_stores_nothing(env):
    res = await callback(env, "state", {"tenant_id": env.tid, "auth": json.dumps({"keys": {}})})
    assert res.status_code == 400
    assert await vault.get_secret(env.pool, env.tid, "wa_link_auth") is None


async def test_callbacks_need_the_sidecar_token(env):
    res = await env.http.post("/internal/wa-link/status", json={"tenant_id": env.tid, "status": "connected"})
    assert res.status_code == 401


# ---------------------------------------------------------------- sending: the owner-approved path

async def test_an_approved_reminder_goes_out_from_the_shops_sim_once(env):
    await pair_and_link(env, auth=auth_json())
    await waiting_for_owner(env)
    reply = await decide_reminder(env.app, env.tid, env.oid, "send", datetime.now(timezone.utc))
    assert reply == "Sent to Ravi."
    msgs = sent_to_debtor(env)
    assert len(msgs) == 1
    assert "INV-1" in msgs[0]["text"] and "shop@upi" in msgs[0]["text"]
    assert env.cloud.to(DEBTOR) == [], "the debtor was not messaged through the Cloud API"
    row = await one(env.pool, "SELECT state FROM obligations WHERE id = %s", env.oid)
    assert row["state"] == "DISPATCHED"
    out = await one(env.pool, "SELECT kind, msg_id FROM outbound_messages WHERE to_phone = %s", DEBTOR)
    assert out["kind"] == "debtor_reminder" and out["msg_id"] == "SIM-1"


async def test_the_agent_path_sends_the_approved_text_through_the_link(env):
    await pair_and_link(env, auth=auth_json())
    ctx = SessionCtx(id="s1", tenant_id=env.tid, trust="TRUSTED")
    res = await deliver_message(env.app, ctx, env.t, {"to": env.pid, "text": "Namaste Ravi ji, INV-1 pending.",
                                                      "obligation_id": env.oid})
    assert res["sent"] is True and "shop's own WhatsApp link" in res["via"]
    assert sent_to_debtor(env)[0]["text"].endswith("Namaste Ravi ji, INV-1 pending."), "the approved text, as approved"


async def test_in_own_mode_smara_sends_nothing_to_the_customer_even_with_a_sim_linked(env):
    """REMINDER_SEND_MODE=own (the default): the owner sends from their own WhatsApp; the SIM is not used."""
    await pair_and_link(env, auth=auth_json())
    env.app.state.settings = dataclasses.replace(env.app.state.settings, reminder_send_mode="own")
    ctx = SessionCtx(id="s2", tenant_id=env.tid, trust="TRUSTED")
    res = await deliver_message(env.app, ctx, env.t, {"to": env.pid, "text": "hello", "obligation_id": env.oid})
    assert res["sent"] is False and res["via"] == "owner_own_whatsapp"
    assert sent_to_debtor(env) == []


async def test_quiet_hours_stop_a_proactive_message_on_the_sim(env, monkeypatch):
    await pair_and_link(env, auth=auth_json())
    monkeypatch.setattr(clock, "is_quiet", lambda *a, **k: True)
    with pytest.raises(wa_link.SendBlocked, match="quiet hours"):
        await wa_link.send_text(env.app, env.t, DEBTOR, "hello", purpose=wa_link.PROACTIVE, kind="debtor_reminder")
    assert sent_to_debtor(env) == []


async def test_a_customer_who_said_stop_gets_nothing_proactive_but_the_acknowledgement_goes(env):
    await pair_and_link(env, auth=auth_json())
    async with env.pool.connection() as c:
        await c.execute("UPDATE parties SET opted_out_at = now() WHERE id = %s", (env.pid,))
    with pytest.raises(wa_link.SendBlocked, match="STOP"):
        await wa_link.send_text(env.app, env.t, DEBTOR, "reminder", purpose=wa_link.PROACTIVE, kind="debtor_reminder")
    assert sent_to_debtor(env) == []
    await wa_link.send_text(env.app, env.t, DEBTOR, "Theek hai, updates band.", purpose=wa_link.REPLY, kind="customer_text")
    assert len(sent_to_debtor(env)) == 1


async def test_the_daily_cap_holds_per_shop(env):
    env.app.state.settings = dataclasses.replace(env.app.state.settings, wa_link_daily_cap=2)
    await pair_and_link(env, auth=auth_json())
    for i in range(2):
        await wa_link.send_text(env.app, env.t, DEBTOR, f"msg {i}", purpose=wa_link.PROACTIVE, kind="debtor_reminder")
    with pytest.raises(wa_link.SendBlocked, match="today's limit of 2") as blocked:
        await wa_link.send_text(env.app, env.t, DEBTOR, "one too many", purpose=wa_link.REPLY, kind="customer_text")
    assert timedelta(0) < blocked.value.retry_after <= timedelta(hours=24, minutes=1)
    assert len(sent_to_debtor(env)) == 2


async def test_sends_from_one_shop_are_paced_apart(env):
    env.app.state.settings = dataclasses.replace(env.app.state.settings, wa_link_send_gap_s=0.25)
    await pair_and_link(env, auth=auth_json())
    await wa_link.send_text(env.app, env.t, DEBTOR, "first", purpose=wa_link.PROACTIVE, kind="debtor_reminder")
    await wa_link.send_text(env.app, env.t, DEBTOR, "second", purpose=wa_link.PROACTIVE, kind="debtor_reminder")
    first, second = env.sidecar.sent
    assert second["at"] - first["at"] >= 0.2


async def test_a_link_that_is_down_refuses_and_the_invoice_waits_for_another_try(env):
    await pair_and_link(env, auth=auth_json())
    await waiting_for_owner(env)
    env.sidecar.down = True
    reply = await decide_reminder(env.app, env.tid, env.oid, "send", datetime.now(timezone.utc))
    assert reply.startswith("Not sent to Ravi:")
    row = await one(env.pool, "SELECT state, next_followup_at, attempt_count FROM obligations WHERE id = %s", env.oid)
    assert row["state"] == "ACTIVE" and row["attempt_count"] == 0
    assert row["next_followup_at"] > datetime.now(timezone.utc)


async def test_an_owner_approval_is_never_sent_by_the_cloud_api_for_a_linked_shop(env):
    await pair_and_link(env, auth=auth_json())
    await waiting_for_owner(env)
    env.sidecar.down = True
    await decide_reminder(env.app, env.tid, env.oid, "send", datetime.now(timezone.utc))
    assert env.cloud.to(DEBTOR) == [], "when the link is down, the send pauses; it does not fall back to Cloud"


# ---------------------------------------------------------------- logout, reconnect and health

async def test_logout_pauses_every_send_forgets_the_credentials_and_tells_the_owner(env):
    await pair_and_link(env, auth=auth_json())
    await waiting_for_owner(env)
    await callback(env, "status", {"tenant_id": env.tid, "status": "logged_out", "phone": ""})
    row = await one(env.pool, "SELECT status FROM wa_links WHERE tenant_id = %s", env.tid)
    assert row["status"] == "logged_out"
    assert await vault.get_secret(env.pool, env.tid, "wa_link_auth") is None
    assert "logged your number out" in env.cloud.sent[-1]["text"]["body"]
    assert await wa_link.paused(env.pool, env.tid)

    reply = await decide_reminder(env.app, env.tid, env.oid, "send", datetime.now(timezone.utc))
    assert reply.startswith("Not sent to Ravi:") and "logged out" in reply
    assert sent_to_debtor(env) == []
    assert (await one(env.pool, "SELECT state FROM obligations WHERE id = %s", env.oid))["state"] == "ACTIVE"


async def test_while_paused_the_scheduler_asks_the_owner_nothing(env):
    await pair_and_link(env, auth=auth_json())
    await callback(env, "status", {"tenant_id": env.tid, "status": "logged_out", "phone": ""})
    cards_before = len(env.cloud.sent)
    await tick(env.app, datetime.now(timezone.utc) + timedelta(days=30))
    owner_cards = [m for m in env.cloud.sent[cards_before:] if m.get("to") == OWNER and m.get("type") == "interactive"]
    assert owner_cards == [], "no reminder cards are raised while the number is paused"
    assert (await one(env.pool, "SELECT state FROM obligations WHERE id = %s", env.oid))["state"] == "ACTIVE"


async def test_after_a_sidecar_restart_the_linked_number_comes_back_from_the_stored_credentials(env):
    await pair_and_link(env, auth=auth_json("KEY-AGAIN"))
    stored = await vault.get_secret(env.pool, env.tid, "wa_link_auth")
    env.sidecar.drop(env.tid)                                      # the sidecar lost its memory
    assert await wa_link.reconcile(env.app) == 1
    assert env.sidecar.pairs[-1] == (env.tid, stored.decode())
    assert (await one(env.pool, "SELECT status FROM wa_links WHERE tenant_id = %s", env.tid))["status"] == "connected"


async def test_the_owner_sees_the_connection_health_in_plain_words(env):
    await dispatch(env.app, owner("s1", "whatsapp status"))
    assert "not set up" in env.cloud.sent[-1]["text"]["body"]
    await pair_and_link(env, auth=auth_json())
    await dispatch(env.app, owner("s2", "status"))
    assert "WhatsApp link: connected" in env.cloud.sent[-1]["text"]["body"]
    await callback(env, "status", {"tenant_id": env.tid, "status": "logged_out", "phone": ""})
    await dispatch(env.app, owner("s3", "whatsapp status"))
    assert "logged out by WhatsApp" in env.cloud.sent[-1]["text"]["body"]


async def test_the_owner_can_disconnect_the_sim_and_that_pauses_sends(env):
    await pair_and_link(env, auth=auth_json())
    await dispatch(env.app, owner("d1", "disconnect whatsapp"))
    assert "disconnected" in env.cloud.sent[-1]["text"]["body"]
    assert (await one(env.pool, "SELECT status FROM wa_links WHERE tenant_id = %s", env.tid))["status"] == "disconnected"
    with pytest.raises(wa_link.SendBlocked, match="disconnected"):
        await wa_link.send_text(env.app, env.t, DEBTOR, "hi", purpose=wa_link.PROACTIVE, kind="debtor_reminder")


# ---------------------------------------------------------------- customers writing to the SIM

async def test_a_customer_stop_on_the_sim_is_honoured_and_acknowledged_on_the_sim(env):
    await pair_and_link(env, auth=auth_json())
    res = await callback(env, "inbound", {"tenant_id": env.tid, "wamid": "wl:A1", "sender": DEBTOR, "kind": "text",
                                          "text": "STOP", "ts": 1})
    assert res.status_code == 200
    assert (await one(env.pool, "SELECT opted_out_at FROM parties WHERE id = %s", env.pid))["opted_out_at"] is not None
    ack = sent_to_debtor(env)
    assert ack and "START" in ack[-1]["text"], "the acknowledgement goes back on the SIM"
    assert env.cloud.to(DEBTOR) == []
    assert any("(STOP)" in m["text"]["body"] for m in env.cloud.sent if m.get("to") == OWNER)


async def test_a_payment_message_on_the_sim_is_kept_as_evidence_for_the_owner(env):
    await pair_and_link(env, auth=auth_json())
    res = await callback(env, "inbound", {"tenant_id": env.tid, "wamid": "wl:A9", "sender": DEBTOR, "kind": "text",
                                          "text": "Ravi paid 5000 yesterday for INV-1", "ts": 2})
    assert res.status_code == 200
    assert await one(env.pool, "SELECT 1 FROM processed_messages WHERE wamid = 'wl:A9'") is not None
    ev = await one(env.pool, "SELECT count(*) AS n FROM continuum_outbox WHERE text LIKE %s", "%paid 5000%")
    assert ev["n"] >= 1, "the customer's words are stored as untrusted evidence"


async def test_the_same_sim_message_is_not_processed_twice(env):
    await pair_and_link(env, auth=auth_json())
    body = {"tenant_id": env.tid, "wamid": "wl:DUP", "sender": DEBTOR, "kind": "text", "text": "STOP", "ts": 3}
    await callback(env, "inbound", body)
    await callback(env, "inbound", body)
    acks = [m for m in sent_to_debtor(env) if "START" in m["text"]]
    assert len(acks) == 1


async def test_with_the_link_service_switched_off_an_old_link_no_longer_pauses_reminders(env):
    """Production on 12 Nov: the link was switched off server-wide, but the shop's old 'disconnected' record kept every
    reminder paused, silently. With no link service, reminders are asked and sent the normal way again."""
    await pair_and_link(env, auth=auth_json())
    await callback(env, "status", {"tenant_id": env.tid, "status": "disconnected", "phone": ""})
    assert await wa_link.paused(env.pool, env.tid, app=env.app)            # link service on: a down link pauses sends

    link, env.app.state.wa_link = env.app.state.wa_link, None              # the server is restarted without WA_LINK_URL
    try:
        assert not await wa_link.paused(env.pool, env.tid, app=env.app)
        assert not await wa_link.uses_link(env.pool, env.tid, app=env.app)
        before = len(env.cloud.sent)
        await tick(env.app, datetime.now(timezone.utc) + timedelta(days=30))
        cards = [m for m in env.cloud.sent[before:] if m.get("to") == OWNER and m.get("type") == "interactive"]
        assert len(cards) == 1, "the owner is asked about the due invoice again"
        assert (await one(env.pool, "SELECT state FROM obligations WHERE id = %s", env.oid))["state"] == "WAITING_OWNER_APPROVAL"
        reply = await decide_reminder(env.app, env.tid, env.oid, "send", datetime.now(timezone.utc))
        assert "Sent to Ravi" in reply and sent_to_debtor(env) == []        # sent the normal way, not on the dead link
    finally:
        env.app.state.wa_link = link
