"""Workspace rules and custom project guidelines discovery for Smara.

Auto-discovers and injects project-specific conventions from:
1. .smararules
2. SMARA.md
3. CLAUDE.md
4. .cursorrules
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


RULE_FILENAMES = [
    ".smararules",
    "SMARA.md",
    "CLAUDE.md",
    ".cursorrules",
    "AGENTS.md",
]

_TRUST_STORE = Path.home() / ".smara" / "trusted_workspaces.json"


def is_workspace_trusted(workspace_root: Path | str) -> bool:
    """Return whether project-controlled instructions may be activated.

    Trust is deliberately kept outside the repository so a checked-in marker
    cannot authorize its own rules, skills, MCP servers, or hooks.  An
    explicit process-local ``SMARA_TRUST_WORKSPACE=1`` override is useful for
    CI and one-shot CLI runs; persistent trust is stored in the user profile.
    """
    if os.getenv("SMARA_TRUST_WORKSPACE", "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    root = str(Path(workspace_root).resolve())
    try:
        data = json.loads(_TRUST_STORE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    entries = data.get("workspaces", []) if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return False
    return root in {str(Path(item).resolve()) for item in entries if isinstance(item, str)}


def trust_workspace(workspace_root: Path | str) -> None:
    """Persist an explicit user trust decision outside the project tree."""
    root = str(Path(workspace_root).resolve())
    try:
        data = json.loads(_TRUST_STORE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        data = {"workspaces": []}
    if isinstance(data, list):
        entries = [item for item in data if isinstance(item, str)]
        payload: Any = {"workspaces": entries}
    elif isinstance(data, dict) and isinstance(data.get("workspaces"), list):
        entries = [item for item in data["workspaces"] if isinstance(item, str)]
        data["workspaces"] = entries
        payload = data
    else:
        payload = {"workspaces": []}
        entries = payload["workspaces"]
    if root not in entries:
        entries.append(root)
    _TRUST_STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _TRUST_STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, _TRUST_STORE)


def untrust_workspace(workspace_root: Path | str) -> None:
    """Remove a workspace from the user-owned trust store."""
    root = str(Path(workspace_root).resolve())
    try:
        data = json.loads(_TRUST_STORE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return
    entries = data.get("workspaces", []) if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return
    entries = [item for item in entries if isinstance(item, str) and str(Path(item).resolve()) != root]
    payload = {"workspaces": entries}
    tmp = _TRUST_STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, _TRUST_STORE)


def discover_workspace_rules(
    workspace_root: Path | str,
    max_chars: int = 4000,
    trusted: Optional[bool] = None,
) -> Dict[str, Any]:
    """Search for project rules, optionally withholding untrusted content.

    ``trusted=None`` preserves the inspection API used by the CLI and older
    callers.  Agent entry points pass an explicit decision; when it is false
    the path is reported for review but the instruction body is never
    returned for prompt injection.
    """
    root = Path(workspace_root).resolve()
    
    candidates: List[Path] = []
    # Search in workspace root first
    for name in RULE_FILENAMES:
        cand = root / name
        if cand.is_file():
            candidates.append(cand)
            break
            
    # Also check .github or .smara directory if present
    if not candidates:
        for folder in [root / ".github", root / ".smara"]:
            if folder.is_dir():
                for name in RULE_FILENAMES:
                    cand = folder / name
                    if cand.is_file():
                        candidates.append(cand)
                        break

    if not candidates:
        return {
            "found": False,
            "path": None,
            "filename": None,
            "content": "",
            "char_count": 0,
            "requires_trust": False,
        }

    rule_path = candidates[0]
    if trusted is False:
        return {
            "found": False,
            "blocked": True,
            "requires_trust": True,
            "path": str(rule_path),
            "filename": rule_path.name,
            "content": "",
            "char_count": 0,
        }
    try:
        content = rule_path.read_text(encoding="utf-8", errors="replace")
        truncated = content[:max_chars]
        if len(content) > max_chars:
            truncated += f"\n... [Workspace rules truncated: {len(content) - max_chars} chars omitted]"
        return {
            "found": True,
            "path": str(rule_path),
            "filename": rule_path.name,
            "content": truncated.strip(),
            "char_count": len(content),
            "requires_trust": False,
        }
    except Exception as exc:
        return {
            "found": False,
            "path": str(rule_path),
            "filename": rule_path.name,
            "content": f"Error reading rules: {exc}",
            "char_count": 0,
            "requires_trust": False,
        }


def format_rules_for_prompt(rules_data: Dict[str, Any]) -> str:
    """Format discovered rules into a high-priority system prompt section."""
    if not rules_data.get("found") or not rules_data.get("content"):
        return ""

    filename = rules_data.get("filename", "Workspace Rules")
    content = rules_data.get("content", "")
    return (
        f"\n\n### Project-Specific Instructions & Rules (from `{filename}`):\n"
        f"{content}\n"
        f"Strictly adhere to the above workspace conventions, styling, test requirements, and architecture constraints."
    )
