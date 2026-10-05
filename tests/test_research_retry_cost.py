import json
import pytest

from smara.autonomous_agent import SmaraAutonomousAgent
from smara.harness import Budget, BudgetExceeded, SessionEngine
from smara.research_modes import QUICK_POLICY, DEEP_POLICY


@pytest.mark.parametrize("mode,endpoint,model,effort", [
    (QUICK_POLICY, "https://api.sarvam.ai/v2", "glm5.3", "low"),
    (DEEP_POLICY, "https://api.sarvam.ai/v2", "glm5.3", "high"),
    (QUICK_POLICY, "https://other.test/v2", "glm5.3", None),
    (QUICK_POLICY, "https://api.sarvam.ai/v2", "sarvam-105b", None),
])
def test_reasoning_effort_is_provider_and_lane_specific(tmp_path, monkeypatch, mode, endpoint, model, effort):
    agent = SmaraAutonomousAgent(api_key="fixture", workspace_root=tmp_path, base_url=endpoint, model=model)
    agent._active_research_policy = mode
    requests = []
    class Response:
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'{"choices": []}'
    def send(request, **kwargs):
        requests.append(json.loads(request.data))
        return Response()
    monkeypatch.setattr("urllib.request.urlopen", send)
    agent._call_model_api([{"role": "user", "content": "question"}], max_tokens=100)
    assert requests[0].get("reasoning_effort") == effort
    assert requests[0]["max_tokens"] == 100


@pytest.mark.parametrize("tool,result,expected", [
    ("research_validate", {"status": "ok", "passed": False, "unresolved_nodes": ["n"]}, "Validation did not pass"),
    ("research_resolve", {"status": "ok", "resolution": {"state": "insufficient"}}, "Node NOT resolved"),
])
def test_failed_research_tool_never_gets_success_guidance(tmp_path, monkeypatch, tool, result, expected):
    engine = SessionEngine(tmp_path, tool, budget=Budget(60, 20, 10, 100_000, 1), constrained=False)
    engine.set("research_required", True)
    agent = SmaraAutonomousAgent(api_key="fixture", profile="research_web", workspace_root=tmp_path, session_engine=engine)
    messages_seen = []
    def model(messages, **kwargs):
        messages_seen.append(json.loads(json.dumps(messages)))
        if len(messages_seen) == 1:
            return {"choices": [{"message": {"tool_calls": [{"id": "call", "function": {"name": tool, "arguments": "{}"}}]}}]}
        return {"choices": [{"message": {"content": "No verified answer"}}]}
    monkeypatch.setattr(agent, "_call_model_api", model)
    monkeypatch.setattr(agent, "execute_tool", lambda *args, **kwargs: json.dumps(result))
    agent.run("A bounded fact", max_iterations=2)
    text = str(messages_seen[1])
    assert expected in text
    assert "Validation passed." not in text
    assert "Node resolved." not in text


def test_provider_budget_failure_is_not_tool_error(tmp_path, monkeypatch):
    session = SessionEngine(tmp_path, "budget", budget=Budget(60, 5, 1, 100, 1))
    agent = SmaraAutonomousAgent(api_key="fixture", workspace_root=tmp_path, session_engine=session)
    monkeypatch.setattr(agent, "_call_model_api", lambda *a, **k: (_ for _ in ()).throw(BudgetExceeded("billed_tokens")))
    result = agent.run("Answer a question", max_iterations=2)
    assert result["status"] == "budget_exhausted"
    assert not result["completed"]


def test_request_packing_fits_remaining_budget_without_increasing_it(tmp_path, monkeypatch):
    session = SessionEngine(tmp_path, "remaining", budget=Budget(60, 5, 4, 12000, 1))
    session.begin_incremental("Research")
    session.set("usage", {"billed_tokens": 6000, "model_calls": 1, "tool_calls": 0, "dollars": .01})
    agent = SmaraAutonomousAgent(api_key="fixture", workspace_root=tmp_path, session_engine=session, toolset="research_web")
    requests = []
    class Response:
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'{"choices": [], "usage": {"total_tokens": 200}}'
    def send(request, **kwargs):
        requests.append(json.loads(request.data))
        return Response()
    monkeypatch.setattr("urllib.request.urlopen", send)
    try:
        agent._call_model_api([{"role": "system", "content": "Answer from evidence"},
            {"role": "user", "content": "Old context " * 1000},
            {"role": "assistant", "content": "Old answer " * 1000},
            {"role": "user", "content": "Finish the original research objective", "_smara_mandatory": True}], max_tokens=8192)
        assert requests[0]["max_tokens"] == 1500
        assert requests[0]["messages"][-1]["content"] == "Finish the original research objective"
        assert session.get("usage")["billed_tokens"] == 6200
        reservation = list(session.get("model_reservations").values())[-1]
        assert reservation["estimated_tokens"] <= 6000
        assert session.get("budget")["billed_tokens"] == 12000
    finally:
        session.close()


def test_remaining_budget_context_overflow_is_not_a_tool_failure(tmp_path, monkeypatch):
    session = SessionEngine(tmp_path, "small_context", budget=Budget(60, 5, 4, 1500, 1))
    session.begin_incremental("Research")
    agent = SmaraAutonomousAgent(api_key="fixture", workspace_root=tmp_path, session_engine=session)
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: pytest.fail("Cannot dispatch an unaffordable request"))
    try:
        with pytest.raises(BudgetExceeded, match="billed_tokens"):
            agent._call_model_api([{"role": "system", "content": "mandatory " * 2000}])
        assert session.get("usage").get("model_calls", 0) == 0
    finally:
        session.close()


@pytest.mark.parametrize("response", [{}, {"choices": []}, {"choices": None}, {"choices": [None]}, {"choices": [{"message": "invalid"}]}])
def test_invalid_provider_completion_fails_closed_without_crashing(tmp_path, monkeypatch, response):
    session = SessionEngine(tmp_path, "malformed", budget=Budget(60, 5, 2, 100_000, 1))
    agent = SmaraAutonomousAgent(api_key="fixture", workspace_root=tmp_path, session_engine=session)
    monkeypatch.setattr(agent, "_call_model_api", lambda *a, **k: response)
    result = agent.run("Answer a question", max_iterations=2)
    assert result["status"] == "tool_error"
    assert not result["completed"]
    assert result["iterations"] == 1
