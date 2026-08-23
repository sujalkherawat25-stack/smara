import asyncio
import json
from pathlib import Path

import httpx

from smara.research import OpenAIResearchSynthesizer, ResearchExecutor
from smara.research_tools import SearchHit
from smara.store import TaskStore
from smara.syntarus_adapter import SyntarusMemory
from smara.worker import run_once


def test_research_task_listing_excludes_ordinary_tasks(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    research = store.create_research("acct_1", "default", "Sources", "Find verified sources", [])
    store.create("acct_1", "default", "Ordinary", "Not research", False)
    store.create_research("acct_2", "default", "Other account", "Keep isolated", [])

    values = store.research_tasks("acct_1")
    assert [item["id"] for item in values] == [research["id"]]


def _research_store(tmp_path: Path) -> tuple[TaskStore, dict]:
    store = TaskStore(str(tmp_path / "smara.db"))
    task = store.create_research("acct_1", "project-a", "Solar evidence", "What does this source say about solar energy?", ["https://example.com/source"])
    return store, task


def test_research_executor_creates_verified_evidence_and_cited_artifact(tmp_path: Path):
    store, task = _research_store(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://example.com/source")
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<html><title>Primary Source</title><meta property='article:published_time' content='2026-08-22'><body>Solar energy produces electricity from sunlight. This verified example source has enough readable text to support a transparent evidence ledger and deterministic report generation without invented claims.</body></html>")

    async def execute() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
            executor = ResearchExecutor(store, client)
            for expected in ("research.fetch_sources", "research.verify_evidence", "research.write_report"):
                step = store.claim_one("test-worker")
                assert step and step["name"] == expected
                outcome = await executor.run_step(step)
                store.complete_step(step["step_id"], "acct_1", outcome.text)

    asyncio.run(execute())
    evidence = store.evidence(task["id"], "acct_1")
    artifacts = store.artifacts(task["id"], "acct_1")
    assert evidence[0]["status"] == "verified"
    assert evidence[0]["content_sha256"]
    assert evidence[0]["citation_label"] == "[1]"
    assert evidence[0]["published_at"] == "2026-08-22"
    assert "missing_publication_date" not in json.loads(evidence[0]["quality_flags"])
    assert "[1]" in artifacts[0]["content"]
    assert store.get(task["id"], "acct_1")["status"] == "completed"


def test_research_synthesis_is_limited_to_verified_citations(tmp_path: Path):
    store, task = _research_store(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<html><title>Primary Source</title><body>Solar energy produces electricity from sunlight. This verified example source has enough readable text to support a transparent evidence ledger and deterministic report generation without invented claims.</body></html>")

    class FakeSynthesizer:
        async def synthesize(self, *, question: str, evidence: list[dict]) -> str:
            assert question.startswith("What does")
            assert [item["citation_label"] for item in evidence] == ["[1]"]
            return "Solar energy can produce electricity from sunlight. [1]"

    async def execute() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
            executor = ResearchExecutor(store, client, synthesizer=FakeSynthesizer())
            for expected in ("research.fetch_sources", "research.verify_evidence", "research.write_report"):
                step = store.claim_one("synthesis-worker")
                assert step and step["name"] == expected
                outcome = await executor.run_step(step)
                store.complete_step(step["step_id"], "acct_1", outcome.text)

    asyncio.run(execute())
    report = store.artifacts(task["id"], "acct_1")[0]["content"]
    assert "## Synthesized findings" in report
    assert "Solar energy can produce electricity" in report
    assert "## Sources" in report
    assert any(event["type"] == "research.report_synthesized" for event in store.events(task["id"], "acct_1"))


def test_invalid_research_synthesis_falls_back_to_deterministic_report(tmp_path: Path):
    store, task = _research_store(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<html><title>Primary Source</title><body>Solar energy produces electricity from sunlight. This verified example source has enough readable text to support a transparent evidence ledger and deterministic report generation without invented claims.</body></html>")

    class InvalidSynthesizer:
        async def synthesize(self, **_kwargs) -> str:
            return "This claim cites a source that was not provided. [9]"

    async def execute() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
            executor = ResearchExecutor(store, client, synthesizer=InvalidSynthesizer())
            for expected in ("research.fetch_sources", "research.verify_evidence", "research.write_report"):
                step = store.claim_one("fallback-worker")
                assert step and step["name"] == expected
                outcome = await executor.run_step(step)
                store.complete_step(step["step_id"], "acct_1", outcome.text)

    asyncio.run(execute())
    report = store.artifacts(task["id"], "acct_1")[0]["content"]
    assert "## Evidence-backed notes" in report
    assert "This claim cites a source that was not provided" not in report
    assert any(event["type"] == "research.report_synthesis_fallback" for event in store.events(task["id"], "acct_1"))


def test_openai_research_synthesizer_bounds_request_and_keeps_citations(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "Supported finding. [1]"}}]}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, headers, json):
            captured.update(url=url, headers=headers, json=json)
            return FakeResponse()

    monkeypatch.setattr("smara.research.httpx.AsyncClient", lambda **_kwargs: FakeClient())

    async def execute():
        return await OpenAIResearchSynthesizer(
            base_url="https://llm.example/v1", api_key="test-provider-key", model="small-model"
        ).synthesize(
            question="What is supported?",
            evidence=[{"citation_label": "[1]", "title": "Source", "url": "https://example.com", "excerpt": "Verified context."}],
        )

    assert asyncio.run(execute()) == "Supported finding. [1]"
    assert captured["url"] == "https://llm.example/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer test-provider-key"
    assert captured["json"]["max_tokens"] == 1000
    assert "[[1]]" not in captured["json"]["messages"][1]["content"]


def test_quality_verification_records_cross_source_agreement(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    task = store.create_research("acct_1", "project-a", "Agreement", "Compare solar sources", ["https://example.com/a", "https://example.com/b"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<html><title>Solar source</title><meta name='datePublished' content='2026-08-22'><body>Solar energy produces electricity from sunlight and reduces emissions. This source reports the same solar energy evidence with enough context for agreement scoring and transparent citation.</body></html>")

    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
            executor = ResearchExecutor(store, client)
            for expected in ("research.fetch_sources", "research.verify_evidence"):
                step = store.claim_one("quality-worker")
                assert step and step["name"] == expected
                outcome = await executor.run_step(step)
                store.complete_step(step["step_id"], "acct_1", outcome.text)

    asyncio.run(execute())
    evidence = store.evidence(task["id"], "acct_1")
    assert all(item["agreement_count"] == 1 for item in evidence)
    assert all("cross_source_agreement" in json.loads(item["quality_flags"]) for item in evidence)


def test_research_worker_writes_only_verified_report_to_memory(tmp_path: Path, monkeypatch):
    store, task = _research_store(tmp_path)

    class FakeMemory:
        async def search(self, *_args, **_kwargs): return {"context": "project context"}
        async def add(self, **kwargs): self.write = kwargs; return {"event_id": "evt_1"}

    fake = FakeMemory()

    async def fake_run_step(self, step):
        from smara.research import ResearchStepResult
        if step["name"] == "research.write_report":
            return ResearchStepResult("done", report="# cited report", verified_evidence_count=1)
        return ResearchStepResult("done")

    monkeypatch.setattr("smara.worker.ResearchExecutor.run_step", fake_run_step)
    for _ in range(3):
        assert asyncio.run(run_once(store, SyntarusMemory(fake)))
    assert fake.write["metadata"]["memory_kind"] == "verified_research"
    assert fake.write["metadata"]["workspace_id"] == "project-a"
    assert fake.write["run_id"].startswith("run_")


def test_research_graph_discovers_sources_before_retrieval(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    task = store.create_research("acct_1", "project-a", "Discovered evidence", "Which source explains solar energy?", [])

    class FakeSearch:
        async def search(self, query, **_kwargs):
            assert "solar" in query
            return [SearchHit("https://example.com/source", "Primary Source", "Solar evidence", "test")]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<html><title>Primary Source</title><body>Solar energy produces electricity from sunlight. This discovered example source has enough readable text to support a transparent evidence ledger and deterministic report generation without invented claims.</body></html>")

    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
            executor = ResearchExecutor(store, client, FakeSearch())
            names = []
            for _ in range(4):
                step = store.claim_one("test-worker")
                assert step
                names.append(step["name"])
                outcome = await executor.run_step(step)
                store.complete_step(step["step_id"], "acct_1", outcome.text)
            return names

    assert asyncio.run(execute()) == ["research.discover_sources", "research.fetch_sources", "research.verify_evidence", "research.write_report"]
    evidence = store.evidence(task["id"], "acct_1")
    assert evidence[0]["status"] == "verified"
    assert store.get(task["id"], "acct_1")["status"] == "completed"
