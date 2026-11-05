"""Long-lived browser for one agent session: one JSON command per stdin line,
one JSON result per stdout line. The page persists between steps, so the agent
can observe -> act -> observe. Same SSRF guard as the one-shot tools.

    {"action": "navigate", "url": "https://..."} | {"action": "read"}
    {"action": "click", "selector": "..."} | {"action": "fill", "selector": "...", "value": "..."}
    {"action": "press", "value": "Enter"}
"""
import asyncio
import json
import sys

from src.box.guard import check_url
from src.box.tools import browser_page

SNAPSHOT_JS = r"""() => {
  const sel = 'a,button,input,select,textarea,[role=button],[role=link],[role=option],[role=tab],[role=menuitem],' +
              '[role=checkbox],[role=listitem],[contenteditable=true],[contenteditable=""],[tabindex="0"]';
  const seen = new Set();
  const els = [...document.querySelectorAll(sel)].filter(e => {
    if (seen.has(e)) return false; seen.add(e);
    const r = e.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && !e.closest('#smara-agent-banner');
  });
  const onScreen = e => { const r = e.getBoundingClientRect(); return r.bottom > 0 && r.top < window.innerHeight; };
  els.sort((x, y) => (onScreen(y) ? 1 : 0) - (onScreen(x) ? 1 : 0));          // what is on screen first
  return els.slice(0, 80).map(e => {
    const tag = e.tagName.toLowerCase();
    const editable = e.isContentEditable;
    const label = e.getAttribute('aria-label') || '';
    const text = (e.innerText || e.value || e.placeholder || label || '').trim().replace(/\s+/g, ' ').slice(0, 60);
    // ids like "ember201" change on every render (LinkedIn and other app-like sites): never use them
    let selector = e.id && !/^(ember|react|radix|headlessui)[-_]?\d+/i.test(e.id) && !/\d{3,}/.test(e.id) ? '#' + CSS.escape(e.id) : null;
    if (!selector && e.name) selector = `${tag}[name="${e.name}"]`;
    if (!selector && label) selector = `${tag}[aria-label="${label.replace(/"/g, '')}"]`;
    if (!selector && editable) selector = `${tag}[contenteditable]`;
    // first line only: a list item's text spans several child elements, and the name is what a person clicks
    const first = (e.innerText || label || '').trim().split('\n')[0].trim().slice(0, 50);
    if (!selector && first) selector = `text=${first}`;                   // matches any element type
    return {selector, tag, type: editable ? 'editable' : (e.type || e.getAttribute('role') || ''), text,
            href: tag === 'a' ? (e.href || '') : ''};
  }).filter(x => x.selector);
}"""


def _text_of(selector: str) -> str:
    """The visible text a selector is aiming at, if it names one (text=..., :has-text("..."))."""
    if selector.startswith("text="):
        return selector[5:].strip().strip('"')
    m = __import__("re").search(r':has-text\("([^"]+)"\)', selector)
    return m.group(1) if m else ""


async def settle(page) -> None:
    """App-like sites keep loading after the first paint: give them a moment, never fail on it."""
    try:
        await page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass


async def smart_click(page, selector: str) -> None:
    """Click like a person would: the selector first, then by its visible text, then by role and name."""
    tries = [lambda: page.locator(selector).first.click(timeout=6000)]
    if text := _text_of(selector):
        tries += [lambda: page.get_by_text(text, exact=False).first.click(timeout=5000),
                  lambda: page.get_by_role("link", name=text).first.click(timeout=3000),
                  lambda: page.get_by_role("button", name=text).first.click(timeout=3000)]
    last = None
    for attempt in tries:
        try:
            await attempt()
            await page.wait_for_load_state("domcontentloaded")
            await settle(page)
            return
        except Exception as e:      # try the next way of finding it
            last = e
    raise last


SIGNALS_JS = """() => {
  const t = ((document.body ? document.body.innerText : '') + ' ' + document.title).toLowerCase();
  const out = [];
  if (document.querySelector('iframe[src*=captcha],iframe[src*=recaptcha],.g-recaptcha,.h-captcha,[id*=captcha],[class*=captcha]')
      || /captcha|verify you are human|not a robot|unusual traffic/.test(t)) out.push('captcha');
  if (document.querySelector('input[type=password]')) out.push('login');
  if (document.querySelector('input[autocomplete=one-time-code]') || /one.time password|verification code|enter otp|enter the otp/.test(t)) out.push('otp');
  if (/card number|cvv|enter upi|select payment method|net banking|pay securely/.test(t)) out.push('payment');
  return out;
}"""


async def snapshot(page) -> dict:
    text = await page.evaluate("() => document.body ? document.body.innerText : ''")
    out = {"url": page.url, "title": await page.title(), "text": text[:3000], "elements": await page.evaluate(SNAPSHOT_JS)}
    if needs := await page.evaluate(SIGNALS_JS):
        out["needs_owner"] = needs          # captcha / login / otp / payment: the owner's part, not the agent's
    return out


async def handle(page, cmd: dict) -> dict:
    a = cmd.get("action")
    if isinstance(cmd.get("selector"), str):               # models sometimes send JSON-escaped quotes: div[role=\\"x\\"]
        cmd = {**cmd, "selector": cmd["selector"].replace('\\"', '"').replace("\\'", "'").strip()}
    if a == "navigate":
        await check_url(str(cmd.get("url", "")))
        await page.goto(cmd["url"], wait_until="domcontentloaded")
        await settle(page)
    elif a == "click":
        await smart_click(page, str(cmd["selector"]))
    elif a == "fill":
        await page.locator(str(cmd["selector"])).first.fill(str(cmd.get("value", "")), timeout=10000)
    elif a == "press":
        await page.keyboard.press(str(cmd.get("value", "Enter")))
        await page.wait_for_load_state("domcontentloaded")
    elif a == "scroll":
        v = str(cmd.get("value") or "down").lower()
        js = {"top": "window.scrollTo(0, 0)", "bottom": "window.scrollTo(0, document.body.scrollHeight)",
              "up": "window.scrollBy(0, -window.innerHeight * 0.8)"}.get(v, "window.scrollBy(0, window.innerHeight * 0.8)")
        await page.evaluate("() => { " + js + " }")
        await page.wait_for_timeout(500)
    elif a == "back":
        await page.go_back(wait_until="domcontentloaded")
    elif a == "wait":
        try:
            ms = int(cmd.get("value") or 1000)
        except (TypeError, ValueError):
            ms = 1000
        await page.wait_for_timeout(max(0, min(ms, 5000)))
    elif a == "select":
        await page.select_option(str(cmd["selector"]), str(cmd.get("value", "")))
        await page.wait_for_load_state("domcontentloaded")
    elif a != "read":
        return {"error": f"unsupported action {a!r}"}
    return await snapshot(page)


async def main() -> None:
    loop = asyncio.get_running_loop()
    async with browser_page() as page:
        print(json.dumps({"ready": True}), flush=True)
        while line := await loop.run_in_executor(None, sys.stdin.readline):
            try:
                out = await handle(page, json.loads(line))
            except Exception as e:
                out = {"error": f"{type(e).__name__}: {str(e)[:300]}"}
            print(json.dumps(out, default=str), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
