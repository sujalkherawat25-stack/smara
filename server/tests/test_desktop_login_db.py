"""Smara Desktop sign-in: the PC shows a code, the owner confirms it from their own chat, the PC collects both tokens once."""
import httpx
from fastapi import FastAPI
from test_agent_db import env, one, owner  # noqa: F401

from src.bridge import approvals, device, login
from src.bridge.device_api import router as device_router
from src.ingest.dispatch import dispatch

_ids = iter(range(1, 1000))


def api(env):
    app = FastAPI()
    app.state = env.app.state
    app.include_router(device_router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bridge.test")


async def say_to_smara(env, text):
    await dispatch(env.app, owner(f"dl-{next(_ids)}", text))
    return env.app.state.wa.sent[-1]["text"]["body"]


def test_parse_code():
    assert login.parse_code("login abcd-efgh") == "ABCD-EFGH"
    assert login.parse_code("Login ABCDEFGH") == "ABCD-EFGH"
    assert login.parse_code("login_abcd-efgh") == "ABCD-EFGH"
    assert login.parse_code("sign in 2345-6789") == "2345-6789"
    assert login.parse_code("login") is None and login.parse_code("what is my login abcd-efgh") is None


async def test_sign_in_needs_the_owners_chat_and_a_yes_and_pays_out_tokens_once(env):
    async with api(env) as http:
        start = (await http.post("/bridge/login/start", json={"name": "Shop PC"})).json()
        code, secret = start["code"], start["device_secret"]
        assert start["message"] == f"login {code}"
        assert (await http.post("/bridge/login/poll", json={"device_secret": secret})).json() == {"status": "pending"}
        asked = await say_to_smara(env, f"login {code.lower()}")
        assert "Shop PC" in asked and code in asked and "YES" in asked
        assert (await http.post("/bridge/login/poll", json={"device_secret": secret})).json() == {"status": "asked"}
        done = await say_to_smara(env, "yes")
        assert "Connected" in done
        got = (await http.post("/bridge/login/poll", json={"device_secret": secret})).json()
        assert got["status"] == "approved" and got["owner_token"].startswith("sma_") and got["device_token"] and got["shop"]
        # one payout only
        assert (await http.post("/bridge/login/poll", json={"device_secret": secret})).json() == {"status": "expired"}
        # both tokens work, and each does only its own job
        assert (await http.get("/bridge/approvals", headers={"Authorization": f"Bearer {got['owner_token']}"})).status_code == 200
        assert (await http.get("/bridge/approvals", headers={"Authorization": f"Bearer {got['device_token']}"})).status_code == 401
        assert (await http.get("/bridge/overview", headers={"Authorization": f"Bearer {got['device_token']}"})).status_code == 200
        row = await one(env.pool, "select secret_hash, code_hash from desktop_logins")
        assert secret not in row["secret_hash"] and len(row["secret_hash"]) == 64


async def test_no_is_final_a_wrong_code_does_nothing_and_a_stray_yes_is_not_swallowed(env):
    async with api(env) as http:
        start = (await http.post("/bridge/login/start", json={"name": "Other PC"})).json()
        assert "wrong" in (await say_to_smara(env, "login ZZZZ-9999")).lower()
        await say_to_smara(env, f"login {start['code']}")
        assert "Cancelled" in await say_to_smara(env, "no")
        assert (await http.post("/bridge/login/poll", json={"device_secret": start["device_secret"]})).json() == {"status": "denied"}
        assert (await http.post("/bridge/login/poll", json={"device_secret": "sdl_nope-nope-nope"})).json() == {"status": "expired"}
        # nothing is waiting any more: "yes" goes to the normal assistant path, not the sign-in
        assert not await login.has_asked(env.pool, env.t.id)


async def test_signing_in_again_from_the_same_pc_replaces_its_tokens(env):
    async with api(env) as http:
        tokens = []
        for _ in range(2):
            start = (await http.post("/bridge/login/start", json={"name": "Same PC"})).json()
            await say_to_smara(env, f"login {start['code']}")
            await say_to_smara(env, "yes")
            tokens.append((await http.post("/bridge/login/poll", json={"device_secret": start["device_secret"]})).json())
        assert (await http.get("/bridge/approvals", headers={"Authorization": f"Bearer {tokens[0]['owner_token']}"})).status_code == 401
        assert (await http.get("/bridge/approvals", headers={"Authorization": f"Bearer {tokens[1]['owner_token']}"})).status_code == 200
        # and 'unpair browser' in chat switches both off
        await say_to_smara(env, "unpair browser")
        assert (await http.get("/bridge/approvals", headers={"Authorization": f"Bearer {tokens[1]['owner_token']}"})).status_code == 401
        assert await device.device_for_token(env.pool, tokens[1]["device_token"]) is None


async def test_sign_out_revokes_this_pcs_tokens(env):
    async with api(env) as http:
        start = (await http.post("/bridge/login/start", json={"name": "Out PC"})).json()
        await say_to_smara(env, f"login {start['code']}")
        await say_to_smara(env, "yes")
        got = (await http.post("/bridge/login/poll", json={"device_secret": start["device_secret"]})).json()
        h = {"Authorization": f"Bearer {got['owner_token']}"}
        assert (await http.post("/bridge/logout", headers=h)).status_code == 200
        assert (await http.get("/bridge/approvals", headers=h)).status_code == 401
        assert await device.device_for_token(env.pool, got["device_token"]) is None
        assert (await http.post("/bridge/logout")).status_code == 401


async def test_start_is_rate_limited(env):
    async with api(env) as http:
        login._starts.clear()
        codes = [(await http.post("/bridge/login/start", json={})).status_code for _ in range(22)]
        assert codes[:20] == [200] * 20 and codes[20] == 429
        login._starts.clear()
