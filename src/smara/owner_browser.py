"""The owner's own browser (Brave, Chrome or Edge) as Smara's browser, through the Smara extension.

Why an extension: since Chromium 136 a browser refuses outside automation of its real profile, and copying sign-in cookies out
of a profile is both fragile and unsafe. The Smara extension, which the owner adds once, works from inside the browser instead:
Smara opens its own tabs there (in a "Smara" tab group the owner can watch), with the owner's sign-ins, and never touches the
owner's other tabs.

How the pieces meet:
  * Each browser helper (native_browser, one per chat or job) runs a Relay: a WebSocket server on 127.0.0.1 with a random
    token, announced in a file under Smara's state folder. Playwright connects to it as if it were a browser's debugging port,
    so the same reading, clicking, approvals and logins work as in Smara's own browser.
  * The browser starts Smara's native messaging host (this file's `host`) when the extension loads. The host tells the
    extension which relays exist; the extension connects to each and carries debugger commands for the tabs that relay opened.
  * Only the extension with Smara's fixed id may start the host (allowed_origins), and only processes of the same Windows user
    can read the relay files. The extension itself refuses commands for any tab Smara did not open.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import struct
import sys
import threading
import time
import uuid
from pathlib import Path

HOST_NAME = "com.smara.desktop"
EXTENSION_ID = "kgbeddkmlpkbokpnifggegjhehlcmkan"      # fixed by the public key in the extension's manifest
# Where each browser looks for native messaging hosts (current user only; no admin rights needed).
REGISTRY_KEYS = {
    "Brave": r"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts",
    "Chrome": r"Software\Google\Chrome\NativeMessagingHosts",
    "Edge": r"Software\Microsoft\Edge\NativeMessagingHosts",
    "Chromium": r"Software\Chromium\NativeMessagingHosts",
}
OWNER_BROWSERS = {   # what the owner can pick, in the order Smara suggests them
    "brave": [r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe",
              r"%ProgramFiles(x86)%\BraveSoftware\Brave-Browser\Application\brave.exe",
              r"%LocalAppData%\BraveSoftware\Brave-Browser\Application\brave.exe"],
    "chrome": [r"%ProgramFiles%\Google\Chrome\Application\chrome.exe", r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
               r"%LocalAppData%\Google\Chrome\Application\chrome.exe"],
    "edge": [r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe", r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"],
}
EXTENSIONS_PAGE = {"brave": "brave://extensions", "chrome": "chrome://extensions", "edge": "edge://extensions"}
CONNECT_SECONDS = 25        # how long a task waits for the owner's browser (it may have to start first)


def state_dir() -> Path:
    from .native_profiles import state_path
    return state_path().parent


def relays_dir() -> Path:
    return state_dir() / "browser-relays"


def heartbeat_path() -> Path:
    return state_dir() / "browser-extension.json"


def installed_browsers() -> dict[str, str]:
    found = {}
    for name, candidates in OWNER_BROWSERS.items():
        for candidate in candidates:
            path = Path(os.path.expandvars(candidate))
            if "%" not in str(path) and path.is_file():
                found[name] = str(path)
                break
    return found


def extension_dir() -> Path:
    """The unpacked extension the owner loads once: shipped beside the app, or in the source tree."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "browser-extension"
    return Path(__file__).resolve().parents[2] / "apps" / "desktop" / "src-tauri" / "resources" / "browser-extension"


def connected(max_age: float = 20.0) -> dict | None:
    """The extension's last heartbeat (written by the host while the browser runs), if it is recent."""
    try:
        beat = json.loads(heartbeat_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return beat if isinstance(beat, dict) and time.time() - float(beat.get("seen", 0)) <= max_age else None


# ---------------------------------------------------------------------------------------------- the relay (per helper)
class OwnerBrowserUnavailable(RuntimeError):
    pass


class Relay:
    """A WebSocket server that looks like a browser's debugging endpoint to Playwright and forwards to the extension."""

    def __init__(self, label: str = "Smara"):
        self.id = uuid.uuid4().hex
        self.token = secrets.token_urlsafe(32)
        self.label = label[:60]
        self.server = None
        self.port = 0
        self.extension = None                   # the extension's socket
        self.extension_ready = asyncio.Event()
        self.playwright = None                  # Playwright's socket
        self.pending: dict[int, asyncio.Future] = {}
        self.next_id = 0
        self.tabs: dict[int, dict] = {}         # tabId -> {"session": root session id, "target": targetInfo}
        self.children: dict[str, int] = {}      # child session id (iframes, workers) -> tabId
        self.path = relays_dir() / f"{self.id}.json"

    async def start(self) -> str:
        from websockets.asyncio.server import serve
        self.server = await serve(self._accept, "127.0.0.1", 0, max_size=64 * 1024 * 1024, ping_interval=None)
        self.port = self.server.sockets[0].getsockname()[1]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps({"id": self.id, "port": self.port, "token": self.token, "pid": os.getpid(),
                                    "label": self.label, "created": time.time()}), encoding="utf-8")
        os.replace(temp, self.path)
        return f"ws://127.0.0.1:{self.port}/cdp/{self.token}"

    async def wait_for_extension(self, seconds: float) -> bool:
        try:
            await asyncio.wait_for(self.extension_ready.wait(), seconds)
            return True
        except asyncio.TimeoutError:
            return False

    async def close(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass
        if self.extension is not None:
            try:
                await self.extension.send(json.dumps({"type": "release"}))
            except Exception:
                pass
        if self.server is not None:
            self.server.close()

    # ---------------------------------------------------------------- connections
    async def _accept(self, socket):
        path = socket.request.path
        if path == f"/extension/{self.token}":
            if self.extension is not None:
                # The extension may be in more than one browser (Brave and Chrome): a task works in one, the first to answer.
                await socket.close(1013, "another browser has this task")
                return
            self.extension = socket
            self.extension_ready.set()
            try:
                async for raw in socket:
                    await self._from_extension(json.loads(raw))
            finally:
                if self.extension is socket:
                    self.extension = None
                    self.extension_ready.clear()
                    for future in self.pending.values():
                        if not future.done():
                            future.set_exception(OwnerBrowserUnavailable("The browser extension disconnected"))
                    for tab in list(self.tabs):
                        await self._detached(tab)
        elif path == f"/cdp/{self.token}":
            self.playwright = socket
            async for raw in socket:
                asyncio.ensure_future(self._from_playwright(json.loads(raw)))
        else:
            await socket.close(1008, "unknown path")

    async def _ask(self, message: dict, timeout: float = 60):
        if self.extension is None:
            raise OwnerBrowserUnavailable("Your browser is not connected to Smara")
        self.next_id += 1
        message["id"] = self.next_id
        future = asyncio.get_running_loop().create_future()
        self.pending[self.next_id] = future
        await self.extension.send(json.dumps(message))
        try:
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(message["id"], None)

    async def _to_playwright(self, message: dict) -> None:
        if self.playwright is not None:
            try:
                await self.playwright.send(json.dumps(message))
            except Exception:
                pass

    # ---------------------------------------------------------------- extension -> Playwright
    async def _from_extension(self, message: dict) -> None:
        kind = message.get("type")
        if kind == "reply":
            future = self.pending.get(message.get("id"))
            if future and not future.done():
                if "error" in message:
                    future.set_exception(RuntimeError(str(message["error"])[:300]))
                else:
                    future.set_result(message.get("result"))
        elif kind == "event":
            tab = message.get("tabId")
            if tab not in self.tabs:
                return
            method, params, child = message.get("method"), message.get("params") or {}, message.get("sessionId")
            if method == "Target.attachedToTarget" and params.get("sessionId"):
                self.children[params["sessionId"]] = tab
            elif method == "Target.detachedFromTarget" and params.get("sessionId"):
                self.children.pop(params["sessionId"], None)
            await self._to_playwright({"method": method, "params": params, "sessionId": child or self.tabs[tab]["session"]})
        elif kind == "tab":                       # a tab one of Smara's tabs opened (a popup or target=_blank link)
            await self._attached(message["tabId"], message["targetInfo"])
        elif kind == "detached":
            await self._detached(message.get("tabId"))

    async def _attached(self, tab: int, target: dict) -> None:
        session = f"smara-tab-{tab}"
        target = {**target, "attached": True, "type": "page", "browserContextId": target.get("browserContextId") or "smara-default"}
        self.tabs[tab] = {"session": session, "target": target}
        await self._to_playwright({"method": "Target.attachedToTarget",
                                   "params": {"sessionId": session, "targetInfo": target, "waitingForDebugger": False}})

    async def _detached(self, tab) -> None:
        entry = self.tabs.pop(tab, None)
        if entry:
            for child in [key for key, owner in self.children.items() if owner == tab]:
                self.children.pop(child, None)
            await self._to_playwright({"method": "Target.detachedFromTarget",
                                       "params": {"sessionId": entry["session"], "targetId": entry["target"]["targetId"]}})

    # ---------------------------------------------------------------- Playwright -> extension
    async def _from_playwright(self, message: dict) -> None:
        ident, method, params, session = message.get("id"), message.get("method", ""), message.get("params") or {}, message.get("sessionId")
        try:
            if session:
                result = await self._page_command(session, method, params)
            else:
                result = await self._browser_command(method, params)
            await self._to_playwright({"id": ident, "sessionId": session, "result": result or {}} if session else {"id": ident, "result": result or {}})
        except Exception as exc:
            reply = {"id": ident, "error": {"code": -32000, "message": str(exc)[:300] or type(exc).__name__}}
            if session:
                reply["sessionId"] = session
            await self._to_playwright(reply)

    async def _browser_command(self, method: str, params: dict):
        """Browser-wide commands. An extension can only reach its own tabs, so these are answered here."""
        if method == "Browser.getVersion":
            return {"protocolVersion": "1.3", "product": "Chrome/Smara-Extension", "revision": "", "userAgent": "Smara", "jsVersion": ""}
        if method == "Target.createTarget":
            opened = await self._ask({"type": "open", "url": params.get("url") or "about:blank", "label": self.label})
            await self._attached(opened["tabId"], opened["targetInfo"])
            return {"targetId": opened["targetInfo"]["targetId"]}
        if method == "Target.closeTarget":
            tab = next((key for key, entry in self.tabs.items() if entry["target"]["targetId"] == params.get("targetId")), None)
            if tab is not None:
                await self._ask({"type": "close", "tabId": tab})
            return {"success": tab is not None}
        if method == "Target.getTargetInfo":
            first = next(iter(self.tabs.values()), None)
            return {"targetInfo": first["target"] if first else {"targetId": "smara-browser", "type": "browser", "title": "", "url": "",
                                                                  "attached": True, "canAccessOpener": False}}
        if method == "Target.getTargets":
            return {"targetInfos": [entry["target"] for entry in self.tabs.values()]}
        if method == "Browser.close":
            if self.extension is not None:
                await self._ask({"type": "release"}, timeout=10)
            return {}
        # Target.setAutoAttach / setDiscoverTargets, Browser.setDownloadBehavior, permissions and the like: the owner's browser
        # keeps its own settings, and Smara's tabs are announced as they open.
        return {}

    async def _page_command(self, session: str, method: str, params: dict):
        tab = next((key for key, entry in self.tabs.items() if entry["session"] == session), None)
        child = None
        if tab is None:
            tab, child = self.children.get(session), session
        if tab is None:
            raise RuntimeError("That tab is closed")
        return await self._ask({"type": "cdp", "tabId": tab, "sessionId": child, "method": method, "params": params})


# ---------------------------------------------------------------------------------------------- the native messaging host
def _read_message(stream):
    header = stream.read(4)
    if len(header) < 4:
        return None
    (length,) = struct.unpack("<I", header)
    if length > 1024 * 1024:
        return None
    return json.loads(stream.read(length).decode("utf-8"))


def _write_message(stream, value) -> None:
    data = json.dumps(value).encode("utf-8")
    stream.write(struct.pack("<I", len(data)) + data)
    stream.flush()


def live_relays() -> list[dict]:
    import psutil
    found = []
    folder = relays_dir()
    if not folder.is_dir():
        return found
    for path in folder.glob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if psutil.pid_exists(int(value["pid"])):
                found.append({key: value[key] for key in ("id", "port", "token", "label")})
            elif time.time() - path.stat().st_mtime > 60:
                path.unlink()                          # left by a helper that crashed
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(found, key=lambda value: value["id"])


def host(stdin=None, stdout=None) -> int:
    """Started by the browser for the Smara extension: tells it which Smara tasks want a browser, until the browser closes."""
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    hello = {}
    done = threading.Event()

    def listen():
        nonlocal hello
        while True:
            try:
                message = _read_message(stdin)
            except (OSError, ValueError):
                message = None
            if message is None:
                done.set()
                return
            if isinstance(message, dict) and message.get("type") == "hello":
                hello = {"browser": str(message.get("browser", ""))[:200], "version": str(message.get("version", ""))[:20]}

    threading.Thread(target=listen, daemon=True).start()
    sent, beat = None, 0.0
    while True:                                   # at least one pass, so the extension always hears the current list
        relays = live_relays()
        if relays != sent:
            _write_message(stdout, {"type": "relays", "relays": relays})
            sent = relays
        if time.time() - beat >= 5:
            beat = time.time()
            try:
                heartbeat_path().parent.mkdir(parents=True, exist_ok=True)
                heartbeat_path().write_text(json.dumps({**hello, "seen": beat, "pid": os.getpid()}), encoding="utf-8")
            except OSError:
                pass
        if done.wait(0.4):
            return 0


# ---------------------------------------------------------------------------------------------- one-time setup
def host_command() -> Path:
    """What the browser starts: the installed Smara (frozen), or a small launcher for a source checkout."""
    folder = state_dir() / "native-host"
    folder.mkdir(parents=True, exist_ok=True)
    if getattr(sys, "frozen", False):
        from .smara_bridge import real_install_path
        return real_install_path(Path(sys.executable).resolve())
    launcher = folder / "smara-browser-host.cmd"
    source = Path(__file__).resolve().parents[1]
    launcher.write_text(f'@echo off\r\nset "PYTHONPATH={source}"\r\n"{sys.executable}" -m smara.owner_browser %*\r\n', encoding="utf-8")
    return launcher


def register() -> dict:
    """Let Brave, Chrome and Edge start Smara's host for the Smara extension (current user only)."""
    manifest = state_dir() / "native-host" / f"{HOST_NAME}.json"
    command = host_command()
    manifest.write_text(json.dumps({"name": HOST_NAME, "description": "Smara Desktop", "path": str(command), "type": "stdio",
                                    "allowed_origins": [f"chrome-extension://{EXTENSION_ID}/"]}, indent=2), encoding="utf-8")
    registered = []
    if sys.platform == "win32":
        import winreg
        for name, key in REGISTRY_KEYS.items():
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key + "\\" + HOST_NAME) as handle:
                winreg.SetValueEx(handle, "", 0, winreg.REG_SZ, str(manifest))
            registered.append(name)
    return {"manifest": str(manifest), "command": str(command), "browsers": registered}


# ---------------------------------------------------------------------------------------------- the owner's choice (Settings)
def choice() -> dict:
    """{"use": bool, "browser": "brave"|"chrome"|"edge", "path": exe or ""}: whether Smara works in the owner's own browser."""
    from .native_management import settings_path
    from .native_profiles import read_object
    saved = read_object(settings_path()).get("owner_browser") or {}
    browsers = installed_browsers()
    name = saved.get("browser") if saved.get("browser") in browsers else next(iter(browsers), "")
    return {"use": bool(saved.get("use")) and bool(name), "browser": name, "path": browsers.get(name, "")}


def manage(request: dict) -> dict:
    """Settings > Browser: status, set up (register the host, show the extension folder), choose, open the extensions page."""
    from .native_management import management_lock, settings_path
    from .native_profiles import read_object, write_object
    action = request.get("action", "status")
    browsers = installed_browsers()
    if action == "save":
        name = request.get("browser")
        if name not in browsers:
            raise ValueError("Choose a browser installed on this PC")
        use = request.get("use")
        if type(use) is not bool:
            raise ValueError("Choose whether Smara uses your own browser")
        if use:
            register()
        with management_lock():
            data = read_object(settings_path())
            data["owner_browser"] = {"use": use, "browser": name}
            write_object(settings_path(), data)
    elif action == "setup":
        register()
    elif action == "open_extensions":
        name = request.get("browser")
        if name not in browsers:
            raise ValueError("Choose a browser installed on this PC")
        import subprocess
        subprocess.Popen([browsers[name], EXTENSIONS_PAGE[name]])
    elif action == "open_folder":
        import subprocess
        subprocess.Popen(["explorer.exe", str(extension_dir())])
    elif action != "status":
        raise ValueError("Unsupported browser setting")
    beat = connected()
    return {**choice(), "installed": sorted(browsers), "extension_folder": str(extension_dir()), "extension_id": EXTENSION_ID,
            "connected": bool(beat), "connected_browser": (beat or {}).get("browser", "")}


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--register":
        print(json.dumps(register()))
        return 0
    return host()


if __name__ == "__main__":
    raise SystemExit(main())
