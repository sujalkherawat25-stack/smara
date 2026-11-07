"""The owned browser for Smara Desktop chat: the same engine the WhatsApp agent uses (munim_bridge).

Playwright runs on a private asyncio loop in its own thread; the synchronous methods here wrap it, so the MCP server
stays synchronous. Pages are read and acted on through munim_bridge: numbered visible controls with names that say what
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

from . import munim_bridge as mb


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


class ManagedBrowser:
    def __init__(self, artifact_root: Path, executable_path: str | None = None, headless: bool = True):
        self.root = Path(artifact_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.executable = executable_path or self._find()
        self.headless = headless
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
            self.browser = await self.runtime.chromium.launch(**options)
        except Exception as exc:
            raise BrowserUnavailable(f"no supported Chromium could start ({type(exc).__name__})") from exc

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
        await page.screenshot(path=str(screenshot), full_page=False)
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
        if session is not None:
            await session.context.close()

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
