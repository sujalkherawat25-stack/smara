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
import munim_bridge  # noqa: E402


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
    """What munim_bridge does: poll, run the command, post the result."""
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
    assert "offline" in (await db.step("s1", {"action": "navigate", "url": "https://example.com"}, tenant_id=env.t.id))["error"]
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
    assert munim_bridge.url_allowed("file:///etc/passwd")
    assert munim_bridge.url_allowed("http://127.0.0.1:8000/admin")
    assert munim_bridge.url_allowed("http://192.168.1.1/")
    assert munim_bridge.url_allowed("http://169.254.169.254/latest")
    assert munim_bridge.url_allowed("http://127.0.0.1/", allow_private=True) is None
    assert munim_bridge.url_allowed("https://93.184.216.34/") is None


BRIDGE_PROBE = r"""
import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import munim_bridge
from playwright.async_api import async_playwright

async def main():
    out = {}
    page_file = Path(sys.argv[2]) / "login.html"
    page_file.write_text("<html><body><input id='user' name='user'><input id='pw' type='password' name='pw'>"
                         "<input id='code' autocomplete='one-time-code'><button id='go'>Next</button></body></html>")
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(page_file.as_uri())
        ok = await munim_bridge.handle(page, {"action": "fill", "selector": "#user", "value": "ravi"}, allow_private=True)
        out["fill_ok"] = not ok.get("error") and ok["url"].startswith("file:")
        out["pw"] = (await munim_bridge.handle(page, {"action": "fill", "selector": "#pw", "value": "x"})).get("error", "")
        out["otp"] = (await munim_bridge.handle(page, {"action": "fill", "selector": "#code", "value": "1"})).get("error", "")
        out["pw_value"] = await page.input_value("#pw")
        out["user_value"] = await page.input_value("#user")
        await page.evaluate("() => { window.__smaraPaused = true }")
        out["paused"] = (await munim_bridge.handle(page, {"action": "click", "selector": "#go"})).get("error", "")
        await page.evaluate("() => { window.__smaraPaused = false }")
        out["resumed"] = not (await munim_bridge.handle(page, {"action": "read"})).get("error")
        out["banner"] = await page.evaluate("() => !!document.getElementById('smara-agent-banner')")
        await browser.close()
    print(json.dumps(out))

asyncio.run(main())
"""


def test_bridge_never_types_into_password_fields_and_honours_the_stop_button(tmp_path):
    """Real Chromium in its own process (Playwright needs a different event loop than the database tests)."""
    import subprocess
    done = subprocess.run([sys.executable, "-c", BRIDGE_PROBE, str(Path(munim_bridge.__file__).parent), str(tmp_path)],
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
            return {"url": args["url"], "title": "q", "elements": [
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
import munim_bridge
from playwright.async_api import async_playwright

async def main():
    out = {}
    d = Path(sys.argv[2])
    (d / "a.html").write_text("<html><body style='height:4000px'><a href='b.html'>next</a>"
        "<select id='s'><option value='x'>X</option><option value='y'>Y</option></select><p style='margin-top:3000px'>bottom text</p></body></html>")
    (d / "b.html").write_text("<html><body><h1>second</h1><input type='password'></body></html>")
    (d / "c.html").write_text("<html><body><div class='g-recaptcha'></div><p>please verify you are human</p></body></html>")
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto((d / "a.html").as_uri())
        await munim_bridge.handle(page, {"action": "scroll", "value": "bottom"}, allow_private=True)
        out["scrolled"] = await page.evaluate("() => window.scrollY") > 500
        await munim_bridge.handle(page, {"action": "select", "selector": "#s", "value": "y"}, allow_private=True)
        out["selected"] = await page.input_value("#s")
        await munim_bridge.handle(page, {"action": "wait", "value": "50"}, allow_private=True)
        r = await munim_bridge.handle(page, {"action": "click", "selector": "a:has-text('next')"}, allow_private=True)
        out["login_flag"] = r.get("needs_owner")
        r = await munim_bridge.handle(page, {"action": "back"}, allow_private=True)
        out["back"] = r["url"].endswith("a.html") and not r.get("needs_owner")
        await page.goto((d / "c.html").as_uri())
        out["captcha_flag"] = (await munim_bridge.handle(page, {"action": "read"}, allow_private=True)).get("needs_owner")
        await browser.close()
    print(json.dumps(out))

asyncio.run(main())
"""


def test_bridge_scroll_select_back_wait_and_owner_needed_signals(tmp_path):
    import subprocess
    done = subprocess.run([sys.executable, "-c", NEW_ACTIONS_PROBE, str(Path(munim_bridge.__file__).parent), str(tmp_path)],
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
    assert "already paired" in out["pc_helper"] and "Do NOT ask the owner to pair again" in out["pc_helper"]
    env.app.state.browsers = router
    res = await web.web_search(env.app, SessionCtx(id="w1", tenant_id=env.t.id, trust="TRUSTED"), {"query": "banarasi saree"})
    assert "already paired" in res["next"]


def test_activity_lines_never_include_typed_values():
    line = munim_bridge.describe_step({"action": "fill", "selector": "#qty", "value": "secret 1234"},
                                      {"url": "https://example.com/x", "title": "Cart"})
    assert "secret" not in line and "example.com" in line and "fill #qty" in line
    assert "error" in munim_bridge.describe_step({"action": "navigate", "url": "https://a.com"}, {"error": "blocked"})


def test_pause_and_resume_commands_use_a_flag_file(tmp_path, monkeypatch):
    monkeypatch.setattr(munim_bridge, "HOME", tmp_path)
    monkeypatch.setattr(munim_bridge, "PAUSE", tmp_path / "paused")
    monkeypatch.setattr(sys, "argv", ["munim-bridge", "pause"])
    assert munim_bridge.main() == 0 and (tmp_path / "paused").exists()
    monkeypatch.setattr(sys, "argv", ["munim-bridge", "resume"])
    assert munim_bridge.main() == 0 and not (tmp_path / "paused").exists()


APP_LIKE_PROBE = r"""
import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import munim_bridge
from playwright.async_api import async_playwright

PAGE = '''<html><body>
<ul><li id="ember201" tabindex="0" onclick="document.getElementById('thread').textContent='Chat with Manvi Jain'">
  <div><span>Manvi Jain</span><p>Oct 10: shared a post</p></div></li>
  <li id="ember202" tabindex="0"><span>Dhiraj Daga</span></li></ul>
<div id="thread"></div>
<div class="msg-form" contenteditable="true" aria-label="Write a message"></div>
</body></html>'''

async def main():
    out = {}
    f = Path(sys.argv[2]) / "msg.html"
    f.write_text(PAGE)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(f.as_uri())
        snap = await munim_bridge.handle(page, {"action": "read"}, allow_private=True)
        sels = [e["selector"] for e in snap["elements"]]
        out["no_dynamic_ids"] = not any("ember" in s for s in sels)
        manvi = next(e["selector"] for e in snap["elements"] if "Manvi Jain" in e["text"])
        out["manvi_selector"] = manvi
        r = await munim_bridge.handle(page, {"action": "click", "selector": manvi}, allow_private=True)
        out["opened"] = await page.text_content("#thread")
        r2 = await munim_bridge.handle(page, {"action": "click", "selector": 'a:has-text("Dhiraj Daga")'}, allow_private=True)
        out["fallback_click_ok"] = not r2.get("error")       # not an <a>: found by its text instead
        box = next(e["selector"] for e in snap["elements"] if e["type"] == "editable")
        await munim_bridge.handle(page, {"action": "fill", "selector": box, "value": "hi manvi"}, allow_private=True)
        out["typed"] = await page.text_content(".msg-form")
        out["banner_not_in_text"] = "Smara is working" not in r["text"]
        await browser.close()
    print(json.dumps(out))

asyncio.run(main())
"""


def test_bridge_handles_app_like_pages_lists_dynamic_ids_and_message_boxes(tmp_path):
    import subprocess
    done = subprocess.run([sys.executable, "-c", APP_LIKE_PROBE, str(Path(munim_bridge.__file__).parent), str(tmp_path)],
                          capture_output=True, text=True, timeout=120)
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert out["no_dynamic_ids"] and out["manvi_selector"].startswith("text=")
    assert out["opened"] == "Chat with Manvi Jain" and out["fallback_click_ok"]
    assert out["typed"] == "hi manvi" and out["banner_not_in_text"]


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
