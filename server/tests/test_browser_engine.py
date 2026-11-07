"""The browser engine on generic local pages (no real sites, no per-site rules): what the agent sees and what each
step reports. Covers the hard cases every real site mixes: div 'buttons', controls far down a long page, a modal in
front of the page, shadow DOM, an iframe, a control that is disabled until a field is filled, a chat box with a Send
button, a password field, and a cart button whose label changes in place.

Needs Playwright and a Chromium-family browser (Brave/Chrome/Edge, or Playwright's own Chromium)."""
import asyncio
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
mb = pytest.importorskip("munim_bridge")
playwright = pytest.importorskip("playwright.async_api")

SHOP = """<!doctype html><html><head><title>Demo Shop - Wireless Earphones</title>
<style>.card{cursor:pointer;border:1px solid #ccc;margin:6px;padding:6px} .modal{position:fixed;inset:20% 30%;background:#fff;border:2px solid}</style></head>
<body>
<header><nav><a href="#home">Home</a> <input type="search" placeholder="Search for products" aria-label="Search for products"></nav></header>
<main>
  <div class="card" onclick="document.getElementById('detail').hidden=false"><div>Sony WI-C100 Wireless Earphones</div><div>Rs 1,499</div></div>
  <section id="detail" hidden>
    <h1>Sony WI-C100</h1>
    <label>Delivery pincode <input id="pin" required oninput="document.getElementById('buy').disabled = this.value.length !== 6"></label>
    <button id="cart" onclick="this.textContent='Go to cart'; document.getElementById('toast').textContent='Added to your cart'">Add to cart</button>
    <div role="status" id="toast"></div>
  </section>
  <div id="filler"></div>
  <button id="buy" disabled onclick="document.title='Checkout'">Buy now</button>
  <div id="host"></div>
  <iframe srcdoc="<button onclick=&quot;this.textContent='Frame clicked'&quot;>Frame button</button>"></iframe>
  <div contenteditable="true" role="textbox" aria-label="Write a message" id="msg"></div>
  <button id="send" onclick="document.getElementById('log').textContent += document.getElementById('msg').innerText; document.getElementById('msg').innerText=''">Send</button>
  <div id="log"></div>
  <input type="password" aria-label="Password">
  <button onclick="document.getElementById('dlg').hidden=false">Open offer</button>
  <div class="modal" role="dialog" aria-label="Offer" id="dlg" hidden><p>Special offer</p><button onclick="this.parentElement.hidden=true">Close</button></div>
</main>
<script>
  const f = document.getElementById('filler');
  for (let i = 0; i < 150; i++) { const a = document.createElement('a'); a.href = '#p' + i; a.textContent = 'Related product ' + i; f.appendChild(a); f.appendChild(document.createElement('br')); }
  const h = document.getElementById('host').attachShadow({mode: 'open'});
  h.innerHTML = '<button onclick="this.textContent=\\'Shadow clicked\\'">Shadow button</button>';
</script></body></html>"""


def ref(page_text: str, role: str, name: str) -> int:
    m = re.search(r"\[(\d+)\] " + re.escape(role) + ' "' + re.escape(name) + '"', page_text)
    assert m, f"{role} {name!r} not in:\n{page_text}"
    return int(m.group(1))


@pytest.fixture(scope="module")
def run():
    async def go(steps):
        async with playwright.async_playwright() as p:
            exe = mb.find_browser("auto")
            browser = await p.chromium.launch(headless=True, **({"executable_path": exe} if exe else {}))
            page = await browser.new_page(viewport={"width": 1280, "height": 800})
            await page.set_content(SHOP)
            try:
                return await steps(page)
            finally:
                await browser.close()
    def drive(steps):
        # Playwright needs a loop that can start processes; the database tests switch Windows to the selector loop.
        loop = asyncio.ProactorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
        try:
            return loop.run_until_complete(go(steps))
        finally:
            loop.close()
    return drive


def test_whole_page_with_roles_states_and_regions(run):
    async def steps(page):
        r = await mb.handle(page, {"action": "read"})
        t = r["page"]
        ref(t, "clickable", "Sony WI-C100 Wireless Earphones Rs 1,499")   # a div that behaves like a button
        ref(t, "button", "Buy now")                                        # far below 150 links: still listed
        assert re.search(r'\[\d+\] button "Buy now" disabled', t)
        ref(t, "button", "Shadow button")                                  # inside shadow DOM
        ref(t, "button", "Frame button")                                   # inside an iframe
        ref(t, "searchbox", "Search for products")
        assert "top bar:" in t and t.index("top bar:") < t.index("page:")
        assert "Password" not in r.get("text", "")[:0]                     # (text present, no secrets in values)
        return r
    run(steps)


def test_click_reports_what_changed_in_place(run):
    async def steps(page):
        r = await mb.handle(page, {"action": "read"})
        card = ref(r["page"], "clickable", "Sony WI-C100 Wireless Earphones Rs 1,499")
        r = await mb.handle(page, {"action": "click", "ref": card})
        assert r["changed"] is True and 'button "Add to cart"' in r["changes"]          # the detail opened: new controls
        cart = ref(r["page"], "button", "Add to cart")
        r = await mb.handle(page, {"action": "click", "ref": cart})
        assert r["target"].startswith(f"[{cart}] button")
        assert '"Add to cart"  ->  [' in r["changes"] and '"Go to cart"' in r["changes"]   # the label changed in place
        assert "Added to your cart" in r["changes"]                                         # the site's own message
        return r
    run(steps)


def test_no_guessing_and_stale_refs_show_the_fresh_page(run):
    async def steps(page):
        await mb.handle(page, {"action": "read"})
        r = await mb.handle(page, {"action": "click", "selector": "button.buy-now-guess"})
        assert "ref number" in r["error"] and "page" in r                                   # never a guessed selector
        r = await mb.handle(page, {"action": "click", "ref": 99999})
        assert "not on the page" in r["error"] and "page" in r
        r = await mb.handle(page, {"action": "click", "selector": "Open offer"})           # an exact name is fine
        assert "dialog" in r["changes"] and "open dialog" in r["page"]
        close = ref(r["page"], "button", "Close")
        r = await mb.handle(page, {"action": "click", "ref": close})
        assert "open dialog" not in r["page"]
        return r
    run(steps)


def test_disabled_until_filled_then_enabled(run):
    async def steps(page):
        r = await mb.handle(page, {"action": "read"})
        r = await mb.handle(page, {"action": "click", "ref": ref(r["page"], "clickable", "Sony WI-C100 Wireless Earphones Rs 1,499")})
        buy = ref(r["page"], "button", "Buy now")
        r = await mb.handle(page, {"action": "click", "ref": buy})
        assert "disabled" in r["error"] and "pincode" in r["error"]
        pin = ref(r["page"], "textbox", "Delivery pincode")
        r = await mb.handle(page, {"action": "fill", "ref": pin, "value": "560001"})
        assert f'[{buy}] button "Buy now" disabled  ->  [{buy}] button "Buy now"' in r["changes"]
        r = await mb.handle(page, {"action": "click", "ref": buy})
        assert r["title"] == "Checkout"
        return r
    run(steps)


def test_shadow_frame_message_box_and_secrets(run):
    async def steps(page):
        r = await mb.handle(page, {"action": "read"})
        r = await mb.handle(page, {"action": "click", "ref": ref(r["page"], "button", "Shadow button")})
        assert '"Shadow clicked"' in r["changes"]
        r = await mb.handle(page, {"action": "click", "ref": ref(r["page"], "button", "Frame button")})
        assert '"Frame clicked"' in r["page"]
        r = await mb.handle(page, {"action": "fill", "ref": ref(r["page"], "textbox", "Write a message"), "value": "Hello there"})
        r = await mb.handle(page, {"action": "press", "value": "Enter"})
        assert r.get("sent_via") in ("enter", "send button")
        assert await page.evaluate("document.getElementById('log').textContent") == "Hello there"
        r = await mb.handle(page, {"action": "fill", "ref": ref(r["page"], "textbox", "Password"), "value": "x"})
        assert "never type" in r["error"]
        return r
    run(steps)


def test_activity_line_never_shows_typed_text():
    line = mb.describe_step({"action": "fill", "ref": 4}, {"url": "https://shop.example/p", "title": "T",
                                                           "target": '[4] textbox "Pincode" value="560001"'})
    assert "560001" not in line and '[4] textbox "Pincode"' in line


APP_LIKE = """<!doctype html><html><head><title>Product</title><style>html,body{margin:0;height:100%;overflow:hidden}
#panel{height:100vh;overflow-y:auto}</style></head><body><div id="root"><div id="panel">
<div style="height:1500px"><div>Wireless earphones</div></div>
<div><div><div class="btn" data-act="cart"><div><div>Add to cart</div></div></div></div></div>
<div style="height:600px"></div></div></div>
<script>  // one listener at the root, like React: the button itself has no role, tabindex, pointer or handler
document.getElementById('root').addEventListener('click', e => {
  const b = e.target.closest('[data-act=cart]'); if (b) { b.querySelector('div div').textContent = 'Go to cart'; document.title = 'In cart'; }
});</script></body></html>"""


def test_buttons_drawn_as_plain_boxes_are_pressed_by_their_text_and_inner_panels_scroll(run):
    async def steps(page):
        await page.set_content(APP_LIKE)
        r = await mb.handle(page, {"action": "read"})
        assert '"Add to cart"' not in r["page"] and "Add to cart" in r["text"]          # no ref, but visible words
        assert re.search(r"scroll: 0\.0 screens above, [1-9]", r["page"])               # the inner panel scrolls
        r = await mb.handle(page, {"action": "scroll", "value": "bottom"})
        assert await page.evaluate("document.getElementById('panel').scrollTop") > 500
        r = await mb.handle(page, {"action": "click", "text": "add to  cart"})          # case and spacing ignored
        assert r["title"] == "In cart" and "title is now: In cart" in r["changes"]
        assert r["target"].startswith('text "add to  cart"')
        r = await mb.handle(page, {"action": "click", "text": "Checkout"})
        assert "no visible control shows exactly" in r["error"] and "page" in r
        return r
    run(steps)
