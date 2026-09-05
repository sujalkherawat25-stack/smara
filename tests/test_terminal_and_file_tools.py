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


def test_terminal_python_alignment():
    """Verify terminal_execute prioritizes the current sys.executable environment."""
    out = terminal_execute("python -c \"import sys; print(sys.executable)\"")
    assert "[Exit Code: 0]" in out
    assert str(Path(sys.executable).resolve()).lower() in out.lower()


def test_final_answer_placeholder_rejection():
    """Verify that template placeholders like <exact answer> are never emitted as final answers."""
    from smara.autonomous_agent import SmaraAutonomousAgent, _is_instruction_placeholder
    assert _is_instruction_placeholder("<exact answer>") is True
    assert _is_instruction_placeholder("[exact answer]") is True
    assert _is_instruction_placeholder("exact answer") is True
    assert _is_instruction_placeholder("42") is False
    assert _is_instruction_placeholder("rabbit") is False

    assert SmaraAutonomousAgent._clean_final_answer("FINAL ANSWER: <exact answer>") == ""
    assert SmaraAutonomousAgent._clean_final_answer("FINAL ANSWER: rabbit") == "rabbit"
    assert SmaraAutonomousAgent._clean_final_answer("I was unable to find page 54. Therefore, the answer is rabbit.") == "rabbit"


def test_pdf_search_empty_query_page_extraction():
    """Verify pdf_search extracts full page structure when query is omitted."""
    from smara.agent_tools import pdf_search
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_file = Path(tmpdir) / "test_doc.pdf"
        try:
            import pypdf
            writer = pypdf.PdfWriter()
            writer.add_blank_page(width=300, height=300)
            writer.add_blank_page(width=300, height=300)
            with open(pdf_file, "wb") as f:
                writer.write(f)

            res = pdf_search(str(pdf_file), page=1)
            assert "Physical Page 1" in res
            assert "Total pages: 2" in res
        except ImportError:
            pytest.skip("pypdf not installed")

