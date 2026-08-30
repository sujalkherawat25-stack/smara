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


@dataclass
class CaseResult:
    name: str
    elapsed_ms: float
    provider_calls: int


async def run_case(name: str, message: str) -> CaseResult:
    provider = FakeProvider()
    runtime = SmaraAgentRuntime(provider)
    started = time.perf_counter()
    await runtime.chat_with_tools(account_id="acct_benchmark", workspace_id="default", message=message)
    return CaseResult(name, round((time.perf_counter() - started) * 1000, 2), provider.complete_calls + provider.stream_calls)


async def main() -> None:
    cases = [
        ("greeting", "hello"),
        ("self_contained", "Explain what a queue is in one sentence."),
        ("current_time", "What is the current time?"),
        ("calculator", "Calculate 2 + 2."),
    ]
    results = [await run_case(name, message) for name, message in cases]
    print(json.dumps({"mode": "offline", "cases": [result.__dict__ for result in results]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=True)
    parser.parse_args()
    asyncio.run(main())
