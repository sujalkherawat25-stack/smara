import asyncio
import hashlib
import json
from datetime import datetime, timezone

import pytest

from smara.autonomous_agent import SmaraAutonomousAgent
from smara.harness import Budget, SessionEngine
from smara.research import RetrievedSource
from smara.research_modes import DEEP_POLICY, QUICK_POLICY, select_research_lane, should_route_to_research
from smara.research_ranking import hybrid_rank
from smara.research_session import CanonicalResearchSession, ResearchStateError
from smara.research_tools import SearchHit


def test_auto_router_selects_bounded_fact_and_broad_investigation():
    quick, quick_decision = select_research_lane("When was Python 3.13 released?")
    deep, deep_decision = select_research_lane(
        "Conduct deep research comparing the market landscape, risks, alternatives, and long-term trends. Produce a comprehensive report."
    )
    assert quick == QUICK_POLICY and quick_decision["selected"] == "quick"
    assert deep == DEEP_POLICY and deep_decision["selected"] == "deep"
    assert deep_decision["score"] >= 3 and deep_decision["reasons"]
    assert select_research_lane("Prepare a comprehensive market comparison report")[0] == DEEP_POLICY
    assert select_research_lane("One fact", "deep")[1]["reasons"] == ["explicit_user_selection"]
    assert should_route_to_research("Research the latest market landscape with sources")
    assert should_route_to_research("Prepare a comprehensive market comparison report with sources")
    assert not should_route_to_research("Implement the latest parser fix and run tests")


def test_auto_router_extracts_bounded_question_from_multiline_harness_prompt():
    prompt = (
        "As of 2026-09-13, answer this live-web research question: According to Cargo's official documentation, what is Cargo.lock used for?\n"
        "Use the canonical research_plan, then prefer research_gather; research_search plus research_fetch is the compatible fallback.\n"
        "Do not expand into a comprehensive report."
    )
    policy, decision = select_research_lane(prompt)
    assert policy == QUICK_POLICY
    assert decision["selected"] == "quick"
    assert decision["score"] < 3


def test_hybrid_ranking_fuses_relevance_authority_and_provider_order():
    hits = [
        SearchHit("https://blog.test/other", "Other topic", "unrelated material", "exa", "unclassified"),
        SearchHit("https://agency.gov/report", "Climate risk report", "official climate risk evidence", "exa", "primary"),
        SearchHit("https://news.test/story", "Climate report", "climate risk summary", "exa", "secondary"),
    ]
    ranked = hybrid_rank("official climate risk report", hits, 3)
    assert ranked[0].url == "https://agency.gov/report"
    assert len(ranked) == 3


class _FakeSearcher:
    async def search(self, query, max_results=8):
        slug = query.replace(" ", "-")
        return [
            SearchHit(f"https://source-{index}.test/{slug}", f"{query} source {index}", f"evidence for {query} value {index}", "fixture", "primary")
            for index in range(max_results)
        ]


class _ConcurrentFetcher:
    def __init__(self):
        self.active = 0
        self.max_active = 0

    async def fetch(self, url):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01)
        text = f"Verified evidence from {url}."
        self.active -= 1
        return RetrievedSource(
            title="Fixture source", excerpt=text, content_sha256=hashlib.sha256(text.encode()).hexdigest(),
            retrieved_at=datetime.now(timezone.utc).isoformat(), raw_content=text.encode(), final_url=url,
        )


def test_quick_gather_executes_ready_wave_concurrently_and_respects_source_cap(tmp_path):
    engine = SessionEngine(tmp_path, "quick-gather", budget=Budget(60, 20, 10, 100_000, 1), constrained=False)
    engine.set("research_policy", QUICK_POLICY.to_dict())
    fetcher = _ConcurrentFetcher()
    session = CanonicalResearchSession(session_engine=engine, searcher=_FakeSearcher(), fetcher=fetcher)
    session.plan("two facts", [{"id": "a", "question": "alpha"}, {"id": "b", "question": "beta"}])
    result = session.gather([{"node_id": "a", "query": "alpha"}, {"node_id": "b", "query": "beta"}], max_sources_per_node=5)
    assert result["status"] == "ok"
    assert 3 <= result["source_count"] <= 8
    assert fetcher.max_active > 1
    assert engine.get("research_state_artifact_id")


def test_gather_schedules_ready_subset_and_reports_blocked_dependencies(tmp_path):
    engine = SessionEngine(tmp_path, "dag-wave", budget=Budget(60, 20, 10, 100_000, 1), constrained=False)
    engine.set("research_policy", DEEP_POLICY.to_dict())
    session = CanonicalResearchSession(session_engine=engine, searcher=_FakeSearcher(), fetcher=_ConcurrentFetcher())
    session.plan(
        "dependent facts",
        [
            {"id": "root", "question": "root fact", "dependencies": []},
            {"id": "child", "question": "child fact", "dependencies": ["root"]},
        ],
    )
    result = session.gather(
        [
            {"node_id": "root", "query": "root fact"},
            {"node_id": "child", "query": "child fact"},
        ],
        max_sources_per_node=3,
    )
    assert result["status"] == "ok"
    assert {item["node_id"] for item in result["blocked"]} == {"child"}
    assert {item["node_id"] for item in result["nodes"]} == {"root"}
    root_evidence = result["nodes"][0]["evidence"][0]["evidence_id"]
    session.resolve("root", "The root fact is verified.", [root_evidence])
    next_result = session.gather([{"node_id": "child", "query": "child fact"}], max_sources_per_node=3)
    assert next_result["status"] == "ok"
    assert not next_result["blocked"]


def test_quick_lane_never_bypasses_three_source_floor_for_analysis(tmp_path):
    engine = SessionEngine(tmp_path, "quick-source-floor", budget=Budget(60, 20, 10, 100_000, 1), constrained=False)
    engine.set("research_policy", QUICK_POLICY.to_dict())
    session = CanonicalResearchSession(session_engine=engine)
    session.plan("analyze a dataset", [{"id": "dataset", "question": "What does it show?"}])
    record = session.index.add(kind="fetched_passage", url="https://data.test/one.csv", content=b"x\n1\n", text="x\n1\n", start=0, end=4)
    session.resolve("dataset", "The dataset contains one value.", [record.id])
    session.analyses.append({"status": "ok", "operation": "describe"})
    session.validation = {"passed": True, "claims": []}
    assert session.can_finalize("The dataset contains one value. FINAL LABEL: supported") == (
        False,
        "research_source_floor_not_met_1_of_3",
    )


def test_deep_report_requires_validation_source_floor_and_durable_artifact(tmp_path):
    engine = SessionEngine(tmp_path, "deep-report", budget=Budget(60, 20, 10, 100_000, 1), constrained=False)
    policy = {**DEEP_POLICY.to_dict(), "min_sources": 2, "target_sources": 2}
    engine.set("research_policy", policy)
    session = CanonicalResearchSession(session_engine=engine)
    session.plan("report", [{"id": "claim", "question": "What is supported?"}])
    records = []
    for index in range(2):
        text = b"The verified value is 42 kilograms."
        records.append(session.index.add(kind="fetched_passage", url=f"https://source-{index}.test/report", content=text, text=text.decode(), start=0, end=len(text)))
    session.resolve("claim", "The verified value is 42 kilograms.", [records[0].id])
    assert session.validate([{"claim": "The verified value is 42 kilograms.", "evidence_ids": [records[0].id]}])["passed"]
    with pytest.raises(ResearchStateError, match="1000"):
        session.write_report("Report", "short")
    report = session.write_report("Report", "Verified analysis. " * 80)
    assert report["status"] == "ok"
    assert engine.resolve_artifact(report["artifact_id"]).startswith(b"# Report")
    assert session.can_finalize("The verified value is 42 kilograms. FINAL LABEL: supported") == (True, "validated")


def test_validation_repairs_stale_provider_evidence_id_from_fetched_passage(tmp_path):
    engine = SessionEngine(tmp_path, "validation-repair", budget=Budget(60, 20, 10, 100_000, 1), constrained=False)
    engine.set("research_policy", QUICK_POLICY.to_dict())
    session = CanonicalResearchSession(session_engine=engine)
    session.plan("verified value", [{"id": "claim", "question": "What is the value?"}])
    text = "The verified value is 42 kilograms."
    record = session.index.add(
        kind="fetched_passage", url="https://source.test/value", content=text.encode(),
        extracted_content=text.encode(), text=text, start=0, end=len(text),
    )
    session.resolve("claim", text, [record.id])
    result = session.validate([{"claim": text, "evidence_ids": ["stale-provider-id"]}])
    assert result["passed"] is True
    assert result["repaired_evidence"][0]["to"] == [record.id]


def test_deep_controller_can_ground_ready_nodes_from_exact_fetched_sentences(tmp_path):
    engine = SessionEngine(tmp_path, "auto-ground", budget=Budget(60, 20, 10, 100_000, 1), constrained=False)
    engine.set("research_policy", DEEP_POLICY.to_dict())
    session = CanonicalResearchSession(session_engine=engine)
    session.plan(
        "RFC facts",
        [
            {"id": "title", "question": "What is the RFC 9110 title?"},
            {"id": "scope", "question": "What does RFC 9110 define?", "dependencies": ["title"]},
        ],
    )
    text = (
        "RFC 9110: HTTP Semantics. "
        "RFC 9110 defines the semantics of the Hypertext Transfer Protocol."
    )
    session.index.add(
        kind="fetched_passage", url="https://source.test/rfc", content=text.encode(),
        extracted_content=text.encode(), text=text, start=0, end=len(text),
    )
    result = session.auto_resolve_from_evidence()
    assert result["status"] == "ok"
    assert {item["node_id"] for item in result["resolved"]} == {"title", "scope"}
    assert not result["remaining"]


def test_auto_ground_leaves_latest_questions_for_cross_source_comparison(tmp_path):
    engine = SessionEngine(tmp_path, "freshness-ground", budget=Budget(60, 20, 10, 100_000, 1), constrained=False)
    engine.set("research_policy", QUICK_POLICY.to_dict())
    session = CanonicalResearchSession(session_engine=engine)
    session.plan(
        "latest Python",
        [{"id": "release", "question": "What is the latest stable Python release?"}],
    )
    passages = (
        ("https://python.test/3.12", "Python 3.12.0 is the newest stable release of Python."),
        ("https://python.test/3.14", "Python 3.14.0 is a stable release of Python."),
    )
    for url, text in passages:
        session.index.add(
            kind="fetched_passage", url=url, content=text.encode(),
            extracted_content=text.encode(), text=text, start=0, end=len(text),
        )

    result = session.auto_resolve_from_evidence()

    assert result["status"] == "no_match"
    assert result["resolved"] == []
    assert result["remaining"] == ["release"]


def test_deep_source_floor_failure_stops_without_provider_retry_loop(tmp_path, monkeypatch):
    """An unreachable Deep source floor must fail closed in one bounded turn."""
    engine = SessionEngine(tmp_path, "deep-floor-stop", budget=Budget(60, 40, 10, 100_000, 1), constrained=False)
    agent = SmaraAutonomousAgent(api_key="fixture", profile="research_web", workspace_root=tmp_path, session_engine=engine)
    monkeypatch.setattr(
        agent._research,
        "fetched_source_urls",
        lambda: [f"https://source-{index}.test" for index in range(14)],
    )
    monkeypatch.setattr(
        agent,
        "execute_tool",
        lambda _name, _args, call_id=None: '{"status":"error","error":"deep research requires at least 20 fetched sources before report generation"}',
    )
    monkeypatch.setattr(
        agent,
        "_call_model_api",
        lambda _messages, tools=None, max_tokens=16384: {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "id": "report-stop",
                        "function": {
                            "name": "research_report",
                            "arguments": json.dumps({"title": "Report", "markdown": "x" * 1200}),
                        },
                    }]
                }
            }]
        },
    )
    result = agent.run("Prepare a comprehensive report on a bounded topic", max_iterations=20)
    assert result["status"] == "needs_input"
    assert result["iterations"] == 1
    assert "deep_source_floor_unreachable_14_of_20" in result["session"]["unresolved_items"]


def test_deep_report_redacts_unverified_provider_urls(tmp_path):
    engine = SessionEngine(tmp_path, "deep-report-redact", budget=Budget(60, 20, 10, 100_000, 1), constrained=False)
    policy = {**DEEP_POLICY.to_dict(), "min_sources": 2, "target_sources": 2}
    engine.set("research_policy", policy)
    session = CanonicalResearchSession(session_engine=engine)
    session.plan("report", [{"id": "claim", "question": "What is supported?"}])
    records = [
        session.index.add(kind="fetched_passage", url=f"https://source-{index}.test/report", content=b"The verified value is 42 kilograms.", text="The verified value is 42 kilograms.", start=0, end=35)
        for index in range(2)
    ]
    session.resolve("claim", "The verified value is 42 kilograms.", [records[0].id])
    assert session.validate([{"claim": "The verified value is 42 kilograms.", "evidence_ids": [records[0].id]}])["passed"]
    report = session.write_report("Report", "Verified analysis. https://unverified.example.invalid/provider-link " + ("x " * 600))
    body = engine.resolve_artifact(report["artifact_id"]).decode("utf-8")
    assert "https://unverified.example.invalid/provider-link" not in body
    assert "[unverified URL omitted]" in body


def test_agent_auto_lane_is_persisted_before_model_execution(tmp_path, monkeypatch):
    engine = SessionEngine(tmp_path, "auto-lane", budget=Budget(60, 10, 2, 50_000, 1), constrained=False)
    agent = SmaraAutonomousAgent(api_key="fixture", profile="research_web", workspace_root=tmp_path, session_engine=engine)
    monkeypatch.setattr(agent, "_call_model_api", lambda messages, tools=None, max_tokens=16384: {"choices": [{"message": {"content": "FINAL ANSWER: insufficient"}}]})
    agent.run("Prepare a comprehensive report comparing market alternatives and risks", max_iterations=1)
    assert engine.get("research_mode") == "deep"
    assert engine.get("research_policy")["min_sources"] == 20
    assert engine.get("research_lane_decision")["reasons"]


def test_agent_synthesizes_validated_claim_when_validation_uses_last_iteration(tmp_path, monkeypatch):
    """A final validation tool call must not become a false budget failure."""
    engine = SessionEngine(tmp_path, "validation-last", budget=Budget(60, 10, 2, 50_000, 1), constrained=False)
    engine.set("research_policy", QUICK_POLICY.to_dict())
    engine.set("research_mode", "quick")
    session = CanonicalResearchSession(session_engine=engine)
    session.plan("What is the verified value?", [{"id": "claim", "question": "What is the verified value?"}])
    text = "The verified value is 42 kilograms."
    records = [
        session.index.add(
            kind="fetched_passage",
            url=f"https://source-{index}.test/value",
            content=text.encode(),
            text=text,
            start=0,
            end=len(text),
        )
        for index in range(3)
    ]
    session.resolve("claim", text, [records[0].id])
    session.validate([{"claim": text, "evidence_ids": [records[0].id]}])

    agent = SmaraAutonomousAgent(api_key="fixture", profile="research_web", workspace_root=tmp_path, session_engine=engine)
    monkeypatch.setattr(
        agent,
        "_call_model_api",
        lambda messages, tools=None, max_tokens=16384: {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "id": "validate-last",
                        "function": {
                            "name": "research_validate",
                            "arguments": json.dumps({"claims": [{"claim": text, "evidence_ids": [records[0].id]}]}),
                        },
                    }]
                }
            }]
        },
    )
    result = agent.run("What is the verified value?", max_iterations=1)
    assert result["status"] == "completed"
    assert text in result["answer"]
    assert "FINAL LABEL: supported" in result["answer"]
    assert "https://source-0.test/value" in result["answer"]
