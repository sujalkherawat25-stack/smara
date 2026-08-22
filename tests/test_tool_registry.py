import asyncio
import json

import httpx

from smara.tool_registry import ToolContext, ToolError, default_tool_registry


def test_default_registry_is_read_only_and_catalogued():
    tools = default_tool_registry().describe()
    assert [tool["name"] for tool in tools] == [
        "calculate",
        "current_time",
        "integration.calendar.list",
        "integration.drive.search",
        "integration.github.list",
        "integration.gmail.search",
        "research.fetch_url",
        "research.web_search",
    ]
    assert all(not tool["side_effecting"] and not tool["requires_approval"] for tool in tools)
    assert all(tool["parameters"].get("additionalProperties") is False for tool in tools)


def test_calculator_rejects_code_and_bounds_results():
    registry = default_tool_registry()
    context = ToolContext("acct_test", "workspace")

    async def execute():
        safe = await registry.invoke("calculate", {"expression": "(12 + 8) * 3"}, context)
        assert safe.content == "60"
        try:
            await registry.invoke("calculate", {"expression": "__import__('os').getcwd()"}, context)
        except ToolError as exc:
            assert "numeric arithmetic" in str(exc)
        else:
            raise AssertionError("calculator must reject code execution")

    asyncio.run(execute())


def test_fetch_tool_returns_bounded_safe_source():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://example.com/source")
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<html><title>Source</title><body>" + ("Readable evidence. " * 30) + "</body></html>")

    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
            result = await default_tool_registry(client).invoke(
                "research.fetch_url", {"url": "https://example.com/source"}, ToolContext("acct_test", "workspace", client)
            )
            data = json.loads(result.content)
            assert data["title"] == "Source"
            assert len(data["excerpt"]) <= 1_800
            assert result.citations == ["https://example.com/source"]

    asyncio.run(execute())


def test_integration_reads_use_runner_but_never_bypass_approval():
    calls = []

    async def runner(provider, action, payload):
        calls.append((provider, action, payload))
        return "read-only result"

    async def execute():
        registry = default_tool_registry(integration_runner=runner)
        result = await registry.invoke(
            "integration.gmail.search",
            {"query": "from:alice@example.com", "limit": 3},
            ToolContext("acct_test", "workspace", integration_runner=runner),
        )
        assert result.content == "read-only result"
        assert calls == [("gmail", "gmail.search", {"query": "from:alice@example.com", "limit": 3})]

    asyncio.run(execute())


def test_agent_can_create_only_an_approval_intent_for_external_work():
    captured = []

    def requester(provider, action, preview, idempotency_key, payload):
        captured.append((provider, action, preview, idempotency_key, payload))
        return {"id": "iact_test", "status": "awaiting_approval"}

    async def execute():
        registry = default_tool_registry(integration_requester=requester)
        result = await registry.invoke(
            "integration.request_approval",
            {"provider": "gmail", "action": "gmail.send", "preview": "Send report", "idempotency_key": "send-report-1", "payload": {"to": "user@example.com"}},
            ToolContext("acct_test", "workspace", integration_requester=requester),
        )
        assert json.loads(result.content) == {"approval_required": True, "status": "awaiting_approval", "action_id": "iact_test"}
        assert captured[0][0:2] == ("gmail", "gmail.send")

    asyncio.run(execute())


def test_agent_desktop_request_creates_intent_without_running_local_action():
    captured = []

    def requester(capability, preview, payload):
        captured.append((capability, preview, payload))
        return {"task_id": "task_desktop", "status": "waiting_approval", "capability": capability}

    async def execute():
        registry = default_tool_registry(desktop_requester=requester)
        result = await registry.invoke(
            "desktop.request_action",
            {"capability": "local_file_read", "preview": "Read the approved notes", "payload": {"path": "C:/notes.md"}},
            ToolContext("acct_test", "workspace", desktop_requester=requester),
        )
        data = json.loads(result.content)
        assert data["approval_required"] is True and data["task_id"] == "task_desktop"
        assert captured[0][0] == "local_file_read"

    asyncio.run(execute())
