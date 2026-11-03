"""Playwright tools. Run only inside the worker subprocess (see client.py)."""
import re
from contextlib import asynccontextmanager
from urllib.parse import quote_plus, urlparse

from playwright.async_api import async_playwright

from src.box.guard import check_url, host_blocked, validate_plan

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"
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


@asynccontextmanager
async def browser_page():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(user_agent=UA, viewport={"width": 1366, "height": 900})

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
            await browser.close()


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
