import asyncio
import json

from smara.agent_step import BoundedAgentStepRuntime
from smara.tool_registry import ToolContext, default_tool_registry


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
