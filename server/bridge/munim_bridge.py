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
  label: [...(e.labels || [])].map(l => l.innerText).join(' ').toLowerCase(),
  frame: location.href.toLowerCase(), framed: window !== window.top})"""
# Whole words only: "pin" is a secret, "pincode" and "shipping" are not (delivery forms need them).
SECRET_WORDS = re.compile(r"(?<![a-z])otp(?![a-z])|one.?time|(?<![a-z])m?pin(?![a-z])|cvv|cvc|card.?(no|num)|cc.?(num|no)|passw|"
                          r"(?<![a-z])upi(?![a-z])|(?<![a-z])vpa(?![a-z])|netbank|expiry|exp.?date|account.?(no|num)|aadhaa?r")
PAYMENT_OR_LOGIN_FRAME = re.compile(r"razorpay|paytm|payu|ccavenue|billdesk|stripe|juspay|cashfree|phonepe|"
                                    r"checkout|payment|/pay|login|signin|auth|accounts\.google|captcha", re.I)


def secret_field(info: dict) -> bool:
    """Password, OTP, PIN, card, UPI or bank fields, and anything inside a payment or sign-in frame: never typed."""
    if info["type"] == "password" or "one-time-code" in info["ac"] or info["ac"].startswith("cc-"):
        return True
    words = info["name"] + " " + info.get("label", "")
    if info["ac"] == "postal-code" or re.search(r"pin.?code|zip|postal", words):
        return False                                   # a delivery pincode box, even one whose id is just "pin"
    return (bool(SECRET_WORDS.search(words))
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
        if u.scheme in ("upi", "intent", "tel", "mailto", "sms", "whatsapp", "paytmmp", "phonepe", "gpay", "tez", "market"):
            return (f"{u.scheme}: links open an app on a phone, not a web page. For a payment, call hand_off_to_owner: the "
                    "owner pays (QR or UPI on their phone, or the site's own pay page); then continue from the fresh page")
        return "only http and https pages can be opened (use the page's own links and buttons instead of a made-up address)"
    if allow_private:
        return None
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80))
    except OSError:
        return None                      # let the browser report an unknown host itself
    if any(ip_private(i[4][0]) for i in infos):
        return "addresses on your own network (localhost, router, office servers) are blocked"
    return None


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


# ---------------------------------------------------------------- popups: close nags, never accept anything
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


# ---------------------------------------------------------------- behave like a person at the keyboard and mouse
import math
import random

_MOUSE: dict[int, tuple] = {}          # page -> where the pointer is now
HIT_JS = """(e, p) => { const t = document.elementFromPoint(p[0], p[1]); return !!t && (e === t || e.contains(t) || t.contains(e)); }"""
SIGNATURE_JS = """() => [location.href, document.title, document.body ? document.body.innerText.length : 0,
  document.querySelectorAll('[role=dialog],[aria-modal=true],dialog[open]').length,
  document.querySelectorAll('[aria-expanded=true]').length].join('\u001f')"""


async def human_move(page, x: float, y: float) -> None:
    """Move the real pointer along a gentle curve, the way a hand does: eases out, a little wobble, never a teleport."""
    sx, sy = _MOUSE.get(id(page), (random.uniform(80, 400), random.uniform(80, 300)))
    dist = math.hypot(x - sx, y - sy)
    steps = max(8, min(36, int(dist / 22)))
    cx = (sx + x) / 2 + random.uniform(-0.12, 0.12) * dist
    cy = (sy + y) / 2 + random.uniform(-0.12, 0.12) * dist
    for i in range(1, steps + 1):
        t = i / steps
        k = t * t * (3 - 2 * t)
        bx = (1 - k) ** 2 * sx + 2 * (1 - k) * k * cx + k * k * x
        by = (1 - k) ** 2 * sy + 2 * (1 - k) * k * cy + k * k * y
        wobble = 0 if i == steps else random.uniform(-1.2, 1.2)
        await page.mouse.move(bx + wobble, by + wobble)
        await page.wait_for_timeout(random.randint(5, 16))
    _MOUSE[id(page)] = (x, y)


async def human_click(page, target) -> bool:
    """Real pointer travel, a short hover, then press and release. False if the point is covered by something else."""
    await target.scroll_into_view_if_needed(timeout=3000)
    box = await target.bounding_box()
    if not box or box["width"] < 1 or box["height"] < 1:
        return False
    for fx, fy in ((random.uniform(0.3, 0.7), random.uniform(0.35, 0.65)), (0.5, 0.5)):
        x, y = box["x"] + box["width"] * fx, box["y"] + box["height"] * fy
        if await target.evaluate(HIT_JS, [x, y]):
            await human_move(page, x, y)
            await page.wait_for_timeout(random.randint(70, 190))      # hover: menus and "enable on hover" widgets react
            await page.mouse.down()
            try:
                await page.wait_for_timeout(random.randint(45, 120))
                await page.mouse.up()
            except Exception:
                pass          # the press already happened (the page may have navigated away): it must never be pressed again
            return True
    return False


async def human_type(page, box, value: str) -> None:
    """Click into the box, clear it, then type with a person's rhythm (key by key, short pauses)."""
    if await human_click(page, box):
        pass
    else:
        await box.focus()
    await page.keyboard.press("ControlOrMeta+A")
    await page.keyboard.press("Delete")
    fast = len(value) > 80
    for i, ch in enumerate(value):
        await page.keyboard.type(ch)
        await page.wait_for_timeout(random.randint(6, 22) if fast else random.randint(35, 120) + (150 if ch == " " and i % 7 == 0 else 0))


SCROLL_POS_JS = """() => { const all = [window.scrollY, ...[...document.querySelectorAll('div,main,section,article')]
  .filter(e => e.scrollHeight > e.clientHeight + 50).slice(0, 40).map(e => e.scrollTop)]; return all.join(); }"""


async def wheel_scroll(page, direction: str) -> None:
    """Scroll with the mouse wheel in a few small steps, with the pointer over the page, like a person reading."""
    vp = page.viewport_size or {"width": 1280, "height": 800}
    await human_move(page, vp["width"] * random.uniform(0.45, 0.6), vp["height"] * random.uniform(0.45, 0.6))
    if direction in ("top", "bottom"):
        for _ in range(16):
            before = await page.evaluate(SCROLL_POS_JS)
            await page.mouse.wheel(0, -1800 if direction == "top" else 1800)
            await page.wait_for_timeout(260)
            if await page.evaluate(SCROLL_POS_JS) == before:
                break
        return
    total = vp["height"] * 0.8 * (-1 if direction == "up" else 1)
    for _ in range(5):
        await page.mouse.wheel(0, total / 5)
        await page.wait_for_timeout(random.randint(60, 140))
    await page.wait_for_timeout(500)


async def signature(page) -> str:
    try:
        return await page.evaluate(SIGNATURE_JS)
    except Exception:
        return ""


def sig_changed(a: str, b: str) -> bool:
    """Address, title, an opened dialog or menu, the focused control, or a real change in how much text there is."""
    pa, pb = a.split(""), b.split("")
    if len(pa) < 5 or len(pb) < 5:
        return a != b
    if pa[0] != pb[0] or pa[1] != pb[1] or pa[3:] != pb[3:]:
        return True
    la, lb = int(pa[2] or 0), int(pb[2] or 0)
    return abs(la - lb) > max(80, 0.05 * max(la, lb))


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



# ---------------------------------------------------------------- what the agent sees: the page as numbered refs
# The same idea as Playwright MCP, Claude in Chrome and browser-use: every control a person could use gets a number,
# with its role, name and state, across the whole page (frames and open shadow roots included). The agent acts ONLY
# by those numbers, so it never guesses a selector, and after every action it is told exactly what changed.
SNAPSHOT_JS = r"""(max) => {
  // the element that really scrolls: the page itself, or (app-like sites) the biggest scrolling panel
  window.__smaraScroller = () => {
    const d = document.scrollingElement || document.documentElement;
    if (d && d.scrollHeight > window.innerHeight + 10) return d;
    let best = null;
    for (const e of document.querySelectorAll('div,main,section,article')) {
      if (e.scrollHeight <= e.clientHeight + 50 || e.clientHeight < window.innerHeight * 0.4) continue;
      if (!/(auto|scroll)/.test(getComputedStyle(e).overflowY)) continue;
      if (!best || e.clientHeight * e.clientWidth > best.clientHeight * best.clientWidth) best = e;
    }
    return best || d;
  };
  const R = window.__smaraRefs = window.__smaraRefs || {map: new WeakMap(), n: 0};
  const vh = window.innerHeight || 800;
  const NATIVE = 'a[href],button,input:not([type=hidden]),select,textarea,summary,[contenteditable=""],[contenteditable=true],' +
    '[role=button],[role=link],[role=tab],[role=menuitem],[role=menuitemcheckbox],[role=menuitemradio],[role=option],' +
    '[role=checkbox],[role=radio],[role=switch],[role=combobox],[role=textbox],[role=searchbox],[role=slider],' +
    '[role=spinbutton],[role=treeitem],[onclick],[tabindex]:not([tabindex="-1"])';
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const shown = e => {
    const r = e.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const s = getComputedStyle(e);
    return s.visibility !== 'hidden' && s.display !== 'none' && parseFloat(s.opacity || '1') > 0.05 &&
           !e.closest('[aria-hidden=true],[inert],#smara-agent-banner');
  };
  const roleOf = e => {
    const r = (e.getAttribute('role') || '').split(' ')[0];
    if (r) return r;
    const t = e.tagName;
    if (t === 'A') return 'link';
    if (t === 'BUTTON' || t === 'SUMMARY') return 'button';
    if (t === 'SELECT') return 'combobox';
    if (t === 'TEXTAREA') return 'textbox';
    if (t === 'INPUT') {
      const ty = (e.type || 'text').toLowerCase();
      return {checkbox: 'checkbox', radio: 'radio', submit: 'button', button: 'button', reset: 'button', image: 'button',
              range: 'slider', file: 'file', search: 'searchbox'}[ty] || 'textbox';
    }
    if (e.isContentEditable) return 'textbox';
    return 'clickable';
  };
  const contextOf = (e, own) => {
    // the nearest short text that is not the control's own words: its card or row's name (not a price)
    const mine = own.toLowerCase();
    let p = e.parentElement;
    for (let up = 0; p && up < 5; up++, p = p.parentElement) {
      for (const x of p.querySelectorAll('h1,h2,h3,h4,h5,h6,[role=heading],p,span,div,b,strong,a,label')) {
        if (x.children.length || x.contains(e) || e.contains(x)) continue;
        const t = clean(x.innerText || x.textContent);
        if (t.length >= 3 && t.length <= 60 && t.toLowerCase() !== mine && !/^([₹]|rs[.]?\s|\d)/i.test(t)) return t;
      }
    }
    return '';
  };
  const nameOf = e => {
    let n = e.getAttribute('aria-label') || '';
    if (!n && e.getAttribute('aria-labelledby'))
      n = e.getAttribute('aria-labelledby').split(/\s+/).map(id => { const x = document.getElementById(id); return x ? x.innerText : ''; }).join(' ');
    if (!n && e.tagName === 'INPUT' && /^(submit|button|reset)$/i.test(e.type)) n = e.value;
    if (!n && e.labels && e.labels.length) n = [...e.labels].map(l => l.innerText).join(' ');
    if (!n && /^(INPUT|TEXTAREA|SELECT)$/.test(e.tagName)) n = e.placeholder || e.title || e.name || '';
    if (!n && !/^(INPUT|TEXTAREA|SELECT)$/.test(e.tagName)) n = e.innerText;
    if (!n) { const i = e.querySelector('img[alt],svg[aria-label],[title]'); n = i ? (i.alt || i.getAttribute('aria-label') || i.title || '') : ''; }
    if (!n) n = e.title || e.placeholder || '';
    return clean(n).slice(0, 80);
  };
  const regionOf = e => {
    if (e.closest('dialog[open],[role=dialog],[role=alertdialog],[aria-modal=true]')) return 'dialog';
    if (e.closest('header,nav,[role=banner],[role=navigation]')) return 'top';
    if (e.closest('footer,[role=contentinfo]')) return 'bottom';
    return 'main';
  };
  const all = [];
  const walk = root => {
    for (const e of root.querySelectorAll('*')) {
      if (e.shadowRoot) walk(e.shadowRoot);
      let take = e.matches(NATIVE) && !e.closest('[disabled] a');
      if (!take) {                       // a div or span that behaves like a button: the top of a pointer region
        const s = getComputedStyle(e);
        if (s.cursor === 'pointer' && (!e.parentElement || getComputedStyle(e.parentElement).cursor !== 'pointer')
            && !e.parentElement?.closest(NATIVE) && clean(e.innerText || e.getAttribute('aria-label') || '').length) take = true;
      }
      if (take && shown(e)) all.push(e);
    }
  };
  walk(document);
  const inView = e => { const r = e.getBoundingClientRect(); return r.bottom > 0 && r.top < vh; };
  let picked = all;
  if (all.length > max) {             // keep what matters most: an open dialog, then what is on screen, then what is near it
    const dist = e => { const r = e.getBoundingClientRect(); return regionOf(e) === 'dialog' ? -1 : inView(e) ? 0 : Math.min(Math.abs(r.top - vh), Math.abs(r.bottom)); };
    const keep = new Set([...all].sort((a, b) => dist(a) - dist(b)).slice(0, max));
    picked = all.filter(e => keep.has(e));
  }
  const out = picked.map(e => {
    let ref = R.map.get(e); if (!ref) { ref = ++R.n; R.map.set(e, ref); }
    e.setAttribute('data-smara-ref', String(ref));
    const role = roleOf(e);
    const item = {ref, role, name: nameOf(e), region: regionOf(e)};
    if (e.disabled || e.getAttribute('aria-disabled') === 'true') item.disabled = true;
    if (e.checked || e.getAttribute('aria-checked') === 'true') item.checked = true;
    if (e.getAttribute('aria-expanded')) item.expanded = e.getAttribute('aria-expanded') === 'true';
    if (e.getAttribute('aria-selected') === 'true' || e.getAttribute('aria-current') && e.getAttribute('aria-current') !== 'false') item.selected = true;
    if (e.required || e.getAttribute('aria-required') === 'true') item.required = true;
    if (e.getAttribute('aria-invalid') === 'true') item.invalid = true;
    if (e.tagName === 'SELECT') item.value = clean(e.selectedOptions[0] ? e.selectedOptions[0].text : '').slice(0, 60);
    else if (/^(textbox|searchbox|combobox|spinbutton)$/.test(role))
      item.value = e.type === 'password' ? (e.value ? '(hidden)' : '') : clean(e.isContentEditable ? e.innerText : e.value).slice(0, 80);
    if (e.type === 'password') item.secret = true;
    return item;
  });
  // Controls that share their role and name on this page ('ADD' on every dish) say which card they belong to, so the
  // agent can tell them apart. Controls with a unique name keep exactly their own name.
  const seenNames = new Map();
  out.forEach(it => { const k = it.role + '|' + it.name; seenNames.set(k, (seenNames.get(k) || 0) + 1); });
  out.forEach((it, i) => {
    if (seenNames.get(it.role + '|' + it.name) > 1) {
      const c = contextOf(picked[i], it.name);
      if (c) it.name = (it.name ? it.name + ' - ' + c : c).slice(0, 100);
    }
  });
  const live = [...document.querySelectorAll('[role=alert],[role=status],[aria-live=assertive],[aria-live=polite],[class*=toast],[class*=snackbar]')]
    .filter(shown).map(x => clean(x.innerText).slice(0, 160)).filter(Boolean).slice(0, 4);
  const sc = (window.__smaraScroller || (() => null))() || document.scrollingElement || document.documentElement;
  const top = sc === document.scrollingElement || sc === document.documentElement ? window.scrollY : sc.scrollTop;
  return {items: out, live, total: all.length,
          scroll: {y: Math.round(top), h: sc ? sc.scrollHeight : 0, vh: sc && sc.clientHeight ? Math.min(sc.clientHeight, vh) : vh}};
}"""

MAX_REFS = 250
PAGES: dict[int, object] = {}          # page -> the page to continue on (a click may open a new tab)
_REFS: dict[int, dict] = {}            # page -> {"next": n, "key": {(frame, local): ref}, "ref": {ref: (frame, local)}}
_SEEN: dict[int, dict] = {}            # page -> {ref: line} the agent saw last (what "changes" are measured against)
_RN: dict[int, dict] = {}              # page -> {ref: (role, name)} from that same snapshot (to find a re-drawn control)
_LIVE: dict[int, list] = {}            # page -> live messages already reported
_ACTED: dict[int, list] = {}            # page -> names of the controls clicked, newest last (loop check)
LOOP_WINDOW, LOOP_REPEATS = 8, 3


def loop_note(page, label: str) -> str:
    """A note when the same control has been clicked LOOP_REPEATS times in the last LOOP_WINDOW clicks on this page."""
    m = re.search(r'"(.*)"', label or "")
    name = (m.group(1) if m else (label or "")).strip().lower()
    if not name:
        return ""
    hist = _ACTED.setdefault(id(page), [])
    hist.append(name)
    del hist[:-LOOP_WINDOW]
    n = hist.count(name)
    if n < LOOP_REPEATS:
        return ""
    return (f"You have clicked '{name}' {n} times in the last {LOOP_WINDOW} clicks. If it is not doing what you want, read "
            "the open dialog or the page for what is still missing, and try a different control.")
_LAST_TYPED: dict[int, int] = {}       # page -> ref of the box Smara last typed into (what Enter should send)


def current(page):
    """The page the next step should use."""
    nxt = PAGES.get(id(page), page)
    try:
        return page if nxt.is_closed() else nxt
    except Exception:
        return page


def _line(it: dict) -> str:
    s = f'[{it["ref"]}] {it["role"]} "{it["name"]}"'
    for flag in ("disabled", "checked", "selected", "required", "invalid"):
        if it.get(flag):
            s += f" {flag}"
    if "expanded" in it:
        s += " expanded" if it["expanded"] else " collapsed"
    if it.get("value") not in (None, ""):
        s += f' value="{it["value"]}"'
    return s


async def snapshot(page) -> dict:
    """{ref: item} for the main page and its frames, plus scroll position and live messages. Refs stay the same for the
    same element between snapshots, so the agent can refer back to them."""
    reg = _REFS.setdefault(id(page), {"next": 0, "key": {}, "ref": {}})
    items, live, total, scroll = {}, [], 0, {}
    for fi, frame in enumerate(page.frames[:12]):
        try:
            got = await frame.evaluate(SNAPSHOT_JS, MAX_REFS if fi == 0 else 80)
        except Exception:
            continue
        if fi == 0:
            scroll = got.get("scroll") or {}
        live += got.get("live") or []
        total += got.get("total") or 0
        for it in got.get("items") or []:
            key = (frame, it["ref"])
            ref = reg["key"].get(key)
            if ref is None:
                reg["next"] += 1
                ref = reg["key"][key] = reg["next"]
                reg["ref"][ref] = key
            it = {**it, "ref": ref, "local": key[1]}
            if fi:
                it["region"] = "frame"
            items[ref] = it
    return {"items": items, "live": live, "total": total, "scroll": scroll}


def render(snap: dict) -> str:
    """The page as the agent reads it: an open dialog first (it is in front of everything), then the page, grouped."""
    items = list(snap["items"].values())
    sc = snap.get("scroll") or {}
    lines = []
    if sc.get("vh"):
        above = sc["y"] / sc["vh"]
        below = max(0.0, (sc["h"] - sc["y"] - sc["vh"]) / sc["vh"])
        lines.append(f"scroll: {above:.1f} screens above, {below:.1f} screens below")
    if snap["total"] > len(items):
        lines.append(f"(showing {len(items)} of {snap['total']} controls: the ones on screen and nearest to it; scroll to see others)")
    for region, title in (("dialog", "open dialog / popup (in front of the page)"), ("top", "top bar"),
                          ("main", "page"), ("frame", "inside frames"), ("bottom", "footer")):
        group = [_line(it) for it in items if it["region"] == region]
        if group:
            lines.append(title + ":")
            lines += ["  " + g for g in group]
    if snap["live"]:
        lines.append("messages on the page: " + " | ".join(dict.fromkeys(snap["live"])))
    return "\n".join(lines)


def diff(before: dict, after: dict, url_changed: bool) -> str:
    """What an action changed, in words: the agent reads this instead of guessing what happened."""
    if url_changed:
        return ""
    b, a = before or {}, after["items"]
    added = [_line(a[r]) for r in a if r not in b]
    removed = [b[r] for r in b if r not in a]
    changed = [f"{b[r]}  ->  {_line(a[r])}" for r in a if r in b and b[r] != _line(a[r])]
    out = []
    if any(a[r]["region"] == "dialog" for r in a if r not in b):
        out.append("a dialog/popup opened: deal with it first (it is in front of the page)")
    if changed:
        out.append("changed: " + "; ".join(changed[:10]) + (f" (+{len(changed) - 10} more)" if len(changed) > 10 else ""))
    if added:
        out.append("new: " + "; ".join(added[:12]) + (f" (+{len(added) - 12} more)" if len(added) > 12 else ""))
    if removed:
        out.append("gone: " + "; ".join(removed[:8]) + (f" (+{len(removed) - 8} more)" if len(removed) > 8 else ""))
    return "\n".join(out)


class RefError(LookupError):
    pass


async def resolve(page, ref) -> tuple:
    """(locator, item) for a ref the agent saw. If the site re-drew that control, find the one control with the same
    role and name again; otherwise say so (the agent gets the fresh page with the error)."""
    try:
        ref = int(str(ref).strip().strip("[]"))
    except ValueError:
        raise RefError(f"{ref!r} is not a ref number")
    reg = _REFS.get(id(page), {"ref": {}})
    key = reg["ref"].get(ref)
    seen = (_RN.get(id(page)) or {}).get(ref)
    if key:
        frame, local = key
        try:
            loc = frame.locator(f'[data-smara-ref="{local}"]').first
            if await loc.count() and await loc.is_visible():
                return loc, ref
        except Exception:
            pass
    if seen:                                            # re-rendered: the same role and name, if exactly one exists now
        snap = await snapshot(page)
        hits = [r for r, it in snap["items"].items() if (it["role"], it["name"]) == seen]
        if len(hits) == 1:
            frame, local = _REFS[id(page)]["ref"][hits[0]]
            return frame.locator(f'[data-smara-ref="{local}"]').first, hits[0]
    raise RefError(f"[{ref}] is not on the page any more")


TEXT_HIT_JS = r"""(want) => {
  // The visible element whose own words are exactly these (case and spacing ignored): the innermost one, preferring
  // an open dialog, then what is on screen. Marked so Playwright can press it with the real pointer.
  const norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const w = norm(want);
  const shown = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 1 && r.height > 1 && s.visibility !== 'hidden' && s.display !== 'none' && !e.closest('[aria-hidden=true],#smara-agent-banner'); };
  document.querySelectorAll('[data-smara-hit]').forEach(e => e.removeAttribute('data-smara-hit'));
  const hits = [...document.querySelectorAll('body *')].filter(e => norm(e.innerText || e.value || e.getAttribute('aria-label')) === w && shown(e));
  const inner = hits.filter(e => !hits.some(o => o !== e && e.contains(o)));
  if (!inner.length) return 0;
  const vh = window.innerHeight;
  const score = e => { const r = e.getBoundingClientRect();
    return (e.closest('dialog[open],[role=dialog],[aria-modal=true]') ? 0 : 2) + (r.bottom > 0 && r.top < vh ? 0 : 1); };
  inner.sort((a, b) => score(a) - score(b));
  inner[0].setAttribute('data-smara-hit', '1');
  return inner.length;
}"""


async def by_text(page, text: str):
    """(locator, how many matched) for the control showing exactly this text, in the page or its frames."""
    for frame in page.frames[:8]:
        try:
            n = await frame.evaluate(TEXT_HIT_JS, text)
        except Exception:
            continue
        if n > 1:
            raise RefError(f"{n} controls show exactly {text!r}, so a click on the text could hit the wrong one: pick the one "
                           "you mean by its ref number from the page below (controls show what they belong to)")
        if n:
            return frame.locator("[data-smara-hit]").first, n
    raise RefError(f"no visible control shows exactly {text!r}")


async def by_name(page, text: str) -> int:
    """A ref for a control named exactly this (when the agent wrote a name instead of a number). Only an exact,
    unambiguous match is used; anything else is an error that shows the page."""
    want = re.sub(r"\s+", " ", re.sub(r"^text=|[\"']", "", text)).strip().lower()
    snap = await snapshot(page)
    hits = [r for r, it in snap["items"].items() if it["name"].lower() == want]
    if len(hits) == 1:
        return hits[0]
    raise RefError(f"{text!r}: use a ref number from the page" + (f" ({len(hits)} controls have that name)" if hits else ""))


# The page's visible links (where each one goes, and its text). Web search reads these: the numbered listing above has
# the controls but not where they lead, so a search could not see any result.
LINKS_JS = r"""() => {
  const out = [], seen = new Set();
  for (const a of document.querySelectorAll('a[href]')) {
    const href = a.href || '';
    if (!/^https?:/i.test(href)) continue;
    const r = a.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    const text = (a.innerText || a.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 240);
    if (!text) continue;
    const key = href + '|' + text;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({tag: 'a', href, text});
    if (out.length >= 300) break;
  }
  return out;
}"""

SCREEN_TEXT_JS = r"""(limit) => {
  // What a person sees on the screen right now, top to bottom: the words of every visible text inside the window.
  const vh = window.innerHeight, vw = window.innerWidth, out = [];
  const walker = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_TEXT);
  let size = 0, last = '';
  for (let n = walker.nextNode(); n && size < limit; n = walker.nextNode()) {
    const t = n.textContent.replace(/\s+/g, ' ').trim();
    if (!t || !n.parentElement || n.parentElement.closest('script,style,noscript,#smara-agent-banner,[aria-hidden=true]')) continue;
    const range = document.createRange(); range.selectNodeContents(n);
    const r = range.getBoundingClientRect();
    if (r.width < 1 || r.height < 1 || r.bottom <= 0 || r.top >= vh || r.right <= 0 || r.left >= vw) continue;
    const st = getComputedStyle(n.parentElement);
    if (st.visibility === 'hidden' || parseFloat(st.opacity || '1') < 0.05) continue;
    if (t === last) continue;
    out.push(t); last = t; size += t.length + 1;
  }
  return out.join('\n');
}"""


async def observe(page, *, changes_since: dict | None = None, url_before: str | None = None, sig_before: str = "",
                  text_limit: int = 2500) -> dict:
    """The page after a step: address, title, what changed, the numbered controls, then the visible text."""
    try:
        await page.evaluate(BANNER_JS)
    except Exception:
        pass
    snap = await snapshot(page)
    lines = {r: _line(it) for r, it in snap["items"].items()}
    out = {"url": page.url, "title": await page.title()}
    url_changed = url_before is not None and url_before != page.url
    if url_before is not None:
        new_live = [m for m in snap["live"] if m not in _LIVE.get(id(page), [])]
        d = diff(changes_since, snap, url_changed)
        what = (["went to a new page"] if url_changed else []) + ([d] if d else []) + (
            ["page says: " + " | ".join(new_live)] if new_live else [])
        before, now = sig_before.split("\u001f"), (await signature(page)).split("\u001f")
        if not url_changed and len(before) >= 3 and len(now) >= 3:
            if before[1] != now[1]:
                what.append(f"title is now: {now[1][:100]}")
            elif not d and sig_changed(sig_before, "\u001f".join(now)):
                what.append("the page's text changed (read the text below)")
        out["changed"] = bool(what)
        out["changes"] = "\n".join(what) if what else ("nothing visible changed on the page. Do not repeat the same step; "
                                                       "read the page and choose differently.")
    _SEEN[id(page)] = lines
    _RN[id(page)] = {r: (it["role"], it["name"]) for r, it in snap["items"].items()}
    _LIVE[id(page)] = snap["live"]
    out["page"] = render(snap)
    text = ""
    for frame in page.frames[:6]:
        try:
            text += "\n" + await frame.evaluate("() => document.body ? document.body.innerText : ''")
        except Exception:
            pass
        if len(text) > 400 and frame is page.frames[0]:
            break
    for banner in ("Smara is working on this page\nStop", "Smara is paused\nResume"):
        text = text.replace(banner, "")
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    try:                                                 # on screen first: what a person is looking at right now
        screen = await page.evaluate(SCREEN_TEXT_JS, min(1800, text_limit))
    except Exception:
        screen = ""
    if screen and text_limit <= 2500:
        rest = text_limit - len(screen)
        text = f"on screen now:\n{screen}" + (f"\n--- rest of the page ---\n{text[:rest]}" if rest > 200 else "")
    out["text"] = text[:text_limit + 60]
    try:
        if needs := await page.evaluate(SIGNALS_JS):
            out["needs_owner"] = needs
            out["hint"] = ("this page needs the owner (" + ", ".join(needs) + "): never solve a CAPTCHA or type secrets; "
                           "hand off with hand_off_to_owner, then continue after they say done")
    except Exception:
        pass
    return out


# ---------------------------------------------------------------- doing things: one step, then report what changed
ACTIONS = ("navigate", "read", "text", "scroll", "back", "wait", "click", "hover", "fill", "select", "press")
DISABLED_JS = "e => !!(e.disabled || e.getAttribute('aria-disabled') === 'true')"
MESSAGE_BOX_JS = """e => (e.isContentEditable || e.tagName === 'TEXTAREA' || e.getAttribute('role') === 'textbox')
  && e.type !== 'search' && e.getAttribute('role') !== 'searchbox'
  && !/search|query|find|filter/i.test((e.getAttribute('aria-label') || '') + ' ' + (e.placeholder || '') + ' ' + (e.name || ''))"""


async def target_of(page, cmd: dict):
    """(locator, ref) for the control a step names: a ref number from the page; or its exact visible text (sites that
    draw buttons as plain boxes have no ref); or, if the agent wrote a name, the one control with exactly that name."""
    if cmd.get("text") and cmd.get("ref") in (None, ""):
        loc, n = await by_text(page, str(cmd["text"]))
        return loc, f'text "{cmd["text"]}"' + (f" (first of {n})" if n > 1 else "")
    ref = cmd.get("ref")
    sel = str(cmd.get("selector") or "").strip()
    if ref in (None, "") and sel:
        m = re.fullmatch(r'\[?(\d+)\]?|[a-z]*\[data-smara-ref="(\d+)"\].*', sel)
        ref = (m.group(1) or m.group(2)) if m else await by_name(page, sel)
    if ref in (None, ""):
        raise RefError("this step needs a ref (the number in [ ] before a control on the page), or text: the exact "
                       "words of a button you can see in the page text that has no ref")
    return await resolve(page, ref)


async def press_once(page, loc) -> None:
    """Real pointer travel and one press. If something covers the control, Playwright's own click says what."""
    await loc.scroll_into_view_if_needed(timeout=4000)
    if await loc.evaluate(DISABLED_JS):
        await page.wait_for_timeout(2000)              # many forms enable it a moment after the last field
        if await loc.evaluate(DISABLED_JS):
            raise RuntimeError("DISABLED")
    try:
        pressed = await human_click(page, loc)
    except Exception:
        pressed = False
    if not pressed:
        await loc.click(timeout=6000)


async def wait_for_effect(page, sig: str, tabs_before: int, wait_ms: int) -> None:
    """Give the site time to react (slow redirects, new tabs, a cart that updates) before looking."""
    waited = 0
    while waited < wait_ms:
        await page.wait_for_timeout(250)
        waited += 250
        if len(page.context.pages) > tabs_before or sig_changed(sig, await signature(page)):
            break
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass
    await settle(page, max_ms=6000)


async def follow_new_tab(page, tabs_before: int):
    """A click that opened a new tab: carry on there, like a person would (never on a private address)."""
    ctx = page.context
    if len(ctx.pages) <= tabs_before:
        return page
    new = ctx.pages[-1]
    try:
        await new.wait_for_load_state("domcontentloaded", timeout=20000)
    except Exception:
        pass
    if url_allowed(new.url) and new.url != "about:blank":
        await new.close()
        raise PermissionError("that opened a page on a private or local address, which is blocked; it was closed")
    await new.bring_to_front()
    await settle(new)
    PAGES[id(page)] = new
    return new


async def type_into(page, box, value: str) -> None:
    """Type like a person. Rich editors (chat and post boxes) only notice real key events."""
    kind = await box.evaluate("""e => e.tagName === 'SELECT' ? 'select' : e.isContentEditable ? 'editable'
        : (e.getAttribute('role') === 'combobox' || e.getAttribute('aria-autocomplete') || e.getAttribute('list')) ? 'suggest' : 'plain'""")
    if kind == "select":
        await box.select_option(label=value)
        return
    if kind == "plain" and len(value) > 300:
        await box.fill(value, timeout=10000)
        return
    await human_type(page, box, value)
    if kind == "suggest":
        await page.wait_for_timeout(1200)               # suggestion lists appear a moment after typing
    got = ((await box.evaluate(BOX_TEXT_JS)) or "").strip()
    if got != value.strip():                          # the box dropped keys: put the text in whole
        if kind == "editable":
            await box.click(timeout=10000)
            await page.keyboard.press("ControlOrMeta+A")
            await page.keyboard.press("Delete")
            await page.keyboard.insert_text(value)
        else:
            await box.fill(value, timeout=10000)


async def send_message(page, box) -> dict:
    """Enter right after typing into a message box. Many sites send on Enter; others add a new line and need their
    Send button. Do whichever works, then CHECK the box emptied, so 'sent' is never a guess."""
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
                     "worked). Read the page and click the send button by its ref, or ask the owner to press Send."}


def failure(e: BaseException, action: str) -> dict:
    """An error the agent can act on: what happened, and what to do next. The fresh page is added by the caller."""
    msg = str(e) or type(e).__name__
    if msg == "DISABLED":
        return {"error": f"could not {action}: that control is disabled. The page wants something first (a required "
                         "field, an option, a size or a delivery pincode), or it is not available. Look at the page for "
                         "what is missing (fields marked required/invalid, messages on the page)."}
    if isinstance(e, RefError):
        return {"error": f"could not {action}: {msg}. Use a ref number from the page below (it is fresh)."}
    if isinstance(e, PermissionError):
        return {"error": msg}
    if "intercepts pointer events" in msg:
        return {"error": f"could not {action}: something is covering that control (a popup, banner or overlay). "
                         "Close it first (look for it under 'open dialog' on the page below), then try again."}
    if "net::" in msg or "navigation" in msg.lower():
        return {"error": f"the page did not load ({msg[:160]}). Try navigate again once, or a different page."}
    return {"error": f"could not {action}: {type(e).__name__}: {msg[:300]}"}


async def page_links(page) -> list[dict]:
    """Visible http(s) links on the page (main frame only): [{tag, href, text}]."""
    try:
        return await page.evaluate(LINKS_JS)
    except Exception:
        return []


async def handle(page, cmd: dict, allow_private: bool = False) -> dict:
    """Run one step and return the page after it: what changed, the numbered controls, the visible text."""
    page = current(page)
    a = cmd.get("action")
    if a not in ACTIONS:
        return {"error": f"unsupported action {a!r}; use one of: {', '.join(ACTIONS)}"}
    try:
        if await page.evaluate("() => !!window.__smaraPaused"):
            return {"error": "the owner paused Smara in the browser (Stop button); ask them to press Resume"}
    except Exception:
        pass
    url_before, seen_before, sig_before = page.url, dict(_SEEN.get(id(page)) or {}), await signature(page)
    extra: dict = {}
    track = a in ("click", "hover", "fill", "select", "press", "scroll", "back")
    try:
        if a == "navigate":
            _LAST_TYPED.pop(id(page), None)
            why = url_allowed(str(cmd.get("url", "")), allow_private)
            if why:
                return {"error": f"blocked url: {why}"}
            await goto(page, cmd["url"])
        elif a == "read":
            await settle(page, max_ms=6000)
        elif a == "text":
            await settle(page, max_ms=6000)
            out = await observe(page, text_limit=15000)
            return {k: out[k] for k in ("url", "title", "text", "needs_owner", "hint") if k in out}
        elif a == "click":
            loc, ref = await target_of(page, cmd)
            extra["target"] = (_SEEN.get(id(page)) or {}).get(ref, ref if isinstance(ref, str) else f"[{ref}]")
            tabs, sig = len(page.context.pages), await signature(page)
            is_link = await loc.evaluate("e => !!e.closest('a[href]') && !/^(#|javascript)/i.test(e.closest('a[href]').getAttribute('href') || '')")
            await press_once(page, loc)
            await wait_for_effect(page, sig, tabs, 5000 if is_link else 2500)
            new = await follow_new_tab(page, tabs)
            if new is not page:
                page, extra["opened_new_tab"] = new, True
        elif a == "hover":
            loc, ref = await target_of(page, cmd)
            await loc.scroll_into_view_if_needed(timeout=4000)
            box = await loc.bounding_box()
            if box:
                await human_move(page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            await page.wait_for_timeout(800)
        elif a == "fill":
            loc, ref = await target_of(page, cmd)
            info = await loc.evaluate(SECRET_TYPES_JS, timeout=10000)
            if secret_field(info):
                return {"error": "I never type into password, OTP, PIN, card, UPI or bank fields, or into payment or "
                                 "sign-in boxes; the owner does that step in the browser",
                        "needs_owner": ["login_or_payment"], "hint": "hand this step to the owner with hand_off_to_owner"}
            extra["target"] = (_SEEN.get(id(page)) or {}).get(ref, ref if isinstance(ref, str) else f"[{ref}]")
            await type_into(page, loc, str(cmd.get("value", "")))
            if isinstance(ref, int):
                _LAST_TYPED[id(page)] = ref
        elif a == "select":
            loc, ref = await target_of(page, cmd)
            value = str(cmd.get("value", ""))
            if await loc.evaluate("e => e.tagName === 'SELECT'"):
                try:
                    await loc.select_option(label=value, timeout=5000)
                except Exception:
                    await loc.select_option(value, timeout=5000)
            else:                                        # a custom drop-down: open it; the agent clicks the option next
                await press_once(page, loc)
                extra["hint"] = "this is not a plain list: it is now open; click the option you want by its ref"
            await settle(page, max_ms=4000)
        elif a == "press":
            key = str(cmd.get("value") or "Enter")
            if cmd.get("ref") not in (None, "") or cmd.get("selector") or cmd.get("text"):
                loc, _ = await target_of(page, cmd)
                await loc.focus(timeout=5000)
            typed = _LAST_TYPED.get(id(page))
            box = None
            if key in ("Enter", "Return") and typed:
                try:
                    box, _ = await resolve(page, typed)
                    if not await box.evaluate(MESSAGE_BOX_JS):
                        box = None
                except Exception:
                    box = None
            if box is not None:
                sent = await send_message(page, box)
                if sent.get("error"):
                    return {**sent, **await observe(page)}
                extra.update(sent)
                _LAST_TYPED.pop(id(page), None)          # sent: a second Enter must never send it again
            else:
                tabs, sig = len(page.context.pages), await signature(page)
                await page.keyboard.press(key)
                await wait_for_effect(page, sig, tabs, 2500)
                new = await follow_new_tab(page, tabs)
                if new is not page:
                    page, extra["opened_new_tab"] = new, True
        elif a == "scroll":
            if cmd.get("ref") not in (None, "") or cmd.get("text"):
                loc, _ = await target_of(page, cmd)
                await loc.scroll_into_view_if_needed(timeout=4000)
            else:
                v = str(cmd.get("value") or "down").lower()
                await wheel_scroll(page, v if v in ("top", "bottom", "up") else "down")
            await settle(page, max_ms=3000)                # long lists load more as you scroll
        elif a == "back":
            await page.go_back(wait_until="domcontentloaded")
            await settle(page)
        elif a == "wait":
            try:
                ms = int(cmd.get("value") or 1000)
            except (TypeError, ValueError):
                ms = 1000
            await page.wait_for_timeout(max(0, min(ms, 8000)))
    except Exception as e:
        out = failure(e, f"{a} {cmd.get('ref') or cmd.get('text') or cmd.get('selector') or cmd.get('url') or ''}".strip())
        try:
            page = current(page)
            out.update({k: v for k, v in (await observe(page)).items() if k not in out})
        except Exception:
            pass
        return out
    if track:
        out = await observe(page, changes_since=seen_before, url_before=url_before, sig_before=sig_before)
        if a == "scroll":                                # scrolling always moves things; the page itself is the news
            out.pop("changed", None)
            out.pop("changes", None)
    else:
        out = await observe(page)
    if cmd.get("links"):                                 # web search asks for the links on the page it just reached
        out["links"] = await page_links(page)
    head = {k: out.pop(k) for k in ("url", "title") if k in out}
    if a == "click" and extra.get("target"):
        note = loop_note(page, extra["target"])
        if note:
            extra["note"] = note
    return {**head, **extra, **out}                      # what happened first; the long page listing last


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
    if what in ("click", "hover", "fill", "select"):       # 'click [31] button "Add to cart"': the control, never typed text
        what += " " + re.sub(r' value=".*$', "", str(result.get("target") or (f"[{cmd['ref']}]" if cmd.get("ref") else cmd.get("selector", ""))))[:70]
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
            await maybe_tally_sync(cfg)
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


async def maybe_tally_sync(cfg: dict) -> None:
    """Opt-in nightly sync of open bills from the owner's TallyPrime to their own Smara shop."""
    ts = cfg.get("tally_sync") or {}
    if not ts.get("enabled"):
        return
    import time
    from datetime import datetime
    import tally_sender
    if not tally_sender.due_now(ts.get("last_run"), datetime.now()):
        return
    ts["last_run"] = time.time()                       # one attempt a day; a failure waits for tomorrow or `tally-sync now`
    cfg["tally_sync"] = ts
    save_config(cfg)
    try:
        payload = await asyncio.to_thread(tally_sender.collect, int(ts.get("port", 9000)), ts.get("company", ""), ts.get("msme"))
        res = await asyncio.to_thread(tally_sender.send, cfg["server"], cfg["token"], payload)
        skipped = payload["_skipped_no_phone"]
        write_status(last_tally_sync=time.time(), tally_sync=f"ok: {res}", tally_no_phone=len(skipped))
        log(f"Tally sync ok: {res}" + (f"; {len(skipped)} parties have no mobile number in Tally" if skipped else ""))
    except Exception as e:
        write_status(tally_sync=f"failed: {str(e)[:200]}")
        log(f"Tally sync failed: {str(e)[:200]}")


def cmd_tally_sync(a) -> int:
    import tally_sender
    cfg = load_config()
    ts = cfg.get("tally_sync") or {}
    if a.action in ("enable", "disable"):
        ts["enabled"] = a.action == "enable"
        ts["port"] = a.port
        if a.company:
            ts["company"] = a.company
        cfg["tally_sync"] = ts
        save_config(cfg)
        print(f"Nightly Tally sync is {'on' if ts['enabled'] else 'off'}.")
        return 0
    if not cfg.get("token"):
        print("Not paired.")
        return 1
    payload = tally_sender.collect(a.port, a.company or ts.get("company", ""), ts.get("msme"))
    skipped = payload.pop("_skipped_no_phone")
    print(f"Found {len(payload['receivables'])} receivables, {len(payload['payables'])} payables; "
          f"{len(skipped)} parties have no mobile number in Tally.")
    if a.action == "check":
        return 0
    print("Sent:", tally_sender.send(cfg["server"], cfg["token"], payload))
    return 0


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


WATCHDOG_TASK = "Smara Helper Watchdog"
WATCHDOG_MINUTES = 5


def watchdog_command() -> tuple[str, str]:
    """(program, arguments) the watchdog runs: the same interpreter and script as the helper, with `ensure`."""
    cmd = startup_command()
    return cmd[0], " ".join(f'"{x}"' for x in cmd[1:-1] + ["ensure"])


def _run_quiet(args: list[str], env: dict | None = None, timeout: int = 60):
    import subprocess
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, creationflags=flags, env=env)


def watchdog_state() -> str:
    """'current' (registered and pointing at this script), 'stale' (registered for another path) or 'missing'."""
    if platform.system() != "Windows":
        return "missing"
    try:
        out = _run_quiet(["schtasks", "/Query", "/TN", WATCHDOG_TASK, "/XML"])
    except Exception:
        return "missing"
    if out.returncode != 0:
        return "missing"
    return "current" if str(Path(__file__).resolve()).lower() in (out.stdout or "").lower() else "stale"


def register_watchdog() -> bool:
    """Windows: every few minutes run `ensure`, which starts the helper only if it is not running, so a crash or a
    killed process is repaired without waiting for the next sign-in. Per-user, no administrator rights, no window;
    runs on battery too. Returns False (the sign-in entry still works) when it cannot be set up."""
    if platform.system() != "Windows":
        return False
    program, arguments = watchdog_command()
    script = (
        "$ErrorActionPreference='Stop';"
        "$a=New-ScheduledTaskAction -Execute $env:SMARA_WD_EXE -Argument $env:SMARA_WD_ARGS;"
        f"$t=New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes {WATCHDOG_MINUTES}) -RepetitionDuration (New-TimeSpan -Days 3650);"
        "$s=New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Hidden;"
        "$p=New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited;"
        f"Register-ScheduledTask -TaskName '{WATCHDOG_TASK}' -Action $a -Trigger $t -Settings $s -Principal $p "
        "-Description 'Starts the Smara PC helper if it is not running. Does nothing while the helper is up.' -Force | Out-Null")
    try:
        out = _run_quiet(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                         env={**os.environ, "SMARA_WD_EXE": program, "SMARA_WD_ARGS": arguments})
    except Exception as e:
        log(f"Could not set up the helper's self-repair check: {type(e).__name__}")
        return False
    if out.returncode != 0:
        log("Could not set up the helper's self-repair check: " + " ".join((out.stderr or out.stdout or "").split())[:200])
        return False
    return True


def remove_watchdog() -> None:
    if platform.system() == "Windows":
        try:
            _run_quiet(["schtasks", "/Delete", "/TN", WATCHDOG_TASK, "/F"])
        except Exception:
            pass


def startup_installed() -> bool:
    """True when the sign-in entry exists (the owner set the helper up); the watchdog is only kept for that case."""
    if platform.system() != "Windows":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as k:
            winreg.QueryValueEx(k, TASK_NAME)
        return True
    except OSError:
        return False


def ensure_watchdog() -> str:
    """Make sure the self-repair check exists and points at this script. Cheap when it is already right."""
    if platform.system() != "Windows" or not startup_installed():
        return "skipped"
    if watchdog_state() == "current":
        return "current"
    return "registered" if register_watchdog() else "failed"


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
    repaired = register_watchdog()
    print("Done. Smara's browser helper now starts by itself when you sign in to Windows and is running now."
          + (" It is also checked every %d minutes and restarted if it ever stops." % WATCHDOG_MINUTES if repaired else ""))
    return 0


def cmd_uninstall_startup() -> int:
    if platform.system() == "Windows":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, TASK_NAME)
        except OSError:
            pass
        remove_watchdog()
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
    tp = sub.add_parser("tally-sync")
    tp.add_argument("action", choices=["enable", "disable", "check", "now"])
    tp.add_argument("--port", type=int, default=9000)
    tp.add_argument("--company", default="")
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
    if a.cmd == "tally-sync":
        return cmd_tally_sync(a)
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
    try:
        if ensure_watchdog() == "registered":
            log("Set up the self-repair check (restarts this helper if it ever stops).")
    except Exception:
        pass                                    # never let the check stop the helper itself
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
