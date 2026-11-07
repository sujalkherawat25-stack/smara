"""In-app manager of per-session browser workers (see session_worker.py).

A worker is a separate process: Playwright needs a different event loop than
psycopg on Windows, and a crashed or hijacked page cannot touch the app.
Workers are capped, time out per step, and are closed when idle or when the
agent session ends. If a worker is gone when an approved click arrives, the
step fails safely and the agent must navigate again (and ask again).
"""
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class BrowserSessions:
    def __init__(self, max_sessions: int = 3, idle_s: int = 600, step_timeout_s: int = 45):
        self._procs: dict[str, tuple[subprocess.Popen, float]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._pages: dict[str, dict] = {}       # last url/title seen per session, shown on approval cards
        self.max_sessions, self.idle_s, self.step_timeout_s = max_sessions, idle_s, step_timeout_s

    def page(self, sid: str) -> dict:
        return self._pages.get(sid, {})

    def alive(self, sid: str) -> bool:
        entry = self._procs.get(sid)
        return bool(entry) and entry[0].poll() is None

    def _start(self, tenant_id: str | None = None) -> subprocess.Popen:
        extra = ["--tenant", str(tenant_id)] if tenant_id else []
        proc = subprocess.Popen([sys.executable, "-m", "src.box.session_worker", *extra], cwd=ROOT, text=True, bufsize=1,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        if not json.loads(proc.stdout.readline() or "{}").get("ready"):
            proc.kill()
            raise RuntimeError("browser worker failed to start")
        return proc

    def _exchange(self, proc: subprocess.Popen, cmd: dict) -> dict:
        proc.stdin.write(json.dumps(cmd) + "\n")
        proc.stdin.flush()
        return json.loads(proc.stdout.readline() or '{"error": "browser worker exited"}')

    async def step(self, sid: str, args: dict, tenant_id: str | None = None) -> dict:
        self._reap()
        lock = self._locks.setdefault(sid, asyncio.Lock())
        async with lock:
            if not self.alive(sid):
                if args.get("action") != "navigate":
                    return {"error": "no open page: navigate first"}
                if len(self._procs) >= self.max_sessions:
                    return {"error": "too many browser sessions open; try again shortly"}
                self._procs[sid] = (await asyncio.to_thread(self._start, tenant_id), time.monotonic())
            proc = self._procs[sid][0]
            cmd = {k: args[k] for k in ("action", "url", "ref", "text", "selector", "value") if args.get(k) is not None}
            try:
                out = await asyncio.wait_for(asyncio.to_thread(self._exchange, proc, cmd), self.step_timeout_s)
            except asyncio.TimeoutError:
                await self.close(sid)
                return {"error": f"browser step timed out after {self.step_timeout_s}s"}
            self._procs[sid] = (proc, time.monotonic())
            if isinstance(out, dict) and out.get("url"):
                self._pages[sid] = {"url": out["url"], "title": out.get("title", ""),
                                      "page": out.get("page") or self._pages.get(sid, {}).get("page", "")}
            return out

    def _reap(self) -> None:
        now = time.monotonic()
        for sid, (proc, last) in list(self._procs.items()):
            if proc.poll() is not None or now - last > self.idle_s:
                proc.kill()
                self._procs.pop(sid, None)

    async def close(self, sid: str) -> None:
        entry = self._procs.pop(sid, None)
        self._pages.pop(sid, None)
        if entry:
            entry[0].kill()

    async def close_all(self) -> None:
        for sid in list(self._procs):
            await self.close(sid)
