"""Repeated buttons on a menu (ADD on every dish) must say which dish they belong to, and a text click that matches
several controls must not guess. Live Chromium on a local page: no internet needed."""
import asyncio
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
import munim_bridge as mb  # noqa: E402

DISHES = ["Margherita Pizza", "Paneer Tikka Pizza", "Pepperoni Pizza", "Veg Supreme Pizza", "Corn Pizza",
          "Farmhouse Pizza", "Chicken Tikka Pizza", "Cheese Burst Pizza", "BBQ Chicken Pizza", "Mushroom Pizza",
          "Garlic Bread", "Cold Drink"]
ITEMS = "".join(
    f'<div class="item"><div class="name">{d}</div><div class="price">Rs {150 + i * 10}</div>'
    f'<button class="add" onclick="openModal({i})">ADD</button></div>' for i, d in enumerate(DISHES))
PAGE = f"""<html><head><meta charset="utf-8"><style>.item{{padding:8px;border-bottom:1px solid #ccc}}
#modal{{display:none;position:fixed;top:20%;left:30%;width:400px;background:#fff;padding:16px;border:1px solid #000}}</style></head>
<body><h2>US Pizza &amp; Cafe, Banswara</h2><div id="cart">Cart: empty</div>{ITEMS}
<div id="modal" role="dialog" aria-modal="true"><h4 id="mtitle"></h4>
<label><input type="radio" name="size"> Regular</label><label><input type="radio" name="size"> Large</label>
<button id="addcart" onclick="addToCart()">Add to cart</button>
<button id="closeModal" aria-label="close Modal" onclick="closeModal()">x</button></div>
<script>
const DISHES = {DISHES!r};
let chosen = null, count = 0;
function openModal(i) {{ chosen = i; document.getElementById('mtitle').textContent = DISHES[i];
  document.getElementById('modal').style.display = 'block'; }}
function closeModal() {{ document.getElementById('modal').style.display = 'none'; }}
function addToCart() {{ count++; document.getElementById('cart').textContent = 'Cart: ' + count + ' item(s), last ' + DISHES[chosen];
  closeModal(); }}
</script></body></html>"""

ADD_LINE = re.compile(r'\[(\d+)\] (\w+)\s+"(ADD[^"]*)"')


@pytest.fixture(scope="module")
def menu_url():
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
    yield f"http://127.0.0.1:{server.server_address[1]}/menu"
    server.shutdown()


def _run(coro_fn):
    # Playwright needs a loop that can start processes; the Windows default selector loop cannot.
    loop = asyncio.ProactorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro_fn())
    finally:
        loop.close()


def test_each_add_names_its_dish_and_a_text_click_on_many_matches_is_refused(menu_url):
    async def go():
        async with async_playwright() as p:
            exe = mb.find_browser("auto")
            browser = await p.chromium.launch(headless=True, **({"executable_path": exe} if exe else {}))
            try:
                page = await browser.new_page()
                out = await mb.handle(page, {"action": "navigate", "url": menu_url}, allow_private=True)
                listing = out.get("page", "")
                adds = ADD_LINE.findall(listing)
                assert len(adds) == len(DISHES)
                for dish in DISHES:                                       # the listing says which ADD is which dish
                    assert any(dish in label for _, _, label in adds), dish
                pepperoni = next(int(r) for r, _, label in adds if "Pepperoni" in label)
                await mb.handle(page, {"action": "click", "ref": pepperoni}, allow_private=True)
                title = await page.evaluate("document.getElementById('mtitle').textContent")
                out = await mb.handle(page, {"action": "read"}, allow_private=True)
                add_to_cart = int(re.search(r'\[(\d+)\] \w+\s+"Add to cart[^"]*"', out.get("page", "")).group(1))
                await mb.handle(page, {"action": "click", "ref": add_to_cart}, allow_private=True)
                cart = await page.evaluate("document.getElementById('cart').textContent")
                out = await mb.handle(page, {"action": "click", "text": "ADD"}, allow_private=True)
                modal_hidden = await page.evaluate("getComputedStyle(document.getElementById('modal')).display") == "none"
                return title, cart, out.get("error", ""), modal_hidden
            finally:
                await browser.close()

    title, cart, error, modal_hidden = _run(go)
    assert title == "Pepperoni Pizza"
    assert cart == "Cart: 1 item(s), last Pepperoni Pizza"
    assert "12 controls show exactly 'ADD'" in error and "ref number" in error
    assert modal_hidden                                                   # the refused click did not open another dish
