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

SNAPSHOT_JS = """() => {
  const els = [...document.querySelectorAll('a,button,input,select,textarea,[role=button]')]
    .filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; }).slice(0, 60);
  return els.map(e => {
    const tag = e.tagName.toLowerCase();
    const text = (e.innerText || e.value || e.placeholder || e.getAttribute('aria-label') || '').trim().slice(0, 60);
    let selector = e.id ? '#' + CSS.escape(e.id) : (e.name ? `${tag}[name="${e.name}"]` : null);
    if (!selector && text) selector = `${tag}:has-text("${text.replace(/"/g, '')}")`;
    return {selector, tag, type: e.type || '', text, href: tag === 'a' ? (e.href || '') : ''};
  }).filter(x => x.selector);
}"""


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
    if a == "navigate":
        await check_url(str(cmd.get("url", "")))
        await page.goto(cmd["url"], wait_until="domcontentloaded")
    elif a == "click":
        await page.click(str(cmd["selector"]))
        await page.wait_for_load_state("domcontentloaded")
    elif a == "fill":
        await page.fill(str(cmd["selector"]), str(cmd.get("value", "")))
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
