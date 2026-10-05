import json
from types import SimpleNamespace
import pytest

from smara.evidence_index import EvidenceIndex
from smara.harness import SessionEngine
from smara.research_completeness import evidence_review_context, review_completeness
from smara.research_session import CanonicalResearchSession, ResearchStateError
from smara.research_tools import SearchHit


def test_failed_page_gets_one_alternative_and_failure_stays_visible(tmp_path):
    calls = []
    class Searcher:
        async def search(self, query, **kwargs):
            return [SearchHit("https://docs.example.org/missing", "Formats", "formats", "fixture", "primary"),
                    SearchHit("https://docs.example.org/formats", "Formats", "formats", "fixture", "primary")]
    class Fetcher:
        async def fetch(self, url):
            calls.append(url)
            if url.endswith("missing"):
                raise RuntimeError("HTTP 404")
            text = "The two source formats are tar.gz and tar.xz."
            return SimpleNamespace(excerpt=text, raw_content=text.encode(), final_url=url, redirect_chain=(),
                                   title="Source formats", published_at="2024-01-01")
    engine = SessionEngine(tmp_path, "recovery")
    try:
        research = CanonicalResearchSession(session_engine=engine, searcher=Searcher(), fetcher=Fetcher())
        research.plan("Source formats", [{"id": "formats", "question": "formats"}])
        result = research.gather([{"node_id": "formats", "query": "formats"}], max_sources_per_node=1)
        assert result["status"] == "ok"
        assert result["nodes"][0]["failures"][0]["url"].endswith("missing")
        assert len(result["nodes"][0]["evidence"]) == 1
        ident = result["nodes"][0]["evidence"][0]["evidence_id"]
        cached = research.fetch("formats", "https://docs.example.org/formats")
        assert cached["deduplicated"] and cached["title"] == "Source formats"
        assert cached["published_at"] == "2024-01-01"
        assert research.index.records[ident].kind == "fetched_passage"
        research.gather([{"node_id": "formats", "query": "formats"}], max_sources_per_node=1)
        assert calls.count("https://docs.example.org/missing") == 1
        assert research.index.failures[0]["canonical_url"].endswith("missing")
    finally:
        engine.close()


def test_complete_but_evidence_inconsistent_answer_fails_review():
    answer = "The latest version is 1.0."
    seen = []
    def reviewer(messages, **kwargs):
        seen.append(json.loads(messages[1]["content"]))
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"passed": True,
            "requirements": [{"requirement": "latest version", "addressed": True, "answer_quote": answer,
                              "evidence_supported": False, "reason": "The cited version is superseded"}]})}}]}
    result = review_completeness("Latest version as of January 1, 2025?", answer, reviewer,
        evidence=[{"url": "https://docs.example.org/releases", "published_at": "2024-12-01",
                   "excerpts": ["Version 2.0 superseded 1.0 on December 1, 2024."]}])
    assert not result["passed"]
    assert seen[0]["fetched_evidence"][0]["published_at"] == "2024-12-01"


def test_evidence_review_excludes_snippets_and_keeps_header_dates():
    index = EvidenceIndex()
    source = index.add(kind="fetched_passage", url="https://docs.example.org/release", content=b"source", text="Version 2.0 released December 2024.",
                       published_at="2024-12-01", source_title="Official release")
    index.add(kind="search_snippet", url="https://docs.example.org/search", content=b"snippet", text="Version 7.0")
    evidence = evidence_review_context(index.records.values(), "Latest version?", "Version 2.0")
    assert len(evidence) == 1 and evidence[0]["evidence_id"] == source.id
    assert evidence[0]["published_at"] == "2024-12-01"


def test_replan_preserves_evidence_and_failures_but_requires_new_validation(tmp_path):
    engine = SessionEngine(tmp_path, "replan")
    try:
        research = CanonicalResearchSession(session_engine=engine)
        research.plan("Formats and failure recovery", [
            {"id": "probe", "question": "Fetch missing page"},
            {"id": "formats", "question": "Which formats?"}])
        old_artifact = engine.get("research_state_artifact_id")
        evidence = research.index.add(kind="fetched_passage", url="https://docs.example.org/formats",
            content=b"Sources use tar.gz and tar.xz.", text="Sources use tar.gz and tar.xz.")
        research.index.record_failure("https://docs.example.org/missing", "HTTP 404")
        research.graph.resolve("formats", "supported", [evidence.id])
        research.claims = [{"node_id": "formats", "claim": "Sources use tar.gz and tar.xz.", "evidence_ids": [evidence.id]}]
        research.validation = {"passed": True}
        engine.set("research_validated_state_artifact_id", old_artifact)
        result = research.plan("Which formats?", [{"id": "formats", "question": "Which formats?"}], replace_plan=True)
        assert result["removed_node_ids"] == ["probe"]
        assert result["retained_evidence_count"] == result["retained_failure_count"] == 1
        assert research.graph.nodes["formats"].state == "unresolved"
        assert research.claims == [] and research.validation == {}
        assert engine.get("research_validated_state_artifact_id") is None
        reloaded = CanonicalResearchSession(session_engine=engine)
        assert set(reloaded.graph.nodes) == {"formats"}
        assert reloaded.index.records[evidence.id].text == evidence.text
        assert reloaded.index.failures[0]["canonical_url"].endswith("/missing")
        assert "probe" in {node["id"] for node in json.loads(engine.resolve_artifact(old_artifact))["graph"]["nodes"]}
    finally:
        engine.close()


@pytest.mark.parametrize("nodes", [
    [{"id": "good", "question": "A"}, {"id": "bad", "question": "B", "dependencies": ["absent"]}],
    [{"id": "x", "question": "A"}, {"id": "x", "question": "B"}],
    [{"id": "x", "question": "A", "dependencies": ["x"]}],
])
@pytest.mark.parametrize("replace_plan", [False, True])
def test_invalid_plan_never_partially_mutates_existing_graph(tmp_path, nodes, replace_plan):
    research = CanonicalResearchSession(state_path=tmp_path / "research.json")
    research.plan("Original request", [{"id": "original", "question": "Original fact"}])
    before = research.snapshot()
    persisted = research.state_path.read_bytes()
    with pytest.raises((ResearchStateError, ValueError)):
        research.plan("Invalid", nodes, replace_plan=replace_plan)
    assert research.snapshot() == before
    assert research.state_path.read_bytes() == persisted


def test_replan_dispatch_does_not_change_original_user_request(tmp_path):
    from smara.autonomous_agent import SmaraAutonomousAgent
    agent = SmaraAutonomousAgent(api_key="fixture", workspace_root=tmp_path, toolset="research_web")
    agent._active_research_task = "Give the formats AND publication date"
    try:
        agent._dispatch_research_plan({"question": "Formats and date", "nodes": [
            {"id": "formats", "question": "Formats?"}, {"id": "date", "question": "Date?"}]})
        result = json.loads(agent._dispatch_research_plan({"question": "Formats only", "replace_plan": True,
            "nodes": [{"id": "formats", "question": "Formats?"}]}))
        assert result["replaced"]
        assert agent._active_research_task == "Give the formats AND publication date"
        review = review_completeness(agent._active_research_task, "The formats are tar.gz and tar.xz.",
            lambda *args, **kwargs: {"choices": [{"message": {"content": json.dumps({
                "passed": False, "requirements": [{"requirement": "publication date", "addressed": False,
                "answer_quote": "", "reason": "The original date request remains unanswered."}]
            })}}]})
        assert not review["passed"]
    finally:
        agent._browser.shutdown()


def test_synthesis_recovery_still_admits_graph_repair(tmp_path, monkeypatch):
    from smara.autonomous_agent import SmaraAutonomousAgent
    class Fetcher:
        async def fetch(self, url):
            if url.endswith("/missing"):
                raise RuntimeError("HTTP 404")
            return SimpleNamespace(excerpt="Unrelated retrieved document.", raw_content=b"Unrelated retrieved document.",
                final_url=url, redirect_chain=(), title="Document", published_at=None)
    engine = SessionEngine(tmp_path, "repair-tools", constrained=False)
    agent = SmaraAutonomousAgent(api_key="fixture", workspace_root=tmp_path, toolset="research_web", session_engine=engine)
    agent._research.fetcher = Fetcher()
    requests = []
    def calls(*items):
        return {"choices": [{"finish_reason": "tool_calls", "message": {"content": "", "tool_calls": [
            {"id": "call-" + str(len(requests)) + "-" + str(i), "type": "function",
             "function": {"name": name, "arguments": json.dumps(arguments)}}
            for i, (name, arguments) in enumerate(items)]}}]}
    def model(messages, tools=None, **kwargs):
        if tools is None:
            return {"choices": [{"finish_reason": "stop", "message": {"content": '{"passed":false,"requirements":[{"requirement":"requested fact","addressed":false,"answer_quote":"","reason":"Not answered"}]}'}}]}
        requests.append({tool["function"]["name"] for tool in tools})
        if len(requests) == 1:
            return calls(("research_plan", {"question": "A requested fact", "nodes": [
                {"id": "fact", "question": "A requested fact"}, {"id": "probe", "question": "Fetch missing page"}]}))
        if len(requests) == 2:
            return calls(
                ("research_fetch", {"node_id": "probe", "url": "https://docs.example.org/missing"}),
                ("research_fetch", {"node_id": "fact", "url": "https://docs.example.org/one"}),
                ("research_fetch", {"node_id": "fact", "url": "https://docs.example.org/one"}),
                ("research_fetch", {"node_id": "fact", "url": "https://docs.example.org/one"}))
        assert "research_plan" in requests[-1]
        assert not requests[-1] & {"research_fetch", "research_gather", "research_search"}
        return calls(("research_plan", {"question": "A requested fact", "replace_plan": True,
                                      "nodes": [{"id": "fact", "question": "A requested fact"}]}))
    monkeypatch.setattr(agent, "_call_model_api", model)
    monkeypatch.setattr(agent, "_build_dynamic_context", lambda: "")
    try:
        result = agent.run("Research a requested fact, recovering from a missing page.", max_iterations=3)
        assert len(requests) == 3
        assert set(agent._research.graph.nodes) == {"fact"}
        assert len(agent._research.index.failures) == 1
        assert not result["completed"]
    finally:
        agent._browser.shutdown()
        engine.close()
