"""Provider-neutral research tools used by Smara's task graph.

The tools deliberately return source records rather than an LLM-written answer.
Smara owns the evidence ledger, verification state, and final citation report.
Search providers are adapters behind one small interface so a provider can be
changed without changing tasks, workers, or the Web/CLI clients.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from .config import settings
from .research import RetrievedSource, canonical_source_url, fetch_public_source, source_quality


class ResearchToolError(RuntimeError):
    """A safe, user-actionable research provider failure."""


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str
    snippet: str
    provider: str
    quality: str = "unclassified"
    quality_flags: tuple[str, ...] = ()


class WebSearchTool:
    """Search the configured provider and return only usable public URLs."""

    name = "research.web_search"
    max_results = 8
    default_urls = {
        "brave": "https://api.search.brave.com/res/v1/web/search",
        "serper": "https://google.serper.dev/search",
        "tavily": "https://api.tavily.com/search",
        "exa": "https://api.exa.ai/search",
    }

    def __init__(self, http_client: httpx.AsyncClient | None = None):
        self._http = http_client

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        include_domains: list[str] | None = None,
        provider_override: str | None = None,
        api_key_override: str | None = None,
        url_override: str | None = None,
    ) -> list[SearchHit]:
        query = query.strip()
        if not query:
            raise ResearchToolError("Research search needs a non-empty question.")
        provider = (provider_override or settings.search_provider).strip().lower()
        api_key = api_key_override if api_key_override is not None else settings.search_api_key
        if not api_key:
            raise ResearchToolError("Smara web-search provider is not configured.")

        search_url = (url_override if url_override is not None else settings.search_url).strip() or self.default_urls.get(provider, "")
        if not search_url:
            raise ResearchToolError(f"Unsupported Smara search provider: {provider}.")
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
                    search_url,
                    params={"q": search_query, "count": count},
                    headers={"Accept": "application/json", "X-Subscription-Token": api_key},
                )
                response.raise_for_status()
                items = ((response.json().get("web") or {}).get("results") or [])
                hits = [SearchHit(str(item.get("url") or ""), str(item.get("title") or ""), str(item.get("description") or ""), provider) for item in items]
            elif provider == "serper":
                response = await client.post(
                    search_url,
                    headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                    json={"q": search_query, "num": count},
                )
                response.raise_for_status()
                items = response.json().get("organic") or []
                hits = [SearchHit(str(item.get("link") or ""), str(item.get("title") or ""), str(item.get("snippet") or ""), provider) for item in items]
            elif provider == "tavily":
                depth = str(getattr(settings, "search_depth", "advanced")).lower()
                if depth not in {"basic", "advanced"}:
                    depth = "advanced"
                response = await client.post(
                    search_url,
                    headers={"Content-Type": "application/json"},
                    json={"api_key": api_key, "query": search_query, "search_depth": depth, "max_results": count, "include_answer": False, "include_raw_content": False},
                )
                response.raise_for_status()
                items = response.json().get("results") or []
                hits = [SearchHit(str(item.get("url") or ""), str(item.get("title") or ""), str(item.get("content") or ""), provider) for item in items]
            elif provider == "exa":
                response = await client.post(
                    search_url,
                    headers={"x-api-key": api_key, "Content-Type": "application/json"},
                    json={
                        "query": search_query,
                        "type": "auto",
                        "numResults": count,
                        "contents": {"highlights": {"maxCharacters": 1200}},
                    },
                )
                response.raise_for_status()
                items = response.json().get("results") or []
                hits = []
                for item in items:
                    highlights = item.get("highlights") or []
                    snippet = " ".join(str(value).strip() for value in highlights if str(value).strip())
                    hits.append(SearchHit(str(item.get("url") or ""), str(item.get("title") or ""), snippet or str(item.get("text") or ""), provider))
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
            canonical = canonical_source_url(url)
            if canonical in seen:
                continue
            seen.add(canonical)
            title = hit.title[:500] or parsed.hostname
            snippet = hit.snippet[:1200]
            quality, flags = source_quality(canonical, title, snippet)
            result.append(SearchHit(canonical, title, snippet, hit.provider, quality, tuple(flags)))
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


@dataclass(frozen=True)
class ResearchPass:
    """Bounded evidence bundle ready for a citation-constrained writer."""

    content: str
    citations: list[str]
    providers: list[str]
    queries: list[str]
    sources: int
    fetched: int
    failed: int


class DeepResearchTool:
    """Run a deterministic, multi-source research pass.

    The model should not have to remember to call search, then fetch every
    result, then deduplicate domains.  This adapter does those predictable
    steps concurrently and returns labelled evidence.  Search snippets are
    retained only when a public page blocks fetching, and are explicitly
    marked as snippets so the final writer cannot mistake them for verified
    page text.
    """

    max_queries = 4
    max_sources = 6
    total_chars = 16_000

    def __init__(self, http_client: httpx.AsyncClient | None = None):
        self._http = http_client

    @staticmethod
    def _search_topic(query: str) -> str:
        """Keep search requests about the subject, not output instructions.

        People naturally ask for a report and then append requirements such as
        word count, citations, or a conclusion.  Passing that entire request
        to a web-search provider harms recall and can make every research
        angle return the same weak result.  The original request remains in
        the evidence bundle for the writer; this only prepares search terms.
        """
        topic = re.sub(r"\s+", " ", query.strip())
        # Do not split on the word "search" itself.  Natural requests often
        # begin with exactly that phrase ("do the deep search on …").  The
        # old splitter consequently searched for "okay so do the deep",
        # which starved the evidence pass of relevant sources.
        topic = re.sub(
            r"^(?:(?:okay|ok|so|please|hey)[,!\s]*)*"
            r"(?:(?:i\s+(?:want|need|would\s+like)\s+(?:you\s+)?to|can\s+you|could\s+you)\s+)?"
            r"(?:(?:do|conduct|perform|run)\s+)?"
            r"(?:(?:a|the)\s+)?(?:deep\s+)?(?:web\s+)?(?:research|search)\s+(?:on|for|about)\s+",
            "",
            topic,
            flags=re.IGNORECASE,
        )
        topic = re.sub(
            r"^(?:please\s+)?(?:give|write|provide|produce|create)\s+(?:me\s+)?(?:a\s+)?"
            r"(?:detailed\s+|comprehensive\s+)?(?:[\d,]+\s*[-–]?\s*word\s+)?"
            r"(?:minimum\s+)?(?:analysis|research|report|brief|breakdown)"
            r"(?:\s+(?:analysis|research|report|brief|breakdown))?\s+(?:of|on|about)\s+",
            "",
            topic,
            flags=re.IGNORECASE,
        )
        # Remove output instructions only after the subject has been
        # extracted.  This keeps "search" in a subject phrase intact while
        # discarding citation/length/report boilerplate that degrades recall.
        topic = re.split(
            r"(?:[.;:]\s*|,\s*)(?:i\s+(?:want|need|would\s+like)|"
            r"(?:please\s+)?(?:include|cite|citation|citations|sources?|references?|end|finish|do\s+not)\b|"
            r"(?:please\s+)?(?:give|write|provide|produce|create)\s+(?:me\s+)?(?:a\s+)?"
            r"(?:complete|comprehensive|detailed)\s+(?:guide|report|analysis|breakdown)\b)",
            topic,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        topic = re.sub(
            r"\s+(?:with|including|and)\s+(?:inline\s+)?(?:citations?|sources?|references?)\b.*$",
            "",
            topic,
            flags=re.IGNORECASE,
        )
        topic = re.sub(
            r"\s+(?:and|with)?\s*(?:at\s+least|minimum(?:\s+of)?)\s+[\d,]+\s*[-–]?\s*words?\b.*$",
            "",
            topic,
            flags=re.IGNORECASE,
        )
        topic = re.sub(r"\betc\.?\b", "", topic, flags=re.IGNORECASE)
        topic = re.sub(r"\s*,\s*", " ", topic)
        topic = re.sub(r"\s+", " ", topic).strip(" .,:;-\u2013")
        # A bounded fallback keeps arbitrary short queries usable.
        return topic[:500] or query[:500]

    async def run(
        self,
        query: str,
        *,
        subqueries: list[str] | None = None,
        max_sources: int = 5,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        focus: str = "",
    ) -> ResearchPass:
        query = str(query or "").strip()
        if not query:
            raise ResearchToolError("Research needs a non-empty question.")
        max_sources = max(2, min(self.max_sources, int(max_sources)))
        search_topic = self._search_topic(query)
        queries: list[str] = [search_topic]
        for candidate in subqueries or []:
            value = str(candidate).strip()
            if value and value.lower() not in {item.lower() for item in queries}:
                queries.append(value)
        # Distinct angles are essential for a useful answer.  They are only
        # added when the caller did not supply enough of them explicitly.
        defaults = (
            f"{search_topic} official documentation primary sources",
            f"{search_topic} peer reviewed survey research evidence",
            f"{search_topic} safety governance evaluation benchmarks",
        )
        for candidate in defaults:
            if len(queries) >= self.max_queries:
                break
            if candidate.lower() not in {item.lower() for item in queries}:
                queries.append(candidate)

        owns_client = self._http is None
        client = self._http or httpx.AsyncClient(timeout=settings.search_timeout_seconds, follow_redirects=False)
        try:
            hits, providers, errors = await self._search_all(client, queries, include_domains)
            # If the primary is unavailable, retry the whole pass against a
            # separately configured provider.  If it returned only a handful
            # of leads, the fallback fills the missing diversity as well.
            fallback_provider = str(getattr(settings, "search_fallback_provider", "") or "").strip().lower()
            fallback_key = str(getattr(settings, "search_fallback_api_key", "") or "")
            minimum_diverse_sources = min(max_sources, 3)
            if fallback_provider and fallback_key and (
                not hits or len({urlparse(hit.url).netloc for hit in hits}) < minimum_diverse_sources
            ):
                fallback_hits, fallback_used, fallback_errors = await self._search_all(
                    client, queries, include_domains,
                    provider=fallback_provider,
                    api_key=fallback_key,
                    url=str(getattr(settings, "search_fallback_url", "") or ""),
                )
                hits.extend(fallback_hits)
                providers.extend(fallback_used)
                errors.extend(fallback_errors)
            if not hits:
                detail = "; ".join(dict.fromkeys(errors))[:300]
                raise ResearchToolError(
                    "No usable sources were returned by the configured research providers."
                    + (f" ({detail})" if detail else "")
                )
            # ``focus`` is optional UI intent.  The actual search topic is a
            # dependable relevance signal when no UI-specific focus exists.
            selected = self._select_sources(hits, max_sources, exclude_domains or [], focus or search_topic)
            fetched = await asyncio.gather(
                *[self._fetch_one(client, hit) for hit in selected],
                return_exceptions=True,
            )
            blocks: list[str] = []
            citations: list[str] = []
            fetched_count = 0
            failed_count = 0
            for index, (hit, result) in enumerate(zip(selected, fetched), start=1):
                url = hit.url
                citations.append(url)
                if isinstance(result, RetrievedSource):
                    fetched_count += 1
                    title = result.title or hit.title or url
                    # Allocate the evidence budget across every selected
                    # source.  A sequential global truncation previously
                    # let the first large page consume the whole context,
                    # leaving the writer with only one or two labels even
                    # though search had successfully found five sources.
                    per_source_budget = max(1_200, (self.total_chars - 2_000) // max(1, len(selected)))
                    excerpt = result.excerpt[:per_source_budget]
                    blocks.append(
                        f"[{index}] {title}\nURL: {url}\nSOURCE TYPE: fetched page\n"
                        f"EVIDENCE:\n{excerpt}"
                    )
                else:
                    failed_count += 1
                    blocks.append(
                        f"[{index}] {hit.title or url}\nURL: {url}\nSOURCE TYPE: search snippet only\n"
                        f"EVIDENCE:\n{hit.snippet[:1200]}\nLIMITATION: page fetch failed or was blocked; do not treat this as verified page text."
                    )
            packed = "\n\n---\n\n".join(blocks)
            if len(packed) > self.total_chars:
                packed = packed[: self.total_chars] + "\n\n[…additional source text omitted by Smara’s context limit]"
            if not packed:
                raise ResearchToolError("Sources were found, but none contained readable evidence.")
            return ResearchPass(
                content=(
                    "[RESEARCH_CONTEXT]\n"
                    f"Research question: {query}\n"
                    f"Search topic: {search_topic}\n"
                    f"PASS COMPLETE: checked {len(queries)} distinct search angles and selected {len(selected)} diverse sources. "
                    f"Fetched {fetched_count} page(s); {failed_count} remain snippet-only.\n"
                    "Use only the labelled evidence below. Cite factual claims with the matching labels [1], [2], etc. "
                    "Separate direct facts from interpretation and state limitations when evidence is snippet-only.\n\n"
                    + packed
                ),
                citations=citations,
                providers=sorted(set(providers)),
                queries=queries,
                sources=len(selected),
                fetched=fetched_count,
                failed=failed_count,
            )
        finally:
            if owns_client:
                await client.aclose()

    async def _search_all(
        self,
        client: httpx.AsyncClient,
        queries: list[str],
        include_domains: list[str] | None,
        *,
        provider: str | None = None,
        api_key: str | None = None,
        url: str | None = None,
    ) -> tuple[list[SearchHit], list[str], list[str]]:
        tool = WebSearchTool(client)
        # The provider is external and may reject a burst of four searches.
        # Bound the fan-out and retry only empty/failed angles once.  This
        # preserves responsiveness while making a multi-source pass actually
        # use its multiple independent angles.
        max_concurrency = max(1, min(4, int(getattr(settings, "search_max_concurrency", 2))))
        semaphore = asyncio.Semaphore(max_concurrency)

        async def search_one(item: str) -> list[SearchHit] | Exception:
            last_error: Exception | None = None
            for attempt in range(2):
                try:
                    async with semaphore:
                        found = await tool.search(
                            item,
                            max_results=5,
                            include_domains=include_domains,
                            provider_override=provider,
                            api_key_override=api_key,
                            url_override=url,
                        )
                    if found:
                        return found
                    last_error = ResearchToolError("search returned no usable sources")
                except Exception as exc:  # converted into a bounded report below
                    last_error = exc
                if attempt == 0:
                    await asyncio.sleep(0.35)
            return last_error or ResearchToolError("search returned no usable sources")

        results = await asyncio.gather(*(search_one(item) for item in queries))
        hits: list[SearchHit] = []
        providers: list[str] = []
        errors: list[str] = []
        for result in results:
            if isinstance(result, Exception):
                errors.append(type(result).__name__)
                continue
            hits.extend(result)
            providers.extend(item.provider for item in result)
        return hits, providers, errors

    @staticmethod
    def _select_sources(hits: list[SearchHit], limit: int, excluded: list[str], focus: str) -> list[SearchHit]:
        excluded_hosts = {str(item).strip().lower().lstrip(".") for item in excluded if str(item).strip()}
        unique: dict[str, SearchHit] = {}
        for hit in hits:
            canonical = canonical_source_url(hit.url)
            host = (urlparse(canonical).hostname or "").lower()
            if not canonical or any(host == item or host.endswith(f".{item}") for item in excluded_hosts):
                continue
            unique.setdefault(canonical, hit)
        terms = {
            item.lower()
            for item in re.findall(r"[a-z0-9][a-z0-9-]{2,}", str(focus or "").lower())
        }
        ranked = sorted(
            unique.values(),
            key=lambda hit: (
                1 if hit.quality == "primary" else 0,
                len(terms & set(re.findall(r"[a-z0-9][a-z0-9-]{2,}", hit.snippet.lower()))) if terms else 0,
                min(len(hit.snippet), 1200),
            ),
            reverse=True,
        )
        selected: list[SearchHit] = []
        hosts: set[str] = set()
        for hit in ranked:
            host = (urlparse(hit.url).hostname or "").lower()
            if host in hosts:
                continue
            selected.append(hit)
            hosts.add(host)
            if len(selected) >= limit:
                return selected
        for hit in ranked:
            if hit not in selected:
                selected.append(hit)
            if len(selected) >= limit:
                break
        return selected

    @staticmethod
    async def _fetch_one(client: httpx.AsyncClient, hit: SearchHit) -> RetrievedSource:
        return await fetch_public_source(client, hit.url)


class ResearchToolRegistry:
    """The small registry boundary used by the research executor."""

    def __init__(self, http_client: httpx.AsyncClient | None = None):
        self.web_search = WebSearchTool(http_client)
        self.fetch_url = FetchUrlTool(http_client)
