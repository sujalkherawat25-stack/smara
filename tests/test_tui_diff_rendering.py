"""Unit tests for Smara Claude-style TUI diff rendering and interactive prompts."""
from __future__ import annotations

from pathlib import Path
import pytest

from smara.tui import Colors, Glyphs, TerminalRenderer


def test_glyphs_fallback():
    utf8_glyphs = Glyphs(utf8=True)
    assert utf8_glyphs.brand == "✦"
    assert utf8_glyphs.prompt == "❯"
    assert utf8_glyphs.ok == "✓"

    ascii_glyphs = Glyphs(utf8=False)
    assert ascii_glyphs.brand == ">"
    assert ascii_glyphs.prompt == ">"
    assert ascii_glyphs.ok == "OK"


def test_tui_paint_plain_vs_colored():
    tui_color = TerminalRenderer(plain=False)
    painted = tui_color.paint("hello", "GREEN")
    assert Colors.GREEN in painted
    assert "hello" in painted

    tui_plain = TerminalRenderer(plain=True)
    assert tui_plain.paint("hello", "GREEN") == "hello"


def test_render_diff(capsys):
    tui = TerminalRenderer(plain=True)
    sample_diff = """--- a/test.py
+++ b/test.py
@@ -1,3 +1,3 @@
-def foo(): return 1
+def foo(): return 2
 # comment"""
    tui.render_diff(sample_diff, title="Test Diff")
    captured = capsys.readouterr().out
    assert "Test Diff" in captured
    assert "--- a/test.py" in captured
    assert "+++ b/test.py" in captured
    assert "+def foo(): return 2" in captured
    assert "-def foo(): return 1" in captured


def test_confirm_action_non_interactive():
    tui = TerminalRenderer(plain=True)
    assert tui.confirm_action("Run command 'rm -rf /'?", default="n") == "n"
    assert tui.confirm_action("Run tests?", default="y") == "y"


def test_print_tree(tmp_path: Path, capsys):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hello')", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_main.py").write_text("def test(): pass", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Project", encoding="utf-8")

    tui = TerminalRenderer(plain=True)
    tui.print_tree(tmp_path, max_depth=2)
    captured = capsys.readouterr().out

    assert tmp_path.name in captured
    assert "src/" in captured
    assert "main.py" in captured
    assert "README.md" in captured
    assert ".git" not in captured


def test_print_banner_and_tool_events(capsys):
    tui = TerminalRenderer(plain=True)
    tui.print_banner(model_label="sarvam-glm5.3-flash", workspace_path="/workspace", zero_friction=True)
    tui.print_tool_start("file_patch", "Updating config.py")
    tui.print_tool_result("file_patch", ok=True, summary="Patched line 12")
    tui.print_tool_result("file_patch", ok=False, summary="SyntaxError in line 12")

    captured = capsys.readouterr().out
    assert "Smara Autonomous CLI" in captured
    assert "sarvam-glm5.3-flash" in captured
    assert "file_patch" in captured
    assert "Patched line 12" in captured
    assert "SyntaxError in line 12" in captured
