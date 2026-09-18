"""Safe plugin/MCP-style capability catalogue.

Smara does not import arbitrary Python from environment variables. Operators
can expose declarative, read-only plugin metadata now; executable external
plugins require a separately authenticated adapter and remain opt-in.
"""
from __future__ import annotations

import json
import re
import os
import time
from pathlib import Path
from typing import Any

_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class PluginManager:
    """Declarative plugin catalogue and safe enable/disable lifecycle.

    Plugin manifests may describe MCP/remote adapters, but Smara never imports
    arbitrary Python from a plugin directory.  Executable integration remains
    behind the authenticated MCP transport.
    """
    def __init__(self, workspace_dir: Path | str):
        self.workspace = Path(workspace_dir).expanduser().resolve()
        self.root = self.workspace / ".smara" / "plugins"
        self.state_path = self.root / "state.json"

    def _state(self) -> dict[str, dict]:
        try: value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError): value = {}
        return value if isinstance(value, dict) else {}

    def _save(self, state: dict[str, dict]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp"); tmp.write_text(json.dumps(state, indent=2), encoding="utf-8"); os.replace(tmp, self.state_path)

    def discover(self) -> list[dict]:
        found: dict[str, dict] = {}
        for path in (self.workspace / ".smara" / "plugins", Path.home() / ".smara" / "plugins"):
            if not path.is_dir(): continue
            for item in path.glob("*/plugin.json"):
                try: data = json.loads(item.read_text(encoding="utf-8"))
                except (OSError, ValueError): continue
                if isinstance(data, dict) and _NAME.fullmatch(str(data.get("name", ""))):
                    data["source_path"] = str(item.parent); found[data["name"]] = data
        states = self._state()
        for row in found.values(): row["enabled"] = bool(states.get(row["name"], {}).get("enabled", row.get("enabled", False)))
        return sorted(found.values(), key=lambda row: row["name"])

    def set_enabled(self, name: str, enabled: bool) -> dict:
        rows = {row["name"]: row for row in self.discover()}
        if name not in rows: raise KeyError(name)
        state = self._state(); state[name] = {"enabled": bool(enabled), "updated_at": time.time()}; self._save(state)
        rows[name]["enabled"] = bool(enabled); return rows[name]

    def remove(self, name: str) -> None:
        rows = {row["name"]: row for row in self.discover()}
        if name not in rows: raise KeyError(name)
        source = Path(rows[name].get("source_path", ""))
        if source.is_relative_to(self.workspace / ".smara" / "plugins") and source != self.root:
            import shutil; shutil.rmtree(source)
        state = self._state(); state.pop(name, None); self._save(state)


def manifests(raw: str = "", *, include_user_integrations: bool = True) -> list[dict[str, Any]]:
    builtins = [
        {"name": "smara-core", "version": "1", "kind": "builtin", "enabled": True, "tools": ["current_time", "calculate"]},
        {"name": "smara-research", "version": "1", "kind": "builtin", "enabled": True, "tools": ["research.deep", "research.web_search", "research.fetch_url"]},
        {"name": "smara-integrations", "version": "1", "kind": "builtin", "enabled": True, "tools": ["integration.*"], "approval_required": True},
        {"name": "smara-desktop", "version": "1", "kind": "builtin", "enabled": True, "tools": ["desktop.request_action", "desktop.request_workflow"], "approval_required": True},
    ]
    if not include_user_integrations:
        builtins = [item for item in builtins if item["name"] != "smara-integrations"]
    discovered = PluginManager(Path.cwd()).discover()
    if discovered:
        builtins.extend({"name": item["name"], "version": str(item.get("version", "1")), "kind": item.get("kind", "mcp"), "enabled": bool(item.get("enabled")), "tools": item.get("tools", []), "approval_required": True} for item in discovered)
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
