"""Focused contract tests for bounded programmatic tool calling."""

import json

from smara.autonomous_agent import SmaraAutonomousAgent, get_tool_schemas
from smara.ptc_kernel import PTC_MAX_CALLS, ProgrammaticToolKernel


def test_kernel_batches_only_allowlisted_calls_and_preserves_results():
    seen = []

    def dispatch(name, args):
        seen.append((name, args))
        return {"calculate": "4", "file_read": "hello"}[name]

    result = ProgrammaticToolKernel(dispatch).execute(
        [
            {"name": "calculate", "args": {"expression": "2 + 2"}},
            {"name": "file_read", "args": {"file_path": "notes.txt"}},
        ]
    )

    assert result.ok is True
    assert seen == [
        ("calculate", {"expression": "2 + 2"}),
        ("file_read", {"file_path": "notes.txt"}),
    ]
    assert json.loads(result.to_model_json()) == {
        "ok": True,
        "calls": [
            {"name": "calculate", "ok": True, "content": "4"},
            {"name": "file_read", "ok": True, "content": "hello"},
        ],
    }


def test_kernel_rejects_mutation_nested_calls_and_oversized_batches_without_dispatch():
    calls_seen = []
    kernel = ProgrammaticToolKernel(lambda name, args: calls_seen.append((name, args)) or "unexpected")

    for invalid in (
        [{"name": "terminal", "args": {"command": "whoami"}}],
        [{"name": "programmatic_tool_call", "args": {"calls": []}}],
        [{"name": "calculate", "args": {}, "extra": True}],
        [{"name": "calculate", "args": ["not-an-object"]}],
        [{"name": "calculate", "args": {}}] * (PTC_MAX_CALLS + 1),
    ):
        result = kernel.execute(invalid)
        assert result.ok is False
        assert result.error

    assert calls_seen == []


def test_agent_exposes_and_executes_programmatic_tool_call():
    schema = next(
        schema for schema in get_tool_schemas("full")
        if schema["function"]["name"] == "programmatic_tool_call"
    )
    params = schema["function"]["parameters"]
    assert params["additionalProperties"] is False
    assert params["properties"]["calls"]["maxItems"] == PTC_MAX_CALLS
    item = params["properties"]["calls"]["items"]
    assert item["additionalProperties"] is False

    agent = SmaraAutonomousAgent(api_key="test-key", model="test-model")
    result = json.loads(
        agent.execute_tool(
            "programmatic_tool_call",
            {"calls": [{"name": "calculate", "args": {"expression": "2 + 2"}}]},
        )
    )
    assert result["ok"] is True
    assert result["calls"][0]["name"] == "calculate"
    assert result["calls"][0]["content"] == "4"
