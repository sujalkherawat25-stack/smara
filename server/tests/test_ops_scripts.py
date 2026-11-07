"""Watchdog logic (no network): what counts as a problem, and when it alerts."""
import json
import sys
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import watchdog  # noqa: E402

Disk = namedtuple("Disk", "total used free")
OK_HEALTH = {"ok": True, "db": True, "continuum": {"ok": True}, "outbox": {"dead": 0}, "sandbox": True,
             "ops": {"failed_sessions_1h": 0}}


def fake(public=403, health=None, code=200):
    def fetch(url, timeout=10):
        if "/webhook" in url:
            return public, "Forbidden" if public == 403 else "<html>redirect</html>"
        return code, json.dumps(health if health is not None else OK_HEALTH)
    return fetch


def test_everything_fine_means_no_problems():
    assert watchdog.run_checks(fake(), lambda p: Disk(100, 40, 60)) == {}


def test_a_lost_proxy_route_is_caught_even_though_the_app_is_healthy():
    bad = watchdog.run_checks(fake(public=302), lambda p: Disk(100, 40, 60))
    assert list(bad) == ["public_webhook"] and "302" in bad["public_webhook"]     # the 11:42 UTC incident


def test_unhealthy_signals_are_reported_by_name():
    h = {**OK_HEALTH, "continuum": {"ok": False}, "outbox": {"dead": 2}, "ops": {"failed_sessions_1h": 9}, "sandbox": False}
    bad = watchdog.run_checks(fake(health=h), lambda p: Disk(100, 90, 10))
    assert set(bad) == {"continuum", "outbox_dead", "failed_sessions", "sandbox", "disk"}
    assert set(watchdog.run_checks(fake(code=500), lambda p: Disk(100, 10, 90))) == {"app_health"}


def test_alerts_once_repeat_every_six_hours_and_announce_recovery():
    bad = {"public_webhook": "route lost"}
    msgs, st = watchdog.decide({}, bad, 1000.0)
    assert len(msgs) == 1 and "ALERT" in msgs[0]
    msgs, st = watchdog.decide(st, bad, 1000.0 + 600)
    assert msgs == []                                                  # same incident: quiet
    msgs, st = watchdog.decide(st, bad, 1000.0 + watchdog.REALERT_S + 1)
    assert len(msgs) == 1 and "ALERT" in msgs[0] and "min" in msgs[0]    # still down: reminder
    msgs, st = watchdog.decide(st, {}, 1000.0 + watchdog.REALERT_S + 60)
    assert len(msgs) == 1 and "recovered" in msgs[0] and st == {"since": {}, "last_alert": {}}
    msgs, _ = watchdog.decide(st, {"disk": "disk is 90% full"}, 99999.0)
    assert len(msgs) == 1                                              # a different problem alerts immediately


# ---------------------------------------------------------------- per-shop usage metering (needs the test database)

from test_agent_db import OWNER, ScriptLLM, call, env, one, owner, say  # noqa: E402,F401
from src.ingest.dispatch import dispatch  # noqa: E402


async def test_each_model_call_is_metered_per_shop(env):
    usage = lambda total, model="gemma4": {"model": model, "prompt": total - 10, "completion": 10, "total": total}
    env.app.state.llm = ScriptLLM({**call("query_ledger_sql", sql="select 1"), "_usage": usage(120)},
                                  {**say("Rs 100 baaki."), "_usage": usage(80, "glm5.3-flash")})
    await dispatch(env.app, owner("m1", "kitna baaki hai?"))
    async with env.pool.connection() as c:
        rows = await (await c.execute("select model, total_tokens, tenant_id from llm_usage order by id")).fetchall()
    assert [(r["model"], r["total_tokens"]) for r in rows] == [("gemma4", 120), ("glm5.3-flash", 80)]
    assert str(rows[0]["tenant_id"]) == env.t.id
    import usage_report
    async with env.pool.connection() as c:
        out = await (await c.execute(usage_report.QUERY, {"days": 30, "main": "gemma4"})).fetchall()
    assert out[0]["total_tokens"] == 200 and out[0]["fallback_calls"] == 1 and out[0]["model_calls"] == 2


# ---------------------------------------------------------------- export and erase one shop's data

async def test_export_has_no_secrets_and_erase_removes_only_that_shop(env):
    from src import tenants
    from src.ingest.importer import import_rows
    from src.ingest.sheet_mapper import SheetRow
    from datetime import date
    import psycopg
    from psycopg.rows import dict_row
    import tenant_data
    other = await tenants.create(env.pool, name="Other Shop", owner_phone="919000000055")
    await import_rows(env.pool, other.id, [SheetRow("Keep Me", "919811100099", "K-1", 100_000, date(2026, 12, 1))])
    env.app.state.llm = ScriptLLM(say("ok"))
    await dispatch(env.app, owner("x1", "hello"))
    async with env.pool.connection() as c:
        await c.execute("insert into bridge_devices (tenant_id, name, token_hash) values (%s, 'pc', 'SECRETHASH')", (env.t.id,))
    url = __import__("os").environ["TEST_DATABASE_URL"]
    with psycopg.connect(url, row_factory=dict_row) as conn:
        dump = tenant_data.export_tenant(conn, env.t.id)
        assert dump["tenant"]["name"] == "Shop" and len(dump["tables"]["parties"]) >= 1
        assert "SECRETHASH" not in json.dumps(dump) and dump["tables"]["agent_sessions"]
        dry = tenant_data.erase_tenant(conn, env.t.id, execute=False)
        assert dry["parties"] >= 1 and dry["tenants"] == 1
        assert conn.execute("select count(*) n from parties where tenant_id = %s", (env.t.id,)).fetchone()["n"] >= 1   # dry run kept it
        tenant_data.erase_tenant(conn, env.t.id, execute=True)
        conn.commit()
    async with env.pool.connection() as c:
        gone = await (await c.execute(
            """select (select count(*) from tenants where id = %(t)s) a, (select count(*) from parties where tenant_id = %(t)s) b,
                      (select count(*) from agent_sessions where tenant_id = %(t)s) c, (select count(*) from obligations where tenant_id = %(t)s) d,
                      (select count(*) from parties where tenant_id = %(o)s) kept""", {"t": env.t.id, "o": other.id})).fetchone()
    assert (gone["a"], gone["b"], gone["c"], gone["d"]) == (0, 0, 0, 0) and gone["kept"] == 1
