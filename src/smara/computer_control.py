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
        os.replace(temporary, path)
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
def note_activity(app: str, title: str, action: str, workspace: str = "") -> None:
    try:
        _write(activity_path(), {"ts": time.time(), "app": str(app)[:60], "title": str(title)[:120], "action": str(action)[:60],
                                 "workspace": str(workspace)[:300]})
    except OSError:
        pass


def last_activity() -> dict:
    return _read(activity_path()) or {}


def view() -> dict:
    return {"control": state(), "apps": allowed_apps(), "known_apps": KNOWN_APPS, "hotkey": HOTKEY, "last": last_activity()}
