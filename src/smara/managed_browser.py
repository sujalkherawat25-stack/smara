"""The owned browser for Smara Desktop chat: the same engine the WhatsApp agent uses (smara_bridge).

Playwright runs on a private asyncio loop in its own thread; the synchronous methods here wrap it, so the MCP server
stays synchronous. Pages are read and acted on through smara_bridge: numbered visible controls with names that say what
they are for, a report of what each step changed, dialogs first, real pointer clicks, and no typing into secret fields.
"""
from __future__ import annotations

import asyncio
import hashlib
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import smara_bridge as mb


class BrowserUnavailable(RuntimeError):
    pass


class StaleObservation(RuntimeError):
    pass


@dataclass
class BrowserSession:
    id: str
    context: Any
    pages: dict[str, Any] = field(default_factory=dict)
    active_tab: str = ""
    generation: int = 0
    observations: dict[str, dict] = field(default_factory=dict)
    dialogs: list[dict] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)


def _running(executable: str) -> bool:
    import psutil
    name = Path(executable).name.lower()
    for process in psutil.process_iter(["name"]):
        if (process.info.get("name") or "").lower() == name:
            return True
    return False


class ManagedBrowser:
    def __init__(self, artifact_root: Path, executable_path: str | None = None, headless: bool = True,
                 profile_dir: Path | None = None, owner_browser: str | None = None, label: str = "Smara"):
        # owner_browser: work in the owner's own browser (this exe) through the Smara extension, with their sign-ins.
        # If it does not answer, Smara's own browser is used and `note` says why.
        self.owner_browser = owner_browser
        self.label = label
        self.relay = None
        self.note = ""
        self.root = Path(artifact_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.executable = executable_path or self._find()
        self.headless = headless
        # A profile folder keeps sign-ins between runs (Smara's own profile, never the owner's personal one). Only one
        # program can hold a profile at a time; when it is busy this browser starts fresh instead of failing.
        self.profile_dir = Path(profile_dir) if profile_dir else None
        self.shared = None
        self.runtime = None
        self.browser = None
        self.sessions: dict[str, BrowserSession] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    @staticmethod
    def _find() -> str | None:
        # The finder the WhatsApp helper uses: an installed Chrome, Edge or Brave. None means Playwright's own build.
        return None if sys.platform.startswith("linux") else mb.find_browser("auto")

    # ------------------------------------------------------------------ the loop and the synchronous facade
    def _call(self, coro, timeout: float = 150):
        if self._loop is None:
            loop = asyncio.ProactorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
            threading.Thread(target=loop.run_forever, name="smara-owned-browser", daemon=True).start()
            self._loop = loop
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    async def _start(self) -> None:
        from playwright.async_api import async_playwright
        options: dict[str, Any] = {"headless": self.headless, "args": ["--disable-dev-shm-usage", "--disable-gpu"]}
        if self.executable:
            options["executable_path"] = self.executable
        try:
            self.runtime = await async_playwright().start()
            if self.owner_browser is not None:
                try:
                    await self._connect_owner()
                    return
                except Exception as exc:
                    if self.relay is not None:
                        await self.relay.close()
                        self.relay = None
                    self.note = (f"Your own browser did not connect ({str(exc)[:160]}), so this is running in Smara's own browser. "
                                 "Check that the Smara extension is on in your browser (Smara Desktop > Settings > Browser).")
            if self.profile_dir is not None:
                try:
                    self.profile_dir.mkdir(parents=True, exist_ok=True)
                    self.shared = await self.runtime.chromium.launch_persistent_context(
                        str(self.profile_dir), accept_downloads=True, **options)
                    self.browser = self.shared
                    return
                except Exception:
                    self.shared = None                 # profile in use by another Smara task: a fresh browser
            self.browser = await self.runtime.chromium.launch(**options)
        except Exception as exc:
            raise BrowserUnavailable(f"no supported Chromium could start ({type(exc).__name__})") from exc

    async def _connect_owner(self) -> None:
        from . import owner_browser as ob
        self.relay = ob.Relay(self.label)
        endpoint = await self.relay.start()
        if not await self.relay.wait_for_extension(3):
            if self.owner_browser and not _running(self.owner_browser):
                import subprocess
                subprocess.Popen([self.owner_browser])     # opens the owner's browser as they would; the extension then answers
            if not await self.relay.wait_for_extension(ob.CONNECT_SECONDS):
                raise ob.OwnerBrowserUnavailable("the Smara extension did not answer")
        self.browser = await self.runtime.chromium.connect_over_cdp(endpoint)
        self.shared = self.browser.contexts[0]

    def start(self) -> None:
        if self.browser is None:
            self._call(self._start())

    # ------------------------------------------------------------------ sessions and tabs
    def create(self, context_options=None, configure_context=None) -> str:
        return self._call(self._create(context_options, configure_context))

    async def _create(self, context_options, configure_context) -> str:
        if self.browser is None:
            await self._start()
        ident = f"browser_{uuid.uuid4().hex[:20]}"
        if self.shared is not None:
            # The profile's one context: configure it once; each session gets its own fresh tab in it.
            context = self.shared
            if configure_context and not getattr(context, "_smara_configured", False):
                configured = configure_context(context)
                if asyncio.iscoroutine(configured):
                    await configured
                context._smara_configured = True
            session = BrowserSession(ident, context)
            if not getattr(context, "_smara_listening", False):
                context.on("page", lambda page: self._attach_to_owner(page))
                context._smara_listening = True
            blank = [p for p in context.pages if p.url in ("about:blank", "") and not self._owned(p)]
            # Reuse a blank tab rather than closing it: in a visible window, closing its only tab closes the window, and the
            # browser then cannot open a new tab.
            for stale in blank[1:]:
                await stale.close()
            self.sessions[ident] = session
            self._attach(session, blank[0] if blank else await context.new_page())
            return ident
        context = await self.browser.new_context(**(context_options or {"accept_downloads": True}))
        try:
            if configure_context:
                configured = configure_context(context)
                if asyncio.iscoroutine(configured):
                    await configured
        except Exception:
            await context.close()
            raise
        session = BrowserSession(ident, context)
        context.on("page", lambda page: self._attach(session, page))
        self._attach(session, await context.new_page())
        self.sessions[ident] = session
        return ident

    def _owned(self, page) -> bool:
        return any(existing is page for session in self.sessions.values() for existing in session.pages.values())

    def _attach_to_owner(self, page) -> None:
        """A tab a page opened in the shared profile belongs to the newest open session."""
        if self.sessions:
            self._attach(list(self.sessions.values())[-1], page)

    def _attach(self, session: BrowserSession, page) -> None:
        if any(existing is page for existing in session.pages.values()):
            return
        tab = f"tab_{uuid.uuid4().hex[:16]}"
        session.pages[tab] = page
        session.active_tab = tab
        session.generation += 1
        page.set_default_timeout(15000)
        page.on("dialog", lambda dialog: asyncio.ensure_future(self._dialog(session, dialog)))

    async def _dialog(self, session: BrowserSession, dialog) -> None:
        session.dialogs.append({"type": dialog.type, "message": dialog.message})
        await dialog.dismiss()

    def page(self, session: BrowserSession):
        return session.pages[session.active_tab]

    def tabs(self, session_id: str) -> list[dict]:
        return self._call(self._tabs(session_id))

    async def _tabs(self, session_id: str) -> list[dict]:
        return [{"tab_id": key, "url": page.url} for key, page in self.sessions[session_id].pages.items()]

    def switch(self, session_id: str, tab_id: str) -> dict:
        return self._call(self._switch(session_id, tab_id))

    async def _switch(self, session_id: str, tab_id: str) -> dict:
        session = self.sessions[session_id]
        if tab_id not in session.pages:
            raise KeyError(tab_id)
        session.active_tab = tab_id
        session.generation += 1
        return await self._observe(session_id)

    def current_url(self, session_id: str) -> str:
        return self._call(self._current_url(session_id))

    async def _current_url(self, session_id: str) -> str:
        return self.page(self.sessions[session_id]).url

    def cookies(self, session_id: str) -> list:
        return self._call(self._cookies(session_id))

    async def _cookies(self, session_id: str) -> list:
        return await self.sessions[session_id].context.cookies()

    # ------------------------------------------------------------------ navigation and reading
    def navigate(self, session_id: str, url: str) -> dict:
        return self._call(self._navigate(session_id, url))

    async def _navigate(self, session_id: str, url: str) -> dict:
        session = self.sessions[session_id]
        page = self.page(session)
        await page.goto(url, wait_until="domcontentloaded")
        await mb.settle(page, max_ms=6000)
        session.generation += 1
        return await self._observe(session_id)

    def observe(self, session_id: str) -> dict:
        return self._call(self._observe(session_id))

    async def _observe(self, session_id: str) -> dict:
        session = self.sessions[session_id]
        page = self.page(session)
        session.generation += 1
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        snap = await mb.snapshot(page)                       # numbered controls on the page and in its frames
        elements = {}
        for ref, item in snap["items"].items():
            entry = {"role": item["role"], "name": item["name"], "text": item["name"], "region": item["region"]}
            for flag in ("value", "disabled", "checked", "required", "invalid", "expanded", "selected"):
                if flag in item:
                    entry[flag] = item[flag]
            elements[str(ref)] = entry
        body_text = ""
        try:
            if await page.locator("body").count():
                body_text = await page.locator("body").inner_text()
        except Exception:
            pass
        ident = f"obs_{uuid.uuid4().hex[:20]}"
        screenshot = self.root / f"{ident}.png"
        try:
            await page.screenshot(path=str(screenshot), full_page=False, timeout=8000)
        except Exception:
            if self.relay is None:
                raise
            # In the owner's browser a tab they moved to the background may not paint; reading the page still works.
            screenshot.write_bytes(b"")
        observation = {"observation_id": ident, "session_id": session_id, "tab_id": session.active_tab,
                       "url": page.url, "title": await page.title(), "timestamp": time.time(),
                       "generation": session.generation, "elements": elements, "listing": mb.render(snap),
                       "total_controls": snap["total"], "body_text": body_text,
                       "screenshot": str(screenshot), "screenshot_sha256": hashlib.sha256(screenshot.read_bytes()).hexdigest()}
        session.observations[ident] = observation
        return observation

    def _bound(self, session: BrowserSession, observation_id: str) -> dict:
        observation = session.observations.get(observation_id)
        if not observation or observation["generation"] != session.generation or observation["tab_id"] != session.active_tab:
            raise StaleObservation("browser observation is stale")
        return observation

    # ------------------------------------------------------------------ a login the owner saved, or a code they gave
    def login(self, session_id: str, observation_id: str, fills: list[tuple[str, str]], submit_ref: str | None = None) -> dict:
        """Type each (ref, value) into its field and optionally press the submit control. The values never appear in the
        action log or the returned observation."""
        return self._call(self._login(session_id, observation_id, fills, submit_ref))

    async def _login(self, session_id, observation_id, fills, submit_ref) -> dict:
        session = self.sessions[session_id]
        self._bound(session, observation_id)
        page = self.page(session)
        attempt = {"observation_id": observation_id, "ref": ",".join(str(r) for r, _ in fills), "action": "login",
                   "started_at": time.time(), "status": "attempted"}
        session.actions.append(attempt)
        try:
            for ref, value in fills:
                await mb.fill_secret(page, str(ref), value)
            if submit_ref:
                out = await mb.handle(page, {"action": "click", "ref": str(submit_ref)}, allow_private=False)
                if out.get("error"):
                    raise ValueError(str(out["error"]))
        except Exception as err:
            attempt.update(status="failed", error=type(err).__name__, finished_at=time.time())
            raise ValueError("The login fields could not be filled; observe again and retry, or hand off to the owner") from None
        attempt.update(status="completed", finished_at=time.time())
        session.generation += 1
        return await self._observe(session_id)

    # ------------------------------------------------------------------ actions on an observed control
    def act(self, session_id: str, observation_id: str, ref: str, action: str, value: str | None = None) -> dict:
        return self._call(self._act(session_id, observation_id, ref, action, value))

    async def _act(self, session_id: str, observation_id: str, ref: str, action: str, value=None) -> dict:
        session = self.sessions[session_id]
        self._bound(session, observation_id)
        verb = {"check": "click"}.get(action, action)        # a checkbox or radio is toggled by a click
        if verb not in {"click", "fill", "select"}:
            raise ValueError("unsupported browser action")
        cmd: dict[str, Any] = {"action": verb, "ref": str(ref)}
        if verb in {"fill", "select"}:
            cmd["value"] = "" if value is None else str(value)
        attempt = {"observation_id": observation_id, "ref": ref, "action": action, "started_at": time.time(), "status": "attempted"}
        session.actions.append(attempt)
        out = await mb.handle(self.page(session), cmd, allow_private=False)
        if out.get("error"):
            attempt.update(status="failed", error=str(out["error"])[:200], finished_at=time.time())
            raise ValueError(str(out["error"]))
        attempt.update(status="completed", finished_at=time.time())
        session.generation += 1
        observed = await self._observe(session_id)
        for key in ("changes", "changed", "note", "opened_new_tab"):   # what the step did, as the owner's agent sees it
            if key in out:
                observed[key] = out[key]
        return observed

    def attribute(self, session_id: str, observation_id: str, ref: str, name: str):
        return self._call(self._attribute(session_id, observation_id, ref, name))

    async def _attribute(self, session_id: str, observation_id: str, ref: str, name: str):
        session = self.sessions[session_id]
        self._bound(session, observation_id)
        locator, _ = await self._locate(session, ref)
        return await locator.get_attribute(name)

    def upload(self, session_id: str, observation_id: str, ref: str, path: Path) -> dict:
        return self._call(self._upload(session_id, observation_id, ref, path))

    async def _upload(self, session_id: str, observation_id: str, ref: str, path: Path) -> dict:
        session = self.sessions[session_id]
        self._bound(session, observation_id)
        locator, _ = await self._locate(session, ref)
        await locator.set_input_files(str(path))
        session.generation += 1
        return await self._observe(session_id)

    def download(self, session_id: str, observation_id: str, ref: str) -> dict:
        return self._call(self._download(session_id, observation_id, ref))

    async def _download(self, session_id: str, observation_id: str, ref: str) -> dict:
        session = self.sessions[session_id]
        self._bound(session, observation_id)
        page = self.page(session)
        locator, _ = await self._locate(session, ref)
        async with page.expect_download(timeout=15000) as pending:
            await locator.click()
        download = await pending.value
        target = self.root / (uuid.uuid4().hex + "-" + download.suggested_filename)
        await download.save_as(str(target))
        session.generation += 1
        return {"path": str(target), "suggested_filename": download.suggested_filename}

    async def _locate(self, session: BrowserSession, ref: str):
        try:
            return await mb.resolve(self.page(session), ref)
        except LookupError as exc:
            raise ValueError(str(exc)) from exc

    def scroll(self, session_id: str, dy: int = 600) -> dict:
        return self._call(self._scroll(session_id, dy))

    async def _scroll(self, session_id: str, dy: int) -> dict:
        session = self.sessions[session_id]
        page = self.page(session)
        await page.mouse.wheel(0, dy)
        await asyncio.sleep(0.3)
        await mb.settle(page, max_ms=3000)                   # long lists load more as you scroll
        session.generation += 1
        return await self._observe(session_id)

    # ------------------------------------------------------------------ closing
    def close(self, session_id: str) -> None:
        self._call(self._close(session_id))

    async def _close(self, session_id: str) -> None:
        session = self.sessions.pop(session_id, None)
        if session is None:
            return
        if session.context is self.shared and self.relay is not None:
            # In the owner's own browser the tabs stay open, so the owner can see where Smara stopped; Smara just lets go.
            if not self.sessions:
                await self._release_owner()
            return
        if session.context is self.shared:
            for page in list(session.pages.values()):      # the shared profile stays open; only this session's tabs close
                if not page.is_closed():
                    await page.close()
            if not self.sessions:
                await self.shared.close()                   # last session: release the profile for other Smara tasks
                self.shared = self.browser = None
            return
        await session.context.close()

    async def _release_owner(self) -> None:
        try:
            if self.browser is not None:
                await self.browser.close()                  # disconnects; the owner's browser keeps running
        except Exception:
            pass
        self.shared = self.browser = None
        if self.relay is not None:
            await self.relay.close()
            self.relay = None

    def cancel(self, session_id: str) -> None:
        self.close(session_id)

    def invalidate_after_restart(self) -> list[str]:
        invalidated = list(self.sessions)
        for ident in invalidated:
            self.close(ident)
        return invalidated

    def shutdown(self) -> None:
        if self._loop is None:
            return
        self._call(self._shutdown())
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._loop = None

    async def _shutdown(self) -> None:
        for ident in list(self.sessions):
            await self._close(ident)
        if self.relay is not None:
            await self._release_owner()
        if self.shared is not None:
            await self.shared.close()
            self.shared = self.browser = None
        if self.browser is not None:
            await self.browser.close()
            self.browser = None
        if self.runtime is not None:
            await self.runtime.stop()
            self.runtime = None

    # ------------------------------------------------------------------ reading state and waiting (owner and test helpers)
    def evaluate(self, session_id: str, script: str, frame_index: int = 0):
        return self._call(self._evaluate(session_id, script, frame_index))

    async def _evaluate(self, session_id: str, script: str, frame_index: int):
        frames = self.page(self.sessions[session_id]).frames
        return await frames[frame_index].evaluate(script)

    def wait_for(self, session_id: str, selector: str, timeout_ms: int = 5000) -> dict:
        return self._call(self._wait_for(session_id, selector, timeout_ms))

    async def _wait_for(self, session_id: str, selector: str, timeout_ms: int) -> dict:
        session = self.sessions[session_id]
        await self.page(session).locator(selector).wait_for(state="visible", timeout=timeout_ms)
        session.generation += 1
        return await self._observe(session_id)
