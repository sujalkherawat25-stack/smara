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
import hashlib
import shutil
import base64
import os
from pathlib import Path
from typing import Any

_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$")


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

    @staticmethod
    def canonical_manifest(value: dict) -> bytes:
        return json.dumps({key: item for key, item in value.items() if key not in {"signature", "signing_key"}}, sort_keys=True, separators=(",", ":")).encode("utf-8")

    @staticmethod
    def verify_manifest_signature(value: dict) -> dict[str, Any]:
        signature = value.get("signature")
        key_id = str(value.get("signing_key") or "")
        if not signature and not key_id:
            return {"status": "unsigned", "key_id": None}
        if not isinstance(signature, str) or not key_id:
            return {"status": "invalid", "key_id": key_id or None, "reason": "signature and signing_key must both be present"}
        try:
            trust_keys = json.loads(os.getenv("SMARA_PLUGIN_TRUST_KEYS", "{}"))
            raw_key = trust_keys.get(key_id)
            if not isinstance(raw_key, str):
                return {"status": "untrusted", "key_id": key_id, "reason": "signing key is not trusted on this device"}
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            Ed25519PublicKey.from_public_bytes(base64.urlsafe_b64decode(raw_key + "=" * (-len(raw_key) % 4))).verify(base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)), PluginManager.canonical_manifest(value))
            return {"status": "verified", "key_id": key_id}
        except Exception as exc:
            return {"status": "invalid", "key_id": key_id, "reason": str(exc)[:180]}

    @staticmethod
    def validate_manifest(value: Any) -> dict:
        """Validate an inert plugin descriptor before it enters the catalogue."""
        if not isinstance(value, dict):
            raise ValueError("plugin manifest must be an object")
        name = str(value.get("name", ""))
        if not _NAME.fullmatch(name):
            raise ValueError("plugin name must be lowercase and bounded")
        version = str(value.get("version", ""))
        if not _VERSION.fullmatch(version):
            raise ValueError("plugin version must use semantic versioning")
        kind = value.get("kind")
        if kind not in {"mcp", "remote_readonly"}:
            raise ValueError("plugin kind must be mcp or remote_readonly")
        tools = value.get("tools", [])
        if not isinstance(tools, list) or len(tools) > 50 or any(not isinstance(tool, str) or len(tool) > 120 for tool in tools):
            raise ValueError("plugin tools must be a bounded list of names")
        endpoint = value.get("endpoint") or value.get("url")
        if endpoint:
            from .mcp_client import _remote_url_allowed
            if not _remote_url_allowed(str(endpoint), allow_private=False):
                raise ValueError("plugin endpoint must be a public HTTPS URL")
        trust = PluginManager.verify_manifest_signature(value)
        if trust["status"] == "invalid":
            raise ValueError(f"plugin manifest signature is invalid: {trust.get('reason', 'verification failed')}")
        clean = {key: value[key] for key in ("name", "version", "kind", "tools", "endpoint", "url", "oauth", "description", "signature", "signing_key") if key in value}
        clean["trust"] = trust
        clean["enabled"] = False
        return clean

    @staticmethod
    def _digest(value: dict) -> str:
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def _workspace_plugin(self, name: str) -> Path:
        return self.root / name

    def install(self, manifest_path: Path | str) -> dict:
        """Install a local declarative manifest.  No code is copied or imported."""
        source = Path(manifest_path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        try:
            value = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("plugin manifest must be valid JSON") from exc
        clean = self.validate_manifest(value)
        target = self._workspace_plugin(clean["name"])
        if target.exists():
            raise ValueError("plugin is already installed; use update")
        target.mkdir(parents=True, exist_ok=False)
        manifest = target / "plugin.json"
        manifest.write_text(json.dumps(clean, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        state = self._state(); state[clean["name"]] = {"enabled": False, "version": clean["version"], "sha256": self._digest(clean), "trust": clean["trust"], "status": "installed", "updated_at": time.time(), "history": [{"event": "installed", "at": time.time()}]}; self._save(state)
        return {**clean, "status": "installed", "sha256": self._digest(clean)}

    def update(self, name: str, manifest_path: Path | str) -> dict:
        rows = {row["name"]: row for row in self.discover()}
        if name not in rows:
            raise KeyError(name)
        source = Path(manifest_path).expanduser().resolve()
        try:
            clean = self.validate_manifest(json.loads(source.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            raise ValueError("plugin update manifest is invalid") from exc
        if clean["name"] != name:
            raise ValueError("plugin update name must match the installed plugin")
        target = self._workspace_plugin(name)
        if not target.is_dir():
            raise ValueError("only workspace-installed plugins can be updated")
        old = json.loads((target / "plugin.json").read_text(encoding="utf-8"))
        if clean["version"] == str(old.get("version")) and self._digest(clean) == self._digest(old):
            return {**clean, "status": "already_current", "sha256": self._digest(clean)}
        backup = target / ".rollback"
        backup.mkdir(exist_ok=True)
        (backup / f"{old.get('version', 'unknown')}-{self._digest(old)[:12]}.json").write_text(json.dumps(old, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp = target / "plugin.json.tmp"; tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); os.replace(tmp, target / "plugin.json")
        state = self._state(); row = state.setdefault(name, {}); row.update({"version": clean["version"], "sha256": self._digest(clean), "trust": clean["trust"], "status": "updated", "updated_at": time.time()}); row.setdefault("history", []).append({"event": "updated", "from": old.get("version"), "to": clean["version"], "at": time.time()}); self._save(state)
        return {**clean, "status": "updated", "sha256": self._digest(clean)}

    def rollback(self, name: str, version: str) -> dict:
        target = self._workspace_plugin(name)
        if not _VERSION.fullmatch(version) or not target.is_dir():
            raise KeyError(name)
        choices = sorted((target / ".rollback").glob(f"{version}-*.json"))
        if not choices:
            raise ValueError("no rollback manifest exists for that version")
        previous = json.loads(choices[-1].read_text(encoding="utf-8"))
        clean = self.validate_manifest(previous)
        tmp = target / "plugin.json.tmp"; tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); os.replace(tmp, target / "plugin.json")
        state = self._state(); row = state.setdefault(name, {}); row.update({"version": version, "sha256": self._digest(clean), "trust": clean["trust"], "status": "rolled_back", "updated_at": time.time()}); row.setdefault("history", []).append({"event": "rolled_back", "to": version, "at": time.time()}); self._save(state)
        return {**clean, "status": "rolled_back", "sha256": self._digest(clean)}

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
        for row in found.values():
            persisted = states.get(row["name"], {})
            manifest = {key: value for key, value in row.items() if key not in {"source_path", "enabled", "lifecycle", "sha256", "trust"}}
            row["enabled"] = bool(persisted.get("enabled", row.get("enabled", False)))
            row["lifecycle"] = persisted.get("status", "discovered")
            row["sha256"] = persisted.get("sha256", self._digest(self.validate_manifest(manifest)))
            row["trust"] = persisted.get("trust", self.verify_manifest_signature(manifest))
        return sorted(found.values(), key=lambda row: row["name"])

    def set_enabled(self, name: str, enabled: bool) -> dict:
        rows = {row["name"]: row for row in self.discover()}
        if name not in rows: raise KeyError(name)
        if rows[name].get("trust", {}).get("status") in {"invalid", "untrusted"}:
            raise ValueError("plugin signing key is not trusted on this device")
        state = self._state(); prior = state.get(name, {}); state[name] = {**prior, "enabled": bool(enabled), "status": "enabled" if enabled else "disabled", "updated_at": time.time()}; state[name].setdefault("history", []).append({"event": "enabled" if enabled else "disabled", "at": time.time()}); self._save(state)
        rows[name]["enabled"] = bool(enabled); return rows[name]

    def remove(self, name: str) -> None:
        rows = {row["name"]: row for row in self.discover()}
        if name not in rows: raise KeyError(name)
        source = Path(rows[name].get("source_path", ""))
        if source.is_relative_to(self.workspace / ".smara" / "plugins") and source != self.root:
            shutil.rmtree(source)
        state = self._state(); state.pop(name, None); self._save(state)

    def health(self, name: str | None = None) -> list[dict]:
        """Run only MCP initialize/tools-list; tool calls are never health checks."""
        rows = self.discover()
        if name is not None:
            rows = [row for row in rows if row["name"] == name]
            if not rows:
                raise KeyError(name)
        results = []
        for row in rows:
            endpoint = row.get("endpoint") or row.get("url")
            result = {"name": row["name"], "enabled": row["enabled"], "checked_at": time.time(), "status": "not_configured", "tool_count": 0}
            if endpoint:
                try:
                    from .mcp_client import MCPRemoteServer
                    remote = MCPRemoteServer(row["name"], str(endpoint), oauth=row.get("oauth") or {}, timeout=8.0)
                    if remote.start(): result.update({"status": "healthy", "tool_count": len(remote.tools)})
                    else: result["status"] = "unhealthy"
                except Exception as exc:
                    result.update({"status": "unhealthy", "error": str(exc)[:240]})
            elif row.get("kind") == "mcp":
                result["status"] = "requires_local_mcp_config"
            results.append(result)
        state = self._state()
        for result in results:
            if result["name"] in state:
                state[result["name"]]["health"] = {key: value for key, value in result.items() if key not in {"name", "enabled"}}
        self._save(state)
        return results


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
