"""Connectors: extra tools the owner plugs in (Gmail, Sheets, Notion, any service that offers an MCP server).

The owner adds a connector in Settings; agents cannot add, change or remove one. Every tool a connector offers asks the
owner's OK in the chat before it runs (approval mode "prompt"), so a connector can read and act only as far as the owner
allows, call by call. Access tokens are kept encrypted with Windows DPAPI, handed to the runtime through its process
environment (never on a command line, in the roster or in the chat), and never shown again in the UI.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlparse

from .native_profiles import protect, read_object, state_path, unprotect, write_object

MAX_CONNECTORS = 12
_ID = re.compile(r"^[a-z][a-z0-9_]{1,23}$")
_ENV = re.compile(r"^[A-Z][A-Z0-9_]{1,39}$")
RESERVED = {"playwright", "codex_apps"}
COMMANDS = {"npx", "uvx", "node", "python", "python3", "docker", "deno", "bunx"}


def store_path() -> Path:
    override = os.environ.get("SMARA_CONNECTORS_FILE")      # tests and evals only
    return Path(override) if override else state_path().parent / "connectors.json"


def _load() -> list[dict]:
    items = read_object(store_path()).get("connectors")
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _save(items: list[dict]) -> None:
    write_object(store_path(), {"connectors": items})


def server_name(identity: str) -> str:
    return "conn_" + identity


def env_name(identity: str, suffix: str) -> str:
    return f"SMARA_CONN_{identity.upper()}_{suffix}"


def public(item: dict) -> dict:
    """What the UI may see: never a token or an env value."""
    return {"id": item["id"], "label": item.get("label", item["id"]), "kind": item["kind"], "url": item.get("url", ""),
            "command": item.get("command", ""), "args": item.get("args", []), "enabled": bool(item.get("enabled", True)),
            "has_token": bool(item.get("token")), "env_names": sorted((item.get("env") or {}))}


def listing() -> dict:
    return {"connectors": [public(item) for item in _load()], "limit": MAX_CONNECTORS}


def _slug(label: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    if not base or not base[0].isalpha():
        base = "svc_" + base
    return base[:24].rstrip("_")


def _url(value: object) -> str:
    parsed = urlparse(str(value or "").strip())
    local = parsed.hostname in {"127.0.0.1", "localhost"}
    if not parsed.hostname or parsed.username or parsed.password or (parsed.scheme != "https" and not (parsed.scheme == "http" and local)):
        raise ValueError("Use the connector's https address (no password inside the link)")
    return parsed.geturl()


def save(request: dict) -> dict:
    label = str(request.get("label") or "").strip()[:60]
    if not label:
        raise ValueError("Give the connector a name, like Gmail")
    kind = request.get("kind")
    if kind not in ("http", "stdio"):
        raise ValueError("Choose a web address or a local command")
    items = _load()
    existing = next((item for item in items if item["id"] == request.get("id")), None) if request.get("id") else None
    identity = existing["id"] if existing else _slug(label)
    if not _ID.match(identity) or identity.startswith("smara") or identity in RESERVED:
        raise ValueError("Pick a different name for this connector")
    if not existing and any(item["id"] == identity for item in items):
        raise ValueError("A connector with that name already exists")
    if not existing and len(items) >= MAX_CONNECTORS:
        raise ValueError(f"At most {MAX_CONNECTORS} connectors; remove one first")
    item: dict = {"id": identity, "label": label, "kind": kind, "enabled": request.get("enabled", True) is not False}
    if kind == "http":
        item["url"] = _url(request.get("url"))
    else:
        command = str(request.get("command") or "").strip()
        if Path(command).stem.lower() not in COMMANDS:
            raise ValueError("The command must be one of: " + ", ".join(sorted(COMMANDS)))
        args = request.get("args") or []
        if not isinstance(args, list) or len(args) > 20 or any(not isinstance(a, str) or len(a) > 300 or "\n" in a for a in args):
            raise ValueError("Arguments must be a short list of plain text")
        item.update({"command": command, "args": args})
        env = request.get("env") or {}
        if not isinstance(env, dict) or len(env) > 10:
            raise ValueError("At most 10 settings")
        old = (existing or {}).get("env") or {}
        item["env"] = {}
        for key, value in env.items():
            if not _ENV.match(str(key)):
                raise ValueError("Setting names look like GITHUB_TOKEN")
            if value in (None, "") and key in old:
                item["env"][key] = old[key]             # blank on edit keeps the stored value
            elif isinstance(value, str) and 0 < len(value) <= 600:
                item["env"][key] = protect(value)
            else:
                raise ValueError(f"Give {key} a value")
    token = request.get("token")
    if isinstance(token, str) and token.strip():
        if len(token) > 600:
            raise ValueError("That access token is too long")
        item["token"] = protect(token.strip())
    elif existing and existing.get("token") and not request.get("clear_token"):
        item["token"] = existing["token"]
    items = [i for i in items if i["id"] != identity] + [item]
    _save(sorted(items, key=lambda i: i["id"]))
    return listing()


def remove(identity: str) -> dict:
    _save([item for item in _load() if item["id"] != identity])
    return listing()


def toggle(identity: str, enabled: bool) -> dict:
    items = _load()
    for item in items:
        if item["id"] == identity:
            item["enabled"] = enabled
    _save(items)
    return listing()


def runtime_settings() -> tuple[dict, dict]:
    """Dotted runtime settings and the environment values for every enabled connector. A broken record is skipped."""
    settings: dict = {}
    environment: dict = {}
    for item in _load():
        try:
            if not item.get("enabled", True):
                continue
            name = "mcp_servers." + server_name(item["id"])
            settings.update({f"{name}.enabled": True, f"{name}.required": False, f"{name}.startup_timeout_sec": 30,
                             f"{name}.tool_timeout_sec": 90, f"{name}.default_tools_approval_mode": "prompt"})
            if item["kind"] == "http":
                settings[f"{name}.url"] = _url(item["url"])
                if item.get("token"):
                    variable = env_name(item["id"], "BEARER")
                    settings[f"{name}.bearer_token_env_var"] = variable
                    environment[variable] = unprotect(item["token"])
            else:
                settings[f"{name}.command"] = item["command"]
                settings[f"{name}.args"] = list(item.get("args") or [])
                names = []
                for key, value in (item.get("env") or {}).items():
                    variable = env_name(item["id"], key)
                    environment[variable] = unprotect(value)
                    names.append(variable)
                if names:
                    settings[f"{name}.env_vars"] = names
        except (KeyError, ValueError, OSError):
            for key in [k for k in settings if k.startswith("mcp_servers." + server_name(str(item.get("id"))) + ".")]:
                settings.pop(key)
    return settings, environment


def prompt_line() -> str:
    """One line for the agent's prompt naming the connected services, or '' when there are none."""
    names = [item.get("label", item["id"]) for item in _load() if item.get("enabled", True)]
    if not names:
        return ""
    return ("Connected services the owner added: " + ", ".join(names) + ". Their tools are named mcp__conn_<name>__<tool>. "
            "Use them for that service instead of the browser. Every call asks the owner first.")


def manage(request: dict) -> dict:
    if set(request) - {"operation", "action", "id", "label", "kind", "url", "command", "args", "env", "token", "clear_token", "enabled"}:
        raise ValueError("Unsupported connector request")
    action = request.get("action", "list")
    if action == "list":
        return listing()
    if action == "save":
        return save(request)
    if action == "remove":
        return remove(str(request.get("id") or ""))
    if action == "toggle":
        return toggle(str(request.get("id") or ""), request.get("enabled") is True)
    raise ValueError("Unsupported connector action")
