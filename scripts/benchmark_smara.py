"""Offline Smara latency/call-count benchmark.

Run with ``python scripts/benchmark_smara.py`` from the Smara repository.  It
uses no network, credentials, or user data and is suitable for CI smoke runs.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from smara.agent_routing import route_request
from smara.agent_runtime import SmaraAgentRuntime
from smara.config import settings
from smara import research_tools
from smara import research as research_module


class FakeProvider:
    _base_url = "offline"
    _api_key = "offline"
    _model = "offline"

    def __init__(self) -> None:
        self.complete_calls = 0
        self.stream_calls = 0

    async def complete(self, *, system: str, message: object) -> str:
        self.complete_calls += 1
        return "Offline benchmark response."

    async def stream_complete(self, *, system: str, message: object):
        self.stream_calls += 1
        yield "Offline "
        yield "benchmark response."


class FakeMemory:
    def __init__(self) -> None:
        self.calls = 0

    async def context_for_conversation(self, query: str, *, account_id: str, workspace_id: str) -> str:
        self.calls += 1
        return "Offline memory fixture."


def _offline_research_transport(request: httpx.Request) -> httpx.Response:
    """Return deterministic public-looking search/pages for the offline corpus."""
    if request.url.host == "api.tavily.com":
        return httpx.Response(
            200,
            request=request,
            json={
                "results": [
                    {
                        "url": "https://docs.python.org/3/whatsnew/3.13.html",
                        "title": "Python 3.13 documentation",
                        "content": "Official release notes and documented language changes.",
                    },
                    {
                        "url": "https://peps.python.org/pep-0703/",
                        "title": "Python free-threaded CPython",
                        "content": "A primary proposal describing the free-threaded build.",
                    },
                    {
                        "url": "https://arxiv.org/abs/2404.11584",
                        "title": "AI agent architecture survey",
                        "content": "A survey of planning, reasoning, tools, and evaluation.",
                    },
                ]
            },
        )
    if request.url.host in {"docs.python.org", "peps.python.org", "arxiv.org"}:
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "text/html; charset=utf-8"},
            text=(
                "<html><head><title>Offline source</title></head><body><main>"
                "This deterministic offline source contains enough substantive evidence "
                "for the benchmark and is never sent to the public internet. "
                "It documents a bounded finding for repeatable research tests. "
                "</main></body></html>"
            ),
        )
    return httpx.Response(404, request=request)


@dataclass
class CaseResult:
    name: str
    lane: str
    elapsed_ms: float
    provider_calls: int
    memory_calls: int
    tool_calls: int


async def run_case(name: str, message: str) -> CaseResult:
    provider = FakeProvider()
    memory = FakeMemory()
    runtime = SmaraAgentRuntime(provider, memory=memory)
    events: list[str] = []
    started = time.perf_counter()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_offline_research_transport),
        follow_redirects=False,
    ) as http:
        await runtime.chat_with_tools(
            account_id="acct_benchmark",
            workspace_id="default",
            message=message,
            http_client=http,
            event_hook=lambda event, payload: events.append(event),
            token_hook=lambda _token: None,
        )
    return CaseResult(
        name,
        route_request(message).lane,
        round((time.perf_counter() - started) * 1000, 2),
        provider.complete_calls + provider.stream_calls,
        memory.calls,
        sum(event == "agent.tool_completed" for event in events),
    )


async def main(output: Path | None = None) -> None:
    # Keep the fixed corpus entirely offline while exercising the same Tavily
    # adapter and deep-research orchestration used in production.
    original_settings = research_tools.settings
    original_public_url_check = research_module._is_public_http_url
    research_tools.settings = replace(
        settings,
        search_provider="tavily",
        search_api_key="offline-fixture",
        search_url="",
        search_fallback_provider="",
        search_fallback_api_key="",
        search_timeout_seconds=2,
    )
    # The fixture uses public-looking hostnames so URL canonicalisation and
    # source selection stay realistic, but no DNS lookup should occur in an
    # offline benchmark.
    fixture_hosts = {"docs.python.org", "peps.python.org", "arxiv.org"}
    research_module._is_public_http_url = lambda url: (urlparse(url).hostname or "").lower() in fixture_hosts
    cases = [
        ("greeting", "hello"),
        ("self_contained", "Explain what a queue is in one sentence."),
        ("memory_recall", "Do you remember my preferred editor?"),
        ("current_time", "What is the current time?"),
        ("calculator", "Calculate 2 + 2."),
        ("search", "Search the latest Python release and cite sources."),
        ("multi_tool_research", "Research the latest Python release and compare it with the previous release."),
        ("durable_desktop", "Create a file in my approved workspace."),
        ("cancellation", "Cancel the current task."),
    ]
    try:
        results = [await run_case(name, message) for name, message in cases]
    finally:
        research_tools.settings = original_settings
        research_module._is_public_http_url = original_public_url_check
    latencies = sorted(result.elapsed_ms for result in results)
    p50 = latencies[len(latencies) // 2]
    p95 = latencies[min(len(latencies) - 1, max(0, int(len(latencies) * 0.95) - 1))]
    payload = {
        "mode": "offline",
        "summary": {"cases": len(results), "p50_ms": p50, "p95_ms": p95},
        "cases": [result.__dict__ for result in results],
    }
    rendered = json.dumps(payload, indent=2)
    print(rendered)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--output", type=Path, help="Write the redacted benchmark JSON to this path")
    args = parser.parse_args()
    asyncio.run(main(args.output))
