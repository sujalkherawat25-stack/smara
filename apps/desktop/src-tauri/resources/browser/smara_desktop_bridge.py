"""Smara Desktop sidecar around the unmodified Smara Browser Bridge (munim_bridge.py, vendored next to this file).

Differences from `munim_bridge.py run`, all so Smara Desktop can manage it:
- the server URL and device token come from the environment (SMARA_BRIDGE_SERVER / SMARA_BRIDGE_TOKEN), because
  Desktop keeps the token in the OS credential store and never writes it to a file;
- every step is reported as one JSON line on stdout (an event feed for the UI);
- stdin accepts JSON lines: {"cmd": "pause"} / {"cmd": "resume"} / {"cmd": "stop"};
- all safety checks (private-network block, no password/OTP/card typing, on-page Stop banner) are the vendored
  bridge's own code and are not re-implemented here.
"""
import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import httpx  # noqa: E402
import munim_bridge as mb  # noqa: E402

SYNC_PAUSE_JS = """(paused) => {
  window.__smaraPaused = !!paused;
  const b = document.getElementById('smara-agent-banner');
  if (!b) return;
  const t = b.querySelector('span'), s = b.querySelector('button');
  if (t) t.textContent = paused ? 'Smara is paused' : 'Smara is working on this page';
  if (s) s.textContent = paused ? 'Resume' : 'Stop';
  b.style.background = paused ? '#b42318' : '#0b5fff';
}"""


def emit(kind: str, **fields) -> None:
    print(json.dumps({"kind": kind, "ts": int(time.time() * 1000), **fields}, default=str), flush=True)


def host_of(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except ValueError:
        return ""


def describe(cmd: dict) -> dict:
    out = {"action": cmd.get("action"), "host": host_of(str(cmd.get("url", "")))}
    for k in ("url", "selector"):
        if cmd.get(k):
            out[k] = str(cmd[k])[:200]
    if cmd.get("value") is not None:
        out["value"] = str(cmd["value"])[:120]
    return out


class Control:
    def __init__(self) -> None:
        self.paused = False
        self.stop = asyncio.Event()
        self.dirty = asyncio.Event()


async def read_stdin(ctl: Control) -> None:
    loop = asyncio.get_running_loop()
    while not ctl.stop.is_set():
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if line == "":                       # Desktop closed the pipe: shut down
            ctl.stop.set()
            return
        try:
            cmd = json.loads(line).get("cmd")
        except ValueError:
            continue
        if cmd in ("pause", "resume"):
            ctl.paused = cmd == "pause"
            ctl.dirty.set()
        elif cmd == "stop":
            ctl.stop.set()


async def page_flags(ctx) -> list:
    flags = []
    for pg in ctx.pages:
        try:
            flags.append(bool(await pg.evaluate("() => !!window.__smaraPaused")))
        except Exception:
            pass
    return flags


async def sync_pause(ctx, ctl: Control) -> None:
    """Keep the on-page banner and the Desktop button in step (either side can pause)."""
    reported, last_page_flag = None, False
    while not ctl.stop.is_set():
        try:
            await asyncio.wait_for(ctl.dirty.wait(), timeout=1.0)
            ctl.dirty.clear()
            for pg in ctx.pages:
                try:
                    await pg.evaluate(SYNC_PAUSE_JS, ctl.paused)
                except Exception:
                    pass
            last_page_flag = ctl.paused
        except asyncio.TimeoutError:
            flag = any(await page_flags(ctx))
            if flag != last_page_flag:               # the banner's own Stop/Resume button was pressed
                ctl.paused = flag
                last_page_flag = flag
        if ctl.paused != reported:
            reported = ctl.paused
            emit("paused", paused=ctl.paused)


async def run(a) -> int:
    from playwright.async_api import async_playwright
    server, token = os.environ.get("SMARA_BRIDGE_SERVER", "").rstrip("/"), os.environ.get("SMARA_BRIDGE_TOKEN", "")
    if not server or not token:
        emit("fatal", message="Not paired: server or device token missing")
        return 2
    ctl = Control()
    args = SimpleNamespace(browser=a.browser, attach=a.attach, profile=a.profile, headless=False)
    async with async_playwright() as p, httpx.AsyncClient(base_url=server, headers={"Authorization": f"Bearer {token}"},
                                                         timeout=45) as http:
        try:
            ctx, exe = await mb.open_browser(p, args)
        except SystemExit as e:
            emit("fatal", message=str(e))
            return 3
        except Exception as e:
            emit("fatal", message=f"Could not open the browser: {type(e).__name__}: {str(e)[:300]}")
            return 3
        await ctx.add_init_script("(" + mb.BANNER_JS + ")()")

        async def guard(route):
            if mb.url_allowed(route.request.url, False):
                await route.abort()
            else:
                await route.continue_()
        await ctx.route("**/*", guard)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        emit("started", browser=exe or a.attach, shop=os.environ.get("SMARA_BRIDGE_SHOP", ""))
        tasks = [asyncio.create_task(read_stdin(ctl)), asyncio.create_task(sync_pause(ctx, ctl))]
        try:
            while not ctl.stop.is_set():
                poll = asyncio.create_task(http.post("/bridge/poll", params={"wait": 20}))
                stopper = asyncio.create_task(ctl.stop.wait())
                await asyncio.wait({poll, stopper}, return_when=asyncio.FIRST_COMPLETED)
                stopper.cancel()
                if not poll.done():                          # Stop pressed during a long poll: leave at once
                    poll.cancel()
                    break
                try:
                    r = poll.result()
                except httpx.TransportError as e:
                    emit("poll", ok=False, status=0, message=type(e).__name__)
                    await asyncio.sleep(3)
                    continue
                if r.status_code == 401:
                    emit("fatal", message="This PC was disconnected from Smara (unpaired). Pair again to continue.", unpaired=True)
                    return 1
                if r.status_code != 200:
                    emit("poll", ok=False, status=r.status_code)
                    await asyncio.sleep(3)
                    continue
                emit("poll", ok=True, status=200)
                try:
                    job = r.json()
                except ValueError:
                    job = {}
                if not job:
                    continue
                cmd = job["command"]
                emit("step", job_id=job["job_id"], **describe(cmd))
                started = time.monotonic()
                if ctl.paused:
                    await page.evaluate(SYNC_PAUSE_JS, True)
                try:
                    result = await mb.handle(page, cmd, False)
                except Exception as e:                       # a page error must never kill the bridge
                    result = {"error": f"{type(e).__name__}: {str(e)[:300]}"}
                emit("result", job_id=job["job_id"], action=cmd.get("action"), ok=not result.get("error"),
                     error=result.get("error"), title=result.get("title", ""), url=result.get("url", ""),
                     host=host_of(result.get("url", "")), ms=int((time.monotonic() - started) * 1000))
                try:
                    await http.post("/bridge/result", json={"job_id": job["job_id"], "result": result})
                except httpx.TransportError as e:
                    emit("poll", ok=False, status=0, message=f"result not delivered: {type(e).__name__}")
        finally:
            for t in tasks:
                t.cancel()
            try:
                await ctx.close()
            except Exception:
                pass
    emit("stopped")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="smara-desktop-bridge")
    ap.add_argument("--browser", default="auto", help="brave | chrome | edge | auto")
    ap.add_argument("--attach", default="", help="CDP URL, e.g. http://127.0.0.1:9222")
    ap.add_argument("--profile", default="", help="dedicated profile folder")
    ap.add_argument("--check", action="store_true", help="report whether Python deps and browsers are available, then exit")
    a = ap.parse_args()
    if a.check:
        try:
            import playwright  # noqa: F401
            have = True
        except ImportError:
            have = False
        emit("check", playwright=have, browsers={n: bool(mb.find_browser(n)) for n in ("brave", "chrome", "edge")})
        return 0
    try:
        code = asyncio.run(run(a))
    except KeyboardInterrupt:
        code = 0
    sys.stdout.flush()
    os._exit(code)           # the stdin reader thread may still be blocked in readline()


if __name__ == "__main__":
    sys.exit(main())
