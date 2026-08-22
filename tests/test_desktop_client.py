import json
from pathlib import Path

import pytest

from smara.desktop_executor import execute_step, pair, _load_state, _save_state


def test_desktop_file_read_write_stays_inside_approved_root(tmp_path: Path):
    state = {"capabilities": ["local_file_read", "local_file_write"], "allowed_roots": [str(tmp_path)]}
    source = tmp_path / "note.txt"
    source.write_text("hello", encoding="utf-8")
    read = json.loads(execute_step({"required_capability": "local_file_read", "executor_payload": {"path": str(source)}}, state))
    assert read["sha256"]
    assert read["content_shared"] is False
    written = json.loads(execute_step({"required_capability": "local_file_write", "executor_payload": {"path": str(tmp_path / "out.txt"), "content": "done"}}, state))
    assert written["bytes_written"] == 4
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "done"
    with pytest.raises(RuntimeError, match="outside"):
        execute_step({"required_capability": "local_file_read", "executor_payload": {"path": str(tmp_path.parent / "note.txt")}}, state)


def test_desktop_terminal_requires_allowlist_and_rejects_shell_operators(tmp_path: Path):
    state = {"capabilities": ["local_terminal"], "allowed_roots": [str(tmp_path)], "terminal_allowlist": ["python"]}
    result = json.loads(execute_step({"required_capability": "local_terminal", "executor_payload": {"argv": ["python", "-c", "print(2+2)"], "cwd": str(tmp_path)}}, state))
    assert result["exit_code"] == 0
    assert "4" in result["output"]
    with pytest.raises(RuntimeError, match="Shell operators"):
        execute_step({"required_capability": "local_terminal", "executor_payload": {"command": "python -c \"print(1)\" & whoami", "cwd": str(tmp_path)}}, state)


def test_desktop_refuses_unapproved_step_and_undeclared_browser(tmp_path: Path):
    state = {"capabilities": ["local_browser"], "allowed_roots": [str(tmp_path)], "browser_domains": ["example.com"]}
    with pytest.raises(RuntimeError, match="approval"):
        execute_step({"requires_approval": True, "required_capability": "local_browser", "executor_payload": {"url": "https://example.com"}}, state)
    with pytest.raises(RuntimeError, match="not declared"):
        execute_step({"required_capability": "local_file_read", "executor_payload": {"path": str(tmp_path / "x")}}, state)


def test_desktop_state_round_trip_is_json(tmp_path: Path):
    path = tmp_path / "desktop.json"
    _save_state(path, {"executor_id": "desktop_1", "token": "opaque", "smara_url": "http://smara"})
    assert _load_state(path)["executor_id"] == "desktop_1"
