import hashlib
import json

import pytest

from smara.harness import Budget, SessionEngine
from smara.autonomous_agent import SmaraAutonomousAgent
from smara.research_completeness import review_completeness


def response(rows, passed=True, finish="stop"):
    return {"choices": [{"finish_reason": finish, "message": {"content": json.dumps({
        "passed": passed, "requirements": rows,
    })}}]}


def row(requirement, quote, addressed=True):
    return {"requirement": requirement, "answer_quote": quote,
            "addressed": addressed, "reason": "Reviewed against the question"}


def test_complete_multi_part_answer_has_bounded_isolated_review():
    answer = "RFC 9110 is HTTP Semantics. Published June 2022."
    seen = []
    def model(messages, **kwargs):
        seen.append((messages, kwargs))
        return response([row("identifier and title", "RFC 9110 is HTTP Semantics."),
                         row("publication date", "Published June 2022.")])
    result = review_completeness("Give RFC identifier, title and publication date", answer, model)
    assert result["passed"]
    assert seen[0][1] == {"tools": None, "max_tokens": 2048}
    assert len(seen[0][0]) == 2
    assert json.loads(seen[0][0][1]["content"])["question"].startswith("Give RFC")
    assert result["answer_sha256"] == hashlib.sha256(answer.encode()).hexdigest()


@pytest.mark.parametrize("question,answer,requirements", [
    ("Give identifier and title", "RFC 9110", [row("identifier", "RFC 9110"), row("title", "", False)]),
    ("Latest version as of 2025-03-31", "Today's latest is 9.0", [row("historical cutoff", "", False)]),
    ("Compare both conflicting sources", "Source A says yes", [row("source A", "Source A says yes"), row("source B", "", False)]),
    ("What is its exact name?", "See this supported citation", [row("exact name", "Invented quote")]),
    ("Give the value", "FINAL LABEL: supported", [row("value", "", False)]),
])
def test_missing_fields_dates_conflicts_or_fabricated_quotes_cannot_pass(question, answer, requirements):
    result = review_completeness(question, answer, lambda *a, **k: response(requirements))
    assert not result["passed"]
    assert result["reason"] == "question_requirements_unanswered"


@pytest.mark.parametrize("payload", [
    {}, {"choices": []}, response([]), response([row("fact", "fact")], passed="true"),
    response([row("fact", "fact")], finish="length"),
    response([{**row("fact", "fact"), "addressed": "true"}]),
])
def test_invalid_or_truncated_review_fails_closed(payload):
    assert not review_completeness("State the fact", "fact", lambda *a, **k: payload)["passed"]


def test_provider_failure_does_not_expose_error_or_accept_answer():
    def model(*args, **kwargs):
        raise RuntimeError("secret credential in error")
    result = review_completeness("question", "answer", model)
    assert result["status"] == "unavailable"
    assert "secret" not in json.dumps(result)


def test_oversized_question_does_not_get_silently_truncated_or_call_provider():
    def model(*a, **k):
        pytest.fail("must not call provider")
    assert not review_completeness("x" * 12001, "answer", model)["passed"]


def test_durable_completion_rejects_missing_or_stale_review(tmp_path):
    session = SessionEngine(tmp_path, "review", budget=Budget(60, 5, 5, 10000, 1))
    session.begin_incremental("question")
    session.set("research_required", True)
    session.set("research_completeness_required", True)
    result = session.finish_incremental("completed", "answer")
    assert result["status"] == "needs_input"
    assert "question completeness review missing, failed, or stale" in result["unresolved_items"]
    session.set("research_completeness_review", {"passed": True, "final_answer_sha256": "stale"})
    result = session.finish_incremental("completed", "answer")
    assert result["status"] == "needs_input"


@pytest.mark.parametrize("complete", [True, False])
def test_agent_final_boundary_preserves_partial_answer_but_blocks_completion(tmp_path, monkeypatch, complete):
    session = SessionEngine(tmp_path, "boundary", budget=Budget(60, 20, 10, 100_000, 1), constrained=False)
    agent = SmaraAutonomousAgent(api_key="offline-fixture", workspace_root=tmp_path, session_engine=session)
    session.set("research_required", True)
    artifact_id, _ = session.artifact_store.put_json({"fixture": "artifact-backed"})
    session.set("research_state_artifact_id", artifact_id)
    session.set("research_validated_state_artifact_id", artifact_id)
    session.set("research_validation", {"passed": True})
    monkeypatch.setattr(agent._research, "can_finalize", lambda answer: (True, "validated"))
    monkeypatch.setattr(agent._research, "primary_outcome", lambda: "supported")
    calls = []
    def model(messages, **kwargs):
        calls.append(kwargs)
        if kwargs.get("max_tokens") == 2048:
            return response([row("identifier", "RFC 9110"), row("title", "HTTP Semantics" if complete else "", complete)])
        return {"choices": [{"message": {"content": "FINAL ANSWER: RFC 9110" + (" is HTTP Semantics" if complete else "")}}]}
    monkeypatch.setattr(agent, "_call_model_api", model)
    result = agent.run("Give the RFC identifier and title", max_iterations=1)
    assert result["completed"] is complete
    assert "RFC 9110" in result["answer"]
    assert session.get("research_completeness_review")["passed"] is complete
    assert calls[-1] == {"tools": None, "max_tokens": 2048}
    if not complete:
        assert "title" in result["session"]["unresolved_items"]
