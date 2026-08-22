"""Provider-neutral research tools used by Smara's task graph.

The tools deliberately return source records rather than an LLM-written answer.
Smara owns the evidence ledger, verification state, and final citation report.
Search providers are adapters behind one small interface so a provider can be
changed without changing tasks, workers, or the Web/CLI clients.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from .config import settings
from .research import RetrievedSource, fetch_public_source


class ResearchToolError(RuntimeError):
    """A safe, user-actionable research provider failure."""


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str
    snippet: str
    provider: str


class WebSearchTool:
    """Search the configured provider and return only usable public URLs."""

    name = "research.web_search"
    max_results = 8

    def __init__(self, http_client: httpx.AsyncClient | None = None):
        self._http = http_client

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        include_domains: list[str] | None = None,
    ) -> list[SearchHit]:
        query = query.strip()
        if not query:
            raise ResearchToolError("Research search needs a non-empty question.")
        if not settings.search_api_key:
            raise ResearchToolError("Smara web-search provider is not configured.")

        provider = settings.search_provider.strip().lower()
        count = max(1, min(self.max_results, int(max_results)))
        domains = [str(domain).strip().lower() for domain in (include_domains or []) if str(domain).strip()]
        search_query = query
        if domains:
            search_query = f"{query} " + " ".join(f"site:{domain}" for domain in domains[:5])
        owns_client = self._http is None
        client = self._http or httpx.AsyncClient(timeout=settings.search_timeout_seconds)
        try:
            if provider == "brave":
                response = await client.get(
                    settings.search_url,
                    params={"q": search_query, "count": count},
                    headers={"Accept": "application/json", "X-Subscription-Token": settings.search_api_key},
                )
                response.raise_for_status()
                items = ((response.json().get("web") or {}).get("results") or [])
                hits = [SearchHit(str(item.get("url") or ""), str(item.get("title") or ""), str(item.get("description") or ""), provider) for item in items]
            elif provider == "serper":
                response = await client.post(
                    settings.search_url,
                    headers={"X-API-KEY": settings.search_api_key, "Content-Type": "application/json"},
                    json={"q": search_query, "num": count},
                )
                response.raise_for_status()
                items = response.json().get("organic") or []
                hits = [SearchHit(str(item.get("link") or ""), str(item.get("title") or ""), str(item.get("snippet") or ""), provider) for item in items]
            else:
                raise ResearchToolError(f"Unsupported Smara search provider: {provider}.")
        except ResearchToolError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise ResearchToolError(f"The configured web-search provider is unavailable: {type(exc).__name__}.") from exc
        finally:
            if owns_client:
                await client.aclose()

        result: list[SearchHit] = []
        seen: set[str] = set()
        for hit in hits:
            url = hit.url.strip()
            parsed = urlparse(url)
            if not url or url in seen or parsed.scheme not in {"http", "https"} or not parsed.hostname:
                continue
            seen.add(url)
            result.append(SearchHit(url, hit.title[:500] or parsed.hostname, hit.snippet[:1200], hit.provider))
            if len(result) >= count:
                break
        return result


class FetchUrlTool:
    """Fetch one public page using Smara's bounded, SSRF-safe reader."""

    name = "research.fetch_url"

    def __init__(self, http_client: httpx.AsyncClient | None = None):
        self._http = http_client

    async def fetch(self, url: str) -> RetrievedSource:
        owns_client = self._http is None
        client = self._http or httpx.AsyncClient(timeout=httpx.Timeout(12.0), follow_redirects=False, headers={"User-Agent": "SmaraResearch/0.1 (+evidence-ledger)"})
        try:
            return await fetch_public_source(client, url)
        finally:
            if owns_client:
                await client.aclose()


class ResearchToolRegistry:
    """The small registry boundary used by the research executor."""

    def __init__(self, http_client: httpx.AsyncClient | None = None):
        self.web_search = WebSearchTool(http_client)
        self.fetch_url = FetchUrlTool(http_client)
