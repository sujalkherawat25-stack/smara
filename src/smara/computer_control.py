"""The owner's live control over computer use: Pause / Resume / Stop, the allowed-apps list, and the last action.

Shared files in the Smara data folder (next to desktop.json), read before every computer step and during pointer
movement and typing:

* ``computer-control.json``  ``{"paused": bool, "stop_at": epoch, "by": "bar|hotkey|settings"}``.
  Written by the on-screen Smara bar (Rust, atomic rename), the Ctrl+Alt+Esc hotkey and Settings. Stop revokes every
  grant made before ``stop_at``; Pause holds every agent until Resume. An unreadable file counts as paused.
* ``computer-apps.json``  ``{"apps": ["excel.exe", ...]}``: the apps a grant may control (process file names).
* ``computer-activity.json``  the last step (app, window title, action, time), shown on the bar.

None of these files ever holds a credential.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
import time

from .native_profiles import state_path

DEFAULT_APPS = ("excel.exe", "tallyprime.exe", "tally.exe", "explorer.exe", "notepad.exe", "calc.exe", "calculatorapp.exe")
# Apps the owner can tick in Settings. Anything else can still be added by its process name.
KNOWN_APPS = {"excel.exe": "Excel", "tallyprime.exe": "TallyPrime", "tally.exe": "Tally.ERP 9", "explorer.exe": "File Explorer",
              "notepad.exe": "Notepad", "calc.exe": "Calculator", "calculatorapp.exe": "Calculator", "winword.exe": "Word",
              "busywin.exe": "Busy", "chrome.exe": "Chrome", "msedge.exe": "Edge", "brave.exe": "Brave", "firefox.exe": "Firefox",
              "acrord32.exe": "Adobe Reader", "mspaint.exe": "Paint", "outlook.exe": "Outlook"}
_EXE = re.compile(r"^[a-z0-9][a-z0-9 ._()-]{0,80}\.exe$")
HOTKEY = "Ctrl+Alt+Esc"


def _folder() -> Path:
    return state_path().parent


def control_path() -> Path:
    return _folder() / "computer-control.json"


def apps_path() -> Path:
    return _folder() / "computer-apps.json"


def activity_path() -> Path:
    return _folder() / "computer-activity.json"


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, ensure_ascii=False)
    try:
        for attempt in range(20):            # Windows refuses to replace a file another process is reading at that instant
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.01)
    finally:
        temporary.unlink(missing_ok=True)


def _read(path: Path):
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


# ---- pause / stop -------------------------------------------------------------------------------------------------
def state() -> dict:
    value = _read(control_path())
    if value is None:          # fail closed: a damaged control file holds every agent
        return {"paused": True, "stop_at": 0.0, "by": "unreadable"}
    return {"paused": bool(value.get("paused")), "stop_at": float(value.get("stop_at") or 0), "by": str(value.get("by") or "")}


def pause(by: str = "settings") -> dict:
    current = state()
    _write(control_path(), {"paused": True, "stop_at": current["stop_at"], "by": by, "at": time.time()})
    return state()


def resume(by: str = "settings") -> dict:
    current = state()
    _write(control_path(), {"paused": False, "stop_at": current["stop_at"], "by": by, "at": time.time()})
    return state()


def stop(by: str = "settings") -> dict:
    """Revoke every grant made until now (all projects) and clear any pause; a new grant is needed to continue."""
    _write(control_path(), {"paused": False, "stop_at": time.time(), "by": by, "at": time.time()})
    return state()


def halted(grant: dict) -> str | None:
    """Why this grant may not act right now, else None."""
    current = state()
    if float(grant.get("created") or 0) < current["stop_at"]:
        return "stopped"
    if current["paused"]:
        return "paused"
    return None


HALT_TEXT = {
    "stopped": "The owner pressed Stop, so computer control is off. Do not try again; tell the owner what you had done so far. "
               "They can switch it on again in Settings > Browser & computer.",
    "paused": "The owner paused computer control (the Smara bar at the top of the screen). Do nothing on the PC until they "
              "press Resume; tell them what you were about to do and wait.",
}


# ---- allowed apps -------------------------------------------------------------------------------------------------
def clean_apps(values) -> list[str]:
    if not isinstance(values, list) or len(values) > 40:
        raise ValueError("Give up to 40 app names")
    out = []
    for value in values:
        name = str(value or "").strip().lower()
        if not _EXE.match(name):
            raise ValueError(f"'{str(value)[:40]}' is not an app's program name (like excel.exe)")
        if name not in out:
            out.append(name)
    return out


def allowed_apps() -> list[str]:
    value = _read(apps_path())
    if not value or not isinstance(value.get("apps"), list):
        return list(DEFAULT_APPS)
    try:
        return clean_apps(value["apps"])
    except ValueError:
        return list(DEFAULT_APPS)


def save_apps(values) -> list[str]:
    apps = clean_apps(values)
    _write(apps_path(), {"apps": apps})
    return apps


# ---- last action (for the bar) ------------------------------------------------------------------------------------
def note_activity(app: str, title: str, action: str, workspace: str = "", agent: str = "") -> None:
    try:
        _write(activity_path(), {"ts": time.time(), "app": str(app)[:60], "title": str(title)[:120], "action": str(action)[:60],
                                 "workspace": str(workspace)[:300], "agent": str(agent)[:80]})
    except OSError:
        pass


def last_activity() -> dict:
    return _read(activity_path()) or {}


def view() -> dict:
    return {"control": state(), "apps": allowed_apps(), "known_apps": KNOWN_APPS, "hotkey": HOTKEY, "last": last_activity(),
            "input": input_holder()}


# ---- one agent on the mouse and keyboard at a time ----------------------------------------------------------------
# ``computer-input.json`` {"holder", "label", "task", "pid", "until"}. An agent takes it before a step that moves the
# pointer, types or brings a window forward, and keeps it while it keeps acting (each step renews it). It lapses
# INPUT_LEASE_S after the last step, or at once when its process is gone. Reading a file, Excel through COM, Tally's
# XML port and the browsers (they run on their own pages) do not need it.
INPUT_LEASE_S = 20


def input_path() -> Path:
    return _folder() / "computer-input.json"


def _lock_path() -> Path:
    return _folder() / "computer-input.lock"


class _Guard:
    """A tiny cross-process critical section: create-exclusive a lock file, remove it after. A lock older than
    3 seconds belongs to a process that died mid-way and is broken."""

    def __enter__(self):
        path = _lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + 3
        while True:
            try:
                os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                return self
            except (FileExistsError, PermissionError):  # Windows says "permission denied" while another process deletes it
                try:
                    if time.time() - path.stat().st_mtime > 3:
                        path.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.monotonic() > deadline:
                    raise ValueError("Could not check who is using the mouse and keyboard; try again")
                time.sleep(0.02)

    def __exit__(self, *_):
        _lock_path().unlink(missing_ok=True)


def _pid_alive(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259                                         # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def input_holder(now: float | None = None) -> dict | None:
    """Who has the mouse and keyboard right now, or None."""
    value = _read(input_path())
    now = time.time() if now is None else now
    if not value or float(value.get("until") or 0) <= now or not _pid_alive(value.get("pid")):
        return None
    return {k: value.get(k) for k in ("holder", "label", "task", "pid", "until")}


def take_input(holder: str, label: str = "", task: str = "") -> None:
    """Take or renew the mouse/keyboard for this agent; refuse while another agent holds it."""
    with _Guard():
        current = input_holder()
        if current and current["holder"] != holder:
            left = max(1, int(float(current["until"]) - time.time()))
            raise ValueError(f"Another agent ({current.get('label') or 'another Smara task'}) is using the mouse and keyboard right now. "
                             f"Wait about {left} seconds and try again, or do work that does not need them (files, excel_live, "
                             "tally_read, the browser). Never fight over the screen.")
        _write(input_path(), {"holder": holder, "label": str(label)[:80], "task": str(task)[:80], "pid": os.getpid(),
                              "until": time.time() + INPUT_LEASE_S})


def release_input(holder: str) -> None:
    try:
        with _Guard():
            current = _read(input_path()) or {}
            if current.get("holder") == holder:
                input_path().unlink(missing_ok=True)
    except (ValueError, OSError):
        pass
