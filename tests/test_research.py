import asyncio
import json
from pathlib import Path

import httpx

from smara.research import ResearchExecutor
from smara.research_tools import SearchHit
from smara.store import TaskStore
from smara.syntarus_adapter import SyntarusMemory
from smara.worker import run_once


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
