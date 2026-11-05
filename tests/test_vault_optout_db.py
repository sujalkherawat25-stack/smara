"""Encrypted per-shop vault, and customers' STOP/START being honoured everywhere."""
from datetime import datetime, timedelta, timezone

import pytest
from test_agent_db import DEBTOR, OWNER, ScriptLLM, call, env, one, owner, say  # noqa: F401

from src import tenants, vault
from src.ingest.debtor_flow import opt_intent
from src.ingest.dispatch import dispatch
from src.ingest.payload import InboundMessage


@pytest.fixture
def master(monkeypatch):
    key = vault.new_master_key()
    monkeypatch.setenv("VAULT_MASTER_KEY", key)
    monkeypatch.setenv("VAULT_MASTER_VERSION", "1")
    monkeypatch.delenv("VAULT_MASTER_KEY_FILE", raising=False)
    monkeypatch.delenv("VAULT_MASTER_KEY_OLD", raising=False)
    return key


# ---------------------------------------------------------------- vault

async def test_secrets_round_trip_and_the_database_never_holds_plaintext(env, master):
    await vault.put_secret(env.pool, env.t.id, "whatsapp_token", "EAAG-very-secret-123")
    assert await vault.get_secret(env.pool, env.t.id, "whatsapp_token") == b"EAAG-very-secret-123"
    async with env.pool.connection() as c:
        raw = await (await c.execute("select ciphertext::text a, (select wrapped_key::text from tenant_keys) b from tenant_secrets")).fetchone()
    assert "very-secret" not in raw["a"] and "very-secret" not in raw["b"]
    await vault.put_secret(env.pool, env.t.id, "whatsapp_token", "rotated-456")            # overwrite
    assert await vault.get_secret(env.pool, env.t.id, "whatsapp_token") == b"rotated-456"
    assert await vault.list_secrets(env.pool, env.t.id) == ["whatsapp_token"]


async def test_a_ciphertext_cannot_be_moved_to_another_name_or_shop(env, master):
    other = await tenants.create(env.pool, name="Other", owner_phone="919000000033")
    await vault.put_secret(env.pool, env.t.id, "a", "alpha")
    await vault.put_secret(env.pool, other.id, "b", "beta")
    async with env.pool.connection() as c:     # an attacker with database access copies one row's ciphertext over another
        await c.execute("""update tenant_secrets set ciphertext = (select ciphertext from tenant_secrets where name = 'a'),
                                                     nonce = (select nonce from tenant_secrets where name = 'a') where name = 'b'""")
    with pytest.raises(Exception):
        await vault.get_secret(env.pool, other.id, "b")


async def test_no_master_key_means_no_access_and_the_wrong_key_cannot_decrypt(env, master, monkeypatch):
    await vault.put_secret(env.pool, env.t.id, "k", "v")
    monkeypatch.setenv("VAULT_MASTER_KEY", vault.new_master_key())                       # a different key
    with pytest.raises(Exception):
        await vault.get_secret(env.pool, env.t.id, "k")
    monkeypatch.delenv("VAULT_MASTER_KEY")
    with pytest.raises(vault.VaultError):
        await vault.put_secret(env.pool, env.t.id, "k2", "v")


async def test_master_key_rotation_rewraps_keys_without_touching_secrets(env, master, monkeypatch):
    await vault.put_secret(env.pool, env.t.id, "k", "keep me")
    monkeypatch.setenv("VAULT_MASTER_KEY_OLD", master)
    monkeypatch.setenv("VAULT_MASTER_VERSION_OLD", "1")
    monkeypatch.setenv("VAULT_MASTER_KEY", vault.new_master_key())
    monkeypatch.setenv("VAULT_MASTER_VERSION", "2")
    assert await vault.rewrap_all(env.pool) == 1
    monkeypatch.delenv("VAULT_MASTER_KEY_OLD")                                           # old key retired
    assert await vault.get_secret(env.pool, env.t.id, "k") == b"keep me"


async def test_erasing_a_shop_shreds_its_key_and_banking_secrets_are_refused(env, master):
    await vault.put_secret(env.pool, env.t.id, "k", "v")
    await vault.shred_tenant(env.pool, env.t.id)
    assert await vault.get_secret(env.pool, env.t.id, "k") is None
    for bad in ("upi_pin", "card_number", "netbanking_login"):
        with pytest.raises(vault.VaultError):
            await vault.put_secret(env.pool, env.t.id, bad, "x")


# ---------------------------------------------------------------- STOP / START

def test_stop_is_recognised_only_when_the_customer_clearly_means_it():
    for t in ("STOP", "stop.", "Band karo", "message mat bhejo", "बंद करो", "Unsubscribe"):
        assert opt_intent(t) == "stop", t
    assert opt_intent("START") == "start" and opt_intent("shuru karo") == "start"
    for t in ("payment kal kar dunga", "10% discount de do", "maine pay kar diya hai, please confirm and stop reminding me later"):
        assert opt_intent(t) is None, t


async def test_stop_is_honoured_everywhere_until_start(env, monkeypatch):
    other = await tenants.create(env.pool, name="Beta Shop", owner_phone="919000000044")
    async with env.pool.connection() as c:                  # the same customer also buys from a second Smara shop
        await c.execute("insert into parties (tenant_id, canonical_name, whatsapp_phone) values (%s, 'Ravi', %s)", (other.id, DEBTOR))
    await dispatch(env.app, InboundMessage(wamid="s1", sender=DEBTOR, kind="text", text="STOP"))
    reply = env.app.state.wa.to(DEBTOR)[-1]["text"]["body"]
    assert "nahi aayenge" in reply and "Shop" in reply and "Beta Shop" in reply             # no "which shop?" question
    assert "STOP" in env.app.state.wa.to(OWNER)[-1]["text"]["body"]                           # each owner is told
    assert "STOP" in env.app.state.wa.to("919000000044")[-1]["text"]["body"]
    async with env.pool.connection() as c:
        n = (await (await c.execute("select count(*) n from parties where opted_out_at is not null")).fetchone())["n"]
    assert n == 2

    # the agent cannot message them
    llm = env.app.state.llm = ScriptLLM(call("send_whatsapp_message", to=env.pid, text="Namaste Ravi ji"),
                                        lambda m: say(m[-1]["content"][:80]))
    await dispatch(env.app, owner("s2", "Ravi ko reminder bhejo"))
    assert "STOP" in llm.last_tool_result()["error"]
    assert (await one(env.pool, "select count(*) n from agent_sessions where status = 'WAITING_APPROVAL'"))["n"] == 0

    # the cron never drafts a reminder for them
    from src.loop.reminders import claim_due
    async with env.pool.connection() as c:
        await c.execute("update obligations set next_followup_at = now() - interval '1 day', state = 'ACTIVE'")
    claimed = await claim_due(env.pool, datetime.now(timezone.utc))
    assert all(str(r["party_id"]) != env.pid for r in claimed)                              # Ravi is skipped

    # START brings them back
    await dispatch(env.app, InboundMessage(wamid="s3", sender=DEBTOR, kind="text", text="start"))
    assert "phir se shuru" in env.app.state.wa.to(DEBTOR)[-1]["text"]["body"]
    claimed = await claim_due(env.pool, datetime.now(timezone.utc) + timedelta(seconds=1))
    assert any(str(r["party_id"]) == env.pid for r in claimed)
