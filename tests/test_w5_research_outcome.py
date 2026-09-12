"""Unit tests for research structural outcome enforcement."""
import json
import pytest
from pathlib import Path

from smara.autonomous_agent import SmaraAutonomousAgent
from smara.harness import Budget, SessionEngine, ToolResult
from smara.research import RetrievedSource
from smara.research_tools import SearchHit


class Response:
    def __init__(self, payload, index):
        self.payload = payload
        self.headers = {"x-request-id": f"research-{index}"}
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return None
    def read(self):
        return json.dumps(self.payload).encode()


def tool(name, args, index):
    return {
        "choices": [{
            "finish_reason": "tool_calls",
            "message": {
                "content": "",
                "tool_calls": [{
                    "id": f"{name}-{index}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)}
                }]
            }
        }],
        "usage": {"total_tokens": 20}
    }


def final(answer):
    return {
        "choices": [{
            "finish_reason": "stop",
            "message": {"content": f"FINAL ANSWER: {answer}"}
        }],
        "usage": {"total_tokens": 10}
    }


class Fetcher:
    def __init__(self, sources):
        self.sources = dict(sources)
    async def fetch(self, url):
        text = self.sources[url]
        raw = f"<html><body><p>{text}</p></body></html>".encode()
        return RetrievedSource("Fixture", text, "", "2026-09-08T00:00:00+00:00", None, raw, url, (), "text/html")


class Searcher:
    async def search(self, query, *, max_results=5):
        return [SearchHit("https://fixture.test/snippet", "Snippet", "The value is 42 kilograms.", "fixture")]


def make_agent(tmp_path, session_id="research", sources=None):
    session = SessionEngine(tmp_path, session_id, budget=Budget(120, 30, 30, 500_000, 2), constrained=False)
    agent = SmaraAutonomousAgent(api_key="fixture", profile="research", workspace_root=tmp_path, session_engine=session, max_iterations=12)
    agent._research.fetcher = Fetcher(sources or {"https://fixture.test/fact": "The value is 42 kilograms."})
    agent._research.searcher = Searcher()
    return agent, session


def drive(monkeypatch, agent, steps, answer, max_iterations=12):
    cursor = {"value": 0}
    def urlopen(*_args, **_kwargs):
        index = cursor["value"]
        cursor["value"] += 1
        action = steps(agent, index) if callable(steps) else steps[min(index, len(steps) - 1)]
        return Response(action, index)
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    return agent.run("Research the sealed fixture and cite only validated evidence.", max_iterations=max_iterations)


def test_research_structural_outcome_omitted_label_is_attached(tmp_path, monkeypatch):
    """When model omits FINAL LABEL line, it is structurally attached from the verified receipt."""
    url = "https://fixture.test/fact"
    source = "Turing designed the ACE architecture."
    claim = "Shannon designed the ACE architecture."
    agent, session = make_agent(tmp_path, "omitted_label", {url: source})
    def steps(agent, index):
        evidence_id = next(iter(agent._research.index.records), "")
        sequence = [
            tool("research_plan", {"question": "Who designed ACE?", "nodes": [{"id": "fact", "question": "Who designed ACE?"}]}, 0),
            tool("research_fetch", {"node_id": "fact", "url": url}, 1),
            tool("research_resolve", {"node_id": "fact", "claim": claim, "evidence_ids": [evidence_id]}, 2),
            tool("research_validate", {"claims": [{"claim": source, "evidence_ids": [evidence_id]}], "require_complete": False}, 3),
            final(source),  # Omitted "FINAL LABEL: insufficient"
        ]
        return sequence[min(index, 4)]
    result = drive(monkeypatch, agent, steps, source)
    assert result["completed"]
    assert "FINAL LABEL: insufficient" in result["answer"] or "insufficient" in result["answer"].lower()
    assert result.get("session", {}).get("research_outcome") == "insufficient"


def test_research_structural_outcome_contradicting_label_is_rejected(tmp_path):
    """When model states an outcome that contradicts the verified receipt, can_finalize rejects it."""
    source = "The sample mass is 58 kilograms."
    claim = "The sample mass is 120 kilograms."
    fixture = tmp_path / "evidence.txt"
    fixture.write_text(source, encoding="utf-8")
    session = SessionEngine(tmp_path, "contradicting_label", budget=Budget(120, 30, 30, 500_000, 2), constrained=False)
    agent = SmaraAutonomousAgent(api_key="fixture", profile="research", workspace_root=tmp_path, session_engine=session)
    agent.execute_tool("research_plan", {"question": "Mass?", "nodes": [{"id": "n1", "question": "Mass?"}]})
    ingest = json.loads(agent.execute_tool("research_ingest_file", {"node_id": "n1", "path": "evidence.txt"}))
    evidence_id = ingest["evidence"]["id"]
    agent.execute_tool("research_resolve", {"node_id": "n1", "claim": claim, "evidence_ids": [evidence_id]})
    agent.execute_tool("research_validate", {"claims": [{"claim": source, "evidence_ids": [evidence_id]}]})
    assert agent._research.primary_outcome() == "refuted"
    ok, reason = agent._research.can_finalize(f"{source}\nFINAL LABEL: supported")
    assert not ok
    assert "research_outcome_mismatch" in reason
    ok_refuted, _ = agent._research.can_finalize(f"{source}\nFINAL LABEL: refuted")
    assert ok_refuted


@pytest.mark.parametrize("outcome", ["supported", "refuted", "insufficient"])
def test_research_outcomes_supported_refuted_insufficient(tmp_path, outcome):
    session = SessionEngine(tmp_path, f"outcome_{outcome}", budget=Budget(120, 30, 30, 500_000, 2), constrained=False)
    agent = SmaraAutonomousAgent(api_key="fixture", profile="research", workspace_root=tmp_path, session_engine=session)
    source = "The reactor temperature was 350 kelvin."
    fixture = tmp_path / "evidence.txt"
    fixture.write_text(source, encoding="utf-8")
    agent.execute_tool("research_plan", {"question": "Temp?", "nodes": [{"id": "n1", "question": "Temp?"}]})
    ingest = json.loads(agent.execute_tool("research_ingest_file", {"node_id": "n1", "path": "evidence.txt"}))
    ev_id = ingest["evidence"]["id"]
    if outcome == "supported":
        claim = "The reactor temperature was 350 kelvin."
    elif outcome == "refuted":
        claim = "The reactor temperature was 900 kelvin."
    else:
        claim = "Pressure caused temperature rise."
    agent.execute_tool("research_resolve", {"node_id": "n1", "claim": claim, "evidence_ids": [ev_id]})
    agent.execute_tool("research_validate", {"claims": [{"claim": source, "evidence_ids": [ev_id]}], "require_complete": False})
    assert agent._research.primary_outcome() == outcome
