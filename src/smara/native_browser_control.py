"""User-only owned-browser settings and handoff control; no model authority."""
import os
from pathlib import Path
import re
import time
import uuid
from .native_profiles import read_object, write_object, state_path
from .native_goals import workspace_key

DEFAULTS = {"visible": False, "transfers": False, "max_actions": 80, "seconds": 900}


def options(workspace):
    from .native_management import settings_path
    return {**DEFAULTS, **read_object(settings_path()).get("browser", {}).get(workspace_key(workspace), {})}


class Control:
    def __init__(self, workspace):
        self.id = uuid.uuid4().hex
        self.path = state_path().parent / "browser-sessions" / (self.id + ".json")
        import psutil
        write_object(self.path, {"id": self.id, "workspace": workspace_key(workspace), "pid": os.getpid(), "process_created": psutil.Process().create_time(),
            "paused": False, "stopped": False, "closed": False, "updated": time.time()})

    def update(self, **values):
        from .native_management import management_lock
        with management_lock():
            current = read_object(self.path)
            write_object(self.path, {**current, **values, "updated": time.time()})

    def state(self): return read_object(self.path)


def stop_owned(value):
    """Terminate only the identified MCP server and its current owned helpers."""
    import psutil
    try:
        owner = psutil.Process(value["pid"])
        if owner.create_time() != value["process_created"]: return
        command = owner.cmdline()
        if "smara.native_browser" not in command and "--native-browser" not in command: return
        children = [(child, child.create_time()) for child in owner.children(recursive=True)]
        for child, created in reversed(children):
            if child.is_running() and child.create_time() == created: child.terminate()
        owner.terminate()
    except (KeyError, psutil.NoSuchProcess):
        return  # Already closed; never infer a replacement process's ownership.
    except psutil.AccessDenied:
        raise ValueError("Could not stop the owned browser helper; close its window or disconnect Smara") from None


def alive(value):
    import psutil
    try: return psutil.Process(value["pid"]).create_time() == value["process_created"]
    except (KeyError, psutil.Error): return False


def manage(request):
    from .native_management import preferences, management_lock, settings_path
    workspace = request.get("workspace", "")
    if not any(workspace_key(p["workspace"]) == workspace_key(workspace) for p in preferences()["projects"]):
        raise ValueError("Choose a registered project")
    action = request.get("action", "status")
    if action == "save":
        value = request.get("options", {})
        if not isinstance(value, dict) or set(value) != set(DEFAULTS): raise ValueError("Invalid browser settings")
        if (type(value["visible"]) is not bool or type(value["transfers"]) is not bool
                or type(value["max_actions"]) is not int or not 1 <= value["max_actions"] <= 500
                or type(value["seconds"]) is not int or not 60 <= value["seconds"] <= 3600):
            raise ValueError("Choose explicit browser visibility/transfers and bounded action/time limits")
        with management_lock():
            data = read_object(settings_path()); data.setdefault("browser", {})[workspace_key(workspace)] = value
            write_object(settings_path(), data)
    elif action in {"resume", "stop"}:
        identity = request.get("id", "")
        if not isinstance(identity, str) or not re.fullmatch("[a-f0-9]{32}", identity): raise ValueError("Invalid browser session")
        path = state_path().parent / "browser-sessions" / (identity + ".json")
        with management_lock():
            value = read_object(path)
            if value.get("workspace") != workspace_key(workspace) or value.get("closed"): raise ValueError("Browser session unavailable")
            if action == "resume" and not alive(value): raise ValueError("Browser helper exited; reconnect without replaying earlier actions")
            if action == "resume" and not value.get("paused"): raise ValueError("Browser is not handed off")
            write_object(path, {**value, "paused": False if action == "resume" else value["paused"],
                "stopped": action == "stop", "updated": time.time()})
        if action == "stop":
            stop_owned(value)
            with management_lock(): write_object(path, {**read_object(path), "closed": True})
    elif action != "status": raise ValueError("Unsupported browser control")
    sessions = []
    directory = state_path().parent / "browser-sessions"
    if directory.exists():
        for path in directory.glob("*.json"):
            value = read_object(path)
            if value.get("workspace") == workspace_key(workspace) and not value.get("closed"):
                value["alive"] = alive(value)
                sessions.append(value)
    return {"options": options(workspace), "sessions": sessions[-20:]}
