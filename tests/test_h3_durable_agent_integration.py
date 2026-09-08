import json
import sys
import urllib.error
from pathlib import Path

import pytest

from smara.autonomous_agent import SmaraAutonomousAgent
from smara.harness import Budget, SessionEngine, ToolCall, ToolResult


class Response:
    def __init__(self, payload, request_id):
        self.payload = payload; self.headers = {"x-request-id": request_id}
    def __enter__(self): return self
    def __exit__(self, *_): return None
    def read(self): return json.dumps(self.payload).encode()


def tool_response(call_id, name, arguments):
    return {"choices": [{"finish_reason": "tool_calls", "message": {"content": "", "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]}}], "usage": {"total_tokens": 100}}


def final_response(answer="done"):
    return {"choices": [{"finish_reason": "stop", "message": {"content": f"FINAL ANSWER: {answer}"}}], "usage": {"total_tokens": 50}}


def test_real_loop_journals_edit_failed_test_repair_and_pass(tmp_path: Path, monkeypatch):
    (tmp_path / "test_calc.py").write_text("from calc import value\n\ndef test_value(): assert value() == 2\n")
    python = str(Path(sys.executable))
    sequence = iter([
        tool_response("write", "file_write", {"path": "calc.py", "content": "def value():\n    return 1\n"}),
        tool_response("fail", "terminal", {"command": f"& '{python}' -m pytest -q", "timeout": 30}),
        tool_response("repair", "patch", {"path": "calc.py", "old_string": "return 1", "new_string": "return 2"}),
        tool_response("pass", "terminal", {"command": f"& '{python}' -m pytest -q", "timeout": 30}),
        final_response(),
    ])
    requests = []
    def urlopen(*args, **kwargs):
        requests.append(1); return Response(next(sequence), f"request-{len(requests)}")
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    budget = Budget(120, 10, 10, 500_000, 1)
    session = SessionEngine(tmp_path, "vertical", budget=budget, constrained=False)
    result = SmaraAutonomousAgent(api_key="fake", workspace_root=tmp_path, session_engine=session).run("repair calc", max_iterations=8)
    assert result["completed"] is True
    assert result["session"]["status"] == "completed"
    record = session.inspect()
    assert [item["name"] for item in record["calls"]] == ["file_write", "terminal", "patch", "terminal"]
    assert not any(item["name"] == "agent_turn" for item in record["calls"])
    events = record["events"]
    assert len([event for event in events if event["type"] == "provider_request"]) == 5
    evidence = [event["payload"] for event in events if event["type"] == "evidence"]
    assert [item["passed"] for item in evidence] == [False, True]
    assert evidence[-1]["subject_revision"] == record["calls"][-1]["after_revision"]


def test_inner_model_budget_stops_before_second_provider_call(tmp_path: Path, monkeypatch):
    responses = [tool_response("read", "file_read", {"file_path": "x.txt"})]
    (tmp_path / "x.txt").write_text("x")
    calls = []
    def urlopen(*args, **kwargs):
        calls.append(1); return Response(responses.pop(0), "only-request")
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    session = SessionEngine(tmp_path, "budgeted", budget=Budget(60, 5, 1, 100_000, 1))
    result = SmaraAutonomousAgent(api_key="fake", workspace_root=tmp_path, session_engine=session).run("read x", max_iterations=5)
    assert result["status"] == "budget_exhausted"
    assert len(calls) == 1
    assert session.inspect()["state"]["usage"]["model_calls"] == 1


def test_persisted_tool_result_is_replayed_without_duplicate_mutation(tmp_path: Path):
    session = SessionEngine(tmp_path, "dedupe", budget=Budget(tool_calls=2))
    session.begin_incremental("write once")
    call = ToolCall("same-call", "file_write", {"path": "x.txt", "content": "one"}, str(tmp_path.resolve()))
    executions = []
    def execute(raw):
        executions.append(1)
        (tmp_path / "x.txt").write_text("one")
        return ToolResult("same-call", "ok", changed_paths=("x.txt",))
    session.execute_incremental(call, execute)
    replayed = session.execute_incremental(call, execute)
    assert replayed.ok
    assert executions == [1]
    assert (tmp_path / "x.txt").read_text() == "one"


def test_cancellation_during_provider_retry_prevents_next_dispatch(tmp_path: Path,monkeypatch):
    from smara.harness import BudgetExceeded
    session=SessionEngine(tmp_path,"provider-cancel",budget=Budget(60,2,3,500_000,1));session.begin_incremental("cancel")
    calls=[]
    def urlopen(*args,**kwargs):
        calls.append(1);session.cancel();raise urllib.error.URLError("transient")
    monkeypatch.setattr("urllib.request.urlopen",urlopen)
    agent=SmaraAutonomousAgent(api_key="fake",workspace_root=tmp_path,session_engine=session)
    with pytest.raises(BudgetExceeded,match="cancelled"):agent._call_model_api([{"role":"user","content":"x"}],max_tokens=32)
    assert calls==[1]
