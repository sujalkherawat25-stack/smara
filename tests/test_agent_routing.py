from __future__ import annotations

import asyncio

from smara.agent_routing import route_request
from smara.agent_runtime import SmaraAgentRuntime


def test_route_exact_safe_requests_to_deterministic_lane():
    time_route = route_request("what time is it?")
    assert time_route.lane == "A"
    assert time_route.deterministic_tool == ("current_time", {})
    calc_route = route_request("calculate 6 * 7")
    assert calc_route.lane == "A"
    assert calc_route.deterministic_tool == ("calculate", {"expression": "6 * 7"})


def test_route_keeps_local_and_external_writes_as_durable_work():
    route = route_request("write a report to my Documents folder")
    assert route.lane == "E"
    assert route.durable_required is True


def test_deterministic_lane_uses_registered_tool_without_provider_call():
    class Provider:
        _model = "unused"
        calls = 0

        async def complete(self, *, system, message):
            self.calls += 1
            return "should not run"

    provider = Provider()
    runtime = SmaraAgentRuntime(provider)
    turn = asyncio.run(runtime.chat_with_tools(
        account_id="acct_1",
        workspace_id="default",
        message="calculate 6 * 7",
    ))
    assert turn.message == "The result is 42."
    assert turn.tools_used == 1
    assert provider.calls == 0


def test_self_contained_lane_skips_shared_memory():
    class Provider:
        _model = "model"

        async def complete(self, *, system, message):
            assert "Relevant shared Syntarus memory" not in system
            return "A direct answer."

    class Memory:
        calls = 0

        async def context_for_conversation(self, query, *, account_id, workspace_id):
            self.calls += 1
            return "should not be retrieved"

    memory = Memory()
    turn = asyncio.run(SmaraAgentRuntime(Provider(), memory).chat_with_tools(
        account_id="acct_1",
        workspace_id="default",
        message="Explain what a Python list is.",
    ))
    assert turn.message == "A direct answer."
    assert turn.memory_used is False
    assert memory.calls == 0
