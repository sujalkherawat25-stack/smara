"""Bounded public search transport for native tools, with no agent/planner imports."""
from __future__ import annotations
import os
from urllib.parse import urlsplit
import httpx
from .native_profiles import read_object, resolve_credential, state_path

PROVIDERS = {
    "tavily": ("https://api.tavily.com/search", "TAVILY_API_KEY"),
    "exa": ("https://api.exa.ai/search", "EXA_API_KEY"),
    "serper": ("https://google.serper.dev/search", "SERPER_API_KEY"),
    "brave": ("https://api.search.brave.com/res/v1/web/search", "BRAVE_SEARCH_API_KEY"),
}


class SearchError(RuntimeError):
    """Only constant, credential-free diagnostic messages may cross MCP."""


def search_configuration():
    saved = read_object(state_path().parent / "native-settings.json")
    selected = str(os.getenv("SMARA_SEARCH_PROVIDER") or saved.get("search_provider") or "").lower()
    if selected:
        if selected not in PROVIDERS:
            raise SearchError("Unsupported search provider. Select Tavily, Exa, Serper or Brave in Settings → Tools.")
        key = os.getenv("SMARA_SEARCH_API_KEY") or resolve_credential(PROVIDERS[selected][1])
    else:
        selected, key = next(((name, key) for name, (_, alias) in PROVIDERS.items()
                              if (key := resolve_credential(alias))), ("tavily", ""))
    endpoint, alias = PROVIDERS[selected]
    custom = os.getenv("SMARA_SEARCH_URL")
    if custom and custom.rstrip("/") != endpoint.rstrip("/"):
        raise SearchError("Search endpoint does not match the selected provider. Remove SMARA_SEARCH_URL; provider keys are never sent to another host.")
    return selected, endpoint, alias, key


def search_status():
    """Local credential readability, not a claim of network/provider health."""
    try:
        provider, endpoint, alias, key = search_configuration()
        return {"provider": provider, "endpoint": endpoint, "credential_alias": alias,
                "configured": bool(key), "network_tested": False,
                "detail": "Credential readable; not network tested" if key else "Save a search API key in Settings → Tools"}
    except (SearchError, RuntimeError):
        return {"provider": "", "configured": False, "network_tested": False,
                "detail": "Search configuration needs attention in Settings → Tools; check provider, endpoint and credential vault"}


async def web_search(query: str, count: int = 5, client=None):
    if not isinstance(query, str) or not query.strip() or len(query) > 500 or len(query.split()) > 75:
        raise SearchError("Search query must be nonempty, at most 500 characters and 75 words. Split a long research question into focused queries.")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 8:
        raise SearchError("Search result count must be 1..8.")
    provider, endpoint, _, key = search_configuration()
    if not key:
        raise SearchError("The selected search provider has no readable API key. Save its key in Settings → Tools, then reconnect. No fallback provider was used.")
    owns = client is None
    client = client or httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False)
    try:
        if provider == "brave":
            response = await client.get(endpoint, params={"q": query, "count": count}, headers={"X-Subscription-Token": key})
        elif provider == "serper":
            response = await client.post(endpoint, json={"q": query, "num": count}, headers={"X-API-KEY": key})
        elif provider == "tavily":
            response = await client.post(endpoint, json={"query": query, "max_results": count, "search_depth": "basic",
                "include_answer": False, "include_raw_content": False}, headers={"Authorization": f"Bearer {key}"})
        else:
            response = await client.post(endpoint, json={"query": query, "numResults": count, "type": "auto",
                "contents": {"highlights": {"maxCharacters": 1200}}}, headers={"x-api-key": key})
        if response.status_code in (401, 403):
            raise SearchError("Search provider rejected authentication/access. Check its API key and plan in Settings → Tools.")
        if response.status_code in (402, 429, 432, 433):
            raise SearchError("Search provider quota/rate limit reached. Check its plan or try later; no automatic retries or provider substitutions.")
        if response.is_redirect:
            raise SearchError("Search provider returned a redirect; credentials were not forwarded. Check provider configuration.")
        response.raise_for_status()
        if len(response.content) > 1_000_000:
            raise SearchError("Search response exceeded the retrieval safety limit.")
        payload = response.json()
        entries = payload.get("organic") if provider == "serper" else (payload.get("web") or {}).get("results") if provider == "brave" else payload.get("results")
        if not isinstance(entries, list):
            raise SearchError("Search provider returned an unexpected response format; no results were invented.")
        output, seen = [], set()
        for item in entries:
            if not isinstance(item, dict):
                continue
            url = str(item.get("link") if provider == "serper" else item.get("url") or "")
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or url in seen:
                continue
            seen.add(url)
            snippet = item.get("snippet") if provider == "serper" else item.get("description") if provider == "brave" else item.get("content") if provider == "tavily" else " ".join(str(s) for s in item.get("highlights") or [])
            output.append({"url": url, "title": str(item.get("title") or parsed.hostname)[:500],
                           "snippet": str(snippet or "")[:1200], "provider": provider})
            if len(output) >= count:
                break
        return {"provider": provider, "results": output, "discovery_only": True}
    except SearchError:
        raise
    except httpx.TimeoutException:
        raise SearchError("Search provider timed out. Try a shorter query or try later; no result was fabricated.") from None
    except httpx.HTTPError:
        raise SearchError("Search provider is unreachable or returned an HTTP error. Check connectivity and Settings → Tools.") from None
    except (ValueError, TypeError, AttributeError):
        raise SearchError("Search provider returned an invalid response; no results were invented.") from None
    finally:
        if owns:
            await client.aclose()


async def academic_search(query: str, client=None):
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        raise SearchError("Academic search needs a nonempty query up to 500 characters.")
    owns = client is None
    client = client or httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False)
    try:
        response = await client.get("https://api.openalex.org/works", params={"search": query, "per-page": 5,
            "select": "id,doi,title,publication_year,primary_location,open_access"})
        response.raise_for_status()
        if len(response.content) > 1_000_000:
            raise SearchError("Academic response exceeded retrieval safety limit.")
        items = response.json().get("results")
        if not isinstance(items, list):
            raise SearchError("Academic provider returned an unexpected response format.")
        return {"provider": "openalex", "results": [{"id": str(item.get("id") or ""), "doi": str(item.get("doi") or ""),
            "title": str(item.get("title") or "")[:500], "year": item.get("publication_year"),
            "url": str((item.get("primary_location") or {}).get("landing_page_url") or item.get("doi") or item.get("id") or "")[:2000],
            "open_access": bool((item.get("open_access") or {}).get("is_oa"))} for item in items[:5] if isinstance(item, dict)], "discovery_only": True}
    except SearchError:
        raise
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        raise SearchError("Academic discovery is unavailable. Try public web search or another source; no records were fabricated.") from None
    finally:
        if owns:
            await client.aclose()
