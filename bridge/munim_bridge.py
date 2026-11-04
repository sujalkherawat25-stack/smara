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
SNAPSHOT_JS = """() => {
  const els = [...document.querySelectorAll('a,button,input,select,textarea,[role=button]')]
    .filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && e.id !== 'smara-agent-banner'
                   && !e.closest('#smara-agent-banner'); }).slice(0, 40);
  return els.map(e => {
    const tag = e.tagName.toLowerCase();
    const text = (e.innerText || e.value || e.placeholder || e.getAttribute('aria-label') || '').trim().slice(0, 60);
    let selector = e.id ? '#' + CSS.escape(e.id) : (e.name ? `${tag}[name="${e.name}"]` : null);
    if (!selector && text) selector = `${tag}:has-text("${text.replace(/"/g, '')}")`;
    return {selector, tag, type: e.type || '', text};
  }).filter(x => x.selector);
}"""
SECRET_TYPES_JS = """e => ({type: (e.type || '').toLowerCase(), ac: (e.autocomplete || '').toLowerCase(),
  name: ((e.name || '') + ' ' + (e.id || '')).toLowerCase()})"""
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


async def handle(page, cmd: dict, allow_private: bool = False) -> dict:
    a = cmd.get("action")
    if a not in ("navigate", "read", "click", "fill", "press"):
        return {"error": f"unsupported action {a!r}"}
    if await page.evaluate("() => !!window.__smaraPaused"):
        return {"error": "the owner paused Smara in the browser (Stop button); ask them to press Resume"}
    if a == "navigate":
        why = url_allowed(str(cmd.get("url", "")), allow_private)
        if why:
            return {"error": f"blocked url: {why}"}
        await page.goto(cmd["url"], wait_until="domcontentloaded", timeout=30000)
    elif a == "click":
        await page.click(str(cmd["selector"]), timeout=10000)
        await page.wait_for_load_state("domcontentloaded")
    elif a == "fill":
        info = await page.locator(str(cmd["selector"])).first.evaluate(SECRET_TYPES_JS, timeout=10000)
        if info["type"] == "password" or "one-time-code" in info["ac"] or any(
                k in info["name"] for k in ("otp", "pin", "cvv", "card", "passw")):
            return {"error": "I never type into password, OTP, PIN or card fields; please do that step yourself in the browser"}
        await page.fill(str(cmd["selector"]), str(cmd.get("value", "")), timeout=10000)
    elif a == "press":
        await page.keyboard.press(str(cmd.get("value", "Enter")))
        await page.wait_for_load_state("domcontentloaded")
    await page.evaluate(BANNER_JS)
    text = await page.evaluate("() => document.body ? document.body.innerText : ''")
    return {"url": page.url, "title": await page.title(), "text": text[:3000],
            "elements": await page.evaluate(SNAPSHOT_JS)}


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


async def run(a) -> int:
    from playwright.async_api import async_playwright
    cfg = load_config()
    if not cfg.get("token"):
        print("Not paired yet. Send 'pair browser' to Smara in WhatsApp, then run: python munim_bridge.py pair --server ... --code ...")
        return 1
    headers = {"Authorization": f"Bearer {cfg['token']}"}
    async with async_playwright() as p, httpx.AsyncClient(base_url=cfg["server"], headers=headers, timeout=45) as http:
        ctx, exe = await open_browser(p, a)
        await ctx.add_init_script("(" + BANNER_JS + ")()")

        async def guard(route):                       # sub-requests too: no pivoting into your private network
            if not a.allow_private and url_allowed(route.request.url, False):
                await route.abort()
            else:
                await route.continue_()
        await ctx.route("**/*", guard)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        print(f"Smara Browser Bridge running for {cfg.get('shop', 'your shop')} using {exe or 'your attached browser'}. Press Ctrl+C to stop.")
        while True:
            try:
                r = await http.post("/bridge/poll", params={"wait": 25})
                if r.status_code == 401:
                    print("This PC was disconnected from Smara (unpaired). Pair again to continue.")
                    return 1
                job = r.json() if r.status_code == 200 else {}
            except (httpx.TransportError, ValueError):
                await asyncio.sleep(3)
                continue
            if not job:
                continue
            try:
                result = await handle(page, job["command"], a.allow_private)
            except Exception as e:                    # a page error must never kill the bridge
                result = {"error": f"{type(e).__name__}: {str(e)[:300]}"}
            await http.post("/bridge/result", json={"job_id": job["job_id"], "result": result})


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
    rp.add_argument("--allow-private", action="store_true", help="allow localhost / private network pages (off by default)")
    sub.add_parser("status")
    sub.add_parser("unpair")
    a = ap.parse_args()
    if a.cmd == "pair":
        return cmd_pair(a)
    if a.cmd == "status":
        cfg = load_config()
        print(f"Paired with {cfg['shop']} at {cfg['server']}" if cfg.get("token") else "Not paired.")
        return 0
    if a.cmd == "unpair":
        CONFIG.unlink(missing_ok=True)
        print("Forgot this pairing. (Send 'unpair browser' in chat to revoke it on the server too.)")
        return 0
    try:
        return asyncio.run(run(a))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
