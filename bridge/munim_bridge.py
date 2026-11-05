"""Smara Browser Bridge: lets Smara use YOUR browser (Brave, Chrome or Edge) on your own PC.

    pip install playwright httpx
    python munim_bridge.py pair --server https://ai.syntarus.com/munim --code ABCD-EFGH     (code from 'pair browser' in chat)
    python munim_bridge.py run                      # opens Brave/Chrome/Edge with a dedicated profile, visible to you
    python munim_bridge.py run --attach http://127.0.0.1:9222    # drive a browser you started with --remote-debugging-port=9222
    python munim_bridge.py status | unpair

How it stays safe:
- It only connects OUT to your Smara server; nothing on your PC is exposed.
- Every page the agent opens shows a banner with a Stop button; Stop pauses the agent at once.
- The server decides what needs your approval (clicks and typing wait for your tap in WhatsApp/Telegram);
  this program refuses a few things on its own as well: private/local network addresses, file:// pages,
  and typing into password or one-time-code fields.
- Your logins stay in the browser profile on this PC; they are never sent to Smara.
"""
import argparse
import asyncio
import ipaddress
import json
import os
import platform
import re
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx

HOME = Path(os.environ.get("MUNIM_BRIDGE_HOME", Path.home() / ".munim-bridge"))
CONFIG = HOME / "config.json"
BANNER_JS = """() => {
  if (window.__smaraBanner) return;
  window.__smaraBanner = true;
  const add = () => {
    if (!document.body || document.getElementById('smara-agent-banner')) return;
    const b = document.createElement('div');
    b.id = 'smara-agent-banner';
    b.style.cssText = 'position:fixed;z-index:2147483647;top:0;left:50%;transform:translateX(-50%);background:#0b5fff;color:#fff;' +
      'font:600 13px system-ui,sans-serif;padding:6px 14px;border-radius:0 0 10px 10px;display:flex;gap:12px;align-items:center;' +
      'box-shadow:0 2px 10px rgba(0,0,0,.3)';
    const t = document.createElement('span'); t.textContent = 'Smara is working on this page';
    const s = document.createElement('button'); s.textContent = 'Stop';
    s.style.cssText = 'background:#fff;color:#0b5fff;border:0;border-radius:6px;padding:3px 10px;font-weight:700;cursor:pointer';
    s.onclick = () => { window.__smaraPaused = !window.__smaraPaused;
      s.textContent = window.__smaraPaused ? 'Resume' : 'Stop';
      t.textContent = window.__smaraPaused ? 'Smara is paused' : 'Smara is working on this page';
      b.style.background = window.__smaraPaused ? '#b42318' : '#0b5fff'; };
    b.append(t, s); document.body.appendChild(b);
  };
  add(); document.addEventListener('DOMContentLoaded', add); setInterval(add, 1500);
}"""
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


def _clean_label(text: str) -> str:
    """'JBL Tune 310C, Hi-Res Audio ... Co...' -> without the cut-off tail a snapshot added."""
    text = text.strip().strip('"').strip("'")
    for tail in ("...", "\u2026"):
        if text.endswith(tail):
            text = text[: -len(tail)].rsplit(" ", 1)[0] if " " in text[: -len(tail)] else text[: -len(tail)]
    return text.strip(" ,")


def _text_of(selector: str) -> str:
    """The visible text a selector is aiming at, if it names one (text=..., :has-text("..."), [aria-label="..."],
    and the made-up [text='...'] the model sometimes writes)."""
    if selector.startswith("text="):
        return _clean_label(selector[5:])
    import re
    m = re.search(r':has-text\("([^"]+)"\)', selector)
    if not m:       # button[aria-label="Send"], a[title='Next'], input[value*="Save"], div[text*='ADD']
        m = re.search(r"""\[(?:aria-label|title|value|placeholder|text|innertext)[*^$~|]?=["']?([^"'\]]+)""", selector, re.I)
    return _clean_label(m.group(1)) if m else ""


def _split_selectors(selector: str) -> list[str]:
    """'a, b, c' -> ['a', 'b', 'c'], ignoring commas inside quotes or brackets."""
    parts, depth, quote, cur = [], 0, "", ""
    for ch in selector:
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif ch in "[(":
            depth += 1
        elif ch in "])":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
            continue
        cur += ch
    parts.append(cur.strip())
    return [x for x in parts if x]


async def settle(page, max_ms: int = 12000) -> None:
    """App-like sites (search results, government portals, travel sites) keep drawing long after 'load' and often
    never go network-idle. Wait until the visible text has stopped changing, so a read is never of a half-built page.
    Never fails."""
    try:
        await page.wait_for_load_state("networkidle", timeout=4000)
    except Exception:
        pass
    size_js = "() => (document.body ? document.body.innerText.length : 0) + document.querySelectorAll('a,button,input').length"
    last, stable, waited = -1, 0, 0
    while waited < max_ms:
        try:
            size = await page.evaluate(size_js)
        except Exception:                                # mid-navigation (Enter or a click opened the next page)
            if page.is_closed():
                return
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=8000)
            except Exception:
                pass
            waited += 500
            last, stable = -1, 0
            continue
        loading = size < 300 and waited < 3000           # near-empty early on: probably still being built
        stable = stable + 1 if size == last and not loading else 0
        if stable >= 2:
            return
        last = size
        await page.wait_for_timeout(500)
        waited += 500


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
SIGNALS_JS = r"""() => {
  const t = ((document.body ? document.body.innerText : '') + ' ' + document.title).toLowerCase();
  const out = [];
  if (document.querySelector('iframe[src*=captcha],iframe[src*=recaptcha],.g-recaptcha,.h-captcha,[id*=captcha],[class*=captcha]')
      || /captcha|verify you are human|not a robot|unusual traffic/.test(t)) out.push('captcha');
  if (document.querySelector('input[type=password]')) out.push('login');
  if (document.querySelector('input[autocomplete=one-time-code]') || /one.time password|verification code|enter otp|enter the otp/.test(t)) out.push('otp');
  if (/card number|cvv|enter upi|select payment method|net banking|pay securely/.test(t)) out.push('payment');
  if (/digital signature certificate|\bdsc\b|emsigner|sign with dsc/.test(t)) out.push('dsc');
  // The site refuses automated browsers (some government portals do): never try to get around it, hand it over.
  if (t.length < 1500 && /permission denied|access denied|request blocked|you have been blocked|automated (access|queries)|bot detected|forbidden.{0,3}403/.test(t)) out.push('blocked');
  return out;
}"""
SECRET_TYPES_JS = """e => ({type: (e.type || '').toLowerCase(), ac: (e.autocomplete || '').toLowerCase(),
  name: ((e.name || '') + ' ' + (e.id || '') + ' ' + (e.placeholder || '') + ' ' + (e.getAttribute('aria-label') || '')).toLowerCase(),
  frame: location.href.toLowerCase(), framed: window !== window.top})"""
SECRET_WORDS = ("otp", "pin", "cvv", "cvc", "card", "passw", "upi", "vpa", "netbank", "expiry", "account number", "aadhaar")
PAYMENT_OR_LOGIN_FRAME = re.compile(r"razorpay|paytm|payu|ccavenue|billdesk|stripe|juspay|cashfree|phonepe|"
                                    r"checkout|payment|/pay|login|signin|auth|accounts\.google|captcha", re.I)


def secret_field(info: dict) -> bool:
    """Password, OTP, PIN, card, UPI or bank fields, and anything inside a payment or sign-in frame: never typed."""
    return (info["type"] == "password" or "one-time-code" in info["ac"] or info["ac"].startswith("cc-")
            or any(k in info["name"] for k in SECRET_WORDS)
            or (info.get("framed") and bool(PAYMENT_OR_LOGIN_FRAME.search(info.get("frame", "")))))
BROWSERS = {
    "Windows": [r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
                r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"],
    "Darwin": ["/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
               "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"],
    "Linux": ["/usr/bin/brave-browser", "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser"],
}


def find_browser(preferred: str = "auto") -> str | None:
    paths = BROWSERS.get(platform.system(), [])
    if preferred != "auto":
        paths = [p for p in paths if preferred.lower() in p.lower()]
    return next((p for p in paths if Path(p).is_file()), None)


# ---------------------------------------------------------------- safety checks done on this PC

def ip_private(ip: str) -> bool:
    a = ipaddress.ip_address(ip)
    return a.is_private or a.is_loopback or a.is_link_local or a.is_multicast or a.is_reserved or a.is_unspecified


def url_allowed(url: str, allow_private: bool = False) -> str | None:
    """None if the URL may be opened, else the reason it may not."""
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        return "only http and https pages can be opened"
    if allow_private:
        return None
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80))
    except OSError:
        return None                      # let the browser report an unknown host itself
    if any(ip_private(i[4][0]) for i in infos):
        return "addresses on your own network (localhost, router, office servers) are blocked"
    return None


_LAST_TYPED: dict[int, str] = {}       # page -> selector of the box Smara last typed into (what Enter should send)
BOX_TEXT_JS = "e => e.isConnected ? (e.isContentEditable ? e.innerText : (e.value ?? '')) : null"
# The message box's own send control: walk up from the box and pick the closest visible, enabled button named like
# Send / Submit / Post / Reply (or the form's submit button). Marked so Playwright can click it like a person would.
FIND_SEND_JS = """box => {
  document.querySelectorAll('[data-smara-send]').forEach(x => x.removeAttribute('data-smara-send'));
  const name = b => ((b.getAttribute('aria-label') || '') + ' ' + (b.innerText || b.value || '') + ' ' + (b.title || '')).trim().toLowerCase();
  const ok = b => { const r = b.getBoundingClientRect(); return r.width > 0 && r.height > 0 && !b.closest('#smara-agent-banner'); };
  const words = /\\b(send|submit|post|reply|comment)\\b/;
  for (let node = box, depth = 0; node && depth < 10; node = node.parentElement, depth++) {
    const buttons = [...node.querySelectorAll('button,[role=button],input[type=submit]')].filter(ok);
    const hit = buttons.find(b => words.test(name(b))) || buttons.find(b => b.type === 'submit' && b.form && b.form.contains(box));
    if (hit) { hit.setAttribute('data-smara-send', '1'); return name(hit) || 'send'; }
  }
  return null;
}"""


async def type_into(page, selector: str, value: str) -> None:
    """Type like a person. Rich message boxes (LinkedIn, Facebook, Slack-style editors) ignore text that is set
    directly; they only notice real input events, and their Send button stays disabled until they do."""
    box = page.locator(selector).first
    if not await box.evaluate("e => e.isContentEditable", timeout=10000):
        await box.fill(value, timeout=10000)
        return
    await box.click(timeout=10000)
    await page.keyboard.press("ControlOrMeta+A")
    await page.keyboard.press("Delete")
    await page.keyboard.insert_text(value)
    if ((await box.evaluate(BOX_TEXT_JS)) or "").strip() != value.strip():   # editor rejected it: last resort
        await box.fill(value, timeout=10000)


async def is_message_box(page, selector: str) -> bool:
    """A box whose text is sent (chat, comment, post editor), as opposed to a search field, which keeps its text after
    Enter and must not be treated as 'not sent'."""
    if re.search(r"search|query|\bq\b|find|filter", selector, re.I):
        return False
    try:
        return bool(await page.locator(selector).first.evaluate(
            "e => e.isContentEditable || e.tagName === 'TEXTAREA' || e.getAttribute('role') === 'textbox'", timeout=3000))
    except Exception:
        return False


async def send_message(page, selector: str) -> dict:
    """Enter right after typing into a message box. Many sites send on Enter; others (LinkedIn by default) add a new
    line and need their Send button. Do whichever works, then CHECK the box emptied, so 'sent' is never a guess."""
    box = page.locator(selector).first
    try:
        before = await box.evaluate(BOX_TEXT_JS, timeout=5000)
    except Exception:
        before = None
    if not (before or "").strip():
        await page.keyboard.press("Enter")
        return {}
    await box.focus()
    await page.keyboard.press("Enter")
    await page.wait_for_timeout(900)

    async def still_there() -> bool:
        try:
            now = await box.evaluate(BOX_TEXT_JS, timeout=2000)
        except Exception:
            return False                                  # the box went away: the site moved on after sending
        return now is not None and now.strip() != "" and before.strip() in now.strip()

    if not await still_there():
        return {"sent_via": "enter"}
    now = await box.evaluate(BOX_TEXT_JS)
    if now and now != before and now.strip() == before.strip():   # Enter only added a new line here: take it back
        await page.keyboard.press("Backspace")
    if await box.evaluate(FIND_SEND_JS):
        await page.locator("[data-smara-send]").first.click(timeout=6000)
        await page.wait_for_timeout(1200)
        if not await still_there():
            try:
                await box.focus(timeout=2000)            # never leave focus on Send: a stray Enter must not press it again
            except Exception:
                pass
            return {"sent_via": "send button"}
    return {"error": "the message is still in the box: it was NOT sent (Enter did not send it and no Send button "
                     "worked). Read the page and look for the send button, or ask the owner to press Send."}


# ---------------------------------------------------------------- self-recovery: what a careful person does when a step fails
POPUP_JS = r"""() => {
  // Close what stands between the agent and the page: cookie/consent banners, sign-up nags, "allow notifications" boxes,
  // app-download sheets. Prefer the privacy-preserving or neutral choice; never press anything that pays, sends or signs in.
  const visible = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const label = b => ((b.getAttribute('aria-label') || '') + ' ' + (b.innerText || b.value || '') + ' ' + (b.title || '')).trim().toLowerCase();
  const prefer = [/^(reject all|reject|decline|decline all|necessary only|only necessary|use necessary cookies only|essential only)$/,
                  /^(no thanks|not now|maybe later|later|skip|dismiss|close|cancel|×|✕|x|continue without accepting)$/];
  // Never accept or agree to anything (cookies, terms, sign-ups, permissions): those stay with the owner.
  const danger = /pay|buy|book|send|submit|sign ?in|sign ?up|log ?in|register|subscribe|download|install|delete|confirm|accept|agree|allow|authori[sz]e|continue/;
  const boxes = [...document.querySelectorAll('[role=dialog],[aria-modal=true],[id*=cookie],[class*=cookie],[id*=consent],[class*=consent],' +
                 '[id*=gdpr],[class*=gdpr],[class*=modal],[class*=popup],[id*=popup],[class*=interstitial]')]
                 .filter(visible).filter(e => !e.closest('#smara-agent-banner'));
  const cls = b => { const c = b.className; return (c && c.baseVal !== undefined ? c.baseVal : c) || ''; };
  const closed = [];
  for (const box of boxes) {
    const buttons = [...box.querySelectorAll('button,[role=button],a,input[type=button],input[type=submit],span[class*=close],div[class*=close],i[class*=close]')].filter(visible);
    let hit = null;
    for (const rx of prefer) { hit = buttons.find(b => rx.test(label(b)) && !danger.test(label(b))); if (hit) break; }
    if (!hit) hit = buttons.find(b => /close|dismiss/.test(cls(b) + ' ' + label(b)) && !danger.test(label(b)));
    if (hit) { hit.click(); closed.push(label(hit) || 'close'); }
  }
  return closed;
}"""

SIMILAR_JS = r"""(words) => {
  // Elements on the page whose visible name shares a word with what the agent was looking for: the agent picks one next.
  const sel = 'a,button,input,select,textarea,[role=button],[role=link],[role=option],[role=tab],[role=combobox],[contenteditable=true],label';
  const name = e => (e.getAttribute('aria-label') || e.innerText || e.value || e.placeholder || e.title || '').trim().replace(/\s+/g, ' ');
  const out = [];
  for (const e of document.querySelectorAll(sel)) {
    const r = e.getBoundingClientRect(); if (!r.width || !r.height || e.closest('#smara-agent-banner')) continue;
    const n = name(e); if (!n) continue;
    const low = n.toLowerCase();
    if (!words.length || words.some(w => low.includes(w.slice(0, Math.max(4, w.length - 2))))) out.push({tag: e.tagName.toLowerCase(), text: n.slice(0, 60),
      selector: e.id && !/\d{3,}/.test(e.id) ? '#' + CSS.escape(e.id) : (e.name ? `${e.tagName.toLowerCase()}[name="${e.name}"]` : `text=${n.split('\n')[0].slice(0, 50)}`)});
    if (out.length >= 10) break;
  }
  return out;
}"""


async def dismiss_popups(page) -> list:
    """Close consent banners and nags in the page and its frames. Never fails."""
    closed = []
    for frame in page.frames[:8]:
        try:
            closed += await frame.evaluate(POPUP_JS)
        except Exception:
            pass
    if closed:
        await page.wait_for_timeout(600)
    return closed


def _locators(page, selector: str):
    """Every reasonable way a person would find this thing, in order: as written, by its visible words, by label,
    placeholder or role, and the same inside iframes (government portals and payment pages often use them)."""
    parts = _split_selectors(selector) if "," in selector and not selector.startswith("text=") else [selector]
    texts = []
    for part in parts:
        t = _text_of(part)
        if t and t not in texts:
            texts.append(t)
            words = t.split()
            if len(words) > 6:                  # a whole product card's text: the name at its start is what to click
                texts.append(" ".join(words[:5]))
    for scope in [page] + list(page.frames[1:6]):
        for part in parts:
            try:
                yield scope.locator(part).first
            except Exception:
                pass
        for text in texts:
            yield scope.get_by_role("button", name=text).first
            yield scope.get_by_role("link", name=text).first
            yield scope.get_by_label(text).first
            yield scope.get_by_placeholder(text).first
            yield scope.get_by_text(text, exact=False).first


async def find(page, selector: str):
    """A visible element for this selector, recovering the way a person would: look again a moment later, let the
    page finish, close popups, scroll to load more, look inside frames. About 8 s at most; None only after all of it."""
    recover = [lambda: page.wait_for_timeout(1200),
               lambda: settle(page, max_ms=4000),
               lambda: dismiss_popups(page),
               lambda: page.evaluate("() => window.scrollBy(0, window.innerHeight * 0.9)"),
               lambda: page.evaluate("() => window.scrollBy(0, window.innerHeight * 0.9)"),
               lambda: page.wait_for_timeout(1500)]
    for step in [None] + recover:
        if step is not None:
            try:
                await step()
            except Exception:
                pass
            await page.wait_for_timeout(250)
        for loc in _locators(page, selector):
            try:
                if await loc.is_visible():
                    return loc
            except Exception:
                continue
    return None


async def similar(page, selector: str) -> list:
    import re
    skip = {"text", "button", "div", "span", "input", "role", "aria", "label", "class", "has", "href", "name", "type"}
    words = [w.lower() for w in re.findall(r"[A-Za-zऀ-ॿ]{3,}", _text_of(selector) or selector) if w.lower() not in skip][:4]
    try:
        return await page.evaluate(SIMILAR_JS, words)
    except Exception:
        return []


OUTWARD_JS = r"""e => {
  const n = ((e.getAttribute('aria-label') || '') + ' ' + (e.innerText || e.value || '') + ' ' + (e.title || '')).toLowerCase();
  return e.type === 'submit' || /\b(send|submit|post|reply|pay|buy|book|place order|checkout|confirm|delete|remove|apply|register|sign up)\b/.test(n);
}"""


async def recovering_click(page, selector: str):
    """Click and return the page to keep working on (a new tab if the click opened one)."""
    ctx = page.context
    before = len(ctx.pages)
    loc = await find(page, selector)
    if loc is None:
        raise LookupError(selector)
    await loc.scroll_into_view_if_needed(timeout=3000)
    disabled = await loc.evaluate("e => !!(e.disabled || e.getAttribute('aria-disabled') === 'true' || "
                                  "/\\bdisabl/i.test(typeof e.className === 'string' ? e.className : ''))")
    if disabled:
        await page.wait_for_timeout(2500)               # many forms enable it a moment after the last field
        disabled = await loc.evaluate("e => !!(e.disabled || e.getAttribute('aria-disabled') === 'true' || "
                                      "/\\bdisabl/i.test(typeof e.className === 'string' ? e.className : ''))")
    if disabled:
        raise RuntimeError("DISABLED")
    # A control that sends, submits, pays, books or confirms is pressed exactly once: a retry could do it twice.
    outward = await loc.evaluate(OUTWARD_JS)
    try:
        await loc.click(timeout=6000, trial=outward)    # trial: check it is clickable without pressing it yet
        if outward:
            await loc.click(timeout=6000, no_wait_after=True)
    except Exception:
        if outward:
            raise
        await dismiss_popups(page)                       # usually a popup or sticky header sits on top of it
        try:
            await loc.click(timeout=4000)
        except Exception:
            await loc.dispatch_event("click")           # still covered: press it directly
    await page.wait_for_timeout(600)
    if len(ctx.pages) > before:                         # opened in a new tab: carry on there, like a person would
        new = ctx.pages[-1]
        try:
            await new.wait_for_load_state("domcontentloaded", timeout=20000)
        except Exception:
            pass
        if url_allowed(new.url) and new.url != "about:blank":
            await new.close()                           # a tab to a private/local address is never used
            raise PermissionError("the link opened a page on a private or local address, which is blocked; it was closed")
        await new.bring_to_front()
        await settle(new)
        return new
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass
    await settle(page)
    return page


async def goto(page, url: str) -> None:
    """Open a page; slow sites (many government portals) get a second, more patient try."""
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except Exception:
        await page.goto(url, wait_until="commit", timeout=45000)
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=20000)
        except Exception:
            pass
    await settle(page)
    await dismiss_popups(page)


def failure(e: BaseException, action: str, selector: str, found: list) -> dict:
    """An error the agent can act on: what happened, and what to try next."""
    msg = str(e) or type(e).__name__
    if msg == "DISABLED" or "not enabled" in msg.lower():
        hint = ("that control is disabled: the page wants something first (fill a required field, pick a date or an "
                "option from a list, tick a box), or the site is holding the step back. Read the page, complete what is "
                "missing, then try again; if nothing is missing, try another way (a different site or link).")
        return {"error": f"could not {action} {selector!r}: {hint}", "similar": found, "hint": hint}
    if isinstance(e, LookupError) or ("timeout" in msg.lower() and "waiting for" in msg.lower()):
        hint = ("not on the page, even after waiting, closing popups, scrolling and looking in frames. Pick one of "
                "'similar' (they are on the page now), or read the page again.") if found else (
                "not on the page, even after waiting, closing popups, scrolling and looking in frames. Read the page "
                "again; the content may have changed or need another step first (a tab, a menu, a search).")
        return {"error": f"could not {action} {selector!r}: {hint}", "similar": found, "hint": hint}
    if "net::" in msg or "navigation" in msg.lower():
        return {"error": f"the page did not load ({msg[:160]}). Try navigate again once, or a different page.",
                "hint": "retry navigate once; if it fails again the site may be down"}
    return {"error": f"{type(e).__name__}: {msg[:300]}", "similar": found,
            "hint": "read the page, then try a different element from 'elements' or 'similar'"}


PAGES: dict[int, object] = {}       # page -> the page to continue on (a click may open a new tab)


def current(page):
    """The page the next step should use."""
    nxt = PAGES.get(id(page), page)
    try:
        return page if nxt.is_closed() else nxt
    except Exception:
        return page


async def handle(page, cmd: dict, allow_private: bool = False) -> dict:
    page = current(page)
    a = cmd.get("action")
    if isinstance(cmd.get("selector"), str):               # models sometimes send JSON-escaped quotes: div[role=\\"x\\"]
        cmd = {**cmd, "selector": cmd["selector"].replace('\\"', '"').replace("\\'", "'").strip()}
    if a not in ("navigate", "read", "scroll", "back", "wait", "click", "fill", "select", "press"):
        return {"error": f"unsupported action {a!r}"}
    if await page.evaluate("() => !!window.__smaraPaused"):
        return {"error": "the owner paused Smara in the browser (Stop button); ask them to press Resume"}
    extra: dict = {}
    sel = str(cmd.get("selector") or "")
    try:
        if a == "navigate":
            _LAST_TYPED.pop(id(page), None)
            why = url_allowed(str(cmd.get("url", "")), allow_private)
            if why:
                return {"error": f"blocked url: {why}"}
            await goto(page, cmd["url"])
        elif a == "read":
            await settle(page)
        elif a == "click":
            new = await recovering_click(page, sel)
            if new is not page:
                PAGES[id(page)] = new
                page, extra = new, {"opened_new_tab": True}
        elif a == "fill":
            box = await find(page, sel)
            if box is None:
                raise LookupError(sel)
            info = await box.evaluate(SECRET_TYPES_JS, timeout=10000)
            if secret_field(info):
                return {"error": "I never type into password, OTP, PIN, card, UPI or bank fields, or into payment or "
                                 "sign-in boxes; please do that step yourself in the browser",
                        "needs_owner": ["login_or_payment"], "hint": "hand this step to the owner"}
            value = str(cmd.get("value", ""))
            kind = await box.evaluate("""e => e.tagName === 'SELECT' ? 'select' : e.isContentEditable ? 'editable'
                : (e.getAttribute('role') === 'combobox' || e.getAttribute('aria-autocomplete') || e.getAttribute('list')) ? 'suggest' : 'plain'""")
            if kind == "select":
                await box.select_option(label=value)
            elif kind == "editable":
                await type_into(page, sel, value)
            elif kind == "suggest":                              # city / product boxes only react to real keystrokes
                await box.fill("")
                await box.press_sequentially(value, delay=60)
                await page.wait_for_timeout(1200)
                extra = {"hint": "this box suggests matches as you type: read the page and click the right suggestion"}
            elif len(value) <= 300:                              # type it key by key, like a person: many forms
                await box.fill("")                               # (GSTIN, PAN, PIN code checks) validate on keystrokes
                await box.press_sequentially(value, delay=25)
                if (await box.input_value()) != value:
                    await box.fill(value, timeout=10000)
            else:
                await box.fill(value, timeout=10000)
            _LAST_TYPED[id(page)] = sel
        elif a == "press":
            key = str(cmd.get("value") or "Enter")
            target = _LAST_TYPED.get(id(page))
            if key == "Enter" and target and await is_message_box(page, target):
                sent = await send_message(page, target)
                if sent.get("error"):
                    return sent
                extra = sent
                _LAST_TYPED.pop(id(page), None)          # sent: a second Enter must never send it again
            else:
                ctx, before = page.context, len(page.context.pages)
                await page.keyboard.press(key)
                await page.wait_for_timeout(500)
                if len(ctx.pages) > before:
                    new = ctx.pages[-1]
                    if url_allowed(new.url) and new.url != "about:blank":
                        await new.close()
                        raise PermissionError("that opened a page on a private or local address, which is blocked; it was closed")
                    await new.bring_to_front()
                    PAGES[id(page)] = new
                    page, extra = new, {"opened_new_tab": True}
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass
            await settle(page)
        elif a == "scroll":
            v = str(cmd.get("value") or "down").lower()
            js = {"top": "window.scrollTo(0, 0)", "bottom": "window.scrollTo(0, document.body.scrollHeight)",
                  "up": "window.scrollBy(0, -window.innerHeight * 0.8)"}.get(v, "window.scrollBy(0, window.innerHeight * 0.8)")
            await page.evaluate("() => { " + js + " }")
            await page.wait_for_timeout(700)
        elif a == "back":
            await page.go_back(wait_until="domcontentloaded")
            await settle(page)
        elif a == "wait":
            try:
                ms = int(cmd.get("value") or 1000)
            except (TypeError, ValueError):
                ms = 1000
            await page.wait_for_timeout(max(0, min(ms, 8000)))
        elif a == "select":
            box = await find(page, sel)
            if box is None:
                raise LookupError(sel)
            value = str(cmd.get("value", ""))
            try:
                await box.select_option(value, timeout=5000)
            except Exception:
                await box.select_option(label=value, timeout=5000)
            await settle(page)
    except Exception as e:
        return failure(e, a, sel or str(cmd.get("url", "")), await similar(page, sel) if sel else [])
    await page.evaluate(BANNER_JS)
    text = await page.evaluate("() => document.body ? document.body.innerText : ''")
    if len(text) < 400:                                   # framed pages (older portals): read the frames too
        for frame in page.frames[1:5]:
            try:
                text += "\n" + await frame.evaluate("() => document.body ? document.body.innerText : ''")
            except Exception:
                pass
    for banner in ("Smara is working on this page\nStop", "Smara is paused\nResume"):
        text = text.replace(banner, "")
    out = {"url": page.url, "title": await page.title(), "text": text[:4000], "elements": await page.evaluate(SNAPSHOT_JS)}
    if needs := await page.evaluate(SIGNALS_JS):
        out["needs_owner"] = needs
        out["hint"] = ("this page needs the owner (" + ", ".join(needs) + "): never solve a CAPTCHA or type secrets; "
                       "hand off with hand_off_to_owner, then continue after they say done")
    out.update(extra)
    return out


# ---------------------------------------------------------------- config

def load_config() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg), encoding="utf-8")
    try:
        CONFIG.chmod(0o600)
    except OSError:
        pass


def cmd_pair(a) -> int:
    r = httpx.post(a.server.rstrip("/") + "/bridge/pair", json={"code": a.code, "name": a.name or platform.node()}, timeout=30)
    if r.status_code != 200:
        print("Pairing failed:", r.json().get("detail", r.text)[:200])
        return 1
    d = r.json()
    save_config({"server": a.server.rstrip("/"), "token": d["device_token"], "shop": d["shop"]})
    print(f"Paired with {d['shop']}. Now run: python munim_bridge.py run")
    return 0


async def open_browser(p, a):
    if a.attach:
        browser = await p.chromium.connect_over_cdp(a.attach)
        ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
        return ctx, None
    exe = find_browser(a.browser)
    if not exe:
        raise SystemExit("No Brave, Chrome or Edge found. Install one, or start your browser with --remote-debugging-port=9222 and use --attach.")
    profile = Path(a.profile) if a.profile else HOME / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    ctx = await p.chromium.launch_persistent_context(str(profile), executable_path=exe, headless=a.headless,
                                                      viewport=None, args=["--start-maximized"])
    return ctx, exe


LOG = HOME / "bridge.log"
STATUS = HOME / "status.json"       # heartbeat for Smara Desktop: last successful poll, last step, paused
PAUSE = HOME / "paused"             # present = paused (Smara Desktop's Pause button creates/deletes it)
PID = HOME / "bridge.pid"
TASK_NAME = "Smara Browser Bridge"


def log(msg: str) -> None:
    """Console when there is one (python), a log file always (pythonw / startup task have no console)."""
    line = f"{__import__('datetime').datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    try:
        HOME.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        if LOG.stat().st_size > 1_000_000:
            LOG.write_text(LOG.read_text(encoding="utf-8")[-200_000:], encoding="utf-8")
    except OSError:
        pass
    if sys.stdout:
        try:
            print(msg, flush=True)
        except Exception:               # a console that cannot show "₹" or Hindi must never stop the helper
            try:
                sys.stdout.buffer.write((msg + "\n").encode("utf-8", "replace"))
                sys.stdout.flush()
            except Exception:
                pass


def write_status(**fields) -> None:
    """Small JSON file Smara Desktop reads to show On/Paused/last step. Never contains the token."""
    try:
        cur = json.loads(STATUS.read_text(encoding="utf-8")) if STATUS.exists() else {}
    except (OSError, ValueError):
        cur = {}
    cur.update(fields, pid=os.getpid())
    try:
        STATUS.write_text(json.dumps(cur), encoding="utf-8")
    except OSError:
        pass


def describe_step(cmd: dict, result: dict) -> str:
    """One activity line: action and site, never typed values."""
    host = urlparse(str(result.get("url") or cmd.get("url") or "")).hostname or ""
    what = cmd.get("action", "?")
    if what in ("click", "fill", "select"):
        what += f" {str(cmd.get('selector', ''))[:60]}"
    if result.get("error"):
        return f"step {what} on {host or 'page'}: error: {str(result['error'])[:120]}"
    return f"step {what} on {host or 'page'}: ok ({str(result.get('title', ''))[:60]})"


class Browser:
    """Opens your browser only when Smara has work for it, and closes it again when it has been idle for a while.
    So the helper can sit in the background all day without a window until it is actually needed."""

    def __init__(self, p, a):
        self.p, self.a, self.ctx, self.page, self.exe, self.last = p, a, None, None, None, 0.0

    def alive(self) -> bool:
        try:
            return self.ctx is not None and self.page is not None and not self.page.is_closed() and bool(self.ctx.pages)
        except Exception:
            return False

    async def ensure(self):
        if self.alive():
            return self.page
        await self.close()
        self.ctx, self.exe = await open_browser(self.p, self.a)
        await self.ctx.add_init_script("(" + BANNER_JS + ")()")

        async def guard(route):                       # sub-requests too: no pivoting into your private network
            if not self.a.allow_private and url_allowed(route.request.url, False):
                await route.abort()
            else:
                await route.continue_()
        await self.ctx.route("**/*", guard)
        self.page = self.ctx.pages[0] if self.ctx.pages else await self.ctx.new_page()
        log(f"opened {self.exe or 'your attached browser'}")
        return self.page

    async def close(self):
        ctx, self.ctx, self.page = self.ctx, None, None
        if ctx is not None and not self.a.attach:
            try:
                await ctx.close()
            except Exception:
                pass

    async def idle_check(self, minutes: float):
        import time
        if self.alive() and minutes > 0 and self.last and time.monotonic() - self.last > minutes * 60:
            log("closing the browser after being idle")
            await self.close()


async def run(a) -> int:
    import time
    from playwright.async_api import async_playwright
    cfg = load_config()
    if not cfg.get("token"):
        log("Not paired yet. Send 'pair browser' to Smara in WhatsApp, then run: python munim_bridge.py pair --code ...")
        return 1
    headers = {"Authorization": f"Bearer {cfg['token']}"}
    HOME.mkdir(parents=True, exist_ok=True)
    PID.write_text(str(os.getpid()))
    async with async_playwright() as p, httpx.AsyncClient(base_url=cfg["server"], headers=headers, timeout=45) as http:
        br = Browser(p, a)
        if not a.lazy:
            await br.ensure()
        log(f"Smara Browser Bridge is on for {cfg.get('shop', 'your shop')}. "
            f"{'Browser opens when Smara needs it.' if a.lazy else 'Browser is open.'} Press Ctrl+C to stop.")
        while True:
            try:
                r = await http.post("/bridge/poll", params={"wait": 25})
                if r.status_code == 401:
                    log("This PC was disconnected from Smara (unpaired). Pair again to continue.")
                    return 1
                job = r.json() if r.status_code == 200 else {}
            except (httpx.TransportError, ValueError):
                await asyncio.sleep(3)
                continue
            write_status(last_poll_at=time.time(), online=True, paused=PAUSE.exists())
            if not job:
                await br.idle_check(a.idle_minutes)
                continue
            if PAUSE.exists():                     # paused from Smara Desktop: answer at once, do nothing
                await http.post("/bridge/result", json={"job_id": job["job_id"], "result": {
                    "error": "the owner paused Smara's browser in Smara Desktop; ask them to press Resume"}})
                log(f"step {job['command'].get('action')} refused: paused")
                continue
            try:
                try:
                    page = await br.ensure()
                    result = await handle(page, job["command"], a.allow_private)
                except Exception as first:             # the owner may have closed the window: reopen once and retry
                    if "closed" not in str(first).lower():
                        raise
                    await br.close()
                    page = await br.ensure()
                    result = await handle(page, job["command"], a.allow_private)
            except SystemExit as e:
                result = {"error": str(e)}
            except Exception as e:                    # a page error must never kill the bridge
                result = {"error": f"{type(e).__name__}: {str(e)[:300]}"}
            br.last = time.monotonic()
            if br.page is not None:
                br.page = current(br.page)            # a click that opened a new tab: the next step works there
            where = result if result.get("url") else {**result, "url": getattr(br.page, "url", "") if br.page else ""}
            log(describe_step(job["command"], where))
            write_status(last_step_at=time.time(), last_step=describe_step(job["command"], where))
            await http.post("/bridge/result", json={"job_id": job["job_id"], "result": result})


def startup_command() -> list[str]:
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    return [str(pyw if pyw.is_file() else exe), str(Path(__file__).resolve()), "run"]


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _start_now() -> None:
    """Start the helper fully on its own: detached, in its own process group and outside the starting program's job,
    so closing the window or app that started it does not stop it."""
    import subprocess
    base = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    breakaway = 0x01000000                     # CREATE_BREAKAWAY_FROM_JOB
    for flags in ((base | breakaway, base) if platform.system() == "Windows" else (0,)):
        try:
            subprocess.Popen(startup_command(), creationflags=flags, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True, start_new_session=platform.system() != "Windows")
            return
        except OSError:
            continue                           # the parent job does not allow breakaway: start it the plain way


def cmd_install_startup() -> int:
    """Windows: start the helper (no window) every time you sign in. The browser only appears when Smara needs it.
    Uses your own per-user 'Run' list, so no administrator rights are needed."""
    if platform.system() != "Windows":
        print("Auto-start is set up for Windows only for now. On macOS/Linux add 'python munim_bridge.py run' to your login items.")
        return 1
    import winreg
    run_line = " ".join(f'"{x}"' for x in startup_command())
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, TASK_NAME, 0, winreg.REG_SZ, run_line)
    _start_now()
    print("Done. Smara's browser helper now starts by itself when you sign in to Windows and is running now.")
    return 0


def cmd_uninstall_startup() -> int:
    if platform.system() == "Windows":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, TASK_NAME)
        except OSError:
            pass
    return cmd_stop()


def cmd_stop() -> int:
    import subprocess
    try:
        pid = int(PID.read_text().strip())
    except (OSError, ValueError):
        print("The helper is not running.")
        return 0
    if platform.system() == "Windows":
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, text=True)
    else:
        try:
            os.kill(pid, 15)
        except OSError:
            pass
    PID.unlink(missing_ok=True)
    print("Stopped.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="munim-bridge")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pp = sub.add_parser("pair")
    pp.add_argument("--server", default="https://ai.syntarus.com/munim")
    pp.add_argument("--code", required=True)
    pp.add_argument("--name", default="")
    rp = sub.add_parser("run")
    rp.add_argument("--browser", default="auto", help="brave | chrome | edge | auto (prefers Brave)")
    rp.add_argument("--attach", default="", help="CDP URL of a browser you started with --remote-debugging-port, e.g. http://127.0.0.1:9222")
    rp.add_argument("--profile", default="", help="browser profile folder (default: a dedicated Smara profile that keeps your logins)")
    rp.add_argument("--headless", action="store_true")
    rp.add_argument("--lazy", action=argparse.BooleanOptionalAction, default=True,
                    help="open the browser only when Smara needs it (default); --no-lazy opens it at start")
    rp.add_argument("--idle-minutes", type=float, default=30, help="close the browser after this long with no work (0 = never)")
    rp.add_argument("--allow-private", action="store_true", help="allow localhost / private network pages (off by default)")
    sub.add_parser("status")
    sub.add_parser("unpair")
    sub.add_parser("stop")
    sub.add_parser("ensure")
    sub.add_parser("pause")
    sub.add_parser("resume")
    sub.add_parser("install-startup")
    sub.add_parser("uninstall-startup")
    a = ap.parse_args()
    if a.cmd == "pair":
        return cmd_pair(a)
    if a.cmd == "install-startup":
        return cmd_install_startup()
    if a.cmd == "uninstall-startup":
        return cmd_uninstall_startup()
    if a.cmd == "stop":
        return cmd_stop()
    if a.cmd == "status":
        cfg = load_config()
        print(f"Paired with {cfg['shop']} at {cfg['server']}" if cfg.get("token") else "Not paired.")
        return 0
    if a.cmd == "unpair":
        CONFIG.unlink(missing_ok=True)
        print("Forgot this pairing. (Send 'unpair browser' in chat to revoke it on the server too.)")
        return 0
    if a.cmd == "pause":
        HOME.mkdir(parents=True, exist_ok=True)
        PAUSE.write_text("paused")
        print("Paused: Smara will not use this browser until you resume.")
        return 0
    if a.cmd == "resume":
        PAUSE.unlink(missing_ok=True)
        print("Resumed.")
        return 0
    if a.cmd == "ensure":                       # what Smara Desktop / startup call: start it only if it is not running
        if running_pid():
            print("Already running.")
            return 0
        _start_now()
        print("Started.")
        return 0
    if running_pid() and running_pid() != os.getpid():
        log("Another Smara Browser Bridge is already running; this one exits.")
        return 0
    return supervise(a)


def running_pid() -> int | None:
    """PID of a live helper from the pid file, else None."""
    try:
        pid = int(PID.read_text().strip())
    except (OSError, ValueError):
        return None
    if pid == os.getpid():
        return pid
    if platform.system() == "Windows":
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return None
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return pid if code.value == 259 else None                         # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return pid
    except OSError:
        return None


def supervise(a) -> int:
    """Keep the helper alive: if the work loop crashes, log why and start it again (backing off up to a minute)."""
    import time
    import traceback
    delay = 5
    while True:
        started = time.monotonic()
        try:
            code = asyncio.run(run(a))
            if code:                            # not paired / unpaired: nothing to retry
                return code
        except KeyboardInterrupt:
            return 0
        except SystemExit as e:
            log(f"stopped: {e}")
            return 1
        except BaseException:
            log("helper crashed, restarting:\n" + traceback.format_exc()[-1500:])
        delay = 5 if time.monotonic() - started > 300 else min(delay * 2, 60)
        time.sleep(delay)


if __name__ == "__main__":
    sys.exit(main())
