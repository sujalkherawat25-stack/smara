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
