import hashlib
import json

import pytest

from smara.harness import Budget, SessionEngine
from smara.autonomous_agent import SmaraAutonomousAgent
from smara.research_completeness import review_completeness, repair_incomplete_answer


def response(rows, passed=True, finish="stop"):
    return {"choices": [{"finish_reason": finish, "message": {"content": json.dumps({
        "passed": passed, "requirements": rows,
    })}}]}


def row(requirement, quote, addressed=True):
    return {"requirement": requirement, "answer_quote": quote,
            "addressed": addressed, "reason": "Reviewed against the question", "evidence_supported": True}


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


def test_evidence_review_schema_explicitly_requires_support_field():
    answer = "Python 3.13.2 was released on February 4, 2025."
    captured = {}

    def model(messages, **kwargs):
        captured["system"] = messages[0]["content"]
        captured["payload"] = json.loads(messages[1]["content"])
        return response([row("version and date", answer)])

    result = review_completeness(
        "Which version and date?", answer, model,
        evidence=[{"evidence_id": "ev-1", "excerpts": [answer]}],
    )
    assert result["passed"]
    assert '"evidence_supported":true|false' in captured["system"]
    assert captured["payload"]["fetched_evidence"][0]["evidence_id"] == "ev-1"


def test_evidence_review_without_support_decision_fails_closed():
    answer = "Python 3.13.2 was released on February 4, 2025."
    unsupported_schema = {"passed": True, "requirements": [{
        "requirement": "version and date", "addressed": True,
        "answer_quote": answer, "reason": "The answer includes both.",
    }]}
    result = review_completeness(
        "Which version and date?", answer,
        lambda *args, **kwargs: {"choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps(unsupported_schema)}}]},
        evidence=[{"evidence_id": "ev-1", "excerpts": [answer]}],
    )
    assert not result["passed"]
    assert result["reason"] == "evidence_consistency_not_established"


@pytest.mark.parametrize("toolset", ["research_web", "coding"])
def test_review_payload_survives_real_request_packing(tmp_path, monkeypatch, toolset):
    answer = "Both required formats are verified."
    question = "Identify both requested formats."
    evidence = [{"evidence_id": "ev-1", "excerpts": ["source text " * 6000 + "evidence_tail"]}]
    captured = []
    class Response:
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self):
            value = response([row("formats", answer)])
            value["usage"] = {"total_tokens": 200}
            return json.dumps(value).encode()
    def send(request, **kwargs):
        captured.append(json.loads(request.data))
        return Response()
    monkeypatch.setattr("urllib.request.urlopen", send)
    agent = SmaraAutonomousAgent(api_key="fixture", workspace_root=tmp_path, toolset=toolset)
    try:
        result = review_completeness(question, answer, agent._call_model_api, evidence=evidence)
        assert result["passed"]
        payload = json.loads(captured[0]["messages"][1]["content"])
        assert payload == {"question": question, "answer": answer, "fetched_evidence": evidence}
        assert "_smara_mandatory" not in captured[0]["messages"][1]
    finally:
        agent._browser.shutdown()


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


def test_answer_repair_is_bounded_mandatory_and_not_a_validation_receipt():
    calls = []
    evidence = [{"url": "https://source.test/spec", "excerpts": ["RFC 9110 is HTTP Semantics."]}]
    def model(messages, **kwargs):
        calls.append((messages, kwargs))
        return {"choices": [{"finish_reason": "stop", "message": {"content": "RFC 9110 is HTTP Semantics."}}]}
    draft = repair_incomplete_answer("Give identifier and title", "RFC 9110",
        {"reason": "question_requirements_unanswered"}, evidence, model)
    assert draft == "RFC 9110 is HTTP Semantics."
    assert len(calls) == 1 and calls[0][1] == {"tools": None, "max_tokens": 4096}
    assert calls[0][0][1]["_smara_mandatory"]
    assert json.loads(calls[0][0][1]["content"])["fetched_evidence"] == evidence


@pytest.mark.parametrize("reason", ["completeness_review_unavailable", "fetched_evidence_missing", "evidence_consistency_not_established"])
def test_answer_repair_does_not_retry_unavailable_or_inconsistent_review(reason):
    def model(*args, **kwargs): pytest.fail("Must not request synthesis")
    assert repair_incomplete_answer("question", "answer", {"reason": reason}, [{"excerpts": ["source"]}], model) is None


def test_answer_repair_failure_does_not_leak_provider_exception():
    def model(*args, **kwargs): raise RuntimeError("secret provider credential")
    assert repair_incomplete_answer("q", "a", {"reason": "question_requirements_unanswered"}, [{"excerpts": ["source"]}], model) is None


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
    passage = "RFC 9110 is HTTP Semantics."
    agent._research.index.add(kind="fetched_passage", url="https://source.test/spec", content=passage.encode(), text=passage, start=0, end=len(passage))
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


@pytest.mark.parametrize("supported", [True, False])
def test_agent_only_adopts_repaired_answer_after_independent_review(tmp_path, monkeypatch, supported):
    session = SessionEngine(tmp_path, "repair-boundary", budget=Budget(60, 20, 6, 100_000, 1), constrained=False)
    agent = SmaraAutonomousAgent(api_key="fixture", workspace_root=tmp_path, session_engine=session)
    session.set("research_required", True)
    artifact_id, _ = session.artifact_store.put_json({"fixture": "artifact-backed"})
    session.set("research_state_artifact_id", artifact_id)
    session.set("research_validated_state_artifact_id", artifact_id)
    session.set("research_validation", {"passed": True})
    monkeypatch.setattr(agent._research, "can_finalize", lambda answer: (True, "validated"))
    monkeypatch.setattr(agent._research, "primary_outcome", lambda: "supported")
    passage = "RFC 9110 is HTTP Semantics."
    agent._research.index.add(kind="fetched_passage", url="https://source.test/spec", content=passage.encode(), text=passage)
    calls = []
    def model(messages, **kwargs):
        calls.append(kwargs)
        if kwargs.get("max_tokens") == 2048:
            answer = json.loads(messages[1]["content"])["answer"]
            if "HTTP Semantics" in answer:
                checked = row("identifier and title", "RFC 9110 is HTTP Semantics.")
                checked["evidence_supported"] = supported
                return response([checked], passed=supported)
            return response([row("title", "", False)], passed=False)
        if kwargs.get("max_tokens") == 4096:
            return {"choices": [{"finish_reason": "stop", "message": {"content": "RFC 9110 is HTTP Semantics.\nFINAL LABEL: supported"}}]}
        return {"choices": [{"finish_reason": "stop", "message": {"content": "FINAL ANSWER: RFC 9110\nFINAL LABEL: supported"}}]}
    monkeypatch.setattr(agent, "_call_model_api", model)
    try:
        result = agent.run("Give the RFC identifier and title", max_iterations=1)
        assert result["completed"] is supported
        assert [call["max_tokens"] for call in calls if "max_tokens" in call][-3:] == [2048, 4096, 2048]
        assert ("HTTP Semantics" in result["answer"]) is supported
        receipt = session.get("research_completeness_review")
        assert receipt["passed"] is supported
        assert receipt["final_answer_sha256"] == hashlib.sha256(result["answer"].encode()).hexdigest()
    finally:
        agent._browser.shutdown()
        session.close()


def test_answer_repair_cannot_dispatch_past_session_budget(tmp_path, monkeypatch):
    session = SessionEngine(tmp_path, "unaffordable-repair", budget=Budget(60, 5, 5, 1, 1))
    agent = SmaraAutonomousAgent(api_key="fixture", workspace_root=tmp_path, session_engine=session)
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: pytest.fail("Unaffordable repair dispatched"))
    try:
        assert repair_incomplete_answer("question", "answer", {"reason": "question_requirements_unanswered"},
            [{"excerpts": ["evidence"]}], agent._call_model_api) is None
        assert (session.get("usage") or {}).get("model_calls", 0) == 0
    finally:
        agent._browser.shutdown()
        session.close()


def test_repair_citation_filter_keeps_only_exact_fetched_provenance():
    from smara.autonomous_agent import _fetched_answer_citations
    fetched = ["https://source.test/spec", "https://source.test/title", "https://source.test/path."]
    answer = ("[Title](https://source.test/title) https://source.test/spec. "
              "https://source.test/path. https://source.test/title "
              "https://unknown.test/invented https://source.test/title/extra "
              "https://source.test.evil/title")
    assert _fetched_answer_citations(answer, fetched) == [
        "https://source.test/title", "https://source.test/spec", "https://source.test/path."]
    assert _fetched_answer_citations(answer, []) == []


def test_review_completeness_parses_markdown_fenced_json():
    answer = "RFC 9110 is HTTP Semantics. Published June 2022."
    fenced_content = f"```json\n{json.dumps({'passed': True, 'requirements': [row('identifier and title', 'RFC 9110 is HTTP Semantics.'), row('publication date', 'Published June 2022.')]})}\n```"
    result = review_completeness(
        "Give RFC identifier, title and publication date",
        answer,
        lambda *a, **k: {"choices": [{"finish_reason": "stop", "message": {"content": fenced_content}}]},
    )
    assert result["passed"]
    assert result["reason"] == "complete"


def test_review_completeness_supports_whitespace_normalized_quotes():
    answer = "First line of text.\n\nSecond line of text with extra   spaces."
    quote = "First line of text. Second line of text with extra spaces."
    result = review_completeness(
        "Give both lines",
        answer,
        lambda *a, **k: {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
            "passed": True, "requirements": [row("both lines", quote)]
        })}}]},
    )
    assert result["passed"]
    assert result["reason"] == "complete"
