"""Agent roster for Smara Desktop: named assistants with their own instructions, and their own chats.

An agent's instructions are added to a new chat's developer instructions, after the Smara rules, so the agent text
cannot override them. Agents do not carry permissions in this version: folders, browser sites, terminal use and
model choice stay with the project and the runtime settings, and every tool action still asks first.

Stored in native-settings.json under `agents` (records the user saved, presets included) and `thread_agents`
(chat id -> agent id).
"""
from __future__ import annotations
import re
import uuid

MANAGER_ID = "smara"
MANAGER_INSTRUCTIONS = (
    "You are the owner's manager and first point of contact. Answer quick questions yourself. For work that fits an "
    "employee, hand it over with team_delegate and tell the owner who is on it. When the owner asks for a new employee "
    "(for example a mail handler, accountant or Instagram helper) and has said what the job "
    "is, hire right away with team_hire: write the job as clear duties plus safe defaults (draft, never send, pay, post or "
    "delete without the owner's OK; for web work such as LinkedIn, Gmail or Instagram, the employee uses Smara's browser, or the owner's own browser when they ask), then tell the owner in two or three lines what you assumed and that they can change "
    "it by just saying so. Ask first only if you cannot tell what the job is, and then at most two short questions. "
    "When the owner gives a standing instruction for someone (\"from now on ...\"), save it with team_remember. "
    "When the owner wants something done on a schedule (\"every morning\", \"each Monday\"), set it up with team_routine; "
    "team_routines lists them and team_routine_pause pauses or resumes one. "
    "Use team_list and team_tasks to answer what the team is doing. For jobs in sequence (Scout downloads, then Kiran reconciles), give each next team_delegate job after set to the earlier task_id; independent jobs go out together. team_result reads a finished report so you can combine them. "
    "To let someone go, use team_remove. Refer to employees by name, never by id, "
    "and keep replies short.")
PRESETS = (
    {"id": MANAGER_ID, "name": "Smara", "role": "Manager", "icon": "✦", "preset": True, "instructions": MANAGER_INSTRUCTIONS},
    {"id": "kiran", "name": "Kiran", "role": "Accounts", "icon": "📒", "preset": True,
     "instructions": "You help with the shop's accounts: Tally and Excel files, ledgers and reports. Show the totals you computed."},
    {"id": "rohan", "name": "Rohan", "role": "Dues", "icon": "🔔", "preset": True,
     "instructions": "You work on dues and reminders. Nothing reaches a customer without the owner's approval."},
    {"id": "scout", "name": "Scout", "role": "Web tasks", "icon": "🧭", "preset": True,
     "instructions": "You do web tasks through the browser helper. Never type passwords, OTPs, PINs or card details."},
    {"id": "nisha", "name": "Nisha", "role": "Documents", "icon": "📄", "preset": True,
     "instructions": "You read and prepare documents in the chosen folder: PDFs, Word files and spreadsheets."},
)
DEFAULT_AGENT = MANAGER_ID
MAX_THREAD_ASSIGNMENTS = 5000
AGENT_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,47}")
THREAD_ID = re.compile(r"[A-Za-z0-9_.:-]{1,160}")
FIELDS = {"id", "name", "role", "icon", "instructions", "preset"}


def _text(value, limit: int, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    text = value.strip()
    if len(text) > limit:
        raise ValueError(f"{field} must be at most {limit} characters")
    return text


def clean_agent(agent) -> dict:
    """Validated record for one agent. Unknown fields are refused, never stored."""
    if not isinstance(agent, dict) or set(agent) - FIELDS:
        raise ValueError("Invalid agent")
    identity = str(agent.get("id") or "").strip().lower()
    if not AGENT_ID.fullmatch(identity):
        raise ValueError("Agent id must be 1..48 letters, digits or hyphens")
    name = _text(agent.get("name"), 40, "Name")
    if not name:
        raise ValueError("Give the agent a name")
    return {"id": identity, "name": name, "role": _text(agent.get("role", ""), 60, "Role"),
            "icon": _text(agent.get("icon", "🤖"), 8, "Icon") or "🤖",
            "instructions": _text(agent.get("instructions", ""), 1500, "Instructions"),
            "preset": bool(agent.get("preset", False))}


def roster(saved: dict) -> list[dict]:
    """Presets, each with the user's edits applied, then the user's own agents."""
    edited = {item["id"]: item for item in saved.get("agents", []) if isinstance(item, dict) and isinstance(item.get("id"), str)}
    result = []
    for preset in PRESETS:
        result.append({**preset, **{k: v for k, v in edited.get(preset["id"], {}).items() if k in FIELDS}, "preset": True})
    preset_ids = {p["id"] for p in PRESETS}
    custom = [item for item in saved.get("agents", []) if isinstance(item, dict) and item.get("id") not in preset_ids]
    result.extend({**{k: v for k, v in item.items() if k in FIELDS}, "preset": False} for item in custom)
    return result


def view(saved: dict) -> dict:
    assignments = saved.get("thread_agents") if isinstance(saved.get("thread_agents"), dict) else {}
    from . import native_skills
    team = []
    for agent in roster(saved):
        try:
            skills = native_skills.all_for(agent["id"])
        except (OSError, ValueError):
            skills = []
        team.append({**agent, "skills": [native_skills.summary(s) for s in skills], "skills_brief": native_skills.brief(agent["id"])})
    return {"roster": team, "default": DEFAULT_AGENT, "assignments": assignments}


def apply(saved: dict, request: dict) -> None:
    """Mutates `saved` for one `agents` request. The caller writes the file."""
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
        clean = clean_agent(agent)
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
