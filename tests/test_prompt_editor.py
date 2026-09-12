"""Unit tests for prompt editor, slash commands, and autocompletions."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock
import pytest

from smara.prompt_editor import (
    PROMPT_TOOLKIT_AVAILABLE,
    SLASH_COMMANDS,
    SmaraCompleter,
    SmaraPromptSession,
)


def test_slash_commands_list():
    command_names = [cmd for cmd, _ in SLASH_COMMANDS]
    assert "/model" in command_names
    assert "/rules" in command_names
    assert "/mcp" in command_names
    assert "/diff" in command_names
    assert "/test" in command_names
    assert "/graph" in command_names


@pytest.mark.skipif(not PROMPT_TOOLKIT_AVAILABLE, reason="prompt_toolkit required")
def test_completer_slash_commands(tmp_path: Path):
    completer = SmaraCompleter(tmp_path)
    
    doc = MagicMock()
    doc.text_before_cursor = "/mo"
    doc.get_word_before_cursor.return_value = "/mo"
    
    completions = list(completer.get_completions(doc, None))
    assert len(completions) >= 1
    assert completions[0].text == "/model"


@pytest.mark.skipif(not PROMPT_TOOLKIT_AVAILABLE, reason="prompt_toolkit required")
def test_completer_file_autocomplete(tmp_path: Path):
    (tmp_path / "server.py").write_text("# server code", encoding="utf-8")
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")

    completer = SmaraCompleter(tmp_path)
    
    doc = MagicMock()
    doc.text_before_cursor = "Please look at @ser"
    doc.get_word_before_cursor.return_value = "@ser"
    
    completions = list(completer.get_completions(doc, None))
    assert len(completions) == 1
    assert completions[0].text == "@server.py"


def test_prompt_session_instantiation(tmp_path: Path):
    session = SmaraPromptSession(tmp_path)
    assert session.workspace_root == tmp_path.resolve()
