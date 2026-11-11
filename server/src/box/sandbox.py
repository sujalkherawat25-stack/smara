"""App-side client for the Python sandbox (see sandbox/runner.py).

mode=container: write a job into the shared /jobs volume and wait for the
isolated smara_sandbox container to answer. mode=local: run the same runner in
a subprocess on this machine (development and tests only; no isolation).
"""
import asyncio
import json
import re
import shutil
import time
from pathlib import Path
from uuid import uuid4

_NAME = re.compile(r"[^A-Za-z0-9._-]")
_JOB = re.compile(r"^[0-9a-f]{32}$")
MAX_INPUT_BYTES = 100_000_000


def safe_name(name: str) -> str:
    n = _NAME.sub("_", Path(name or "file").name)[:120].lstrip(".")
    return n or "file"


class Sandbox:
    def __init__(self, jobs_dir: str, mode: str = "local"):
        self.jobs, self.mode = Path(jobs_dir), mode
        self.jobs.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return self.mode in ("local", "container")

    async def run(self, code: str, inputs: dict[str, bytes], timeout_s: int = 60) -> dict:
        if not self.enabled:
            return {"error": "the Python sandbox is not enabled on this server"}
        if sum(len(b) for b in inputs.values()) > MAX_INPUT_BYTES:
            return {"error": "input files are too large for the sandbox (100 MB max)"}
        jid = uuid4().hex
        d = self.jobs / jid
        d.mkdir(mode=0o700)                 # job dirs are private: a running job cannot read another's
        (d / "in").mkdir(mode=0o700)
        for name, data in inputs.items():
            (d / "in" / safe_name(name)).write_bytes(data)
        (d / "code.py").write_text(code, encoding="utf-8")
        (d / "job.json").write_text(json.dumps({"timeout_s": timeout_s}), encoding="utf-8")
        (d / "ready").touch()
        if self.mode == "local":
            from sandbox.runner import execute
            result = await asyncio.to_thread(execute, d, isolate=False)
        else:
            result = await self._wait(d, timeout_s + 20)
        return {**result, "job_id": jid}

    async def _wait(self, d: Path, limit_s: int) -> dict:
        deadline = time.monotonic() + limit_s
        while time.monotonic() < deadline:
            f = d / "result.json"
            if f.exists():
                return json.loads(f.read_text(encoding="utf-8"))
            await asyncio.sleep(0.3)
        return {"error": f"sandbox did not answer within {limit_s}s", "exit_code": -1}

    def healthy(self) -> bool:
        if self.mode == "local":
            return True
        if self.mode != "container":
            return False
        try:
            return time.time() - float((self.jobs / ".heartbeat").read_text()) < 30
        except (OSError, ValueError):
            return False

    def read_output(self, job_id: str, name: str) -> bytes:
        if not _JOB.match(job_id) or safe_name(name) != name:
            raise ValueError("invalid sandbox file reference")
        return (self.jobs / job_id / "out" / name).read_bytes()

    def cleanup(self, older_than_s: int = 86_400) -> None:
        cutoff = time.time() - older_than_s
        for d in self.jobs.iterdir():
            if d.is_dir() and _JOB.match(d.name) and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
