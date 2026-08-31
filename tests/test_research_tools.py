import asyncio
from types import SimpleNamespace

import httpx

from smara import research_tools
from smara.research import canonical_source_url, source_quality


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


def test_source_urls_are_canonicalized_and_quality_is_visible(monkeypatch):
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
        return httpx.Response(200, json={"web": {"results": [
            {"url": "https://openai.com/news/agents/?utm_source=feed", "title": "Official announcement", "description": "Primary source"},
            {"url": "https://openai.com/news/agents/", "title": "Duplicate", "description": "Same primary source"},
            {"url": "https://aiagentsdirectory.com/news", "title": "Directory", "description": "Discovery listing"},
        ]}})

    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await research_tools.WebSearchTool(client).search("agents", max_results=5)

    hits = asyncio.run(execute())
    assert len(hits) == 2
    assert hits[0].url == "https://openai.com/news/agents"
    assert hits[0].quality == "primary"
    assert "primary_source" in hits[0].quality_flags
    assert hits[1].quality == "discovery_only"
    assert canonical_source_url("https://example.com/a/?utm_medium=x#section") == "https://example.com/a"
    assert source_quality("https://www.youtube.com/watch?v=abc")[0] == "discovery_only"


def test_tavily_uses_advanced_depth_by_default(monkeypatch):
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
        assert request.content.find(b'"search_depth":"advanced"') >= 0
        return httpx.Response(200, json={"results": []})

    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await research_tools.WebSearchTool(client).search("quality")

    assert asyncio.run(execute()) == []


def test_deep_research_searches_the_subject_not_output_requirements(monkeypatch):
    monkeypatch.setattr(
        research_tools,
        "settings",
        SimpleNamespace(
            search_provider="brave",
            search_api_key="test-key",
            search_url="https://search.test/web",
            search_timeout_seconds=2,
            search_max_concurrency=1,
            search_fallback_provider="",
            search_fallback_api_key="",
            search_fallback_url="",
        ),
    )
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "search.test":
            requested.append(request.url.params["q"])
            return httpx.Response(200, json={"web": {"results": [
                {"url": f"https://source{len(requested)}.example/report", "title": "Source", "description": "Evidence"},
            ]}})
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<title>Source</title><p>Readable evidence.</p>")

    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await research_tools.DeepResearchTool(client).run(
                "Produce a 1,200-word research brief on current AI-agent architecture. Include five citations and end with limitations.",
                max_sources=3,
            )
            assert result.sources == 3

    asyncio.run(execute())
    assert requested[0] == "current AI-agent architecture"
    assert all("1,200-word" not in item for item in requested)


def test_deep_research_searches_the_subject_after_conversational_wrapper():
    topic = research_tools.DeepResearchTool._search_topic(
        "okay so do the deep search on ai agent, and graph engineering etc. "
        "i want the complete guide of how make such system"
    )
    assert topic == "ai agent and graph engineering"


def test_deep_research_keeps_the_subject_when_request_mentions_search():
    topic = research_tools.DeepResearchTool._search_topic(
        "Please do a deep search on graph-based AI agent architecture with citations and at least 1200 words."
    )
    assert topic == "graph-based AI agent architecture"


def test_deep_research_ranks_sources_against_the_search_topic():
    hits = [
        research_tools.SearchHit("https://irrelevant.example", "Other", "Weather forecast and sports scores", "brave"),
        research_tools.SearchHit("https://relevant.example", "Graph agents", "Graph engineering for AI agent workflows", "brave"),
    ]
    selected = research_tools.DeepResearchTool._select_sources(hits, 1, [], "AI-agent graph engineering")
    assert selected[0].url == "https://relevant.example"


def test_deep_research_preserves_evidence_for_each_selected_source(monkeypatch):
    monkeypatch.setattr(
        research_tools,
        "settings",
        SimpleNamespace(
            search_provider="brave", search_api_key="test-key", search_url="https://search.test/web",
            search_timeout_seconds=2, search_max_concurrency=1,
            search_fallback_provider="", search_fallback_api_key="", search_fallback_url="",
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "search.test":
            suffix = request.url.params["q"][-1]
            return httpx.Response(200, json={"web": {"results": [
                {"url": f"https://source{suffix}.example/report", "title": f"Source {suffix}", "description": "Evidence"},
            ]}})
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<title>Source</title><p>" + ("Evidence. " * 2_000) + "</p>")

    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await research_tools.DeepResearchTool(client).run("agent source 1", subqueries=["agent source 2", "agent source 3"], max_sources=3)
            assert result.sources == 3
            assert all(f"[{index}]" in result.content for index in range(1, 4))

    asyncio.run(execute())


def test_page_reader_ignores_javascript_and_stylesheet_noise():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=(
                "<html><head><title>Readable report</title>"
                "<style>body{font-family:fake} .noise{display:none}</style>"
                "<script>window.__BOOTSTRAP__='not evidence';</script></head>"
                "<body><main>"
                "This is the substantive report text that should be extracted and cited. "
                + ("It contains a documented finding. " * 8)
                + "</main><script>console.log('ignore me')</script></body></html>"
            ),
        )

    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await research_tools.FetchUrlTool(client).fetch("https://example.com/report")

    source = asyncio.run(execute())
    assert "substantive report text" in source.excerpt
    assert "__BOOTSTRAP__" not in source.excerpt
    assert "font-family" not in source.excerpt
