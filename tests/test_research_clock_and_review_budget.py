import json

import pytest

from smara.autonomous_agent import SmaraAutonomousAgent, _clock_only_research_node
from smara.harness import Budget, BudgetExceeded, SessionEngine


@pytest.mark.parametrize("question", [
    "Today's date in Asia/Kolkata per application clock (2026-10-02) — no web retrieval needed",
    "What is today's date?", "Current date", "Current local time in Europe/London",
])
def test_clock_only_nodes_are_recognized(question):
    assert _clock_only_research_node(question)


@pytest.mark.parametrize("question", [
    "Latest release as of today's date", "What is the publication date?",
    "Version as of 2024-01-01", "Today's date and the latest stable release",
    "Current date of the next scheduled launch", "Release date in Asia/Kolkata",
])
def test_dated_external_requirements_are_not_clock_only(question):
    assert not _clock_only_research_node(question)


def test_clock_plan_rejection_preserves_existing_graph_and_original_request(tmp_path):
    agent = SmaraAutonomousAgent(api_key="fixture", workspace_root=tmp_path, toolset="research_web")
    agent._active_research_task = "What is today's date in Asia/Kolkata and the latest stable release?"
    try:
        agent._research.plan("A historical fact", [{"id": "history", "question": "Version as of 2024-01-01"}])
        before = agent._research.snapshot()
        result = json.loads(agent._dispatch_research_plan({"question": "Date and release", "replace_plan": True,
            "nodes": [{"id": "date", "question": "Today's date in Asia/Kolkata"},
                      {"id": "release", "question": "Latest stable release"}]}))
        assert result["reason"] == "runtime_clock_is_not_web_evidence"
        assert result["clock_node_ids"] == ["date"]
        assert "Current date in Asia/Kolkata:" in result["application_clock_answer"]
        assert agent._research.snapshot() == before
        assert "latest stable release" in agent._active_research_task
        accepted = json.loads(agent._dispatch_research_plan({"question": "Latest stable release", "replace_plan": True,
            "nodes": [{"id": "release", "question": "Latest stable release"}]}))
        assert accepted["replaced"] and set(agent._research.graph.nodes) == {"release"}
    finally:
        agent._browser.shutdown()


@pytest.mark.parametrize("budget", [Budget(60, 10, 1, 100_000, 1), Budget(60, 10, 10, 16_000, 1),
                                    Budget(60, 10, 10, 100_000, .015)])
def test_loop_cannot_spend_final_review_headroom(tmp_path, monkeypatch, budget):
    session = SessionEngine(tmp_path, "review-reserve", budget=budget)
    agent = SmaraAutonomousAgent(api_key="fixture", workspace_root=tmp_path, session_engine=session, toolset="research_web")
    session.set("research_required", True)
    agent._active_research_task = "Give the exact release version."
    agent._research_loop_dispatch = True
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: pytest.fail("Loop consumed reserved review capacity"))
    try:
        with pytest.raises(BudgetExceeded):
            agent._call_model_api([{"role": "user", "content": "Research"}], tools=None, max_tokens=256)
        assert (session.get("usage") or {}).get("model_calls", 0) == 0
        assert session.budget == budget
    finally:
        agent._browser.shutdown()
        session.close()


def test_final_review_is_not_subject_to_loop_headroom(tmp_path, monkeypatch):
    session = SessionEngine(tmp_path, "review-dispatch", budget=Budget(60, 10, 1, 10_000, 1))
    agent = SmaraAutonomousAgent(api_key="fixture", workspace_root=tmp_path, session_engine=session, toolset="research_web")
    session.set("research_required", True)
    dispatched = []
    class Reply:
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps({"choices": [{"message": {"content": "review"}}], "usage": {"total_tokens": 50}}).encode()
    def request(req, **kwargs):
        dispatched.append(json.loads(req.data))
        return Reply()
    monkeypatch.setattr("urllib.request.urlopen", request)
    try:
        agent._call_model_api([{"role": "user", "content": "Review mandatory evidence", "_smara_mandatory": True}],
                              tools=None, max_tokens=2048)
        assert len(dispatched) == 1
        assert session.get("usage")["model_calls"] == 1
    finally:
        agent._browser.shutdown()
        session.close()


def test_tight_budget_switches_to_evidence_synthesis_before_dispatch(tmp_path, monkeypatch):
    session = SessionEngine(tmp_path, "synthesis-switch", budget=Budget(60, 10, 6, 25_000, 1))
    agent = SmaraAutonomousAgent(api_key="fixture", workspace_root=tmp_path,
                                session_engine=session, toolset="research_web", research_mode="quick")
    agent._research.plan("Release", [{"id": "release", "question": "Exact release version"}])
    text = "Version 2.0 is the stable release."
    source = agent._research.index.add(kind="fetched_passage", url="https://docs.example.org/releases",
                                       content=text.encode(), text=text)
    requests = []
    def model(messages, **kwargs):
        requests.append(([dict(item) for item in messages], kwargs))
        assert agent._research_loop_dispatch
        return {"choices": [{"message": {"content": "FINAL ANSWER: I need more evidence to establish the version."}}]}
    monkeypatch.setattr(agent, "_call_model_api", model)
    try:
        result = agent.run("Give the exact stable release version", max_iterations=1)
        assert not result["completed"]
        names = {tool["function"]["name"] for tool in requests[0][1]["tools"]}
        assert names == {"research_plan", "research_inspect", "research_resolve", "research_validate", "research_report"}
        state = requests[0][0][-1]
        assert state["_smara_mandatory"] and source.id in state["content"]
        assert "Exact release version" in state["content"]
        assert not agent._research_loop_dispatch
    finally:
        agent._browser.shutdown()
        session.close()


def test_provider_retry_cannot_spend_protected_review_capacity(tmp_path, monkeypatch):
    import io
    import urllib.error
    session = SessionEngine(tmp_path, "retry-review-reserve", budget=Budget(60, 10, 10, 20_000, 1))
    agent = SmaraAutonomousAgent(api_key="fixture", workspace_root=tmp_path,
                                session_engine=session, toolset="research_web")
    session.set("research_required", True)
    agent._research_loop_dispatch = True
    calls = []
    def request(*args, **kwargs):
        calls.append(1)
        raise urllib.error.HTTPError("https://provider", 503, "unavailable", {"Retry-After": "0"}, io.BytesIO(b"unavailable"))
    monkeypatch.setattr("urllib.request.urlopen", request)
    try:
        with pytest.raises(BudgetExceeded, match="research_final_review_headroom"):
            agent._call_model_api([{"role": "user", "content": "Evidence " * 180}], max_tokens=256)
        assert len(calls) == 1
        assert session.get("usage")["billed_tokens"] + agent._research_review_token_headroom() <= 20_000
        responses = [event["payload"] for event in session.inspect()["events"] if event["type"] == "provider_response"]
        assert responses[-1]["status"] == "research_final_review_headroom"
    finally:
        agent._browser.shutdown()
        session.close()


def test_budget_exhaustion_during_research_recovers_with_evidence_synthesis(tmp_path, monkeypatch):
    session = SessionEngine(tmp_path, "budget-recovery", budget=Budget(60, 10, 6, 25_000, 1))
    agent = SmaraAutonomousAgent(api_key="fixture", workspace_root=tmp_path,
                                session_engine=session, toolset="research_web", research_mode="quick")
    agent._research.plan("Release", [{"id": "release", "question": "Exact release version"}])
    text = "Version 2.0 is the stable release."
    source = agent._research.index.add(kind="fetched_passage", url="https://docs.example.org/releases",
                                       content=text.encode(), text=text)
    def model_fail(*args, **kwargs):
        raise BudgetExceeded("billed_tokens: mandatory context exceeds remaining allowance")
    monkeypatch.setattr(agent, "_call_model_api", model_fail)
    try:
        result = agent.run("Give the exact stable release version", max_iterations=3)
        assert "API_ERROR" not in result.get("answer", "")
        assert "https://docs.example.org/releases" in result.get("answer", "")
        assert "FINAL LABEL: insufficient" in result.get("answer", "")
    finally:
        agent._browser.shutdown()
        session.close()
