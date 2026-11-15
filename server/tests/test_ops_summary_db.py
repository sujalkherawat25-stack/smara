"""The Smara section of the private Syntarus dashboard, and Smara's curiosity with someone new."""
import httpx
from test_agent_db import OWNER, env, one  # noqa: F401

from src import chatlog, ops_summary
from src.config import Settings


async def test_the_summary_counts_accounts_usage_and_runs_and_shows_no_message_text(env):
    await chatlog.record(env.pool, env.t.id, "telegram", "owner", "my secret plan: buy the building")
    await chatlog.record(env.pool, env.t.id, "telegram", "smara", "Here is a reply that must not be shown")
    base = (await one(env.pool, "select count(*) as n from tenants"))["n"]
    async with env.pool.connection() as c:
        await c.execute("INSERT INTO llm_usage (tenant_id, model, prompt_tokens, completion_tokens, total_tokens) "
                        "VALUES (%s, 'glm5.3-flash', 100, 20, 120)", (env.t.id,))
        await c.execute("INSERT INTO agent_sessions (tenant_id, trust, trigger, status, channel, steps) "
                        "VALUES (%s, 'TRUSTED', 'owner_message', 'DONE', 'telegram', 3)", (env.t.id,))
        await c.execute("INSERT INTO invite_codes (code, label) VALUES ('SMA-TEST', 'x')")
        await c.execute("INSERT INTO waitlist (phone, first_text) VALUES ('tg:77', 'hello')")
        await c.execute("INSERT INTO tenants (name, owner_phone, telegram_chat_id) VALUES ('Asha', 'tg555', 555)")
    out = await ops_summary.build(env.pool, 30, {"ok": True})
    assert out["totals"]["accounts"] == base + 1 and out["totals"]["new_accounts"] >= 1 and out["totals"]["on_telegram"] >= 1
    assert out["totals"]["owner_messages"] == 1 and out["totals"]["smara_messages"] == 1
    assert out["llm"]["total_tokens"] == 120 and out["llm"]["by_model"][0]["model"] == "glm5.3-flash"
    assert out["runs"] == {"DONE": 1}
    assert out["waitlist"]["waiting"] == 1 and out["invites"]["open"] == 1
    assert len(out["daily"]) == 30 and out["daily"][-1]["owner_messages"] == 1
    mine = next(a for a in out["accounts"] if a["name"] == env.t.name)
    assert mine["messages"] == 1 and mine["tokens"] == 120 and mine["runs"] == 1 and mine["contact"].startswith("…")
    asha = next(a for a in out["accounts"] if a["name"] == "Asha")
    assert asha["contact"] == "Telegram only" and "owner_phone" not in asha and len(asha["account"]) == 8
    assert out["recent_runs"][0]["status"] == "DONE" and out["recent_runs"][0]["tokens"] == 0
    blob = str(out)
    assert "secret plan" not in blob and "must not be shown" not in blob and OWNER not in blob


async def test_the_summary_route_needs_the_shared_token_and_is_off_without_one():
    from src import main
    for token, header, status in (("", "Bearer x", 404), ("s3cret-token", "", 401), ("s3cret-token", "Bearer wrong", 401)):
        main.app.state.settings = Settings(ops_dashboard_token=token)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="https://smara.test") as http:
            r = await http.get("/internal/ops/summary", headers={"authorization": header} if header else {})
        assert r.status_code == status


async def test_a_new_person_gets_the_getting_to_know_you_instructions_and_an_old_hand_does_not(env):
    from src.agent.prompt import build_system
    text = await build_system(env.app, env.t, "TRUSTED")
    assert "This person is new to you" in text and "Ask for nothing they have not offered" in text
    for i in range(16):
        await chatlog.record(env.pool, env.t.id, "telegram", "owner", f"message {i}")
    assert "This person is new to you" not in await build_system(env.app, env.t, "TRUSTED")
    assert "This person is new to you" not in await build_system(env.app, env.t, "UNTRUSTED")
