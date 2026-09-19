"""Offline H0 regressions for the legacy CLI execution spine."""
from __future__ import annotations

import json
from pathlib import Path

from smara.autonomous_agent import SmaraAutonomousAgent
from smara.browser_sidecar import BrowserSidecarEngine
from smara.goal_engine import GoalPlanner, GoalRunner
from smara.self_healing import SelfHealingEngine


def _response(content: str = "", name: str | None = None, args: dict | None = None) -> dict:
    message = {"role": "assistant", "content": content}
    if name:
        message["tool_calls"] = [{"id": "call", "type": "function", "function": {"name": name, "arguments": json.dumps(args or {})}}]
    return {"choices": [{"message": message, "finish_reason": "tool_calls" if name else "stop"}]}


def test_failed_test_cannot_complete_or_be_cleared_by_command_text(tmp_path: Path):
    agent = SmaraAutonomousAgent(api_key="fake", workspace_root=tmp_path)
    responses = iter([
        _response(name="file_write", args={"path": "example.py", "content": "x = 1\n"}),
        _response(name="terminal", args={"command": "exit 1"}),
        _response("FINAL ANSWER: all tests passed"),
    ])
    agent._call_model_api = lambda *_args, **_kwargs: next(responses)  # type: ignore[method-assign]
    agent._build_dynamic_context = lambda: ""  # type: ignore[method-assign]
    result = agent.run("repair", max_iterations=3)
    assert result["status"] == "tool_error"
    assert not result["completed"]
    assert any("Verification Gate" in row["observation"] for row in result["trace"])


def test_mutating_batch_is_serialized_and_test_receipt_matches_current_revision(tmp_path: Path):
    agent = SmaraAutonomousAgent(api_key="fake", workspace_root=tmp_path)
    responses = iter([
        {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [
            {"id": "write", "type": "function", "function": {"name": "file_write", "arguments": json.dumps({"path": "x.py", "content": "x = 1\n"})}},
            {"id": "test", "type": "function", "function": {"name": "terminal", "arguments": json.dumps({"command": "python -m py_compile x.py"})}},
        ]}, "finish_reason": "tool_calls"}]},
        _response("FINAL ANSWER: repaired"),
    ])
    agent._call_model_api = lambda *_args, **_kwargs: next(responses)  # type: ignore[method-assign]
    agent._build_dynamic_context = lambda: ""  # type: ignore[method-assign]
    result = agent.run("repair", max_iterations=2)
    assert result["status"] == "completed"
    assert [row["tool_name"] for row in result["trace"][:2]] == ["file_write", "terminal"]


def test_screenshot_and_unimplemented_action_fail_closed(tmp_path: Path):
    engine = BrowserSidecarEngine(tmp_path)
    engine.browser_bin = None
    shot = engine.capture_screenshot("https://example.invalid", tmp_path / "missing.png")
    assert shot["success"] is False
    assert shot["file_path"] is None
    flow = engine.run_e2e_flow("unsupported", [{"action": "click", "target": "#missing"}])
    assert flow.success is False
    assert flow.steps[0].status == "failed"


def test_goal_runner_rejects_failed_outputs_and_schedules_reverse_dag(tmp_path: Path):
    runner = GoalRunner(tmp_path)
    failed = runner.execute_goal("check", executor_fn=lambda *_: {"ok": False, "message": "not run"})
    assert failed.status == "failed"
    plan = GoalPlanner.plan("check", model_reasoner=lambda _: [
        {"id": "second", "capability": "local_file_read", "dependencies": ["first"]},
        {"id": "first", "capability": "local_file_read"},
    ])
    completed = runner.execute_goal("check", executor_fn=lambda *_: {"ok": True, "exit_code": 0}, model_reasoner=lambda _: [item.to_dict() for item in plan])
    assert completed.status == "completed"
    assert [step.id for step in completed.steps if step.status == "completed"] == ["second", "first"]


def test_no_model_terminal_fallback_never_invents_successful_command():
    objective = "perform a real deployment"
    step = GoalPlanner.plan(objective)[1]
    assert step.capability == "local_terminal"
    assert "argv" not in step.payload
    assert "command" not in step.payload
    assert "recipe" not in step.payload

    repaired = SelfHealingEngine().diagnose_failure(
        "missing required parameter: argv",
        "local_terminal",
        {"objective": objective},
    )
    assert repaired["mutated_payload"] == {"objective": objective}


def test_self_healing_treats_terminal_exit_code_as_failure():
    calls = []

    def execute(_capability, _payload, _title):
        calls.append(True)
        return {"action": "local_terminal", "exit_code": 7, "output": "command failed"}

    result = SelfHealingEngine(max_attempts=3).execute_with_healing(
        execute,
        "local_terminal",
        {"argv": ["python", "-c", "raise SystemExit(7)"]},
        "Run the command",
    )
    assert calls == [True, True, True]
    assert result["_self_healed"] is False
    assert result["_attempts"] == 3
