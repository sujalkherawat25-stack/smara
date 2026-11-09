"""Agent roster for Smara Desktop: named assistants, each with its own instructions, folders, browser sites and chats.

Stored in native-settings.json under `agents` (saved records and presets the user edited) and `thread_agents`
(chat id -> agent id). Nothing here runs an agent or grants access: project folders and browser sites must still
pass the project's own checks, and the runtime still asks before every tool action.
"""
from __future__ import annotations
import os
import re
import uuid
from .native_profiles import load_profiles

PRESETS = (
    {"id": "kiran", "name": "Kiran", "role": "Accounts", "icon": "📒", "preset": True, "terminal": False, "memory_scope": "project",
     "instructions": "You help with the shop's accounts: Tally and Excel files, ledgers and reports. Show the totals you computed."},
    {"id": "rohan", "name": "Rohan", "role": "Dues & reminders", "icon": "🔔", "preset": True, "terminal": False, "memory_scope": "project",
     "instructions": "You work on dues and reminders. Nothing reaches a customer without the owner's approval."},
    {"id": "scout", "name": "Scout", "role": "Web tasks", "icon": "🧭", "preset": True, "terminal": False, "memory_scope": "personal",
     "instructions": "You do web tasks on this PC through the browser helper. Never type passwords, OTPs, PINs or card details."},
    {"id": "nisha", "name": "Nisha", "role": "Documents", "icon": "📄", "preset": True, "terminal": False, "memory_scope": "project",
     "instructions": "You read and prepare documents in the chosen folder: PDFs, Word files and spreadsheets."},
)
DEFAULT_AGENT = "kiran"
MAX_THREAD_ASSIGNMENTS = 5000
AGENT_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,47}")
THREAD_ID = re.compile(r"[A-Za-z0-9_.:-]{1,160}")
FIELDS = {"id", "name", "role", "icon", "instructions", "preset", "folders", "origins", "terminal", "memory_scope", "model_profile"}


def _text(value, limit: int, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    text = value.strip()
    if len(text) > limit:
        raise ValueError(f"{field} must be at most {limit} characters")
    return text


def _folders(value, projects) -> list[str]:
    if not isinstance(value, list) or len(value) > 20:
        raise ValueError("Folders must be a list of up to 20 project folders")
    known = {os.path.normcase(p["workspace"]): p["workspace"] for p in projects}
    chosen = []
    for item in value:
        key = os.path.normcase(str(item))
        if key not in known:
            raise ValueError("An agent can only use folders that are already projects")
        chosen.append(known[key])
    return list(dict.fromkeys(chosen))


def _origins(value) -> list[str]:
    if not isinstance(value, list) or len(value) > 50 or not all(isinstance(item, str) for item in value):
        raise ValueError("Sites must be a list of up to 50 public origins")
    if value:
        from .native_browser import configured_origins
        configured_origins(value)
    return list(dict.fromkeys(value))


def clean_agent(agent, projects, profiles) -> dict:
    """Validated record for one agent. Unknown fields are dropped, never copied."""
    if not isinstance(agent, dict) or set(agent) - FIELDS:
        raise ValueError("Invalid agent")
    identity = str(agent.get("id") or "").strip().lower()
    if not AGENT_ID.fullmatch(identity):
        raise ValueError("Agent id must be 1..48 letters, digits or hyphens")
    name = _text(agent.get("name"), 40, "Name")
    if not name:
        raise ValueError("Give the agent a name")
    scope = agent.get("memory_scope", "project")
    if scope not in ("project", "personal"):
        raise ValueError("Memory must be project or personal")
    if type(agent.get("terminal", False)) is not bool:
        raise ValueError("Terminal must be on or off")
    model = _text(agent.get("model_profile", ""), 48, "Model")
    if model and not any(profile.get("id") == model for profile in profiles):
        raise ValueError("Choose a configured model for this agent")
    return {"id": identity, "name": name, "role": _text(agent.get("role", ""), 60, "Role"),
            "icon": _text(agent.get("icon", "🤖"), 8, "Icon") or "🤖",
            "instructions": _text(agent.get("instructions", ""), 1500, "Instructions"),
            "preset": bool(agent.get("preset", False)), "folders": _folders(agent.get("folders", []), projects),
            "origins": _origins(agent.get("origins", [])), "terminal": agent.get("terminal", False),
            "memory_scope": scope, "model_profile": model}


def roster(saved: dict) -> list[dict]:
    """Presets, each with the user's edits applied, then the user's own agents."""
    edited = {item["id"]: item for item in saved.get("agents", []) if isinstance(item, dict) and isinstance(item.get("id"), str)}
    result = []
    for preset in PRESETS:
        base = {**preset, "folders": [], "origins": [], "model_profile": ""}
        result.append({**base, **{k: v for k, v in edited.get(preset["id"], {}).items() if k in FIELDS}, "preset": True})
    custom = [item for item in saved.get("agents", []) if isinstance(item, dict) and not any(item.get("id") == p["id"] for p in PRESETS)]
    result.extend({**{k: v for k, v in item.items() if k in FIELDS}, "preset": False} for item in custom)
    return result


def view(saved: dict) -> dict:
    assignments = saved.get("thread_agents") if isinstance(saved.get("thread_agents"), dict) else {}
    return {"roster": roster(saved), "default": DEFAULT_AGENT, "assignments": assignments}


def apply(saved: dict, projects: list, request: dict) -> None:
    """Mutates `saved` for one `agents` request. The caller writes the file."""
    profiles, _, _ = load_profiles()
    action = request.get("action")
    if action == "save":
        agent = request.get("agent")
        if not isinstance(agent, dict):
            raise ValueError("Invalid agent")
        identity = str(agent.get("id") or "").strip().lower()
        if not identity:
            base = re.sub(r"[^a-z0-9]+", "-", str(agent.get("name") or "").lower()).strip("-")[:30] or "agent"
            identity = f"{base}-{uuid.uuid4().hex[:6]}"
            agent = {**agent, "id": identity}
        clean = clean_agent(agent, projects, profiles)
        if clean["preset"] and clean["id"] not in {p["id"] for p in PRESETS}:
            raise ValueError("Only the built-in agents can be presets")
        saved["agents"] = [item for item in saved.get("agents", []) if isinstance(item, dict) and item.get("id") != clean["id"]] + [clean]
    elif action == "delete":
        # A built-in agent is not removed: deleting it drops the user's edits and restores the defaults.
        identity = str(request.get("agent_id") or "").strip().lower()
        saved["agents"] = [item for item in saved.get("agents", []) if item.get("id") != identity]
        saved["thread_agents"] = {thread: agent for thread, agent in (saved.get("thread_agents") or {}).items() if agent != identity}
    elif action == "assign":
        thread = str(request.get("thread_id") or "")
        identity = str(request.get("agent_id") or "").strip().lower()
        if not THREAD_ID.fullmatch(thread):
            raise ValueError("Invalid chat id")
        if identity not in {item["id"] for item in roster(saved)}:
            raise ValueError("Unknown agent")
        assignments = dict(saved.get("thread_agents") or {})
        assignments[thread] = identity
        while len(assignments) > MAX_THREAD_ASSIGNMENTS:
            assignments.pop(next(iter(assignments)))
        saved["thread_agents"] = assignments
    else:
        raise ValueError("Unsupported agent action")
