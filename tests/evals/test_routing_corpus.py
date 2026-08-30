"""Small deterministic promotion corpus for the latency rollout.

This is intentionally provider-free. It protects the routing contract and
call-count shape in CI; live answer quality and p95 measurements are captured
by ``scripts/benchmark_smara.py`` with an operator-provided provider.
"""
from __future__ import annotations

import pytest

from smara.agent_routing import route_request


@pytest.mark.parametrize(
    ("message", "lane", "durable", "deterministic"),
    [
        ("hi", "B", False, False),
        ("calculate 12 * 7", "A", False, True),
        ("what time is it?", "A", False, True),
        ("remember my preferred editor", "C", False, False),
        ("search the latest Python release and cite sources", "D", False, False),
        ("research the latest security news", "D", False, False),
        ("write a file in my approved workspace", "E", True, False),
        ("run a terminal command", "E", True, False),
        ("cancel the task", "E", True, False),
    ],
)
def test_owner_corpus_routes_safely(message: str, lane: str, durable: bool, deterministic: bool):
    decision = route_request(message)
    assert decision.lane == lane
    assert decision.durable_required is durable
    assert (decision.deterministic_tool is not None) is deterministic
    if durable:
        assert decision.tools_allowed == ()


def test_attachment_corpus_never_silently_downgrades_to_chitchat():
    decision = route_request("summarize this image", has_attachments=True)
    assert decision.lane == "B"
    assert decision.durable_required is False
