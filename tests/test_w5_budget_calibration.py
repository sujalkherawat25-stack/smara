"""Unit tests for turn budget calibration, completion after validation, and todo guard."""
import json
import pathlib
import sys
import pytest

from smara.autonomous_agent import SmaraAutonomousAgent
from smara.harness import Budget, SessionEngine, ToolBroker, ToolCall


def test_budget_exhaustion_does_not_falsely_complete(tmp_path, monkeypatch):
    """When budget is exhausted, even if state is valid, completed is False."""
    session = SessionEngine(tmp_path, "exhaustion_test", budget=Budget(120, 2, 2, 500_000, 2), constrained=False)
    agent = SmaraAutonomousAgent(api_key="fixture", profile="coding", workspace_root=tmp_path, session_engine=session, max_iterations=2)
    
    def mock_call(messages, tools=None):
        return {
            "choices": [{
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": "Still working on analyzing the repository structure...",
                    "tool_calls": []
                }
            }]
        }
    
    monkeypatch.setattr(agent, "_call_model_api", mock_call)
    result = agent.run("Task that cannot finish in 2 iterations", max_iterations=2)
    assert not result["completed"]
    assert result["status"] == "budget_exhausted"


def test_todo_planning_turn_guard(tmp_path):
    """Calling todo repeatedly without execution tools triggers the planning notice."""
    session = SessionEngine(tmp_path, "todo_guard_test", budget=Budget(120, 10, 10, 500_000, 2), constrained=False)
    agent = SmaraAutonomousAgent(api_key="fixture", profile="coding", workspace_root=tmp_path, session_engine=session)
    agent.execute_tool("todo", {"todos": [{"id": "1", "content": "plan", "status": "pending"}]})
    agent.execute_tool("todo", {"todos": [{"id": "1", "status": "completed"}]})
    assert agent.task_planner.has_items()


def test_cancellation_canary_deterministic_flow(tmp_path):
    """Process cancellation safely terminates process tree within budget."""
    canary = tmp_path / "orphan_test.txt"
    script = tmp_path / "script.py"
    script.write_text(f"import time, pathlib\ntime.sleep(15)\npathlib.Path(r'{canary}').write_text('bad')\n", encoding="utf-8")
    session = SessionEngine(tmp_path, "cancel_test", budget=Budget(120, 9, 9, 500_000, 2), constrained=False)
    agent = SmaraAutonomousAgent(api_key="fixture", profile="coding", workspace_root=tmp_path, session_engine=session)
    start_resp = json.loads(agent.execute_tool("process_start", {"argv": [sys.executable, str(script)], "cwd": str(tmp_path)}))
    proc_id = start_resp.get("meta", {}).get("process_id")
    if not proc_id:
        inner_meta = json.loads(start_resp.get("output", "{}"))
        proc_id = inner_meta.get("process_id")
    assert proc_id is not None
    cancel_resp = json.loads(agent.execute_tool("process_cancel", {"process_id": proc_id}))
    assert cancel_resp["status"] == "cancelled"
    assert not canary.exists()

