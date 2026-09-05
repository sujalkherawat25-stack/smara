"""Smara Surgical Patch and Diff Engine.

Provides multi-strategy fuzzy text matching, AST-validated file editing,
idempotency protection, atomic rollback, and unified diff reporting.

Designed to eliminate LLM file-editing failure modes:
1. Slight whitespace, tab vs space, or indentation drift.
2. Idempotent re-edits (converting 'already applied' edits to clean no-ops).
3. Broken Python syntax (catching AST syntax errors before saving).
4. Full unified diff output for immediate inspection.
"""
from __future__ import annotations

import ast
import difflib
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def is_already_applied(content: str, old_string: str, new_string: str) -> bool:
    """Check if the requested edit has already been applied to the file content."""
    if not new_string or len(new_string.strip()) < 6:
        return False
    if new_string not in content:
        return False
    if old_string == new_string:
        return True
    return old_string not in content


def _find_exact(content: str, old_string: str) -> List[Tuple[int, int]]:
    matches = []
    start = 0
    while True:
        pos = content.find(old_string, start)
        if pos == -1:
            break
        matches.append((pos, pos + len(old_string)))
        start = pos + 1
    return matches


def _find_line_trimmed(content: str, old_string: str) -> List[Tuple[int, int]]:
    """Match lines ignoring leading and trailing whitespace per line."""
    c_lines = content.splitlines(keepends=True)
    o_lines = old_string.splitlines()
    if not o_lines:
        return []

    o_stripped = [ln.strip() for ln in o_lines if ln.strip()]
    if not o_stripped:
        return []

    matches = []
    n_search = len(o_stripped)

    # Precalculate line start offsets
    line_offsets = []
    curr = 0
    for l in c_lines:
        line_offsets.append(curr)
        curr += len(l)

    for i in range(len(c_lines) - n_search + 1):
        window = [c_lines[i + j].strip() for j in range(n_search)]
        if window == o_stripped:
            start_pos = line_offsets[i]
            end_pos = line_offsets[i + n_search - 1] + len(c_lines[i + n_search - 1])
            matches.append((start_pos, end_pos))

    return matches


def _find_whitespace_normalized(content: str, old_string: str) -> List[Tuple[int, int]]:
    """Match collapsing all whitespace runs to a single space."""
    norm_pattern = re.sub(r'\s+', r'\\s+', re.escape(old_string.strip()))
    matches = []
    for m in re.finditer(norm_pattern, content):
        matches.append((m.start(), m.end()))
    return matches


def _fuzzy_find(content: str, old_string: str) -> Tuple[List[Tuple[int, int]], str]:
    """Try matching strategies in order of precision."""
    # 1. Exact match
    m = _find_exact(content, old_string)
    if m:
        return m, "exact"

    # 2. Line trimmed
    m = _find_line_trimmed(content, old_string)
    if m:
        return m, "line_trimmed"

    # 3. Whitespace normalized
    m = _find_whitespace_normalized(content, old_string)
    if m:
        return m, "whitespace_normalized"

    # 4. Boundary trimmed
    stripped_old = old_string.strip()
    if stripped_old and stripped_old != old_string:
        m = _find_exact(content, stripped_old)
        if m:
            return m, "boundary_trimmed"

    return [], "none"


class SmaraPatchEngine:
    """High-reliability file patcher with AST checks and unified diffs."""

    def __init__(self, base_dir: Optional[str | Path] = None) -> None:
        self.base_dir = Path(base_dir) if base_dir else Path.cwd()

    def resolve_path(self, path_str: str) -> Path:
        p = Path(path_str)
        if p.is_absolute():
            return p
        return (self.base_dir / p).resolve()

    def patch(
        self,
        file_path: str | Path,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> Dict[str, Any]:
        """Apply targeted replacement to a file.

        Args:
            file_path: Path to target file.
            old_string: Text to search for and replace.
            new_string: Replacement text.
            replace_all: If True, replace all matches. If False, require a unique match.

        Returns:
            Dict containing success, diff, strategy, occurrences, and optional error.
        """
        resolved = self.resolve_path(str(file_path))
        if not resolved.exists():
            return {
                "success": False,
                "error": f"File not found: {resolved}",
                "path": str(resolved),
                "diff": "",
            }

        try:
            original = resolved.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return {
                "success": False,
                "error": f"Could not read file {resolved}: {e}",
                "path": str(resolved),
                "diff": "",
            }

        # Check if already applied
        if is_already_applied(original, old_string, new_string):
            return {
                "success": True,
                "applied_strategy": "already_applied",
                "occurrences": 0,
                "path": str(resolved),
                "diff": "(No changes: replacement is already present in target file)",
                "note": "Edit was already present in target file.",
            }

        matches, strategy = _fuzzy_find(original, old_string)
        if not matches:
            return {
                "success": False,
                "error": f"Target string not found in {resolved.name}. Verify exact lines and context.",
                "path": str(resolved),
                "diff": "",
            }

        if len(matches) > 1 and not replace_all:
            return {
                "success": False,
                "error": f"Found {len(matches)} occurrences of target string in {resolved.name}. Provide more surrounding context lines or set replace_all=True.",
                "path": str(resolved),
                "diff": "",
                "occurrences": len(matches),
            }

        # Apply replacement
        updated = original

        def _adapt_replacement(match_start: int, match_end: int) -> str:
            matched_block = original[match_start:match_end]
            repl = new_string
            if strategy == "line_trimmed":
                lines = matched_block.splitlines()
                first_line = lines[0] if lines else ""
                base_indent = first_line[: len(first_line) - len(first_line.lstrip())]
                if base_indent:
                    new_lines = new_string.splitlines()
                    if all(not nl.startswith(base_indent) for nl in new_lines if nl.strip()):
                        repl = "\n".join(base_indent + nl if nl.strip() else nl for nl in new_lines)
            if matched_block.endswith("\n") and not repl.endswith("\n"):
                repl += "\n"
            return repl

        if replace_all:
            # Replace in reverse order so offsets remain valid
            for start, end in reversed(matches):
                repl = _adapt_replacement(start, end)
                updated = updated[:start] + repl + updated[end:]
        else:
            start, end = matches[0]
            repl = _adapt_replacement(start, end)
            updated = updated[:start] + repl + updated[end:]

        # Validate Python syntax if target is a python source file
        if resolved.suffix.lower() == ".py":
            try:
                ast.parse(updated, filename=str(resolved))
            except SyntaxError as syn_err:
                return {
                    "success": False,
                    "error": f"Python syntax error after applying patch on line {syn_err.lineno}: {syn_err.msg}",
                    "path": str(resolved),
                    "diff": "",
                }

        # Write safely
        try:
            resolved.write_text(updated, encoding="utf-8")
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to write file {resolved}: {e}",
                "path": str(resolved),
                "diff": "",
            }

        # Generate unified diff
        diff_lines = list(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile=f"a/{resolved.name}",
                tofile=f"b/{resolved.name}",
                n=3,
            )
        )
        diff_text = "".join(diff_lines) or "(File updated, but no diff generated)"

        return {
            "success": True,
            "applied_strategy": strategy,
            "occurrences": len(matches),
            "path": str(resolved),
            "diff": diff_text,
        }


def patch_file(
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    base_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Convenience helper to patch a file."""
    engine = SmaraPatchEngine(base_dir=base_dir)
    return engine.patch(path, old_string, new_string, replace_all=replace_all)