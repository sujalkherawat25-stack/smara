"""The engine notices when the agent keeps clicking the same control with no progress, and not when it moves through
different controls. Live Chromium on a local page."""
import asyncio
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
import smara_bridge as mb  # noqa: E402

PAGE = """<html><head><meta charset="utf-8"></head><body><h2>Checkout</h2>
<button id="next">Next</button><button>Item one</button><button>Item two</button><button>Item three</button>
</body></html>"""


@pytest.fixture(scope="module")
def page_url():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/checkout"
    server.shutdown()


def _run(coro_fn):
    loop = asyncio.ProactorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro_fn())
    finally:
        loop.close()


def test_the_same_control_clicked_again_and_again_gets_a_note_and_distinct_ones_do_not(page_url):
    async def go():
        async with async_playwright() as p:
            exe = mb.find_browser("auto")
            browser = await p.chromium.launch(headless=True, **({"executable_path": exe} if exe else {}))
            try:
                page = await browser.new_page()
                await mb.handle(page, {"action": "navigate", "url": page_url}, allow_private=True)
                notes = []
                for _ in range(3):                                # 'Next' does nothing: the same control, no progress
                    out = await mb.handle(page, {"action": "click", "text": "Next"}, allow_private=True)
                    notes.append(out.get("note", ""))
                distinct = []
                for label in ("Item one", "Item two", "Item three"):    # different controls: moving on, not looping
                    out = await mb.handle(page, {"action": "click", "text": label}, allow_private=True)
                    distinct.append(out.get("note", ""))
                return notes, distinct
            finally:
                await browser.close()

    notes, distinct = _run(go)
    assert notes[0] == "" and notes[1] == ""
    assert "clicked 'next' 3 times" in notes[2]
    assert distinct == ["", "", ""]
