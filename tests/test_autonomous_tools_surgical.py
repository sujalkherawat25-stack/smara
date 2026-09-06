"""Tests for surgical agent tools and autonomous execution primitives."""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from smara.agent_tools import (
    file_read,
    file_write,
    list_directory,
    search_files,
    code_graph_tool,
)
from smara.autonomous_agent import (
    SmaraAutonomousAgent,
    get_tool_schemas,
    _extract_text_tool_calls,
)


def test_file_read_windowing(tmp_path: Path):
    test_file = tmp_path / "sample.py"
    lines = [f"line_{i} = {i}" for i in range(1, 51)]
    test_file.write_text("\n".join(lines), encoding="utf-8")

    # Read first 10 lines
    res = file_read(str(test_file), offset=1, limit=10)
    assert "[File: sample.py (50 lines total) - Showing lines 1 to 10]" in res
    assert "1 | line_1 = 1" in res
    assert "10 | line_10 = 10" in res
    assert "11 | line_11 = 11" not in res

    # Read slice from line 20 to 24
    res_slice = file_read(str(test_file), offset=20, limit=5)
    assert "Showing lines 20 to 24" in res_slice
    assert "20 | line_20 = 20" in res_slice
    assert "24 | line_24 = 24" in res_slice
    assert "25 | line_25 = 25" not in res_slice


def test_list_directory_formatting(tmp_path: Path):
    d1 = tmp_path / "sub1"
    d1.mkdir()
    (d1 / "test_a.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "test_b.py").write_text("print('b')", encoding="utf-8")
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref", encoding="utf-8")

    res = list_directory(str(tmp_path), max_depth=2)
    assert f"[Directory: {tmp_path.name}]" in res
    assert "sub1" in res
    assert "test_a.txt" in res
    assert "test_b.py" in res
    assert ".git" not in res


def test_search_files(tmp_path: Path):
    f1 = tmp_path / "mod1.py"
    f1.write_text("def find_my_secret_token():\n    return 42\n", encoding="utf-8")
    f2 = tmp_path / "mod2.py"
    f2.write_text("x = 100\n", encoding="utf-8")

    res = search_files(query="find_my_secret_token", path=str(tmp_path))
    assert "mod1.py" in res
    assert "find_my_secret_token" in res
    assert "mod2.py" not in res


def test_code_graph_tool(tmp_path: Path):
    code = """
class AlphaService:
    def execute(self):
        return True

def run_service():
    return AlphaService().execute()
"""
    (tmp_path / "service.py").write_text(code, encoding="utf-8")

    res = code_graph_tool(operation="inspect_symbol", symbol="AlphaService", workspace_root=tmp_path)
    assert "AlphaService" in res
    assert "execute" in res


def test_get_tool_schemas():
    full_schemas = get_tool_schemas("full")
    names = [s["function"]["name"] for s in full_schemas]
    assert "file_read" in names
    assert "file_write" in names
    assert "list_directory" in names
    assert "search_files" in names
    assert "code_graph" in names
    assert "terminal" in names

    worker_schemas = get_tool_schemas("worker")
    w_names = [s["function"]["name"] for s in worker_schemas]
    assert "file_read" in w_names
    assert "list_directory" in w_names


def test_extract_text_tool_calls_json_and_xml():
    # JSON block
    text_json = '```json\n{"name": "file_read", "arguments": {"path": "main.py", "offset": 1, "limit": 20}}\n```'
    calls_json = _extract_text_tool_calls(text_json)
    assert len(calls_json) == 1
    name_json, args_json = calls_json[0]
    assert name_json == "file_read"
    assert args_json["path"] == "main.py"

    # XML tags
    text_xml = '<tool_call>\n<name>bash</name>\n<arguments>\n{"command": "pytest"}\n</arguments>\n</tool_call>'
    calls_xml = _extract_text_tool_calls(text_xml)
    assert len(calls_xml) == 1
    name_xml, args_xml = calls_xml[0]
    assert name_xml == "bash"
    assert args_xml["command"] == "pytest"
