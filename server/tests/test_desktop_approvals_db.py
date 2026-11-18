"""Smara Desktop approvals: an owner token from the owner's own chat answers PC approvals; first answer wins."""
import httpx
from fastapi import FastAPI
from test_agent_db import ScriptLLM, call, env, one, owner, say  # noqa: F401

from src.bridge import approvals, device
from src.bridge.device_api import router as device_router
from src.ingest.dispatch import dispatch


def api(env):
    """The device API sharing the test app's state, so a decision resumes the real agent run."""
    app = FastAPI()
    app.state = env.app.state
    app.include_router(device_router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bridge.test")


_asks = iter(range(1, 1000))


async def owner_token(env, http):
    await dispatch(env.app, owner(f"da-ask-{next(_asks)}", "desktop approvals"))   # a fresh message id: repeats are ignored
    text = env.app.state.wa.sent[-1]["text"]["body"]
    code = text.split("approvals code: ")[1].split()[0]
    r = await http.post("/bridge/approvals/pair", json={"code": code, "name": "Shop PC"})
    assert r.status_code == 200, r.text
    return r.json()["owner_token"], code


async def waiting_browser_step(env, wamid="ds1"):
    env.app.state.llm = ScriptLLM(
        call("browser_step", action="navigate", url="https://example.com/form"),
        call("browser_step", action="click", selector="#next"),
        say("Clicked next."))
    await dispatch(env.app, owner(wamid, "click next on example.com"))
    return str((await one(env.pool, "select id from agent_sessions where status = 'WAITING_APPROVAL'"))["id"])


async def test_pairing_is_single_use_stored_hashed_and_the_device_token_cannot_approve(env):
    async with api(env) as http:
        token, code = await owner_token(env, http)
        assert token.startswith("sma_")
        assert (await http.post("/bridge/approvals/pair", json={"code": code, "name": "x"})).status_code == 400
        row = await one(env.pool, "select token_hash from owner_desktop_tokens")
        assert row["token_hash"] != token and len(row["token_hash"]) == 64
        dev_code = await device.start_pairing(env.pool, env.t.id)
        dev_token = (await http.post("/bridge/pair", json={"code": dev_code, "name": "PC"})).json()["device_token"]
        assert (await http.get("/bridge/approvals", headers={"Authorization": f"Bearer {dev_token}"})).status_code == 401
        assert (await http.post("/bridge/approvals/x", json={"choice": "approve"},
                                headers={"Authorization": f"Bearer {dev_token}"})).status_code == 401
        assert (await http.get("/bridge/approvals")).status_code == 401


async def test_desktop_sees_the_same_card_and_approving_runs_the_step(env):
    async with api(env) as http:
        token, _ = await owner_token(env, http)
        h = {"Authorization": f"Bearer {token}"}
        sid = await waiting_browser_step(env)
        listed = (await http.get("/bridge/approvals", headers=h)).json()["approvals"]
        assert [a["id"] for a in listed] == [sid] and listed[0]["tool"] == "browser_step" and listed[0]["site_choice"] is True
        assert "click" in listed[0]["card"].lower() and listed[0]["step"]["action"] == "click"
        r = await http.post(f"/bridge/approvals/{sid}", json={"choice": "approve"}, headers=h)
        assert r.status_code == 200, r.text
        assert [s["action"] for s in env.app.state.browsers.steps] == ["navigate", "click"]
        assert (await http.post(f"/bridge/approvals/{sid}", json={"choice": "reject"}, headers=h)).status_code == 409
        logged = await one(env.pool, "select channel, content from chat_messages where kind = 'owner_desktop_decision'")
        assert logged["channel"] == "desktop" and logged["content"].startswith("[Approved in Smara Desktop]")


async def test_chat_first_then_desktop_is_already_handled_and_reject_works(env):
    async with api(env) as http:
        token, _ = await owner_token(env, http)
        h = {"Authorization": f"Bearer {token}"}
        sid = await waiting_browser_step(env)
        await dispatch(env.app, owner("ds2", button=f"ag_no:{sid}"))                        # answered in chat
        assert (await http.post(f"/bridge/approvals/{sid}", json={"choice": "approve"}, headers=h)).status_code == 409
        assert [s["action"] for s in env.app.state.browsers.steps] == ["navigate"]          # the click never ran
        assert (await http.get("/bridge/approvals", headers=h)).json()["approvals"] == []


async def test_site_choice_grants_the_site_like_the_chat_button(env):
    async with api(env) as http:
        token, _ = await owner_token(env, http)
        sid = await waiting_browser_step(env)
        r = await http.post(f"/bridge/approvals/{sid}", json={"choice": "site"}, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        grants = (await one(env.pool, "select grants from agent_sessions where id::text = %s", sid))["grants"]
        assert grants and grants[0]["host"] == "example.com"


async def test_chat_only_approvals_are_not_listed_or_answerable(env):
    async with api(env) as http:
        token, _ = await owner_token(env, http)
        h = {"Authorization": f"Bearer {token}"}
        sid = await waiting_browser_step(env)
        async with env.pool.connection() as c:                                            # pretend it is a customer message
            await c.execute("UPDATE agent_sessions SET pending = jsonb_set(pending, '{name}', '\"send_whatsapp_message\"') "
                            "WHERE id::text = %s", (sid,))
        assert (await http.get("/bridge/approvals", headers=h)).json()["approvals"] == []
        assert (await http.post(f"/bridge/approvals/{sid}", json={"choice": "approve"}, headers=h)).status_code == 403
        assert (await one(env.pool, "select status from agent_sessions where id::text = %s", sid))["status"] == "WAITING_APPROVAL"


async def test_another_shops_token_sees_and_answers_nothing(env):
    async with api(env) as http:
        sid = await waiting_browser_step(env)
        async with env.pool.connection() as c:
            other = (await (await c.execute(
                "INSERT INTO tenants (name, owner_phone) VALUES ('Other shop', '919999999999') RETURNING id")).fetchone())["id"]
        code = await approvals.start_pairing(env.pool, str(other))
        token = (await http.post("/bridge/approvals/pair", json={"code": code, "name": "x"})).json()["owner_token"]
        h = {"Authorization": f"Bearer {token}"}
        assert (await http.get("/bridge/approvals", headers=h)).json()["approvals"] == []
        assert (await http.post(f"/bridge/approvals/{sid}", json={"choice": "approve"}, headers=h)).status_code == 409
        assert (await one(env.pool, "select status from agent_sessions where id::text = %s", sid))["status"] == "WAITING_APPROVAL"


async def test_stop_desktop_approvals_and_unpair_browser_revoke_the_token(env):
    async with api(env) as http:
        token, _ = await owner_token(env, http)
        await dispatch(env.app, owner("da2", "stop desktop approvals"))
        assert "no longer" in env.app.state.wa.sent[-1]["text"]["body"]
        assert (await http.get("/bridge/approvals", headers={"Authorization": f"Bearer {token}"})).status_code == 401
        token2, _ = await owner_token(env, http)
        await dispatch(env.app, owner("da3", "unpair browser"))
        assert (await http.get("/bridge/approvals", headers={"Authorization": f"Bearer {token2}"})).status_code == 401


def test_decisions_are_rate_limited_per_token():
    approvals._recent.clear()
    assert all(approvals.rate_ok("t1") for _ in range(approvals.RATE_MAX))
    assert approvals.rate_ok("t1") is False and approvals.rate_ok("t2") is True
