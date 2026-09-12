"""Workspace rules and custom project guidelines discovery for Smara.

Auto-discovers and injects project-specific conventions from:
1. .smararules
2. SMARA.md
3. CLAUDE.md
4. .cursorrules
"""
from __future__ import annotations

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


def discover_workspace_rules(
    workspace_root: Path | str,
    max_chars: int = 4000,
) -> Dict[str, Any]:
    """Search workspace root and immediate parent for project rule files."""
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
        }

    rule_path = candidates[0]
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
        }
    except Exception as exc:
        return {
            "found": False,
            "path": str(rule_path),
            "filename": rule_path.name,
            "content": f"Error reading rules: {exc}",
            "char_count": 0,
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
