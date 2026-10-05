import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from smara import research_tools


@pytest.mark.parametrize("provider", ["exa", "tavily", "brave", "serper"])
def test_domain_filters_are_alternatives_not_conjunctive_sites(monkeypatch, provider):
    monkeypatch.setattr(research_tools, "settings", SimpleNamespace(
        search_provider=provider, search_api_key="fixture", search_url="", search_timeout_seconds=2))
    monkeypatch.delenv("SMARA_SEARCH_PROVIDER", raising=False)
    monkeypatch.delenv("SMARA_SEARCH_URL", raising=False)
    domains = ["publisher.example", "library.example"]

    def handler(request):
        if provider == "brave":
            assert request.url.params["q"] == "release policy (site:publisher.example OR site:library.example)"
        else:
            body = json.loads(request.content)
            if provider in {"exa", "tavily"}:
                assert body["query"] == "release policy"
                assert body["includeDomains" if provider == "exa" else "include_domains"] == domains
            else:
                assert body["q"] == "release policy (site:publisher.example OR site:library.example)"
        urls = ["https://publisher.example/a", "https://docs.library.example/b",
                "https://publisher.example.evil.test/c", "https://unrelated.example/d"]
        if provider == "brave":
            return httpx.Response(200, json={"web": {"results": [{"url": url} for url in urls]}})
        if provider == "serper":
            return httpx.Response(200, json={"organic": [{"link": url} for url in urls]})
        return httpx.Response(200, json={"results": [{"url": url} for url in urls]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await research_tools.WebSearchTool(client).search("release policy", include_domains=domains)

    assert [hit.url for hit in asyncio.run(run())] == ["https://publisher.example/a", "https://docs.library.example/b"]


def test_invalid_domain_is_rejected_before_network(monkeypatch):
    monkeypatch.setattr(research_tools, "settings", SimpleNamespace(
        search_provider="exa", search_api_key="fixture", search_url="", search_timeout_seconds=2))
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: pytest.fail("Invalid filter dispatched"))) as client:
            await research_tools.WebSearchTool(client).search("policy", include_domains=["example.org site:evil.org"])
    with pytest.raises(ValueError, match="valid public hostnames"):
        asyncio.run(run())
