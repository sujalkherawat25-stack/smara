from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from smara.local_agent import LocalAutonomousAgent
from smara.local_agent_runtime import _compact_history, _live_web_fallback_answer, _live_web_query


def test_packaged_top_level_import_can_compact_history():
    """PyInstaller loads the executor modules without a package parent."""
    source_root = Path(__file__).resolve().parents[1] / "src" / "smara"
    code = (
        "import sys; "
        f"sys.path.insert(0, {str(source_root)!r}); "
        "from local_agent_runtime import _compact_history; "
        "assert _compact_history([{'role':'user','content':'ok'}])"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


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


def test_live_web_intent_uses_previous_question_for_short_follow_up():
    context = [{"role": "user", "content": "Is there any chance of rainfall today in Banswara Rajasthan?"}]
    query = _live_web_query("do the web search", context)
    assert query is not None
    assert "Banswara" in query
    assert "live information for" in query


def test_live_web_intent_does_not_override_explicit_opt_out():
    assert _live_web_query("How do I search the web without a live web search?", []) is None
    assert _live_web_query("What is the current git branch?", []) is None


def test_live_web_fallback_is_source_backed():
    raw = '{"action":"local_integration","provider":"tavily","results":[{"title":"Weather","url":"https://weather.example","snippet":"Overcast; 28% chance of rain."}],"citations":["https://weather.example"]}'
    answer = _live_web_fallback_answer(raw, "Banswara weather today")
    assert answer is not None
    assert "https://weather.example" in answer
    assert "28% chance" in answer


def test_shared_turn_preflights_current_web_request(monkeypatch, tmp_path: Path):
    from smara import local_agent_runtime as runtime

    calls = []

    def fake_action(capability, payload):
        calls.append((capability, payload))
        return {
            "action": "local_integration",
            "provider": payload["provider"],
            "operation": "search",
            "results": [{"title": "Official weather", "url": "https://weather.example/today", "snippet": "Rain chance 40%."}],
            "citations": ["https://weather.example/today"],
            "proof": {"results": 1},
        }

    def fake_model(self, history):
        assert any("LIVE_WEB_PREFLIGHT_PRESENT" in str(item.get("content")) for item in history if isinstance(item, dict))
        return {"kind": "answer", "answer": "The live result reports a 40% chance of rain."}

    monkeypatch.setattr(runtime.OpenAICompatiblePlanner, "__call__", fake_model)
    config = runtime.LocalModelConfig(base_url="http://127.0.0.1:1", model="unused")
    result = runtime.run_shared_local_turn(
        prompt="Will it rain in Banswara today?",
        state_path=tmp_path / "desktop.json",
        config=config,
        workspace_id=str(tmp_path),
        action_executor=fake_action,
    )
    assert calls and calls[0][0] == "local_integration"
    assert result["live_web"]["citations"] == ["https://weather.example/today"]
    assert "https://weather.example/today" in result["answer"]


def test_shared_turn_recovers_subject_for_follow_up_after_restart(monkeypatch, tmp_path: Path):
    from smara import local_agent_runtime as runtime
    from smara.local_conversation_memory import SQLiteConversationMemory

    state = tmp_path / "desktop.json"
    memory = SQLiteConversationMemory.for_state(state)
    memory.append_exchange(
        conversation_id="follow-up",
        workspace_id=str(tmp_path),
        user_message="Is there any chance of rainfall today in Banswara Rajasthan?",
        assistant_message="I need to check live data.",
    )
    calls = []

    def fake_action(capability, payload):
        calls.append(payload)
        return {"action": "local_integration", "provider": "tavily", "results": [{"title": "Forecast", "url": "https://weather.example", "snippet": "Rain likely."}], "citations": ["https://weather.example"]}

    monkeypatch.setattr(runtime.OpenAICompatiblePlanner, "__call__", lambda self, history: {"kind": "answer", "answer": "Live result received."})
    result = runtime.run_shared_local_turn(
        prompt="do the web search",
        state_path=state,
        config=runtime.LocalModelConfig(base_url="http://127.0.0.1:1", model="unused"),
        conversation_id="follow-up",
        workspace_id=str(tmp_path),
        action_executor=fake_action,
    )
    assert calls and "Banswara" in calls[0]["query"]
    assert result["live_web"]["query"].startswith("Is there any chance")


def test_explicit_quick_lane_forces_bounded_live_web_preflight(monkeypatch, tmp_path: Path):
    from smara import local_agent_runtime as runtime

    calls = []

    def fake_action(capability, payload):
        calls.append((capability, payload))
        return {
            "action": "local_integration",
            "provider": payload["provider"],
            "results": [{"title": "PSF license", "url": "https://www.python.org/psf/license/", "snippet": "PSF License Agreement."}],
            "citations": ["https://www.python.org/psf/license/"],
        }

    monkeypatch.setattr(runtime.OpenAICompatiblePlanner, "__call__", lambda self, history: {"kind": "answer", "answer": "PSF License Agreement."})
    result = runtime.run_shared_local_turn(
        prompt="Find the official Python Software Foundation license page.",
        state_path=tmp_path / "desktop.json",
        config=runtime.LocalModelConfig(base_url="http://127.0.0.1:1", model="unused"),
        workspace_id=str(tmp_path),
        action_executor=fake_action,
        research_mode="quick",
    )
    assert calls and calls[0][1]["operation"] == "search"
    assert calls[0][1]["query"].startswith("Find the official Python")
    assert result["research_mode"] == "quick"
