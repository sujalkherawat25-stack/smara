"""Playwright tools. Run only inside the worker subprocess (see client.py)."""
import os
import re
import shutil
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote_plus, urlparse

from playwright.async_api import async_playwright

from src.box.guard import check_url, host_blocked, validate_plan

# Only for the headless fallback, which would otherwise announce "HeadlessChrome". A real headed browser keeps its own.
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"
VIEWPORT = {"width": 1366, "height": 900}
LOCALE, TIMEZONE = "en-IN", "Asia/Kolkata"          # the shops are in India; a US/UTC browser on an Indian site stands out
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
TEXT_CAP = 1500

# Smallest containers that look like a supplier card (selectors on IndiaMART are
# unstable, so this keys off the visible "Contact Supplier" button text).
CARD_JS = """() => {
  const hits = [...document.querySelectorAll('div,li,article')].filter(e => {
    const t = e.innerText || '';
    return t.length > 30 && t.length < 700 && /contact supplier/i.test(t);
  });
  return hits.filter(e => !hits.some(o => o !== e && e.contains(o))).map(e => e.innerText);
}"""
_PRICE = re.compile(r"₹\s?[\d,]+(?:\.\d+)?(?:\s*/\s*[A-Za-z]+)?")


def start_virtual_display() -> subprocess.Popen | None:
    """A virtual screen (Xvfb) so the server runs a normal, headed browser instead of a headless one. None when there is
    already a display, or Xvfb is not installed (then the browser falls back to headless)."""
    if os.environ.get("DISPLAY") or not shutil.which("Xvfb"):
        return None
    number = 100 + os.getpid() % 800                  # one screen per worker process, so workers never share one
    proc = subprocess.Popen(["Xvfb", f":{number}", "-screen", "0", "1366x900x24", "-nolisten", "tcp"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    sock = Path(f"/tmp/.X11-unix/X{number}")
    for _ in range(50):
        if sock.exists() or proc.poll() is not None:
            break
        time.sleep(0.1)
    if proc.poll() is not None:
        return None
    os.environ["DISPLAY"] = f":{number}"
    return proc


def profile_dir(tenant_id: str | None) -> Path | None:
    """Where a shop's browser keeps its cookies between chats (cookie choices, sign-ins the owner completed), like a
    person's own browser. None when profiles are off (BROWSER_PROFILE_ROOT unset) or the id is not a plain id."""
    base = os.environ.get("BROWSER_PROFILE_ROOT", "").strip()
    if not base or not tenant_id or not _SAFE_ID.match(tenant_id):
        return None
    path = Path(base) / tenant_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def launch_options(headless: bool) -> dict:
    opts = {"headless": headless, "args": ["--lang=en-IN", "--window-size=1366,900"]}
    return opts


def context_options(headless: bool) -> dict:
    opts = {"viewport": VIEWPORT, "locale": LOCALE, "timezone_id": TIMEZONE}
    if headless:
        opts["user_agent"] = UA                        # headless only: do not announce HeadlessChrome
    return opts


@asynccontextmanager
async def browser_page(tenant_id: str | None = None):
    # BROWSER_CDP_URL points the server at a hosted remote browser (a provider's real browsers and network). The same
    # SSRF route guard applies either way. Empty = run Chromium on this server: headed on a virtual screen when Xvfb is
    # installed, with the shop's own persistent profile when BROWSER_PROFILE_ROOT is set; else headless and fresh.
    cdp = os.environ.get("BROWSER_CDP_URL", "").strip()
    display = None if cdp else start_virtual_display()
    headless = not cdp and not os.environ.get("DISPLAY")
    browser = ctx = None
    try:
        async with async_playwright() as p:
            if cdp:
                browser = await p.chromium.connect_over_cdp(cdp)
                ctx = await browser.new_context(viewport=VIEWPORT, locale=LOCALE, timezone_id=TIMEZONE)
            else:
                profile = profile_dir(tenant_id)
                if profile is not None:
                    try:                               # one browser per profile at a time; a second chat gets a fresh one
                        ctx = await p.chromium.launch_persistent_context(str(profile), **launch_options(headless), **context_options(headless))
                    except Exception:
                        ctx = None
                if ctx is None:
                    browser = await p.chromium.launch(**launch_options(headless))
                    ctx = await browser.new_context(**context_options(headless))

            async def guard(route):  # subrequests too: no pivoting into private networks
                if host_blocked(urlparse(route.request.url).hostname or ""):
                    await route.abort()
                else:
                    await route.continue_()

            await ctx.route("**/*", guard)
            page = await ctx.new_page()
            page.set_default_timeout(15000)
            try:
                yield page
            finally:
                if browser is not None:
                    await browser.close()
                else:
                    await ctx.close()
    finally:
        if display is not None:
            display.kill()


def parse_card(text: str) -> dict | None:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    skip = {"contact supplier", "get best price", "call", "send enquiry", "verified"}
    body = [l for l in lines if l.lower() not in skip]
    if not body:
        return None
    price = next(iter(_PRICE.findall(text)), None)
    rest = [l for l in body[1:] if not _PRICE.search(l)]
    return {"title": body[0][:120], "price": price,
            "location": next((l for l in rest if len(l) <= 40), None)}


def parse_cards(texts: list[str], limit: int) -> list[dict]:
    seen, out = set(), []
    for t in texts:
        card = parse_card(t)
        if card and card["title"].lower() not in seen:
            seen.add(card["title"].lower())
            out.append(card)
    return out[:limit]


async def scrape_indiamart_quotes(product: str, limit: int = 10) -> dict:
    """Read-only public search. Heuristic extraction; not validated on the live site."""
    url = f"https://dir.indiamart.com/search.mp?ss={quote_plus(product.strip()[:80])}"
    async with browser_page() as page:
        await page.goto(url, wait_until="domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        quotes = parse_cards(await page.evaluate(CARD_JS), min(limit, 20))
    return {"product": product, "source": url, "quotes": quotes}


async def run_personal_browser_task(plan: list[dict]) -> dict:
    """Execute an owner-approved plan. Caller must have obtained approval."""
    plan = validate_plan(plan)
    extracted: list[str] = []
    async with browser_page() as page:
        for step in plan:
            a = step["action"]
            if a == "goto":
                await check_url(step["url"])
                await page.goto(step["url"], wait_until="domcontentloaded")
            elif a == "click":
                await page.click(step["selector"])
            elif a == "fill":
                await page.fill(step["selector"], str(step.get("value", "")))
            elif a == "press":
                await page.keyboard.press(str(step.get("value", "Enter")))
            elif a == "wait":
                await page.wait_for_timeout(min(int(step.get("value", 1000)), 5000))
            elif a == "extract_text":
                extracted.append((await page.inner_text(step["selector"]))[:TEXT_CAP])
        return {"url": page.url, "title": await page.title(), "extracted": extracted}
