from __future__ import annotations

from pathlib import Path

from smara.local_agent import LocalAutonomousAgent
from smara.local_agent_runtime import _compact_history


def test_compaction_keeps_newest_history_in_chronological_order():
    history = [{"role": "user", "content": "objective"}]
    history.extend({"role": "tool", "content": f"tool {index} " + "x" * 400} for index in range(12))
    compacted = _compact_history(history, max_chars=1_500)
    contents = [item["content"] for item in compacted]
    assert contents[0] == "objective"
    tail_numbers = [int(value.split()[1]) for value in contents[1:] if value.startswith("tool ")]
    assert tail_numbers == sorted(tail_numbers)
    assert sum(len(value) for value in contents) <= 1_500


def test_agent_marks_structured_action_error_as_unsuccessful(tmp_path: Path):
    plans = iter([
        {"kind": "local_action", "title": "read", "objective": "read", "capability": "local_file_read", "payload": {"path": "missing.txt"}},
        {"kind": "answer", "answer": "I could not read the file."},
    ])
    agent = LocalAutonomousAgent(tmp_path / "desktop.json", action_executor=lambda *_: {"ok": False, "error": "missing"})
    result = agent.run_turn("read it", model_callable=lambda _history: next(plans))
    assert result["completed"] is True
    assert result["steps"][0]["ok"] is False


def test_agent_stops_repeated_local_action(tmp_path: Path):
    action = {"kind": "local_action", "title": "repeat", "objective": "repeat", "capability": "local_calculate", "payload": {"expression": "1+1"}}
    agent = LocalAutonomousAgent(tmp_path / "desktop.json", max_steps=8, action_executor=lambda *_: {"ok": False, "error": "bad"})
    result = agent.run_turn("repeat", model_callable=lambda _history: action)
    assert result["completed"] is False
    assert result["failure_reason"] == "repeated_action"
    assert len(result["steps"]) == 3


def test_strip_thinking_removes_scratchpads():
    from smara.local_agent_runtime import _strip_thinking
    assert _strip_thinking("<think>Analyzing user query...</think>Final response") == "Final response"
    assert _strip_thinking("<thinking>Step 1\nStep 2</thinking>Result") == "Result"
    assert _strip_thinking("[THOUGHT]Internal thought[/THOUGHT]Clean output") == "Clean output"


def test_parse_plan_dynamic_formats():
    from smara.local_agent_runtime import _parse_plan
    # Flexible action format
    plan1 = _parse_plan('{"action": "local_python", "payload": {"code": "import urllib.request"}}')
    assert plan1 is not None
    assert plan1["kind"] == "local_action"
    assert plan1["capability"] == "local_python"
    assert plan1["payload"]["code"] == "import urllib.request"

    # Embedded code format
    plan2 = _parse_plan('{"action": "local_python", "code": "print(123)"}')
    assert plan2 is not None
    assert plan2["kind"] == "local_action"
    assert plan2["capability"] == "local_python"
    assert plan2["payload"]["code"] == "print(123)"

    # Text embedded JSON with thinking
    plan3 = _parse_plan('<think>Let me fetch the data</think>\n```json\n{"action": "local_python", "payload": {"code": "fetch()"}}\n```')
    assert plan3 is not None
    assert plan3["capability"] == "local_python"

