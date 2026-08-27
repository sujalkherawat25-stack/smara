"""Safe plugin/MCP-style capability catalogue.

Smara does not import arbitrary Python from environment variables. Operators
can expose declarative, read-only plugin metadata now; executable external
plugins require a separately authenticated adapter and remain opt-in.
"""
from __future__ import annotations

import json
import re
from typing import Any

_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def manifests(raw: str = "", *, include_user_integrations: bool = True) -> list[dict[str, Any]]:
    builtins = [
        {"name": "smara-core", "version": "1", "kind": "builtin", "enabled": True, "tools": ["current_time", "calculate"]},
        {"name": "smara-research", "version": "1", "kind": "builtin", "enabled": True, "tools": ["research.web_search", "research.fetch_url"]},
        {"name": "smara-integrations", "version": "1", "kind": "builtin", "enabled": True, "tools": ["integration.*"], "approval_required": True},
        {"name": "smara-desktop", "version": "1", "kind": "builtin", "enabled": True, "tools": ["desktop.request_action"], "approval_required": True},
    ]
    if not include_user_integrations:
        builtins = [item for item in builtins if item["name"] != "smara-integrations"]
    if not raw.strip():
        return builtins
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("SMARA_PLUGIN_MANIFESTS must be valid JSON.") from exc
    if not isinstance(values, list) or len(values) > 32:
        raise ValueError("SMARA_PLUGIN_MANIFESTS must be a list with at most 32 entries.")
    for value in values:
        if not isinstance(value, dict) or not _NAME.fullmatch(str(value.get("name", ""))):
            raise ValueError("Plugin names must be lowercase and bounded.")
        if value.get("kind") not in {"mcp", "remote_readonly"}:
            raise ValueError("External plugins must be MCP or remote_readonly descriptors.")
        if not isinstance(value.get("tools", []), list) or len(value["tools"]) > 50:
            raise ValueError("Plugin tool lists are bounded.")
    return builtins + [{"name": item["name"], "version": str(item.get("version", "1")), "kind": item["kind"], "enabled": bool(item.get("enabled", False)), "tools": item.get("tools", []), "approval_required": True} for item in values]
