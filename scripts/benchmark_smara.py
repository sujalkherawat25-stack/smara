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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from smara.agent_routing import route_request
from smara.agent_runtime import SmaraAgentRuntime


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
    await runtime.chat_with_tools(
        account_id="acct_benchmark",
        workspace_id="default",
        message=message,
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
    results = [await run_case(name, message) for name, message in cases]
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
