import asyncio
from types import SimpleNamespace

import httpx

from smara import research_tools


def test_brave_search_adapter_returns_public_deduplicated_hits(monkeypatch):
    monkeypatch.setattr(
        research_tools,
        "settings",
        SimpleNamespace(
            search_provider="brave",
            search_api_key="test-key",
            search_url="https://search.test/web",
            search_timeout_seconds=2,
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-subscription-token"] == "test-key"
        assert request.url.params["count"] == "4"
        return httpx.Response(200, json={"web": {"results": [
            {"url": "https://example.com/a", "title": "A", "description": "first"},
            {"url": "https://example.com/a", "title": "duplicate", "description": "ignored"},
            {"url": "javascript:alert(1)", "title": "unsafe", "description": "ignored"},
            {"url": "https://example.com/b", "title": "B", "description": "second"},
        ]}})

    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await research_tools.WebSearchTool(client).search("solar", max_results=4)

    hits = asyncio.run(execute())
    assert [(hit.url, hit.title) for hit in hits] == [("https://example.com/a", "A"), ("https://example.com/b", "B")]


def test_search_without_provider_key_is_explicit(monkeypatch):
    monkeypatch.setattr(
        research_tools,
        "settings",
        SimpleNamespace(search_api_key="", search_provider="brave", search_url="", search_timeout_seconds=2),
    )

    async def execute():
        await research_tools.WebSearchTool().search("missing provider")

    try:
        asyncio.run(execute())
    except research_tools.ResearchToolError as exc:
        assert "not configured" in str(exc)
    else:
        raise AssertionError("missing search configuration must not silently return empty evidence")


def test_tavily_search_adapter_keeps_key_server_side(monkeypatch):
    monkeypatch.setattr(
        research_tools,
        "settings",
        SimpleNamespace(
            search_provider="tavily",
            search_api_key="tavily-test-key",
            search_url="",
            search_timeout_seconds=2,
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read().decode()
        assert '"api_key":"tavily-test-key"' in body
        assert request.url == httpx.URL("https://api.tavily.com/search")
        return httpx.Response(200, json={"results": [{"url": "https://example.com/tavily", "title": "Tavily source", "content": "Tavily evidence"}]})

    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await research_tools.WebSearchTool(client).search("tavily query")

    hits = asyncio.run(execute())
    assert hits[0].provider == "tavily"
    assert hits[0].url == "https://example.com/tavily"
