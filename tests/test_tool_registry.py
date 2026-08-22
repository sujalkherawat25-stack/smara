import asyncio
import json

import httpx

from smara.tool_registry import ToolContext, ToolError, default_tool_registry


def test_default_registry_is_read_only_and_catalogued():
    tools = default_tool_registry().describe()
    assert [tool["name"] for tool in tools] == ["calculate", "current_time", "research.fetch_url", "research.web_search"]
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
