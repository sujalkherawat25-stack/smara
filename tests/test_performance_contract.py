import asyncio
import json

from smara import agent_events
from smara.performance import TimingTrace, request_id


def test_request_id_accepts_safe_caller_value_and_rejects_unbounded_input():
    assert request_id("browser:abc-123") == "browser:abc-123"
    generated = request_id("not safe/with spaces")
    assert generated.startswith("req_")
    assert len(request_id("x" * 500)) < 100


def test_timing_trace_serializes_only_timings_and_counters():
    trace = TimingTrace("req_test")
    trace.mark("route_started")
    trace.increment("provider_calls")
    payload = trace.as_dict()
    assert payload["trace_id"] == "req_test"
    assert payload["counters"] == {"provider_calls": 1}
    assert "prompt" not in json.dumps(payload).lower()


def test_done_keeps_legacy_fields_and_adds_optional_timing_contract():
    raw = agent_events.done(
        memory_used=False,
        tools_used=0,
        total_ms=4,
        request_id="req_test",
        timings={"trace_id": "req_test", "timings": {"total_ms": 4}, "counters": {}},
        task_id="task_123",
    )
    payload = json.loads(raw.removeprefix("data: ").strip())
    assert payload["type"] == "done"
    assert payload["total_ms"] == 4
    assert payload["request_id"] == "req_test"
    assert payload["timings"]["trace_id"] == "req_test"
    assert payload["task_id"] == "task_123"
