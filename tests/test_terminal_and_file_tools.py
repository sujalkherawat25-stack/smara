"""Tests for terminal_execute, file_write, and browser_action_tool."""
import json
import os
import sys
import tempfile
from pathlib import Path
import pytest
from smara.agent_tools import terminal_execute, file_write, browser_action_tool


def test_terminal_execute_basic():
    cmd = "echo SmaraTerminalReady"
    out = terminal_execute(cmd)
    assert "[Exit Code: 0]" in out
    assert "SmaraTerminalReady" in out


def test_terminal_execute_with_cwd():
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "marker.txt"
        test_file.write_text("marker_content", encoding="utf-8")

        if sys.platform == "win32":
            cmd = "Get-Content marker.txt"
        else:
            cmd = "cat marker.txt"

        out = terminal_execute(cmd, cwd=tmpdir)
        assert "[Exit Code: 0]" in out
        assert "marker_content" in out


def test_terminal_execute_nonexistent_cwd():
    out = terminal_execute("echo test", cwd="Z:/nonexistent/directory/path/123")
    assert "Error: Working directory does not exist" in out


def test_file_write_creates_nested_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        nested_file = Path(tmpdir) / "sub" / "dir" / "created.py"
        code = "def sample():\n    return 42\n"

        out = file_write(str(nested_file), code)
        assert "successfully written" in out
        assert nested_file.exists()
        assert nested_file.read_text(encoding="utf-8") == code


def test_file_write_rejects_syntax_error():
    with tempfile.TemporaryDirectory() as tmpdir:
        broken_file = Path(tmpdir) / "broken.py"
        bad_code = "def syntax_error(\n"

        out = file_write(str(broken_file), bad_code)
        assert "File Write Error: Python syntax error" in out
        assert not broken_file.exists()


def test_browser_action_scrape():
    # Test scraping a public HTTP endpoint
    out = browser_action_tool(action="scrape", url="https://example.com")
    data = json.loads(out)
    assert data.get("success") is True
    assert "example" in data.get("title", "").lower() or "example" in data.get("content_snippet", "").lower()


def test_browser_action_screenshot():
    with tempfile.TemporaryDirectory() as tmpdir:
        screen_path = Path(tmpdir) / "test_shot.png"
        out = browser_action_tool(action="screenshot", url="https://example.com", output_path=str(screen_path))
        data = json.loads(out)
        assert data.get("action") == "screenshot"
        assert data.get("success") is True
