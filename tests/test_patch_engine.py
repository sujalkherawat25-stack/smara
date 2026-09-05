"""Tests for Smara Surgical Patch Engine."""
import tempfile
from pathlib import Path
import pytest
from smara.patch_engine import SmaraPatchEngine, patch_file
from smara.agent_tools import patch_file_tool


def test_exact_patch():
    with tempfile.TemporaryDirectory() as tmpdir:
        f = Path(tmpdir) / "sample.py"
        f.write_text("def hello():\n    print('old')\n", encoding="utf-8")

        res = patch_file(str(f), "print('old')", "print('new')")
        assert res["success"]
        assert res["applied_strategy"] == "exact"
        assert "print('new')" in f.read_text(encoding="utf-8")
        assert "+    print('new')" in res["diff"]


def test_whitespace_and_indentation_resilience():
    with tempfile.TemporaryDirectory() as tmpdir:
        f = Path(tmpdir) / "sample.py"
        # Content has 4 spaces
        f.write_text("def compute():\n    x = 1\n    y = 2\n    return x + y\n", encoding="utf-8")

        # Search string has 2 spaces or line-trimmed
        old_str = "x = 1\ny = 2"
        new_str = "x = 10\ny = 20"

        res = patch_file(str(f), old_str, new_str)
        assert res["success"]
        content = f.read_text(encoding="utf-8")
        assert "x = 10" in content


def test_already_applied_idempotency():
    with tempfile.TemporaryDirectory() as tmpdir:
        f = Path(tmpdir) / "sample.py"
        f.write_text("VAL = 'target_value'\n", encoding="utf-8")

        # Request replacing old value with target_value when target_value is already present
        res = patch_file(str(f), "VAL = 'initial'", "VAL = 'target_value'")
        assert res["success"]
        assert res["applied_strategy"] == "already_applied"
        assert res["occurrences"] == 0


def test_python_ast_syntax_error_rejection():
    with tempfile.TemporaryDirectory() as tmpdir:
        f = Path(tmpdir) / "sample.py"
        f.write_text("def ok():\n    pass\n", encoding="utf-8")

        # Patch in invalid Python syntax
        res = patch_file(str(f), "pass", "def broken(:")
        assert not res["success"]
        assert "Python syntax error" in res["error"]
        # Original file must remain untouched
        assert "def ok():" in f.read_text(encoding="utf-8")


def test_patch_tool_interface():
    with tempfile.TemporaryDirectory() as tmpdir:
        f = Path(tmpdir) / "test.txt"
        f.write_text("Alpha\nBeta\nGamma\n", encoding="utf-8")

        out = patch_file_tool(str(f), "Beta", "Beta_Updated")
        assert "Patch applied successfully" in out
        assert "Beta_Updated" in f.read_text(encoding="utf-8")
