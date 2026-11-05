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
    if isinstance(cmd.get("selector"), str):               # models sometimes send JSON-escaped quotes: div[role=\\"x\\"]
        cmd = {**cmd, "selector": cmd["selector"].replace('\\"', '"').replace("\\'", "'").strip()}
    if a not in ("navigate", "read", "scroll", "back", "wait", "click", "fill", "select", "press"):
        return {"error": f"unsupported action {a!r}"}
    if await page.evaluate("() => !!window.__smaraPaused"):
        return {"error": "the owner paused Smara in the browser (Stop button); ask them to press Resume"}
    if a == "navigate":
        why = url_allowed(str(cmd.get("url", "")), allow_private)
        if why:
            return {"error": f"blocked url: {why}"}
        await page.goto(cmd["url"], wait_until="domcontentloaded", timeout=30000)
        await settle(page)
    elif a == "click":
        await smart_click(page, str(cmd["selector"]))
    elif a == "fill":
        info = await page.locator(str(cmd["selector"])).first.evaluate(SECRET_TYPES_JS, timeout=10000)
        if info["type"] == "password" or "one-time-code" in info["ac"] or any(
                k in info["name"] for k in ("otp", "pin", "cvv", "card", "passw")):
            return {"error": "I never type into password, OTP, PIN or card fields; please do that step yourself in the browser"}
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
    await page.evaluate(BANNER_JS)
    text = await page.evaluate("() => document.body ? document.body.innerText : ''")
    for banner in ("Smara is working on this page\nStop", "Smara is paused\nResume"):
        text = text.replace(banner, "")
    out = {"url": page.url, "title": await page.title(), "text": text[:3000], "elements": await page.evaluate(SNAPSHOT_JS)}
    if needs := await page.evaluate(SIGNALS_JS):
        out["needs_owner"] = needs
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
        print(msg, flush=True)


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
