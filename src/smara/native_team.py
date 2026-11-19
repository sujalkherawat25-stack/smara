"""Smara's team: the manager (Smara, renamable) hires employees, gives them standing rules, and hands them jobs.

An MCP server over stdio, offered in every Desktop chat. Changing the team always asks the owner first: hiring,
changing an employee's job and remembering a rule are shown as an approval card in chat (approval mode "prompt"),
so a web page or file can never quietly rewrite who an employee is. Reading the team and handing a job to an
employee need no prompt: the job runs as that employee's background task, within its budget, and every outward
step inside it still asks the owner.

Employees are stored with the agent roster (native-settings.json, `agents`), written under the management lock.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

from . import native_agents

MAX_BYTES = 256 * 1024
MANAGER_ID = native_agents.MANAGER_ID
RULES_HEADING = "Standing rules from the owner:"
MAX_RULES = 30


def _tool(name, description, properties=None, required=None, title=""):
    return {"name": name, "title": title or name, "description": description,
            "inputSchema": {"type": "object", "properties": properties or {}, "required": required or [], "additionalProperties": False},
            "annotations": {}}


_EMP = {"type": "string", "description": "The employee's id from team_list (or their name)"}
TOOLS = [
    _tool("team_list", "List the owner's team: Smara (the manager) and every employee with id, name, role, job and standing rules, "
          "plus what each one is working on now. Read this before hiring, changing or handing out work.", title="Look at the team"),
    _tool("team_hire", "Hire a new employee once the owner has said what the job is. Write the job with safe defaults (draft, never "
          "send, pay, post or delete without the owner's OK). Ask first only if the job itself is unclear. The owner confirms "
          "in an approval card.",
          {"name": {"type": "string", "description": "A short first name, e.g. Meera"},
           "role": {"type": "string", "description": "Job title in 1-4 words, e.g. Mail handler, Accountant, Instagram manager"},
           "job": {"type": "string", "description": "The job description in plain words, written to the employee as 'You ...': duties, "
                   "tools and sites to use, what to always ask the owner about, and what never to do"},
           "icon": {"type": "string", "description": "One emoji"}}, ["name", "role", "job"], title="Hire an employee"),
    _tool("team_update", "Change an employee's name, role or whole job description (also renames Smara, the manager, when the owner asks). "
          "The owner confirms in an approval card.",
          {"employee": _EMP, "name": {"type": "string"}, "role": {"type": "string"}, "job": {"type": "string"}, "icon": {"type": "string"}},
          ["employee"], title="Change an employee"),
    _tool("team_remember", "Save one standing rule the owner gave an employee (\"from now on ...\", \"always ...\", \"never ...\"), so it is "
          "followed in every future chat and task of that employee. The owner confirms in an approval card.",
          {"employee": _EMP, "rule": {"type": "string", "description": "The rule in one sentence, in the owner's words"}},
          ["employee", "rule"], title="Remember a rule"),
    _tool("team_delegate", "Hand a job to one employee. It runs in the background as that employee's task (visible on the task board) "
          "and reports back when done. Use it for work that takes several steps; answer quick questions yourself.",
          {"employee": _EMP, "task": {"type": "string", "description": "What to do, with the details the owner gave"},
           "done_when": {"type": "string", "description": "How the employee knows the job is finished"}},
          ["employee", "task"], title="Hand a job to an employee"),
    _tool("team_tasks", "What each employee is doing: their background tasks with state and latest progress.",
          {"employee": {"type": "string", "description": "Optional: only this employee"}}, title="See the team's work"),
]
PROMPTED = {"team_hire", "team_update", "team_remember"}
READONLY = {"team_list", "team_tasks"}
for _entry in TOOLS:
    _entry["annotations"].update(readOnlyHint=_entry["name"] in READONLY, destructiveHint=False,
                                 idempotentHint=_entry["name"] in READONLY, openWorldHint=False)


def approval_mode(name: str) -> str:
    return "prompt" if name in PROMPTED else "approve"


def _settings():
    from .native_management import settings_path
    from .native_profiles import read_object
    return read_object(settings_path())


def _mutate(change) -> dict:
    """Read, change and write the roster under the same lock the Settings window uses."""
    from .native_management import management_lock, settings_path
    from .native_profiles import read_object, write_object
    with management_lock():
        saved = read_object(settings_path())
        result = change(saved)
        write_object(settings_path(), saved)
    return result


def _find(saved: dict, who: str) -> dict:
    who = str(who or "").strip()
    if not who:
        raise ValueError("Say which employee")
    roster = native_agents.roster(saved)
    found = next((a for a in roster if a["id"] == who.lower()), None) \
        or next((a for a in roster if a["name"].lower() == who.lower()), None)
    if not found:
        raise ValueError(f"No employee called {who!r}. Call team_list for the names.")
    return found


def split_rules(instructions: str) -> tuple[str, list[str]]:
    """The job text and the standing rules saved under it."""
    if RULES_HEADING not in instructions:
        return instructions.strip(), []
    job, _, rules = instructions.partition(RULES_HEADING)
    return job.strip(), [line.lstrip("- ").strip() for line in rules.splitlines() if line.strip()]


def join_rules(job: str, rules: list[str]) -> str:
    return job.strip() + (("\n\n" + RULES_HEADING + "\n" + "\n".join(f"- {rule}" for rule in rules)) if rules else "")


def _save(saved: dict, record: dict) -> dict:
    native_agents.apply(saved, {"action": "save", "agent": {k: record[k] for k in native_agents.FIELDS if k in record}})
    return _find(saved, record["id"]) if record.get("id") else record


def _clean_text(value, field: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"Give the {field}")
    if len(text) > limit:
        raise ValueError(f"Keep the {field} under {limit} characters")
    return text


def _employee_view(agent: dict) -> dict:
    job, rules = split_rules(agent.get("instructions", ""))
    return {"id": agent["id"], "name": agent["name"], "role": agent.get("role", ""), "manager": agent["id"] == MANAGER_ID,
            "job": job, "rules": rules}


def _tasks(employee: str = "") -> list[dict]:
    from .native_goals import GoalStore
    try:
        goals = GoalStore().list()
    except Exception:
        return []
    out = []
    for goal in goals:
        spec = goal.get("spec") or {}
        if employee and spec.get("agent_id") != employee:
            continue
        checkpoint = goal.get("checkpoint") or {}
        out.append({"id": goal.get("id"), "employee": spec.get("agent_id") or MANAGER_ID, "state": goal.get("state"),
                    "task": str(spec.get("objective", ""))[:300], "progress": str(checkpoint.get("summary") or goal.get("detail") or "")[:300]})
    return out[:40]


def call(name: str, args: dict, workspace: str = "") -> dict:
    if name == "team_list":
        saved = _settings()
        working = {}
        for task in _tasks():
            if task["state"] in {"queued", "running", "awaiting_approval", "blocked", "paused"}:
                working.setdefault(task["employee"], []).append(task["task"][:120])
        return {"team": [{**_employee_view(a), "working_on": working.get(a["id"], [])} for a in native_agents.roster(saved)]}
    if name == "team_tasks":
        who = ""
        if args.get("employee"):
            who = _find(_settings(), args["employee"])["id"]
        return {"tasks": _tasks(who)}
    if name == "team_hire":
        record = {"name": _clean_text(args.get("name"), "name", 40), "role": _clean_text(args.get("role"), "role", 60),
                  "instructions": _clean_text(args.get("job"), "job description", 1400), "icon": str(args.get("icon") or "🧑‍💼")[:8]}

        def hire(saved):
            if any(a["name"].lower() == record["name"].lower() for a in native_agents.roster(saved)):
                raise ValueError(f"There is already someone called {record['name']}. Pick another name.")
            before = {a["id"] for a in native_agents.roster(saved)}
            native_agents.apply(saved, {"action": "save", "agent": record})
            new = next(a for a in native_agents.roster(saved) if a["id"] not in before)
            return _employee_view(new)
        hired = _mutate(hire)
        return {"hired": hired, "tell_the_owner": f"{hired['name']} ({hired['role']}) is on the team. They appear in the sidebar; "
                "the owner can chat with them directly or you can hand them jobs with team_delegate."}
    if name == "team_update":
        def update(saved):
            agent = dict(_find(saved, args.get("employee")))
            job, rules = split_rules(agent.get("instructions", ""))
            if args.get("name"):
                agent["name"] = _clean_text(args["name"], "name", 40)
            if args.get("role"):
                agent["role"] = _clean_text(args["role"], "role", 60)
            if args.get("icon"):
                agent["icon"] = str(args["icon"])[:8]
            if args.get("job"):
                job = _clean_text(args["job"], "job description", 1400)
            agent["instructions"] = join_rules(job, rules)
            return _employee_view(_save(saved, agent))
        return {"updated": _mutate(update)}
    if name == "team_remember":
        rule = _clean_text(args.get("rule"), "rule", 300).replace("\n", " ")

        def remember(saved):
            agent = dict(_find(saved, args.get("employee")))
            job, rules = split_rules(agent.get("instructions", ""))
            if rule.lower() not in (r.lower() for r in rules):
                rules = (rules + [rule])[-MAX_RULES:]
            agent["instructions"] = join_rules(job, rules)
            if len(agent["instructions"]) > 1500:
                raise ValueError("This employee's job and rules are full. Rewrite the job with team_update to make room.")
            return _employee_view(_save(saved, agent))
        return {"remembered": rule, "employee": _mutate(remember)}
    if name == "team_delegate":
        saved = _settings()
        agent = _find(saved, args.get("employee"))
        task = _clean_text(args.get("task"), "task", 4000)
        done = str(args.get("done_when") or "").strip() or "The task is finished and the result is reported clearly."
        from .native_goals import operate
        from .native_management import preferences
        prefs = preferences()
        target = workspace or prefs.get("workspace") or ""
        if not target:
            raise ValueError("Open a project first")
        goal = operate({"action": "create", "workspace": target, "profile_id": prefs.get("active_model") or "",
                        "objective": task, "success_criteria": done, "agent_id": agent["id"], "sandbox": "read-only",
                        "max_cycles": 6, "max_requests": 24})
        try:
            operate({"action": "start_service"})          # the background worker picks the job up
        except Exception:
            pass
        return {"task_id": goal.get("id"), "employee": agent["name"], "state": goal.get("state"),
                "tell_the_owner": f"{agent['name']} has the job. It shows on the task board; approvals it needs come to you."}
    raise ValueError("Unknown team tool")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default="")
    args = parser.parse_args(argv)
    initialized = False
    for line in sys.stdin:
        identity = None
        try:
            if len(line) > MAX_BYTES:
                raise ValueError("Oversized request")
            request = json.loads(line)
            identity = request.get("id")
            if identity is None:
                continue
            if request.get("jsonrpc") != "2.0":
                raise ValueError("Invalid protocol")
            method = request.get("method")
            if method == "initialize":
                initialized = True
                result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "smara-team", "version": "1"}}
            elif method == "ping":
                result = {}
            elif not initialized:
                raise ValueError("Initialize first")
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                params = request.get("params") or {}
                try:
                    out = call(str(params.get("name")), params.get("arguments") or {}, args.workspace)
                    result = {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}], "isError": False}
                except Exception as error:
                    message = str(error) if isinstance(error, ValueError) else type(error).__name__
                    result = {"content": [{"type": "text", "text": "Team change refused: " + re.sub(r"\s+", " ", message)[:400]}], "isError": True}
            else:
                raise ValueError("Unknown method")
            reply = {"jsonrpc": "2.0", "id": identity, "result": result}
        except (ValueError, TypeError, KeyError):
            reply = {"jsonrpc": "2.0", "id": identity, "error": {"code": -32600, "message": "Invalid request"}}
        print(json.dumps(reply, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
