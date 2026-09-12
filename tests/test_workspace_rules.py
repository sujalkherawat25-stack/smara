"""Unit tests for workspace coding rules and guidelines discovery."""
from __future__ import annotations

from pathlib import Path
import pytest

from smara.workspace_rules import (
    RULE_FILENAMES,
    discover_workspace_rules,
    format_rules_for_prompt,
)


def test_no_rules_found(tmp_path: Path):
    res = discover_workspace_rules(tmp_path)
    assert res["found"] is False
    assert res["content"] == ""
    assert res["filename"] is None
    assert format_rules_for_prompt(res) == ""


def test_discover_rules_in_root(tmp_path: Path):
    rules_file = tmp_path / ".smararules"
    rules_file.write_text("Always use strict typing and ruff format.", encoding="utf-8")

    res = discover_workspace_rules(tmp_path)
    assert res["found"] is True
    assert res["filename"] == ".smararules"
    assert "Always use strict typing" in res["content"]

    prompt_sec = format_rules_for_prompt(res)
    assert "Project-Specific Instructions & Rules (from `.smararules`):" in prompt_sec
    assert "Always use strict typing" in prompt_sec


def test_discover_rules_priority(tmp_path: Path):
    # Both CLAUDE.md and .smararules exist -> .smararules has higher precedence in RULE_FILENAMES
    (tmp_path / "CLAUDE.md").write_text("Claude rules", encoding="utf-8")
    (tmp_path / ".smararules").write_text("Smara rules", encoding="utf-8")

    res = discover_workspace_rules(tmp_path)
    assert res["found"] is True
    assert res["filename"] == ".smararules"
    assert res["content"] == "Smara rules"


def test_discover_rules_github_subfolder(tmp_path: Path):
    gh_dir = tmp_path / ".github"
    gh_dir.mkdir()
    (gh_dir / "SMARA.md").write_text("GitHub folder guidelines", encoding="utf-8")

    res = discover_workspace_rules(tmp_path)
    assert res["found"] is True
    assert res["filename"] == "SMARA.md"
    assert "GitHub folder guidelines" in res["content"]


def test_rules_content_truncation(tmp_path: Path):
    rules_file = tmp_path / "CLAUDE.md"
    long_content = "X" * 1000
    rules_file.write_text(long_content, encoding="utf-8")

    res = discover_workspace_rules(tmp_path, max_chars=100)
    assert res["found"] is True
    assert len(res["content"]) > 100
    assert "Workspace rules truncated" in res["content"]
