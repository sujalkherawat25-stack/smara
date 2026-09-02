from __future__ import annotations

import asyncio
import time

from smara.agent_routing import route_request
from smara.agent_runtime import SmaraAgentRuntime


def test_route_exact_safe_requests_to_deterministic_lane():
    time_route = route_request("what time is it?")
    assert time_route.lane == "A"
    assert time_route.deterministic_tool == ("current_time", {})
    calc_route = route_request("calculate 6 * 7")
    assert calc_route.lane == "A"
    assert calc_route.deterministic_tool == ("calculate", {"expression": "6 * 7"})


def test_route_natural_clock_and_identity_requests_reliably():
    assert route_request("what time it is").deterministic_tool == ("current_time", {})
    identity = route_request("Do you know me?")
    assert identity.lane == "C"
    assert identity.memory_needed is True


def test_route_detailed_citation_requests_to_deterministic_deep_research():
    route = route_request("Give me a detailed analysis of current AI agent architecture with citations")
    assert route.deterministic_tool is not None
    assert route.deterministic_tool[0] == "research.deep"
    assert "research.deep" in route.tools_allowed


def test_route_hyphenated_word_target_to_deterministic_deep_research():
    route = route_request("Give me a 1,200-word analysis of current AI-agent architecture with sources")
    assert route.deterministic_tool is not None
    assert route.deterministic_tool[0] == "research.deep"


def test_route_conversational_deep_search_to_deterministic_research():
    route = route_request(
        "okay so do the deep search on ai agent and graph engineering. "
        "i want the complete guide of how make such system"
    )
    assert route.lane == "D"
    assert route.deterministic_tool is not None
    assert route.deterministic_tool[0] == "research.deep"


def test_route_keeps_local_and_external_writes_as_durable_work():
    route = route_request("write a report to my Documents folder")
    assert route.lane == "E"
    assert route.durable_required is True


def test_local_github_request_enters_approval_lane_when_hosted_integrations_are_off():
    route = route_request("List my GitHub repositories", local_only=True)
    assert route.lane == "E"
    assert route.durable_required is True
    assert "paired desktop" in route.reason


def test_github_request_remains_read_only_when_hosted_integrations_are_enabled():
    route = route_request("List my GitHub repositories")
    assert route.lane == "D"
    assert route.durable_required is False
    assert "integration.github.list" in route.tools_allowed


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


def test_deterministic_lane_emits_tool_lifecycle_events():
    class Provider:
        _model = "unused"

        async def complete(self, *, system, message):
            raise AssertionError("deterministic tools should not call the provider")

    events = []
    turn = asyncio.run(SmaraAgentRuntime(Provider()).chat_with_tools(
        account_id="acct_1",
        workspace_id="default",
        message="calculate 6 * 7",
        event_hook=lambda name, payload: events.append((name, payload)),
    ))
    assert turn.tools_used == 1
    assert [name for name, _ in events if name.startswith("agent.tool_")] == [
        "agent.tool_requested", "agent.tool_completed"
    ]
    assert events[-1][1]["ok"] is True


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


def test_memory_timeout_is_non_fatal_and_reported_as_unavailable():
    class Provider:
        _model = "model"

        async def complete(self, *, system, message):
            return "A direct answer while memory is unavailable."

    class SlowMemory:
        async def context_for_conversation(self, query, *, account_id, workspace_id):
            await asyncio.sleep(2)
            return "late memory"

    started = time.perf_counter()
    turn = asyncio.run(SmaraAgentRuntime(Provider(), SlowMemory()).chat_with_tools(
        account_id="acct_1",
        workspace_id="default",
        message="What do you remember about my project?",
    ))
    assert turn.memory_used is False
    assert "memory is unavailable" in turn.message
    assert time.perf_counter() - started < 1.9


def test_attachment_text_is_sent_once_for_direct_chat():
    class Provider:
        _model = "model"
        system = ""
        message = ""

        async def complete(self, *, system, message):
            self.system, self.message = system, message
            return "Summarized."

    provider = Provider()
    asyncio.run(SmaraAgentRuntime(provider).chat_with_tools(
        account_id="acct_1",
        workspace_id="default",
        message="Summarize this file",
        attachment_context="Attachment: notes.txt\nUNIQUE_ATTACHMENT_PHRASE",
    ))
    assert "UNIQUE_ATTACHMENT_PHRASE" not in provider.system
    assert str(provider.message).count("UNIQUE_ATTACHMENT_PHRASE") == 1
