"""Browser on the owner's device: pairing, the job queue, routing, site grants, and the bridge's own safety checks."""
import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from test_agent_db import OWNER, FakeBrowsers, ScriptLLM, call, env, one, owner, say  # noqa: F401

from src.bridge import device
from src.bridge.device import BrowserRouter, DeviceBrowser
from src.bridge.device_api import router as device_router
from src.ingest.dispatch import dispatch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
import smara_bridge  # noqa: E402


def api(env):
    app = FastAPI()
    app.state.pool = env.pool
    app.include_router(device_router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bridge.test")


async def pair(env, http):
    code = await device.start_pairing(env.pool, env.t.id)
    r = await http.post("/bridge/pair", json={"code": code, "name": "Sujal PC"})
    assert r.status_code == 200
    return r.json()["device_token"], code


async def fake_bridge(http, token, results: dict | None = None, rounds=3):
    """What smara_bridge does: poll, run the command, post the result."""
    h = {"Authorization": f"Bearer {token}"}
    for _ in range(rounds):
        job = (await http.post("/bridge/poll", params={"wait": 3}, headers=h)).json()
        if job:
            cmd = job["command"]
            out = (results or {}).get(cmd["action"], {"url": "https://example.com/page", "title": "Page", "text": "hi", "elements": []})
            await http.post("/bridge/result", json={"job_id": job["job_id"], "result": out}, headers=h)


# ---------------------------------------------------------------- pairing

async def test_pairing_code_is_single_use_and_token_is_stored_hashed(env):
    async with api(env) as http:
        token, code = await pair(env, http)
        again = await http.post("/bridge/pair", json={"code": code, "name": "x"})
        assert again.status_code == 400                                    # one-time
        bad = await http.post("/bridge/pair", json={"code": "ZZZZ-ZZZZ", "name": "x"})
        assert bad.status_code == 400
        row = await one(env.pool, "select token_hash from bridge_devices")
        assert row["token_hash"] != token and len(row["token_hash"]) == 64
        assert (await http.post("/bridge/poll", params={"wait": 0}, headers={"Authorization": "Bearer nope"})).status_code == 401


async def test_revoking_disconnects_the_device(env):
    async with api(env) as http:
        token, _ = await pair(env, http)
        assert await device.online_device(env.pool, env.t.id)
        assert await device.revoke_all(env.pool, env.t.id) == 1
        assert not await device.online_device(env.pool, env.t.id)
        assert (await http.post("/bridge/poll", params={"wait": 0}, headers={"Authorization": f"Bearer {token}"})).status_code == 401


async def test_the_overview_is_the_paired_shops_numbers_only(env):
    async with api(env) as http:
        assert (await http.get("/bridge/overview")).status_code == 401
        assert (await http.get("/bridge/overview", headers={"Authorization": "Bearer nope"})).status_code == 401
        token, _ = await pair(env, http)
        async with env.pool.connection() as c:
            await c.execute("update tenants set telegram_chat_id = 555, owner_last_channel = 'telegram' where id = %s", (env.t.id,))
            await c.execute("insert into tally_syncs (tenant_id, receivables) values (%s, 1)", (env.t.id,))
        r = await http.get("/bridge/overview", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        o = r.json()
        assert o["shop"] == "Shop" and o["device"] == "Sujal PC"
        assert o["dues"] == {"open": 1, "open_paise": 1_000_000, "overdue": 1, "overdue_paise": 1_000_000}   # not the other shop's 99,900,000
        assert o["channels"] == {"telegram_linked": False, "whatsapp_window_open": False, "last_used": "telegram"}   # no bot on this server
        assert o["waiting_for_owner"] == 0 and o["payables"] == {"open": 0, "open_paise": 0} and o["tally_last_sync_at"]
        assert "Ravi" not in r.text and "SECRET" not in r.text                 # totals only, no customer names
        await device.revoke_all(env.pool, env.t.id)
        assert (await http.get("/bridge/overview", headers={"Authorization": f"Bearer {token}"})).status_code == 401


async def test_a_device_only_sees_its_own_shops_jobs(env):
    from src import tenants
    other = await tenants.create(env.pool, name="Other", owner_phone="919000000077")
    async with api(env) as http:
        token, _ = await pair(env, http)
        async with env.pool.connection() as c:
            await c.execute("insert into bridge_jobs (tenant_id, session_id, command) values (%s, 's', %s)",
                            (other.id, json.dumps({"action": "navigate", "url": "https://x.com"})))
        assert (await http.post("/bridge/poll", params={"wait": 0}, headers={"Authorization": f"Bearer {token}"})).json() == {}


# ---------------------------------------------------------------- the job queue and the router

async def test_device_browser_round_trip_and_offline_message(env):
    db = DeviceBrowser(env.pool, step_timeout_s=10)
    assert "not connected" in (await db.step("s1", {"action": "navigate", "url": "https://example.com"}, tenant_id=env.t.id))["error"]
    async with api(env) as http:
        token, _ = await pair(env, http)
        out, _ = await asyncio.gather(db.step("s1", {"action": "navigate", "url": "https://example.com"}, tenant_id=env.t.id),
                                      fake_bridge(http, token, rounds=1))
        assert out["title"] == "Page" and db.alive("s1") and db.page("s1")["url"] == "https://example.com/page"
        assert "navigate first" in (await db.step("s2", {"action": "click", "selector": "#x"}, tenant_id=env.t.id))["error"]


async def test_a_job_nobody_answers_times_out_instead_of_hanging(env):
    async with api(env) as http:
        await pair(env, http)
        db = DeviceBrowser(env.pool, step_timeout_s=1)
        out = await db.step("s1", {"action": "navigate", "url": "https://example.com"}, tenant_id=env.t.id)
    assert "did not answer" in out["error"]
    assert (await one(env.pool, "select status from bridge_jobs"))["status"] == "EXPIRED"


async def test_router_uses_the_owners_device_when_online_else_the_server_browser(env):
    server = FakeBrowsers()
    router = BrowserRouter(env.pool, server, DeviceBrowser(env.pool, step_timeout_s=10))
    await router.step("a", {"action": "navigate", "url": "https://example.com"}, tenant_id=env.t.id)
    assert router.where("a") == "server" and [s["action"] for s in server.steps] == ["navigate"]     # no bridge yet
    async with api(env) as http:
        token, _ = await pair(env, http)
        out, _ = await asyncio.gather(router.step("b", {"action": "navigate", "url": "https://example.com"}, tenant_id=env.t.id),
                                      fake_bridge(http, token, rounds=1))
    assert router.where("b") == "device" and out["title"] == "Page" and len(server.steps) == 1
    assert router.where("a") == "server"                                                            # a running session never switches
    await router.close("b")
    assert not router.alive("b")


async def test_a_new_page_moves_to_the_owners_pc_once_its_helper_is_online(env):
    server = FakeBrowsers()
    router = BrowserRouter(env.pool, server, DeviceBrowser(env.pool, step_timeout_s=10))
    await router.step("a", {"action": "navigate", "url": "https://example.com"}, tenant_id=env.t.id)
    assert router.where("a") == "server"                                                             # helper was offline
    await router.step("a", {"action": "read"}, tenant_id=env.t.id)
    assert router.where("a") == "server"                                                             # same page: no switch
    async with api(env) as http:
        token, _ = await pair(env, http)
        out, _ = await asyncio.gather(router.step("a", {"action": "navigate", "url": "https://example.com/menu"}, tenant_id=env.t.id),
                                      fake_bridge(http, token, rounds=1))
    assert router.where("a") == "device" and out["title"] == "Page"                                  # next page: the PC
    assert "a" not in server.open                                                                    # the server page was closed


async def test_a_paired_pc_is_the_only_browser_never_silently_the_server(env):
    server = FakeBrowsers()
    router = BrowserRouter(env.pool, server, DeviceBrowser(env.pool, step_timeout_s=10))
    async with api(env) as http:
        await pair(env, http)
    async with env.pool.connection() as conn:                       # the PC was last seen 30 s ago: busy on a slow page, still there
        await conn.execute("UPDATE bridge_devices SET last_seen_at = now() - interval '30 seconds'")
    assert (await device.reachable_device(env.pool, env.t.id)) is not None
    async with env.pool.connection() as conn:                       # gone for an hour: say so, do not use the server browser
        await conn.execute("UPDATE bridge_devices SET last_seen_at = now() - interval '1 hour'")
    out = await router.step("z", {"action": "navigate", "url": "https://example.com"}, tenant_id=env.t.id)
    assert "not connected right now" in out["error"] and server.steps == [] and router.where("z") == ""


# ---------------------------------------------------------------- owner chat commands

async def test_owner_pairs_and_unpairs_from_chat(env):
    env.app.state.llm.enabled = False
    await dispatch(env.app, owner("pb1", "pair browser"))
    text = env.app.state.wa.sent[-1]["text"]["body"]
    code = text.split("--code ")[1].split()[0]
    async with api(env) as http:
        assert (await http.post("/bridge/pair", json={"code": code, "name": "PC"})).status_code == 200
    await dispatch(env.app, owner("pb2", "browser status"))
    assert "online" in env.app.state.wa.sent[-1]["text"]["body"]
    await dispatch(env.app, owner("pb3", "unpair browser"))
    assert "disconnected" in env.app.state.wa.sent[-1]["text"]["body"]
    assert not await device.online_device(env.pool, env.t.id)


# ---------------------------------------------------------------- 'approve this site': autonomy with limits

async def test_owner_allows_one_site_and_the_agent_stops_asking_except_for_risky_steps(env):
    br = env.app.state.browsers
    env.app.state.llm = ScriptLLM(
        call("browser_step", action="navigate", url="https://example.com/form"),
        call("browser_step", action="click", selector="#next"),            # first act: needs a tap
        call("browser_step", action="fill", selector="#qty", value="2"),   # same site now allowed
        call("browser_step", action="click", selector="button:has-text('Place order')"),   # risky: asks again
        say("Done for now."))
    await dispatch(env.app, owner("g1", "fill the form on example.com"))
    sid = (await one(env.pool, "select id from agent_sessions where status = 'WAITING_APPROVAL'"))["id"]
    card = env.app.state.wa.sent[-1]["interactive"]["action"]["buttons"]
    assert [b["reply"]["id"].split(":")[0] for b in card] == ["ag_ok", "ag_site", "ag_no"]
    await dispatch(env.app, owner("g2", button=f"ag_site:{sid}"))
    assert [s["action"] for s in br.steps] == ["navigate", "click", "fill"]           # fill ran with no second tap
    assert (await one(env.pool, "select status from agent_sessions"))["status"] == "WAITING_APPROVAL"   # Place order waits
    assert "Place order" in env.app.state.wa.sent[-1]["interactive"]["body"]["text"]
    grants = (await one(env.pool, "select grants from agent_sessions"))["grants"]
    assert grants[0]["host"] == "example.com"


async def test_a_grant_never_covers_secrets_other_sites_or_customer_sessions(env):
    from src.agent.policy import SessionCtx, pre_tool_use
    from src.agent.tools import TOOL_NAMES
    ctx = SessionCtx(id="s", tenant_id="t", trust="TRUSTED", grants=("example.com",), host="example.com")
    v = lambda c, a: pre_tool_use(c, "browser_step", a, TOOL_NAMES).verdict
    assert v(ctx, {"action": "click", "selector": "#next"}) == "allow"
    assert v(ctx, {"action": "click", "selector": "#pay-now"}) == "approve"
    assert v(ctx, {"action": "press", "value": "Enter"}) == "allow"                    # fine on a normal page (search boxes)
    sign_in = SessionCtx(id="s", tenant_id="t", trust="TRUSTED", grants=("example.com",), host="example.com",
                         page="https://example.com/login sign in")
    assert v(sign_in, {"action": "click", "selector": "#next"}) == "approve"           # never on a sign-in / payment page
    assert v(ctx, {"action": "click", "selector": "a:has-text('Delete account')"}) == "approve"
    other = SessionCtx(id="s", tenant_id="t", trust="TRUSTED", grants=("example.com",), host="evil.com")
    assert v(other, {"action": "click", "selector": "#next"}) == "approve"
    sysctx = SessionCtx(id="s", tenant_id="t", trust="SYSTEM", grants=("example.com",), host="example.com")
    assert v(sysctx, {"action": "click", "selector": "#next"}) == "approve"            # scheduled jobs never inherit a grant


async def test_grants_expire(env):
    from datetime import datetime, timedelta, timezone
    from src.agent_loop import active_grants
    soon = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    gone = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    assert active_grants({"grants": [{"host": "a.com", "until": soon}, {"host": "b.com", "until": gone}]}) == ("a.com",)


# ---------------------------------------------------------------- the bridge program's own checks (real Chromium)

def test_bridge_blocks_private_networks_and_odd_schemes():
    assert smara_bridge.url_allowed("file:///etc/passwd")
    assert "hand_off_to_owner" in smara_bridge.url_allowed("upi://pay?pa=shop@bank&am=100")
    assert smara_bridge.url_allowed("http://127.0.0.1:8000/admin")
    assert smara_bridge.url_allowed("http://192.168.1.1/")
    assert smara_bridge.url_allowed("http://169.254.169.254/latest")
    assert smara_bridge.url_allowed("http://127.0.0.1/", allow_private=True) is None
    assert smara_bridge.url_allowed("https://93.184.216.34/") is None


BRIDGE_PROBE = r"""
import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import smara_bridge
from playwright.async_api import async_playwright

import re
async def R(mb, page, role, name):
    # the ref of the control with this role and exact name, from a fresh read (as the agent would see it)
    r = await mb.handle(page, {"action": "read"}, allow_private=True)
    m = re.search(r"\[(\d+)\] " + re.escape(role) + ' "' + re.escape(name) + '"', r["page"])
    return int(m.group(1)) if m else None

async def main():
    out = {}
    mb = smara_bridge
    page_file = Path(sys.argv[2]) / "login.html"
    page_file.write_text("<html><body><input id='user' name='user'><input id='pw' type='password' name='pw'>"
                         "<input id='code' name='code' autocomplete='one-time-code'><button id='go'>Next</button></body></html>")
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(page_file.as_uri())
        ok = await mb.handle(page, {"action": "fill", "ref": await R(mb, page, "textbox", "user"), "value": "ravi"}, allow_private=True)
        out["fill_ok"] = not ok.get("error") and ok["url"].startswith("file:")
        out["pw"] = (await mb.handle(page, {"action": "fill", "ref": await R(mb, page, "textbox", "pw"), "value": "x"})).get("error", "")
        out["otp"] = (await mb.handle(page, {"action": "fill", "ref": await R(mb, page, "textbox", "code"), "value": "1"})).get("error", "")
        out["pw_value"] = await page.input_value("#pw")
        out["user_value"] = await page.input_value("#user")
        go = await R(mb, page, "button", "Next")
        await page.evaluate("() => { window.__smaraPaused = true }")
        out["paused"] = (await mb.handle(page, {"action": "click", "ref": go})).get("error", "")
        await page.evaluate("() => { window.__smaraPaused = false }")
        out["resumed"] = not (await mb.handle(page, {"action": "read"})).get("error")
        out["banner"] = await page.evaluate("() => !!document.getElementById('smara-agent-banner')")
        await browser.close()
    print(json.dumps(out))

asyncio.run(main())
"""


def test_bridge_never_types_into_password_fields_and_honours_the_stop_button(tmp_path):
    """Real Chromium in its own process (Playwright needs a different event loop than the database tests)."""
    import subprocess
    done = subprocess.run([sys.executable, "-c", BRIDGE_PROBE, str(Path(smara_bridge.__file__).parent), str(tmp_path)],
                          capture_output=True, text=True, timeout=120)
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert out["fill_ok"] and out["user_value"] == "ravi"
    assert "never type" in out["pw"] and "never type" in out["otp"] and out["pw_value"] == ""
    assert "paused" in out["paused"] and out["resumed"] and out["banner"]


# ---------------------------------------------------------------- web search

def test_search_results_are_unwrapped_deduplicated_and_skip_the_engine_itself():
    from src.agent.web import parse_results
    els = [
        {"tag": "a", "text": "DuckDuckGo", "href": "https://duckduckgo.com/"},
        {"tag": "a", "text": "Best pizza in Banswara - Zomato", "href": "//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.zomato.com%2Fbanswara&rut=x"},
        {"tag": "a", "text": "Top 10 pizza places with reviews, menus and prices in Banswara", "href": "//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.zomato.com%2Fbanswara&rut=x"},
        {"tag": "a", "text": "Pizza Hut Banswara", "href": "https://www.bing.com/ck/a?u=https%3A%2F%2Fwww.pizzahut.co.in%2Fbanswara"},
        {"tag": "button", "text": "Search", "href": ""},
    ]
    out = parse_results(els)
    assert [r["url"] for r in out] == ["https://www.zomato.com/banswara", "https://www.pizzahut.co.in/banswara"]
    assert out[0]["title"].startswith("Best pizza") and out[0]["snippet"].startswith("Top 10")


async def test_agent_searches_then_reads_a_result(env):
    from src.agent.policy import SessionCtx, pre_tool_use
    from src.agent.tools import TOOL_NAMES
    assert pre_tool_use(SessionCtx(id="s", tenant_id="t", trust="TRUSTED"), "web_search", {"query": "pizza"}, TOOL_NAMES).verdict == "allow"
    assert pre_tool_use(SessionCtx(id="s", tenant_id="t", trust="UNTRUSTED", party_id="p"), "web_search", {"query": "x"}, TOOL_NAMES).verdict == "deny"
    br = env.app.state.browsers

    async def step(sid, args, **kw):
        br.steps.append(args)
        br.open.add(sid)
        if "duckduckgo" in args.get("url", ""):
            return {"url": args["url"], "title": "q", "links": [
                {"tag": "a", "text": "Best pizza in Banswara", "href": "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fpizza"}]}
        return {"url": args.get("url", ""), "title": "Pizza", "text": "Mario's, Rs 299", "elements": []}
    br.step = step
    env.app.state.llm = ScriptLLM(call("web_search", query="best pizza banswara"),
                                  lambda m: call("browser_step", action="navigate", url=json.loads(m[-1]["content"])["results"][0]["url"]),
                                  say("Mario's, Rs 299 (example.org)."))
    await dispatch(env.app, owner("ws1", "best pizza shop in banswara?"))
    assert [s["url"] for s in br.steps if s["action"] == "navigate"][1] == "https://example.org/pizza"
    assert env.app.state.wa.sent[-1]["text"]["body"].startswith("Mario")


async def test_search_api_is_used_on_the_server_and_falls_back_to_the_browser(env):
    from src.agent.policy import SessionCtx
    from src.agent import web
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={"web": {"results": [{"title": "Mario's", "url": "https://example.org/m", "description": "Rs 299"}]}})
    real = web.api_search

    async def fake(key, query, client=None):
        return await real(key, query, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    web.api_search = fake
    try:
        env.app.state.settings = type("S", (), {"brave_search_api_key": "k"})()
        out = await web.web_search(env.app, SessionCtx(id="s", tenant_id=env.t.id, trust="TRUSTED"), {"query": "pizza banswara"})
    finally:
        web.api_search = real
    assert out["engine"] == "brave-api" and out["results"][0]["url"] == "https://example.org/m" and "q=pizza+banswara" in calls[0]


# ---------------------------------------------------------------- autonomy on the owner's own browser

class DeviceFakeBrowsers(FakeBrowsers):
    """A fake browser that reports it is the owner's PC."""
    def where(self, sid):
        return "device" if sid in self.open else ""


async def test_on_the_owners_pc_the_agent_clicks_and_types_by_itself_but_asks_before_spending(env):
    br = env.app.state.browsers = DeviceFakeBrowsers()
    env.app.state.llm = ScriptLLM(
        call("browser_step", action="navigate", url="https://example.com/menu"),
        call("browser_step", action="click", selector="a:has-text('Order Online')"),
        call("browser_step", action="fill", selector="#search", value="veg pizza"),
        call("browser_step", action="press", value="Enter"),
        call("browser_step", action="scroll", value="down"),
        call("browser_step", action="click", selector="button:has-text('Place order')"),     # spends money: asks
        say("Done."))
    await dispatch(env.app, owner("au1", "find veg pizza on example.com and get it ready"))
    assert [s["action"] for s in br.steps] == ["navigate", "click", "fill", "press", "scroll"]       # no taps for those
    sid = (await one(env.pool, "select id from agent_sessions where status = 'WAITING_APPROVAL'"))["id"]
    assert "Place order" in env.app.state.wa.sent[-1]["interactive"]["body"]["text"]
    await dispatch(env.app, owner("au2", button=f"ag_no:{sid}"))
    assert [s["action"] for s in br.steps][-1] == "scroll"                                          # rejected: never ran


async def test_login_captcha_and_payment_are_handed_to_the_owner_who_taps_done(env):
    br = env.app.state.browsers = DeviceFakeBrowsers()
    llm = env.app.state.llm = ScriptLLM(
        call("browser_step", action="navigate", url="https://example.com/account"),
        call("hand_off_to_owner", reason="Please sign in to your account in the open browser window."),
        call("browser_step", action="click", selector="#orders"),
        say("Opened your orders after you signed in."))
    await dispatch(env.app, owner("ho1", "check my orders on example.com"))
    sid = (await one(env.pool, "select id from agent_sessions where status = 'WAITING_APPROVAL'"))["id"]
    card = env.app.state.wa.sent[-1]["interactive"]
    assert "Your turn in the browser" in card["body"]["text"] and "sign in" in card["body"]["text"]
    assert [b["reply"]["title"] for b in card["action"]["buttons"]] == ["Done, continue", "Cancel"]
    await dispatch(env.app, owner("ho2", button=f"ag_ok:{sid}"))
    done = json.loads([m for m in llm.seen[-1] if m["role"] == "tool"][-2]["content"])
    assert done["owner_done"] is True and done["url"].startswith("https://example.com")               # fresh page handed back
    assert [s["action"] for s in br.steps] == ["navigate", "read", "click"]
    assert env.app.state.wa.sent[-1]["text"]["body"].startswith("Opened your orders")


async def test_only_the_owners_own_conversation_can_hand_off(env):
    from src.agent.policy import SessionCtx, pre_tool_use
    from src.agent.tools import TOOL_NAMES
    for trust in ("UNTRUSTED", "SYSTEM"):
        ctx = SessionCtx(id="s", tenant_id="t", trust=trust, party_id="p")
        assert pre_tool_use(ctx, "hand_off_to_owner", {"reason": "x"}, TOOL_NAMES).verdict == "deny"


NEW_ACTIONS_PROBE = r"""
import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import smara_bridge
from playwright.async_api import async_playwright

import re
async def R(mb, page, role, name):
    # the ref of the control with this role and exact name, from a fresh read (as the agent would see it)
    r = await mb.handle(page, {"action": "read"}, allow_private=True)
    m = re.search(r"\[(\d+)\] " + re.escape(role) + ' "' + re.escape(name) + '"', r["page"])
    return int(m.group(1)) if m else None

async def main():
    out = {}
    mb = smara_bridge
    d = Path(sys.argv[2])
    (d / "a.html").write_text("<html><body style='height:4000px'><a href='b.html'>next</a>"
        "<select id='s' name='s'><option value='x'>X</option><option value='y'>Y</option></select><p style='margin-top:3000px'>bottom text</p></body></html>")
    (d / "b.html").write_text("<html><body><h1>second</h1><input type='password'></body></html>")
    (d / "c.html").write_text("<html><body><div class='g-recaptcha'></div><p>please verify you are human</p></body></html>")
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto((d / "a.html").as_uri())
        sel, nxt = await R(mb, page, "combobox", "s"), await R(mb, page, "link", "next")
        await mb.handle(page, {"action": "scroll", "value": "bottom"}, allow_private=True)
        out["scrolled"] = await page.evaluate("() => window.scrollY") > 500
        await mb.handle(page, {"action": "select", "ref": sel, "value": "Y"}, allow_private=True)
        out["selected"] = await page.input_value("#s")
        await mb.handle(page, {"action": "wait", "value": "50"}, allow_private=True)
        r = await mb.handle(page, {"action": "click", "ref": nxt}, allow_private=True)
        out["login_flag"] = r.get("needs_owner")
        r = await mb.handle(page, {"action": "back"}, allow_private=True)
        out["back"] = r["url"].endswith("a.html") and not r.get("needs_owner")
        await page.goto((d / "c.html").as_uri())
        out["captcha_flag"] = (await mb.handle(page, {"action": "read"}, allow_private=True)).get("needs_owner")
        await browser.close()
    print(json.dumps(out))

asyncio.run(main())
"""


def test_bridge_scroll_select_back_wait_and_owner_needed_signals(tmp_path):
    import subprocess
    done = subprocess.run([sys.executable, "-c", NEW_ACTIONS_PROBE, str(Path(smara_bridge.__file__).parent), str(tmp_path)],
                          capture_output=True, text=True, timeout=120)
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert out["scrolled"] and out["selected"] == "y" and out["back"]
    assert out["login_flag"] == ["login"] and out["captcha_flag"] == ["captcha"]


# ---------------------------------------------------------------- no commands for an owner whose PC is already paired

async def test_pair_browser_does_not_ask_again_when_a_pc_is_already_paired(env):
    env.app.state.llm.enabled = False
    async with api(env) as http:
        await pair(env, http)
    await dispatch(env.app, owner("np1", "pair browser"))
    assert "already connected" in env.app.state.wa.sent[-1]["text"]["body"]                     # online: nothing to do
    async with env.pool.connection() as c:
        await c.execute("update bridge_devices set last_seen_at = now() - interval '3 hours'")
    await dispatch(env.app, owner("np2", "Ha pair browser"))                                     # in a short sentence too
    body = env.app.state.wa.sent[-1]["text"]["body"]
    assert "already paired" in body and "--code" not in body and "python" not in body            # offline: no commands
    await dispatch(env.app, owner("np3", "pair new browser"))
    body = env.app.state.wa.sent[-1]["text"]["body"]
    assert "Smara Desktop > Settings > Browser" in body and "--code" in body                     # deliberate new PC: a code


async def test_an_offline_paired_pc_is_explained_to_the_agent_not_re_paired(env):
    from src.agent import web
    from src.agent.policy import SessionCtx
    server = FakeBrowsers()

    async def blocked(sid, args, **kw):
        server.steps.append(args)
        return {"url": args.get("url", ""), "title": "403", "text": "403 Forbidden", "elements": []}
    server.step = blocked
    router = BrowserRouter(env.pool, server, DeviceBrowser(env.pool, step_timeout_s=2))
    async with api(env) as http:
        await pair(env, http)
    async with env.pool.connection() as c:
        await c.execute("update bridge_devices set last_seen_at = now() - interval '2 hours'")
    out = await router.step("s9", {"action": "navigate", "url": "https://www.indiamart.com/"}, tenant_id=env.t.id)
    assert "already paired" in out["error"] and "Do NOT ask the owner to pair again" in out["error"] and server.steps == []
    env.app.state.browsers = router
    res = await web.web_search(env.app, SessionCtx(id="w1", tenant_id=env.t.id, trust="TRUSTED"), {"query": "banarasi saree"})
    assert "already paired" in res["next"]


def test_activity_lines_never_include_typed_values():
    line = smara_bridge.describe_step({"action": "fill", "selector": "#qty", "value": "secret 1234"},
                                      {"url": "https://example.com/x", "title": "Cart"})
    assert "secret" not in line and "example.com" in line and "fill #qty" in line
    assert "error" in smara_bridge.describe_step({"action": "navigate", "url": "https://a.com"}, {"error": "blocked"})


def test_pause_and_resume_commands_use_a_flag_file(tmp_path, monkeypatch):
    monkeypatch.setattr(smara_bridge, "HOME", tmp_path)
    monkeypatch.setattr(smara_bridge, "PAUSE", tmp_path / "paused")
    monkeypatch.setattr(sys, "argv", ["smara-bridge", "pause"])
    assert smara_bridge.main() == 0 and (tmp_path / "paused").exists()
    monkeypatch.setattr(sys, "argv", ["smara-bridge", "resume"])
    assert smara_bridge.main() == 0 and not (tmp_path / "paused").exists()


SEND_PROBE = r"""
import asyncio, json, sys
sys.path.insert(0, sys.argv[1])
import smara_bridge as mb
from playwright.async_api import async_playwright
SENT = "<script>window.sent=[]</script>"
LINKEDIN_LIKE = SENT + '''<form onsubmit="return false"><div contenteditable=true role=textbox><p><br></p></div>
  <button class=msg-form__send-button type=submit disabled>Send</button></form>
  <script>let s='';const ed=document.querySelector('[role=textbox]'),b=document.querySelector('button');
  ed.addEventListener('input',()=>{s=ed.innerText.trim();b.disabled=!s});
  b.addEventListener('click',()=>{if(s){window.sent.push(s);ed.innerHTML='<p><br></p>';s='';b.disabled=true}})</script>'''
ENTER_SENDS = SENT + '''<div contenteditable=true role=textbox id=box></div><script>box.addEventListener('keydown',e=>{
  if(e.key==='Enter'){e.preventDefault();if(box.innerText.trim())window.sent.push(box.innerText.trim());box.innerText=''}})</script>'''
NOTHING = SENT + '<div contenteditable=true role=textbox id=box></div>'
async def one(browser, html, sel):
    page = await browser.new_page(); await page.set_content(html)
    r = await mb.handle(page, {"action": "read"})
    box = int(__import__("re").search(r"\[(\d+)\] textbox", r["page"]).group(1))
    await mb.handle(page, {"action": "fill", "ref": box, "value": "hi manvi"})
    first = await mb.handle(page, {"action": "press", "value": "Enter"})
    await mb.handle(page, {"action": "press", "value": "Enter"})
    return {"via": first.get("sent_via"), "error": first.get("error", ""), "sent": await page.evaluate("window.sent")}
async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        out = {"linkedin": await one(browser, LINKEDIN_LIKE, 'div[role="textbox"]'),
               "enter": await one(browser, ENTER_SENDS, '#box'), "nothing": await one(browser, NOTHING, '#box')}
        await browser.close()
    print(json.dumps(out))
asyncio.run(main())
"""


def test_enter_in_a_message_box_really_sends_and_says_so_when_it_did_not(tmp_path):
    """LinkedIn adds a new line on Enter and needs its Send button (which has no aria-label and stays disabled until
    real input events arrive); chat apps send on Enter; and when nothing sends, the agent must hear NOT sent."""
    import subprocess
    done = subprocess.run([sys.executable, "-c", SEND_PROBE, str(Path(smara_bridge.__file__).parent)],
                          capture_output=True, text=True, timeout=120)
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert out["linkedin"] == {"via": "send button", "error": "", "sent": ["hi manvi"]}        # exactly once
    assert out["enter"]["via"] == "enter" and out["enter"]["sent"] == ["hi manvi"]
    assert out["nothing"]["via"] is None and "NOT sent" in out["nothing"]["error"] and out["nothing"]["sent"] == []


RECOVERY_PROBE = r"""
import asyncio, json, sys
sys.path.insert(0, sys.argv[1])
import smara_bridge as mb
from playwright.async_api import async_playwright

import re
async def R(mb, page, role, name):
    # the ref of the control with this role and exact name, from a fresh read (as the agent would see it)
    r = await mb.handle(page, {"action": "read"}, allow_private=True)
    m = re.search(r"\[(\d+)\] " + re.escape(role) + ' "' + re.escape(name) + '"', r["page"])
    return int(m.group(1)) if m else None

async def page_with(browser, html):
    page = await browser.new_page(); await page.set_content(html); return page
async def main():
    out = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        # consent banner with Accept and Reject: Reject is chosen; with only Accept: nothing is accepted
        pg = await page_with(browser, '''<div id=cookie-banner class=cookie>We use cookies <button onclick="window.choice='accept';this.parentNode.remove()">Accept all</button>
            <button onclick="window.choice='reject';this.parentNode.remove()">Reject all</button></div><button id=go onclick="window.went=1">Go</button>''')
        await mb.dismiss_popups(pg)                      # what opening a page does
        await mb.handle(pg, {"action": "click", "ref": await R(mb, pg, "button", "Go")})
        out["consent_choice"] = await pg.evaluate("window.choice || null")
        out["went"] = await pg.evaluate("window.went || 0")
        pg = await page_with(browser, '''<div class=cookie-consent>Cookies <button onclick="window.choice='accept'">I agree</button></div><p>x</p>''')
        await mb.dismiss_popups(pg)
        out["accept_only_choice"] = await pg.evaluate("window.choice || null")
        # a disabled button: a clear message, never a forced press
        pg = await page_with(browser, '<button id=b disabled onclick="window.pressed=1">Search</button>')
        r = await mb.handle(pg, {"action": "click", "ref": await R(mb, pg, "button", "Search")})
        out["disabled_said"] = "disabled" in r.get("error", "")
        out["disabled_pressed"] = await pg.evaluate("window.pressed || 0")
        # a guessed selector is never used: the agent gets the page to pick a ref from
        pg = await page_with(browser, '<button>Track order</button><a href="#">Order history</a>')
        r = await mb.handle(pg, {"action": "click", "selector": 'button[aria-label="Orders"]'})
        out["guess_refused"] = "ref number" in r.get("error", "") and 'button "Track order"' in r.get("page", "")
        # a submit button is pressed exactly once even when the page reacts slowly
        pg = await page_with(browser, '<form onsubmit="window.n=(window.n||0)+1;return false"><input name=q><button type=submit>Submit</button></form>')
        await mb.handle(pg, {"action": "click", "ref": await R(mb, pg, "button", "Submit")})
        out["submits"] = await pg.evaluate("window.n || 0")
        # card fields inside a frame are never typed
        pg = await page_with(browser, '<iframe srcdoc="<input id=num placeholder=\'Card number\'>"></iframe>')
        await pg.wait_for_timeout(300)
        r = await mb.handle(pg, {"action": "fill", "ref": await R(mb, pg, "textbox", "Card number"), "value": "4111111111111111"})
        out["card_refused"] = "never type" in r.get("error", "")
        # a click that opens a new tab: the next step works in that tab
        pg = await page_with(browser, '<a href="#" onclick="const w = window.open(); w.document.write(`<title>Second</title><p>second tab</p>`); return false">Open details</a>')
        r = await mb.handle(pg, {"action": "click", "ref": await R(mb, pg, "link", "Open details")})
        out["new_tab"] = bool(r.get("opened_new_tab")) and r.get("title") == "Second"
        await browser.close()
    print(json.dumps(out))
asyncio.run(main())
"""


def test_the_bridge_never_accepts_never_guesses_presses_once_and_never_types_secrets(tmp_path):
    import subprocess
    done = subprocess.run([sys.executable, "-c", RECOVERY_PROBE, str(Path(smara_bridge.__file__).parent)],
                          capture_output=True, text=True, timeout=180)
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert out["consent_choice"] == "reject" and out["went"] == 1          # privacy choice, then the real click
    assert out["accept_only_choice"] is None                               # never accepts on the owner's behalf
    assert out["disabled_said"] and out["disabled_pressed"] == 0
    assert out["guess_refused"]
    assert out["submits"] == 1
    assert out["card_refused"]
    assert out["new_tab"]


HUMAN_PROBE = r'''
import asyncio, json, re, sys, tempfile, time
sys.path.insert(0, sys.argv[1])
import smara_bridge as mb
from playwright.async_api import async_playwright

LOGGER = """<script>window.ev={move:0,wheel:0,key:0,untrusted:0,down:0};
['mousemove','wheel','keydown','mousedown'].forEach(t=>document.addEventListener(t,e=>{
  window.ev[t==='mousemove'?'move':t==='wheel'?'wheel':t==='keydown'?'key':'down']++; if(!e.isTrusted) window.ev.untrusted++;},true));</script>"""
PAGES = {
 "card": LOGGER + """<div style="height:300px"></div><a id=card href="#product" onclick="document.title='PRODUCT PAGE';document.body.insertAdjacentHTML('beforeend','<h1>SONY PS5 product details and price</h1>');return false">
   <div><span>SONY PS5 Console Digital (CFI-2116B01Y) 825 GB</span></div></a><p>footer text</p>""",
 "dead": LOGGER + """<button>Does nothing</button>""",
 "hover": LOGGER + """<button id=b disabled onclick="document.title='CLICKED'">Search</button>
   <div id=area style="width:900px;height:500px;background:#ddd" onmousemove="document.getElementById('b').disabled=false">move over me</div>""",
 "typing": LOGGER + """<input id=q placeholder="Search products"><div id=out></div>""",
 "scroll": LOGGER + """<div style="height:5000px">tall</div>""",
}

async def main():
    out = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(); ctx = await browser.new_context(viewport={"width": 1280, "height": 800})
        async def fresh(name):
            pg = await ctx.new_page(); await pg.set_content(PAGES[name]); return pg
        # 1. card title inside a link: real pointer travel, effect seen, clicked through a numbered handle
        pg = await fresh("card")
        r = await mb.handle(pg, {"action": "read"})
        m = re.search(r'\[(\d+)\] link "SONY PS5 Console Digital', r["page"])
        out["card_listed"] = bool(m)
        card = int(m.group(1))
        r = await mb.handle(pg, {"action": "click", "ref": card})
        ev = await pg.evaluate("window.ev")
        out["card_changed"] = r.get("changed") is True and "PRODUCT PAGE" in r.get("title", "")
        out["pointer_travelled"] = ev["move"] >= 8 and ev["down"] == 1 and ev["untrusted"] == 0
        # 1b. the same element found again after the page re-rendered (attributes wiped)
        pg = await fresh("card"); r = await mb.handle(pg, {"action": "read"})
        card = int(re.search(r'\[(\d+)\] link "SONY', r["page"]).group(1))
        await pg.evaluate("() => document.querySelectorAll('[data-smara-ref]').forEach(e => e.removeAttribute('data-smara-ref'))")
        r = await mb.handle(pg, {"action": "click", "ref": card})
        out["ref_survives_rerender"] = r.get("changed") is True
        # 2. a click that does nothing is reported, so the agent does not repeat it
        pg = await fresh("dead"); r = await mb.handle(pg, {"action": "read"})
        r = await mb.handle(pg, {"action": "click", "ref": int(re.search(r'\[(\d+)\] button "Does nothing"', r["page"]).group(1))})
        out["dead_click_reported"] = r.get("changed") is False and "Do not repeat" in r.get("changes", "")
        # 3. a control that only enables after the pointer moves over the page
        pg = await fresh("hover"); await pg.mouse.move(5, 5)
        await pg.evaluate("document.getElementById('b').disabled = true")
        await pg.mouse.move(300, 400); await pg.mouse.move(310, 410)
        r = await mb.handle(pg, {"action": "click", "selector": "Search"})          # an exact name works as well as a ref
        out["hover_enabled_click"] = r.get("title") == "CLICKED"
        # 4. typing: real key events, human pace
        pg = await fresh("typing"); t = time.monotonic()
        r = await mb.handle(pg, {"action": "fill", "selector": "Search products", "value": "sony ps5 console"})
        ev = await pg.evaluate("window.ev"); val = await pg.evaluate("document.getElementById('q').value")
        out["typed_like_a_person"] = val == "sony ps5 console" and ev["key"] >= 16 and ev["untrusted"] == 0 and (time.monotonic() - t) > 0.5
        # 5. wheel scrolling
        pg = await fresh("scroll")
        await mb.handle(pg, {"action": "scroll", "value": "down"}); y1 = await pg.evaluate("window.scrollY")
        await mb.handle(pg, {"action": "scroll", "value": "bottom"}); y2 = await pg.evaluate("window.scrollY")
        await mb.handle(pg, {"action": "scroll", "value": "top"}); y3 = await pg.evaluate("window.scrollY")
        ev = await pg.evaluate("window.ev")
        out["wheel_scroll"] = y1 > 200 and y2 > y1 and y3 == 0 and ev["wheel"] >= 5 and ev["untrusted"] == 0
        await ctx.close()
    print(json.dumps(out))
    

asyncio.run(main())
'''


def test_the_bridge_moves_the_real_pointer_types_like_a_person_and_reports_clicks_that_did_nothing(tmp_path):
    """Refs survive a re-render, the pointer travels before it presses, keys are real key events, scrolling is the
    wheel, and a click that changed nothing says so (the Flipkart loop: three 'ok' clicks, same page)."""
    import subprocess
    done = subprocess.run([sys.executable, "-c", HUMAN_PROBE, str(Path(smara_bridge.__file__).parent)],
                          capture_output=True, text=True, timeout=240)
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert all(out.values()), [k for k, v in out.items() if not v]


async def test_pressing_enter_in_a_message_box_needs_the_owners_tap_but_search_boxes_do_not(env):
    br = env.app.state.browsers = DeviceFakeBrowsers()
    env.app.state.llm = ScriptLLM(
        call("browser_step", action="navigate", url="https://www.linkedin.com/messaging/"),
        call("browser_step", action="fill", selector="input[aria-label='Search messages']", value="Manvi Jain"),
        call("browser_step", action="press", value="Enter"),                           # search: runs by itself
        call("browser_step", action="click", selector="text=Manvi Jain"),
        call("browser_step", action="fill", selector='div[role="textbox"]', value="hi manvi, this is Smara"),
        call("browser_step", action="press", value="Enter"),                           # sends a message: waits
        say("Sent."))
    await dispatch(env.app, owner("lk1", "LinkedIn pe Manvi ko hi bolo"))
    assert [s["action"] for s in br.steps] == ["navigate", "fill", "press", "click", "fill"]
    card = env.app.state.wa.sent[-1]["interactive"]["body"]["text"]
    assert "SEND this message on" in card and "hi manvi, this is Smara" in card                 # the owner sees the exact text
    sid = (await one(env.pool, "select id from agent_sessions where status = 'WAITING_APPROVAL'"))["id"]
    await dispatch(env.app, owner("lk2", button=f"ag_ok:{sid}"))
    assert [s["action"] for s in br.steps][-1] == "press"                               # sent only after the tap


SEARCH_ENTER_PROBE = r"""
import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import smara_bridge
from playwright.async_api import async_playwright

async def main():
    f = Path(sys.argv[2]) / "search.html"
    f.write_text("<html><body><form onsubmit=\"event.preventDefault(); document.getElementById('r').textContent='results for '+document.getElementById('q').value\">"
                 "<input id='q' name='q' placeholder='Search'></form><div id='r'></div></body></html>")
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(f.as_uri())
        await smara_bridge.handle(page, {"action": "fill", "selector": "Search", "value": "veg pizza"}, allow_private=True)
        r = await smara_bridge.handle(page, {"action": "press", "value": "Enter"}, allow_private=True)
        print(json.dumps({"error": r.get("error", ""), "results": await page.text_content("#r")}))
        await browser.close()

asyncio.run(main())
"""


def test_enter_in_a_search_box_just_searches_and_is_not_reported_as_unsent(tmp_path):
    import subprocess
    done = subprocess.run([sys.executable, "-c", SEARCH_ENTER_PROBE, str(Path(smara_bridge.__file__).parent), str(tmp_path)],
                          capture_output=True, text=True, timeout=120)
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert out["error"] == "" and out["results"] == "results for veg pizza"


def test_a_rupee_sign_in_a_log_line_never_stops_the_helper(monkeypatch, tmp_path):
    import io
    monkeypatch.setattr(smara_bridge, "HOME", tmp_path)
    monkeypatch.setattr(smara_bridge, "LOG", tmp_path / "bridge.log")

    class Cp1252Console(io.TextIOWrapper):
        pass
    console = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", console)
    smara_bridge.log("step click text=₹389 TOTAL on blinkit.com: ok")                   # used to raise UnicodeEncodeError
    assert "₹389" in (tmp_path / "bridge.log").read_text(encoding="utf-8")


def test_approvals_read_the_controls_name_from_the_page_listing():
    from src.agent.policy import SessionCtx, approval_text, sensitive_step, submits_typed_text
    listing = ('page:\n  [21] button "Send"\n  [4] link "Order Online"\n  [7] button "Add to cart"\n  [9] button "Place order"\n'
               '  [3] searchbox "Search messages" value=""\n  [5] textbox "Write a message" value="hi"')
    ctx = SessionCtx(id="s", tenant_id="t", trust="TRUSTED", controls=listing)
    assert sensitive_step({"action": "click", "ref": 21}, ctx) and sensitive_step({"action": "click", "ref": 9}, ctx)
    assert not sensitive_step({"action": "click", "ref": 4}, ctx) and not sensitive_step({"action": "click", "ref": 7}, ctx)
    card = approval_text("browser_step", {"action": "click", "ref": 9}, {"url": "https://shop.example/cart", "title": "Cart", "page": listing})
    assert "the “Place order” button" in card and "shop.example/cart" in card
    enter = {"action": "press", "value": "Enter"}
    assert submits_typed_text(enter, SessionCtx(id="s", tenant_id="t", trust="TRUSTED", controls=listing, last_fill="5"))
    assert not submits_typed_text(enter, SessionCtx(id="s", tenant_id="t", trust="TRUSTED", controls=listing, last_fill="3"))
