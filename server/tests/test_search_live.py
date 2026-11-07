"""Web search on a real Chromium, end to end: the helper's page step returns the links, and web_search reads them.
Local pages stand in for DuckDuckGo and Bing (their redirect links included), so no internet is needed."""
import asyncio
import base64
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import munim_bridge as mb  # noqa: E402
from src.agent import web  # noqa: E402
from src.agent.policy import SessionCtx  # noqa: E402


def _b64(url: str) -> str:
    return base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")


@pytest.fixture(scope="module")
def site():
    pages: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = pages.get(self.path.split("?")[0])
            self.send_response(200 if body else 404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write((body or "not found").encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    mario = quote(base + "/mario", safe="")
    bing_target = base + "/rajasthan-pizza"
    pages.update({
        "/ddg": ('<html><body><div class="result">'
                 f'<a class="result__a" href="//duckduckgo.com/l/?uddg={mario}&rut=1">Mario\'s Pizza, Banswara</a>'
                 f'<a class="result__snippet" href="//duckduckgo.com/l/?uddg={mario}&rut=1">'
                 'Best cheese pizza in Banswara, open till 11 pm</a></div></body></html>'),
        "/bing": ('<html><body><li class="b_algo">'
                  f'<a href="https://www.bing.com/ck/a?!&&p=1&ntb=1&u=a1{_b64(bing_target)}">Pizza spots in Banswara</a>'
                  '</li></body></html>'),
        "/mario": "<html><body><h1>Mario's Pizza</h1></body></html>",
        "/rajasthan-pizza": "<html><body><h1>Pizza spots</h1></body></html>",
    })
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield base
    server.shutdown()


def _run(coro_fn):
    # Playwright needs a loop that can start processes; the Windows default selector loop cannot.
    loop = asyncio.ProactorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro_fn())
    finally:
        loop.close()


class _RealPage:
    """The search's browser: the real helper engine on one real page."""

    def __init__(self, page):
        self.page = page
        self.steps = []

    async def step(self, sid, args, tenant_id=None):
        self.steps.append(args)
        return await mb.handle(self.page, args, allow_private=True)

    async def close(self, sid):
        pass

    def where(self, sid):
        return "server"


def test_the_page_step_returns_links_only_when_asked_and_search_reads_both_engines(site, monkeypatch):
    async def go():
        async with async_playwright() as p:
            exe = mb.find_browser("auto")
            browser = await p.chromium.launch(headless=True, **({"executable_path": exe} if exe else {}))
            try:
                page = await browser.new_page()
                plain = await mb.handle(page, {"action": "navigate", "url": site + "/ddg"}, allow_private=True)
                assert "links" not in plain                                   # the agent's own page reads are unchanged
                ddg = await mb.handle(page, {"action": "navigate", "url": site + "/ddg", "links": True}, allow_private=True)
                bing = await mb.handle(page, {"action": "navigate", "url": site + "/bing", "links": True}, allow_private=True)
                monkeypatch.setattr(web, "ENGINES", (("duckduckgo", site + "/ddg?q={q}"), ("bing", site + "/bing?q={q}")))
                app = SimpleNamespace(state=SimpleNamespace(browsers=_RealPage(page),
                                                            settings=SimpleNamespace(brave_search_api_key=""), pool=None))
                found = await web.web_search(app, SessionCtx(id="w1", tenant_id="t1", trust="TRUSTED"),
                                             {"query": "pizza banswara"})
                return ddg, bing, found
            finally:
                await browser.close()

    ddg, bing, found = _run(go)
    first = web.parse_results(ddg["links"])[0]
    assert first["url"].endswith("/mario") and "open till 11 pm" in first["snippet"]
    assert web.parse_results(bing["links"])[0]["url"].endswith("/rajasthan-pizza")
    assert found["engine"] == "duckduckgo" and found["results"][0]["title"] == "Mario's Pizza, Banswara"
