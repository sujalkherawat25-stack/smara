"""Durable, bounded handles for long-running local terminal processes.

The ordinary local-terminal action remains a one-shot command.  This module
adds an explicit start/poll/cancel protocol for tasks that legitimately need
to stay alive across agent turns or a Desktop process restart.  Process state
and output are local-only, bounded, and never sent to Hosted Smara implicitly.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


MAX_SESSIONS = 32
MAX_SESSION_SECONDS = 60 * 60
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_POLL_CHARS = 16_000
MAX_METADATA_BYTES = 512 * 1024
SESSION_ID_RE = re.compile(r"^term_[0-9a-f]{24}$")
TERMINAL_STATUSES = {"completed", "failed", "finished", "cancelled", "expired", "output_limit", "lost"}
_PROCESS_HANDLES: dict[str, subprocess.Popen] = {}


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pid_alive(pid: int) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        # ``os.kill(pid, 0)`` is not a reliable liveness probe on Windows:
        # it can succeed for an exited process. Query the real process exit
        # code instead, without spawning a shell or logging process details.
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
            kernel32.OpenProcess.restype = ctypes.c_void_p
            kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
            kernel32.GetExitCodeProcess.restype = ctypes.c_int
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_int
            handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            code = ctypes.c_uint32()
            ok = bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code)))
            kernel32.CloseHandle(handle)
            return ok and code.value == 259  # STILL_ACTIVE
        except (AttributeError, OSError, ValueError):
            return False
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError, PermissionError):
        return False
    return True


def _stop_pid(pid: int, process: subprocess.Popen | None = None) -> None:
    """Terminate a process tree without invoking a shell."""
    if not _pid_alive(pid):
        return
    if os.name == "nt":
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                check=False,
            )
    else:
        with contextlib.suppress(OSError):
            os.killpg(pid, signal.SIGTERM)
    if process is not None and process.poll() is None:
        with contextlib.suppress(OSError):
            process.terminate()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and _pid_alive(pid):
        time.sleep(0.05)
    if _pid_alive(pid):
        if os.name != "nt":
            with contextlib.suppress(OSError):
                os.killpg(pid, signal.SIGKILL)
        if process is not None:
            with contextlib.suppress(OSError):
                process.kill()
    if process is not None:
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            process.wait(timeout=2)


def _safe_value(value: object) -> str:
    """Redact obvious inline credential values from local metadata."""
    text = str(value)[:500]
    return re.sub(
        r"(?i)([A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASS)[A-Z0-9_]*)\s*=\s*[^\s]+",
        r"\1=[REDACTED]",
        text,
    )


def _atomic_write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        with contextlib.suppress(OSError):
            os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class PersistentTerminalStore:
    """Manage local process handles backed by a small atomic metadata file."""

    def __init__(self, state_dir: Path, *, allowed_roots: Iterable[Path] = ()):
        self.root = Path(state_dir).expanduser().resolve()
        self.log_dir = self.root / "terminal-sessions"
        self.metadata_path = self.log_dir / "sessions.json"
        self.allowed_roots = tuple(Path(item).expanduser().resolve() for item in allowed_roots)
        self._lock = threading.RLock()

    @contextlib.contextmanager
    def _locked(self):
        # Watchdogs, UI polls and fresh bridge processes share this journal.
        # An instance-local mutex alone allows one writer to lose another's update.
        from .harness import _SessionLock, SessionBusy
        with self._lock:
            guard = _SessionLock(self.log_dir / "sessions.lock")
            deadline = time.monotonic() + 15
            while True:
                try:
                    guard.__enter__()
                    break
                except SessionBusy:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.05)
            try:
                yield
            finally:
                guard.__exit__(None, None, None)

    def _read(self) -> list[dict[str, Any]]:
        try:
            if self.metadata_path.stat().st_size > MAX_METADATA_BYTES:
                return []
            value = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            return []
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)][:MAX_SESSIONS]

    def _write(self, entries: list[dict[str, Any]]) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.metadata_path, entries[:MAX_SESSIONS])

    @staticmethod
    def _stop_sandbox(entry: dict[str, Any]) -> None:
        from .sandbox import container_name_from_argv, stop_workspace_container
        argv = entry.get("argv")
        if isinstance(argv, list):
            stop_workspace_container(container_name_from_argv(argv))

    @staticmethod
    def _find(entries: list[dict[str, Any]], session_id: str) -> dict[str, Any] | None:
        return next((item for item in entries if item.get("id") == session_id), None)

    @staticmethod
    def _pid(entry: dict[str, Any]) -> int:
        try:
            return int(entry.get("pid") or 0)
        except (TypeError, ValueError, OverflowError):
            return 0

    def _validate_session_id(self, session_id: object) -> str:
        if not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id):
            raise ValueError("terminal session id is invalid")
        return session_id

    def _validate_cwd(self, cwd: Path) -> Path:
        candidate = cwd.expanduser().resolve()
        if not candidate.is_dir():
            raise ValueError("terminal session working directory must be an existing folder")
        if self.allowed_roots and not any(candidate == root or root in candidate.parents for root in self.allowed_roots):
            raise ValueError("terminal session working directory is outside the approved workspace")
        return candidate

    def start(
        self,
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
        executable: str | None = None,
        max_seconds: int = 900,
    ) -> dict[str, Any]:
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
            raise ValueError("terminal session argv must be a non-empty string array")
        if len(argv) > 64 or any(len(item) > 4_000 for item in argv):
            raise ValueError("terminal session argv is too large")
        cwd = self._validate_cwd(cwd)
        if not isinstance(max_seconds, int) or isinstance(max_seconds, bool) or not 1 <= max_seconds <= MAX_SESSION_SECONDS:
            raise ValueError(f"max_seconds must be between 1 and {MAX_SESSION_SECONDS}")
        with self._locked():
            entries = self._read()
            # Serialize only within this approved workspace. Independent
            # workspaces must be able to host their own long-running task.
            active = [
                item for item in entries
                if item.get("status") == "running"
                and Path(str(item.get("cwd") or "")).resolve() == cwd
                and _pid_alive(self._pid(item))
            ]
            if len(active) >= 1:
                raise RuntimeError("Only one persistent terminal session may run at a time for this workspace.")
            session_id = f"term_{uuid.uuid4().hex[:24]}"
            log_path = self.log_dir / f"{session_id}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            safe_env = dict(env or {})
            safe_argv = [_safe_value(item) for item in argv]
            log_handle = log_path.open("ab")
            creationflags = 0
            if os.name == "nt":
                creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                process = subprocess.Popen(
                    argv,
                    cwd=str(cwd),
                    env=safe_env or None,
                    stdin=subprocess.DEVNULL,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    shell=False,
                    creationflags=creationflags,
                    start_new_session=os.name != "nt",
                )
            except (OSError, subprocess.SubprocessError):
                log_handle.close()
                log_path.unlink(missing_ok=True)
                raise
            finally:
                log_handle.close()
            _PROCESS_HANDLES[session_id] = process
            entry = {
                "id": session_id,
                "pid": process.pid,
                "executable": _safe_value(executable or Path(argv[0]).name),
                "argv": safe_argv,
                "cwd": str(cwd),
                "log_path": str(log_path),
                "offset": 0,
                "bytes": 0,
                "max_seconds": max_seconds,
                "started_at": _timestamp(),
                "updated_at": _timestamp(),
                "status": "running",
                "exit_code": None,
            }
            self._write([entry] + [item for item in entries if item.get("id") != session_id])
            threading.Thread(target=self._watch, args=(session_id, process),
                             name=f"terminal-deadline-{session_id}", daemon=True).start()
            return self._public(entry, output="", done=False)

    def _refresh(self, entry: dict[str, Any]) -> None:
        if entry.get("status") != "running":
            return
        session_id = entry["id"]
        process = _PROCESS_HANDLES.get(session_id)
        code = process.poll() if process is not None else entry.get("exit_code")
        # The owned Popen handle is authoritative. A PID liveness probe can
        # observe exit just after poll() returned None; declaring 'finished'
        # in that interval would permanently lose the genuine exit receipt.
        alive = code is None if process is not None else _pid_alive(self._pid(entry))
        try:
            elapsed = time.time() - datetime.fromisoformat(entry["started_at"]).timestamp()
            log_size = Path(entry["log_path"]).stat().st_size
        except (OSError, ValueError, TypeError):
            elapsed, log_size = 0, 0
        reason = ("output_limit" if log_size > MAX_OUTPUT_BYTES else
                  "expired" if elapsed >= int(entry.get("max_seconds") or MAX_SESSION_SECONDS) else None)
        if alive and reason:
            self._stop_sandbox(entry)
            _stop_pid(self._pid(entry), process)
            code = process.poll() if process is not None else None
            alive = code is None if process is not None else _pid_alive(self._pid(entry))
            entry["status"] = "cancellation_uncertain" if alive else reason
        elif not alive:
            self._stop_sandbox(entry)
            entry["status"] = ("expired" if reason == "expired" and code in {124, 137, 143} else
                               "completed" if code == 0 else "failed" if code is not None else "finished")
        if entry["status"] != "running":
            entry["exit_code"] = code
            entry["updated_at"] = _timestamp()
            _PROCESS_HANDLES.pop(session_id, None)

    def _watch(self, session_id: str, process: subprocess.Popen) -> None:
        # Enforce time/output limits and record genuine exit status without a poll.
        # Docker also has its own deadline if this owning process exits.
        while True:
            time.sleep(0.1)
            try:
                with self._locked():
                    entries = self._read()
                    entry = self._find(entries, session_id)
                    if entry is None or entry.get("status") != "running":
                        return
                    self._refresh(entry)
                    if entry.get("status") != "running":
                        self._write(entries)
                        return
            except (OSError, RuntimeError):
                if process.poll() is not None:
                    return

    def _read_output(self, entry: dict[str, Any], *, max_chars: int) -> str:
        pending_text = str(entry.pop("pending_output", ""))
        if pending_text:
            entry["pending_output"] = pending_text[max_chars:]
            if len(pending_text) >= max_chars:
                return pending_text[:max_chars]
            return pending_text + self._read_output(entry, max_chars=max_chars - len(pending_text))
        path = Path(str(entry.get("log_path") or ""))
        if not path.is_file():
            return ""
        try:
            size = min(path.stat().st_size, MAX_OUTPUT_BYTES)
            offset = max(0, min(int(entry.get("offset") or 0), size))
            with path.open("rb") as handle:
                handle.seek(offset)
                data = handle.read(min(max(0, size - offset), max_chars * 4))
        except (OSError, ValueError):
            return ""
        import codecs
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        text = decoder.decode(data, final=len(data) == size - offset)
        pending, _ = decoder.getstate()
        returned = text[:max_chars]
        consumed = len(data) - len(pending)
        if len(text) > max_chars:
            # Count bytes up to the returned character boundary, even for
            # malformed UTF-8. Never advance over output not returned.
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            produced = 0
            consumed = 0
            for byte in data:
                chunk = decoder.decode(bytes([byte]), final=False)
                produced += len(chunk)
                consumed += 1
                if produced >= len(returned):
                    entry["pending_output"] = text[len(returned):produced]
                    break
        entry["offset"] = offset + consumed
        entry["bytes"] = size
        return returned

    def poll(self, session_id: str, *, max_chars: int = MAX_POLL_CHARS) -> dict[str, Any]:
        session_id = self._validate_session_id(session_id)
        max_chars = max(1, min(int(max_chars), MAX_POLL_CHARS))
        with self._locked():
            entries = self._read()
            entry = self._find(entries, session_id)
            if entry is None:
                raise KeyError(session_id)
            self._refresh(entry)
            output = self._read_output(entry, max_chars=max_chars)
            entry["updated_at"] = _timestamp()
            self._write(entries)
            more_output = entry.get("offset", 0) < entry.get("bytes", 0) or bool(entry.get("pending_output"))
            done = entry.get("status") in TERMINAL_STATUSES and not more_output
            return self._public(entry, output=output, done=done)

    def cancel(self, session_id: str, *, reason: str = "cancelled on Desktop") -> dict[str, Any]:
        session_id = self._validate_session_id(session_id)
        with self._locked():
            entries = self._read()
            entry = self._find(entries, session_id)
            if entry is None:
                raise KeyError(session_id)
            if entry.get("status") == "running":
                self._stop_sandbox(entry)
                _stop_pid(self._pid(entry), _PROCESS_HANDLES.get(session_id))
                entry["status"] = "cancellation_uncertain" if _pid_alive(self._pid(entry)) else "cancelled"
                entry["cancel_reason"] = str(reason)[:240]
                entry["updated_at"] = _timestamp()
                _PROCESS_HANDLES.pop(session_id, None)
            output = self._read_output(entry, max_chars=MAX_POLL_CHARS)
            self._write(entries)
            return self._public(entry, output=output, done=entry["status"] in TERMINAL_STATUSES)

    def list(self) -> list[dict[str, Any]]:
        with self._locked():
            entries = self._read()
            changed = False
            for entry in entries:
                before = entry.get("status")
                self._refresh(entry)
                changed = changed or entry.get("status") != before
            if changed:
                self._write(entries)
            return [self._public(entry, output="", done=entry.get("status") in TERMINAL_STATUSES) for entry in entries]

    def has_active(self, cwd: Path | None = None) -> bool:
        """Return whether a live session exists, optionally for one workspace."""
        candidate = cwd.expanduser().resolve() if cwd is not None else None
        with self._locked():
            active = False
            entries = self._read()
            changed = False
            for entry in entries:
                if entry.get("status") != "running":
                    continue
                self._refresh(entry)
                if entry.get("status") != "running":
                    changed = True
                    continue
                if candidate is None or Path(str(entry.get("cwd") or "")).resolve() == candidate:
                    active = True
            if changed:
                self._write(entries)
            return active

    @staticmethod
    def _public(entry: dict[str, Any], *, output: str, done: bool) -> dict[str, Any]:
        return {
            "action": "local_terminal_session",
            "session_id": entry.get("id"),
            "status": entry.get("status"),
            "done": bool(done),
            "pid": entry.get("pid"),
            "executable": entry.get("executable"),
            "argv": entry.get("argv", []),
            "cwd": entry.get("cwd"),
            "started_at": entry.get("started_at"),
            "updated_at": entry.get("updated_at"),
            "exit_code": entry.get("exit_code"),
            "output": output[:MAX_POLL_CHARS],
            "output_offset": entry.get("offset", 0),
            "output_bytes": entry.get("bytes", 0),
            "has_more_output": entry.get("offset", 0) < entry.get("bytes", 0) or bool(entry.get("pending_output")),
            "proof": "Output is read from a local bounded session log; no terminal stream is uploaded implicitly.",
        }


__all__ = [
    "MAX_SESSIONS",
    "MAX_SESSION_SECONDS",
    "MAX_OUTPUT_BYTES",
    "MAX_POLL_CHARS",
    "PersistentTerminalStore",
]
