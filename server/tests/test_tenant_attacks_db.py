"""Shop A tries to reach shop B. Two shops, real Postgres, the real tools and policy. Every attack must come back empty or refused.

Shop "Shop" (env.t) has Ravi / INV-1. Shop "Other" (env.other) has Secret / SECRET-9 (Rs 9,99,000). Nothing of "Other" may ever
show up in anything "Shop" can read, write or send.
"""
import json
from uuid import uuid4

import pytest
from test_agent_db import OWNER, FakeContinuum, env, one  # noqa: F401

from src.agent import actions, bookkeeping, extras, tools
from src.agent.policy import SessionCtx, pre_tool_use
from src.agent.tools import TOOL_NAMES
from src.ingest import owner_files

pytestmark = pytest.mark.asyncio


def trusted(env, tenant=None):
    return SessionCtx(str(uuid4()), (tenant or env.t).id, "TRUSTED")


async def other_ids(env):
    p = await one(env.pool, "select id from parties where tenant_id = %s", env.other.id)
    o = await one(env.pool, "select id from obligations where tenant_id = %s", env.other.id)
    return str(p["id"]), str(o["id"])


def leaked(res) -> bool:
    s = json.dumps(res, default=str).lower()
    return any(x in s for x in ("secret-9", "secret", "9990000", "99900000", "919111111111"))


# ------------------------------------------------------------------ the SQL tool

SQL_ATTACKS = [
    "select * from obligations",
    "select * from parties",
    "select invoice_ref, tenant_id from obligations where invoice_ref = 'SECRET-9'",
    "select * from obligations where tenant_id <> (current_setting('smara.tenant_id')::uuid)",
    "select * from obligations union all select * from obligations",
    "with x as (select * from obligations) select * from x",
    "select (select count(*) from obligations) as n, canonical_name from parties",
]


@pytest.mark.parametrize("sql", SQL_ATTACKS)
async def test_sql_tool_only_sees_own_shop(env, sql):
    res = await tools.query_ledger_sql(env.app, trusted(env), {"sql": sql})
    assert not leaked(res), res


async def test_sql_tool_cannot_switch_shop_with_set_config(env):
    """The row filter reads a per-request setting. A query must not be able to change it."""
    ctx = trusted(env)
    for sql in (
        f"select set_config('smara.tenant_id', '{env.other.id}', true), invoice_ref from obligations",
        f"with s as (select set_config('smara.tenant_id', '{env.other.id}', true)) select o.invoice_ref from s, obligations o",
        f"select invoice_ref from obligations, (select set_config('smara.tenant_id', '{env.other.id}', true)) s",
        "select set_config('role', 'smara', true)",
        "select set_config('session_authorization', 'smara', true)",
    ):
        res = await tools.query_ledger_sql(env.app, ctx, {"sql": sql})
        assert not leaked(res), (sql, res)
    # and the setting is back to the right shop for the next query
    again = await tools.query_ledger_sql(env.app, ctx, {"sql": "select invoice_ref from obligations"})
    assert [r["invoice_ref"] for r in again["rows"]] == ["INV-1"]


@pytest.mark.parametrize("sql", [
    "select * from tenants", "select * from chat_messages", "select * from shop_facts", "select * from owner_skills",
    "select * from tenant_secrets", "select * from tenant_keys", "select * from agent_sessions", "select * from llm_usage",
    "select * from invite_codes", "select * from waitlist", "select * from whatsapp_connections", "select * from outbound_messages",
    "select * from owner_inbound_log", "select * from customer_routing", "select * from bridge_devices",
])
async def test_sql_tool_cannot_read_other_tables(env, sql):
    res = await tools.query_ledger_sql(env.app, trusted(env), {"sql": sql})
    assert "error" in res and "rows" not in res, res


@pytest.mark.parametrize("sql", [
    "select pg_read_file('/etc/passwd')", "select pg_ls_dir('.')", "select lo_import('/etc/passwd')",
    "select * from pg_authid", "select * from pg_shadow", "select current_setting('data_directory')",
    "select pg_sleep(30)", "select dblink('host=localhost', 'select 1')", "copy obligations to program 'id'",
    "select * from pg_stat_activity where query ilike '%secret%'", "select pg_terminate_backend(1)",
    "insert into obligations select * from obligations", "update obligations set amount_paise = 1",
    "delete from obligations", "drop table obligations", "select 1; drop table obligations",
    "select * from obligations for update", "explain analyze select * from obligations",
])
async def test_sql_tool_refuses_dangerous_statements(env, sql):
    res = await tools.query_ledger_sql(env.app, trusted(env), {"sql": sql})
    rows = res.get("rows") or []
    assert not leaked(res)
    assert "error" in res or sql.startswith(("select * from pg_stat_activity", "explain")) or not rows, res
    n = await one(env.pool, "select count(*) n from obligations")
    assert n["n"] == 2                                          # nothing was written or dropped


# ------------------------------------------------------------------ ids that belong to the other shop

async def test_other_shops_party_and_obligation_ids_are_refused(env):
    pid, oid = await other_ids(env)
    ctx = trusted(env)
    assert await actions.validate_gated(env.app, ctx, "send_whatsapp_message", {"to": pid, "text": "hello"})
    assert await actions.validate_gated(env.app, ctx, "propose_ledger_adjustment", {"obligation_id": oid, "kind": "UTR_PROOF"})
    res = await tools.resolve_continuum_memory(env.app, ctx, {"party_id": pid, "query": "anything"})
    assert "error" in res
    res = await tools.write_continuum_evidence(env.app, ctx, {"party_id": pid, "text": "Secret owes nothing"})
    assert "error" in res
    n = await one(env.pool, "select count(*) n from continuum_outbox")
    assert n["n"] == 0                                          # nothing queued into the other shop's memory


async def test_customer_session_is_locked_to_its_own_party(env):
    pid, oid = await other_ids(env)
    mine = str((await one(env.pool, "select id from parties where tenant_id = %s", env.t.id))["id"])
    debtor = SessionCtx(str(uuid4()), env.t.id, "UNTRUSTED", party_id=mine)
    v = lambda n, a: pre_tool_use(debtor, n, a, TOOL_NAMES).verdict
    assert v("query_ledger_sql", {"sql": "select 1"}) == "deny"                    # a customer's message gets no ledger reads
    assert v("send_whatsapp_message", {"to": "owner", "text": "x"}) == "deny"
    assert v("run_python", {"code": "print(1)"}) == "deny"
    assert v("browser_step", {"action": "navigate", "url": "https://a.com"}) == "deny"
    assert v("record_payment", {"customer": "Ravi", "rupees": 1}) == "deny"
    assert v("save_skill", {"name": "x", "description": "x", "instructions": "x"}) == "deny"
    assert v("propose_ledger_adjustment", {"party_id": pid, "obligation_id": oid, "kind": "UTR_PROOF"}) == "deny"
    err = await actions.validate_gated(env.app, debtor, "propose_ledger_adjustment", {"obligation_id": oid, "kind": "UTR_PROOF"})
    assert err                                                                       # unknown obligation: it belongs to the other shop


async def test_names_that_exist_only_in_the_other_shop_are_not_found(env):
    ctx = trusted(env)
    assert "error" in (await bookkeeping.plan_payment(env.pool, env.t.id, {"customer": "Secret", "rupees": 500}))[1:] or \
        (await bookkeeping.plan_payment(env.pool, env.t.id, {"customer": "Secret", "rupees": 500}))[1]
    found = await bookkeeping.find_customer(env.app, ctx, {"name": "Secret"})
    assert not leaked(found), found
    phone_try = await bookkeeping.set_customer_phone(env.app, ctx, {"customer": "Secret", "phone": "9876500000"})
    assert not leaked(phone_try)
    same = await one(env.pool, "select whatsapp_phone from parties where tenant_id = %s", env.other.id)
    assert same["whatsapp_phone"] == "919111111111"                                  # untouched


# ------------------------------------------------------------------ files

async def test_files_are_per_shop(env, tmp_path):
    base = env.app.state.settings.owner_files_dir
    ref_other = owner_files.save(base, env.other.id, "secret_ledger.xlsx", b"TOP SECRET OF OTHER SHOP")
    token = ref_other.split(":")[1]
    ctx = trusted(env)
    with pytest.raises(Exception):
        owner_files.read(base, env.t.id, token)                                       # the token only opens in its own shop
    listing = await extras.list_my_files(env.app, ctx, {})
    assert not leaked(listing) and "secret_ledger" not in json.dumps(listing)
    with pytest.raises(Exception):
        await actions._fetch(env.app, ref_other, ctx)
    for bad in (f"local:../{env.other.id}/{token}:x", f"local:{token}/../../x:x", "local:..:x", "local::x", "local:%2e%2e:x"):
        with pytest.raises(Exception):
            await actions._fetch(env.app, bad, ctx)


async def test_another_shops_output_or_unattached_files_cannot_be_opened_or_sent(env):
    other_out = "sandbox:" + uuid4().hex + ":report.xlsx"
    ctx = trusted(env)
    v = lambda n, a: pre_tool_use(ctx, n, a, TOOL_NAMES).verdict
    assert v("send_file_to_owner", {"file_ref": other_out}) == "deny"
    assert v("read_attachment", {"file_ref": other_out}) == "deny"
    assert v("inspect_and_import_file", {"file_ref": other_out, "mode": "inspect"}) == "deny"
    assert v("run_python", {"code": "print(1)", "file_refs": [other_out]}) == "deny"
    assert v("gst_reconcile", {"books_file_ref": other_out, "gstr2b_file_ref": other_out}) == "deny"
    assert v("read_attachment", {"file_ref": "telegram:SOMEONEELSESFILEID:x.xlsx"}) == "deny"
    assert v("read_attachment", {"file_ref": "whatsapp:123:x.xlsx"}) == "deny"
    # a customer's session never reaches the owner's kept files
    debtor = SessionCtx(str(uuid4()), env.t.id, "UNTRUSTED", party_id="p")
    assert pre_tool_use(debtor, "read_attachment", {"file_ref": "local:abcdef:x.xlsx"}, TOOL_NAMES).verdict == "deny"


# ------------------------------------------------------------------ scheduled work and memory

async def test_followups_and_chat_history_are_per_shop(env):
    ctx_a, ctx_b = trusted(env), trusted(env, env.other)
    await extras.schedule_followup(env.app, ctx_b, {"instruction": "SECRET-9 chase", "in_minutes": 120})
    assert "SECRET-9" not in json.dumps(await extras.list_followups(env.app, ctx_a, {}))
    hist = await tools.search_chat_history(env.app, ctx_a, {"query": "SECRET-9 Secret"})
    assert not leaked(hist), hist


async def test_database_itself_refuses_set_config_to_the_agent_role(env):
    """Even if a query got past the word filter, the role the agent runs as cannot call set_config, so the shop filter cannot move."""
    import psycopg
    async with env.pool.connection() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('smara.tenant_id', %s, true)", (str(env.t.id),))
            await conn.execute("SET LOCAL ROLE smara_agent_ro")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                async with conn.transaction():                  # a savepoint, rolled back when the call is refused
                    await conn.execute("SELECT set_config('smara.tenant_id', %s, true)", (str(env.other.id),))
    async with env.pool.connection() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('smara.tenant_id', %s, true)", (str(env.t.id),))
            await conn.execute("SET LOCAL ROLE smara_agent_ro")
            for fn in ("pg_terminate_backend(1)", "pg_cancel_backend(1)"):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    async with conn.transaction():
                        await conn.execute(f"SELECT {fn}")


# ------------------------------------------------------------------ invite codes and public routes

async def test_wrong_invite_guesses_are_throttled_per_sender_and_new_codes_are_long(env):
    from src.ingest import onboarding
    onboarding._misses.clear()
    code = onboarding.new_code()
    assert len(code.split("-")[1]) == 6
    async with env.pool.connection() as c:
        await c.execute("INSERT INTO invite_codes (code, label, max_uses) VALUES (%s, 'x', 1)", (code,))
    for i in range(onboarding.GUESS_LIMIT):
        assert await onboarding._valid_invite(env.pool, f"SMA-NOPE{i:02d}", "tg1") is None
    assert await onboarding._valid_invite(env.pool, code, "tg1") is None            # locked out, even with the right code
    assert (await onboarding._valid_invite(env.pool, code, "tg2"))["code"] == code  # someone else is not affected
    assert (await onboarding._valid_invite(env.pool, code))["code"] == code         # internal checks are never counted


def test_api_docs_are_not_published_and_internal_routes_check_the_secret_before_the_body():
    from fastapi.testclient import TestClient

    from src.main import app
    assert app.docs_url is None and app.openapi_url is None and app.redoc_url is None
    from src.ingest import wa_link_connect
    for r in wa_link_connect.router.routes:
        if r.path.startswith("/internal/wa-link/"):
            assert any(d.call is wa_link_connect._sidecar_only for d in r.dependant.dependencies), r.path
