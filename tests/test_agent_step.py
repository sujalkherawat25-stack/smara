import asyncio
import json

from smara.agent_step import AgentStepError, BoundedAgentStepRuntime
from smara.tool_registry import ToolContext, ToolRegistry, ToolResult, ToolSpec, default_tool_registry


class FakeProvider:
    _base_url = "https://llm.example/v1"
    _api_key = "test-key"
    _model = "test-model"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def complete(self, *, system: str, message: str) -> str:
        self.calls.append((system, message))
        return self.responses.pop(0)


class StreamingProvider(FakeProvider):
    async def stream_complete(self, *, system: str, message: str):
        self.calls.append((system, message))
        for token in ("The result ", "is 42."):
            await asyncio.sleep(0)
            yield token


class FailingStreamProvider(FakeProvider):
    async def stream_complete(self, *, system: str, message: str):
        self.calls.append((system, message))
        if False:
            yield ""
        raise RuntimeError("stream unavailable")


class PartialStreamProvider(FakeProvider):
    async def stream_complete(self, *, system: str, message: str):
        self.calls.append((system, message))
        yield "Partial answer"
        raise RuntimeError("connection dropped")


class EnvelopeStreamingProvider(FakeProvider):
    async def stream_complete(self, *, system: str, message: str):
        self.calls.append((system, message))
        for token in ('{"action":"final",', '"answer":"Clean final answer."}'):
            await asyncio.sleep(0)
            yield token


class CountingTool:
    spec = ToolSpec(
        "count_once",
        "Return a deterministic observation.",
        {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"], "additionalProperties": False},
    )

    def __init__(self):
        self.calls = 0

    async def run(self, arguments, context):
        self.calls += 1
        return ToolResult(True, f"observed {arguments['value']}")


class SlowTool:
    spec = ToolSpec(
        "slow_tool",
        "A deliberately slow test tool.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    )

    async def run(self, arguments, context):
        await asyncio.sleep(0.1)
        return ToolResult(True, "late result")


def test_agent_step_selects_only_registry_tool_and_returns_final_answer():
    provider = FakeProvider([
        json.dumps({"action": "tool", "name": "calculate", "arguments": {"expression": "6 * 7"}}),
        json.dumps({"action": "final", "answer": "The calculated result is 42."}),
    ])
    events = []

    async def execute():
        return await BoundedAgentStepRuntime(provider, default_tool_registry()).run(
            task={"objective": "Calculate 6 * 7"},
            tool_context=ToolContext("acct_test", "workspace"),
            event_hook=lambda name, payload: events.append((name, payload)),
        )

    result = asyncio.run(execute())
    assert result.text == "The calculated result is 42."
    assert result.tools_used == 1
    assert [event[0] for event in events] == ["agent.tool_requested", "agent.tool_completed"]
    assert events[1][1]["ok"] is True


def test_agent_step_can_request_a_previewable_local_pdf_task_without_executing_it():
    requested = []

    def desktop_requester(capability, preview, payload):
        requested.append((capability, preview, payload))
        return {"task_id": "task_pdf", "status": "waiting_approval", "capability": capability}

    provider = FakeProvider([
        json.dumps({"action": "tool", "name": "desktop.request_action", "arguments": {
            "capability": "local_file_write",
            "preview": "Create a local PDF report named report.pdf",
            "payload": {"operation": "create_pdf", "path": "report.pdf", "title": "Local report"},
        }}),
        json.dumps({"action": "final", "answer": "The local PDF task is ready for approval."}),
    ])

    async def execute():
        registry = default_tool_registry(desktop_requester=desktop_requester)
        return await BoundedAgentStepRuntime(provider, registry).run(
            task={"objective": "Create a PDF in my local workspace"},
            tool_context=ToolContext("acct_test", "workspace", desktop_requester=desktop_requester),
        )

    result = asyncio.run(execute())
    assert result.tools_used == 1
    assert requested == [("local_file_write", "Create a local PDF report named report.pdf", {"operation": "create_pdf", "path": "report.pdf", "title": "Local report"})]


def test_agent_step_streams_final_provider_tokens_after_bounded_planning():
    provider = StreamingProvider([
        json.dumps({"action": "final", "answer": "The result is 42."}),
    ])
    tokens = []

    async def execute():
        return await BoundedAgentStepRuntime(provider, default_tool_registry()).run(
            task={"objective": "Calculate 6 * 7"},
            tool_context=ToolContext("acct_test", "workspace"),
            token_hook=tokens.append,
        )

    result = asyncio.run(execute())
    assert tokens == ["The result ", "is 42."]
    assert result.text == "The result is 42."
    assert "do not return a JSON envelope" in provider.calls[-1][0]


def test_agent_step_falls_back_to_non_stream_when_stream_fails_before_output():
    provider = FailingStreamProvider([
        json.dumps({"action": "final", "answer": "Draft"}),
        "Recovered answer.",
    ])
    tokens = []

    async def execute():
        return await BoundedAgentStepRuntime(provider, default_tool_registry()).run(
            task={"objective": "Answer"},
            tool_context=ToolContext("acct_test", "workspace"),
            token_hook=tokens.append,
        )

    result = asyncio.run(execute())
    assert result.text == "Recovered answer."
    assert tokens == ["Recovered answer."]


def test_agent_step_does_not_duplicate_partial_output_after_stream_drop():
    provider = PartialStreamProvider([
        json.dumps({"action": "final", "answer": "Draft"}),
    ])
    tokens = []

    async def execute():
        return await BoundedAgentStepRuntime(provider, default_tool_registry()).run(
            task={"objective": "Answer"},
            tool_context=ToolContext("acct_test", "workspace"),
            token_hook=tokens.append,
        )

    result = asyncio.run(execute())
    assert result.text == "Partial answer"
    assert tokens == ["Partial answer"]


def test_agent_step_never_streams_a_planner_json_envelope_as_the_final_answer():
    provider = EnvelopeStreamingProvider([
        json.dumps({"action": "final", "answer": "Draft"}),
    ])
    tokens = []

    async def execute():
        return await BoundedAgentStepRuntime(provider, default_tool_registry()).run(
            task={"objective": "Answer"},
            tool_context=ToolContext("acct_test", "workspace"),
            token_hook=tokens.append,
        )

    result = asyncio.run(execute())
    assert result.text == "Clean final answer."
    assert tokens == ["Clean final answer."]


def test_agent_step_never_grants_unregistered_tool_access():
    provider = FakeProvider([
        json.dumps({"action": "tool", "name": "local_terminal", "arguments": {"command": "whoami"}}),
        json.dumps({"action": "final", "answer": "The requested tool is unavailable."}),
    ])
    events = []

    async def execute():
        return await BoundedAgentStepRuntime(provider, default_tool_registry()).run(
            task={"objective": "Run a local command"},
            tool_context=ToolContext("acct_test", "workspace"),
            event_hook=lambda name, payload: events.append((name, payload)),
        )

    result = asyncio.run(execute())
    assert result.text == "The requested tool is unavailable."
    assert events[1][1]["ok"] is False
    assert "not registered" in events[1][1]["preview"]


def test_agent_step_requires_provider_configuration():
    provider = FakeProvider([])
    provider._api_key = ""

    async def execute():
        return await BoundedAgentStepRuntime(provider, default_tool_registry()).run(
            task={"objective": "Do work"}, tool_context=ToolContext("acct_test", "workspace")
        )

    try:
        asyncio.run(execute())
    except Exception as exc:
        assert "provider is not configured" in str(exc)
    else:
        raise AssertionError("missing provider must stop the agent step")


def test_agent_step_can_only_request_external_approval():
    provider = FakeProvider([
        json.dumps({"action": "tool", "name": "integration.request_approval", "arguments": {
            "provider": "gmail", "action": "gmail.send", "preview": "Send update",
            "idempotency_key": "send-update-1", "payload": {"to": "user@example.com"},
        }}),
        json.dumps({"action": "final", "answer": "I requested approval before sending."}),
    ])

    def requester(provider_name, action, preview, key, payload):
        return {"id": "iact_test", "status": "awaiting_approval"}

    async def execute():
        return await BoundedAgentStepRuntime(provider, default_tool_registry(integration_requester=requester)).run(
            task={"objective": "Send the update by email"},
            tool_context=ToolContext("acct_test", "workspace", integration_requester=requester),
        )

    result = asyncio.run(execute())
    assert result.tools_used == 1
    assert result.text == "I requested approval before sending."


def test_agent_step_does_not_repeat_an_identical_tool_side_effect():
    tool = CountingTool()
    request = json.dumps({"action": "tool", "name": "count_once", "arguments": {"value": "one"}})
    provider = FakeProvider([request, request, json.dumps({"action": "final", "answer": "Used the first observation."})])
    events = []

    async def execute():
        return await BoundedAgentStepRuntime(provider, ToolRegistry([tool])).run(
            task={"objective": "Observe one value"},
            tool_context=ToolContext("acct_test", "workspace"),
            event_hook=lambda name, payload: events.append((name, payload)),
        )

    result = asyncio.run(execute())
    assert result.text == "Used the first observation."
    assert result.tools_used == 1
    assert tool.calls == 1
    assert any(event[1].get("ok") is False and "Repeated identical" in event[1].get("preview", "") for event in events)


def test_agent_step_times_out_one_tool_and_can_still_return_a_final_answer():
    provider = FakeProvider([
        json.dumps({"action": "tool", "name": "slow_tool", "arguments": {}}),
        json.dumps({"action": "final", "answer": "The tool timed out safely."}),
    ])
    events = []

    async def execute():
        return await BoundedAgentStepRuntime(
            provider,
            ToolRegistry([SlowTool()]),
            tool_timeout_seconds=0.01,
        ).run(
            task={"objective": "Use the slow tool"},
            tool_context=ToolContext("acct_test", "workspace"),
            event_hook=lambda name, payload: events.append((name, payload)),
        )

    result = asyncio.run(execute())
    assert result.text == "The tool timed out safely."
    assert result.tools_used == 0
    assert any(event[1].get("ok") is False and "timed out" in event[1].get("preview", "") for event in events)


def test_agent_step_enforces_a_total_wall_clock_budget():
    class SlowProvider(FakeProvider):
        async def complete(self, *, system: str, message: str) -> str:
            await asyncio.sleep(0.1)
            return '{"action":"final","answer":"late"}'

    async def execute():
        return await BoundedAgentStepRuntime(
            SlowProvider([]),
            default_tool_registry(),
            max_seconds=0.05,
        ).run(task={"objective": "Answer"}, tool_context=ToolContext("acct_test", "workspace"))

    try:
        asyncio.run(execute())
    except AgentStepError as exc:
        assert "bounded execution time" in str(exc)
    else:
        raise AssertionError("the total agent deadline must be enforced")
