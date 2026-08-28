import json
import os
from pathlib import Path

import pytest

from smara.desktop_executor import (
    DesktopRunner,
    _load_state,
    _save_state,
    delete_local_credential,
    execute_step,
    local_credential_summaries,
    resolve_local_credential,
    save_local_credential,
)


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


def test_local_credential_vault_injects_only_requested_alias_and_redacts_output(monkeypatch, tmp_path: Path):
    vault = tmp_path / "credentials.json"
    monkeypatch.setenv("SMARA_DESKTOP_CREDENTIALS", str(vault))
    secret = "local-only-test-secret"
    save_local_credential("TAVILY_API_KEY", secret, "tavily")
    assert secret not in vault.read_text(encoding="utf-8") if os.name == "nt" else True
    assert local_credential_summaries()[0]["name"] == "TAVILY_API_KEY"
    assert resolve_local_credential("TAVILY_API_KEY") == secret
    state = {"capabilities": ["local_terminal"], "allowed_roots": [str(tmp_path)], "terminal_allowlist": ["python"]}
    result = json.loads(execute_step({
        "required_capability": "local_terminal",
        "executor_payload": {
            "argv": ["python", "-c", "import os; print(os.environ['TAVILY_API_KEY'])"],
            "cwd": str(tmp_path),
            "credential_env": ["TAVILY_API_KEY"],
        },
    }, state))
    assert secret not in result["output"]
    assert "REDACTED LOCAL CREDENTIAL" in result["output"]
    assert result["credential_env"] == ["TAVILY_API_KEY"]
    assert delete_local_credential("TAVILY_API_KEY") is True
    assert local_credential_summaries() == []


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
    if os.name == "nt":
        stored = path.read_text(encoding="utf-8")
        assert "opaque" not in stored
        assert "token_dpapi" in stored


def test_desktop_runner_refreshes_step_before_completion(monkeypatch, tmp_path: Path):
    calls = []
    step = {
        "step_id": "step_1",
        "required_capability": "local_file_read",
        "executor_payload": {"path": str(tmp_path / "note.txt")},
    }

    class Response:
        status_code = 200

        def __init__(self, payload=None):
            self.payload = payload

        def json(self):
            return self.payload or {}

        def raise_for_status(self):
            return None

    class Client:
        def post(self, path, **kwargs):
            calls.append(path)
            if path.endswith("/claim"):
                return Response({"step": step})
            return Response()

    monkeypatch.setattr("smara.desktop_executor.execute_step", lambda *_args: "result")
    state = {
        "smara_url": "https://smara.example",
        "executor_id": "desktop_1",
        "token": "opaque",
        "capabilities": ["local_file_read"],
    }
    assert DesktopRunner(tmp_path / "desktop.json").run_once(Client(), state) is True
    assert calls == [
        "https://smara.example/v1/executors/heartbeat",
        "https://smara.example/v1/executors/claim",
        "https://smara.example/v1/executors/steps/step_1/heartbeat",
        "https://smara.example/v1/executors/steps/step_1/complete",
    ]
