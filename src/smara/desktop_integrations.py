"""Privacy-first adapters for personal tools on the paired desktop.

These adapters are intentionally small and read-oriented.  The hosted task
contains only the provider, operation, and bounded arguments; the credential
is resolved from the desktop's encrypted vault and is sent directly to the
provider over HTTPS.  No credential or raw provider response is returned to
Smara's hosted control plane.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

import httpx


MAX_LOCAL_INTEGRATION_RESULTS = 20
MAX_LOCAL_INTEGRATION_TEXT = 2_000
MAX_LOCAL_INTEGRATION_OUTPUT = 32_000
@dataclass(frozen=True)
class LocalConnectorSpec:
    """Public, secret-free contract for one installed desktop connector."""

    provider: str
    operation: str
    credential_alias: str
    auth_mode: str
    risk: str
    scopes: tuple[str, ...]
    max_results: int
    max_requests_per_run: int = 1


LOCAL_CONNECTORS: dict[str, LocalConnectorSpec] = {
    "tavily": LocalConnectorSpec(
        "tavily", "search", "TAVILY_API_KEY", "local_api_key", "read_only",
        ("web.search",), 5,
    ),
    "exa": LocalConnectorSpec(
        "exa", "search", "EXA_API_KEY", "local_api_key", "read_only",
        ("web.search",), 5,
    ),
    "github": LocalConnectorSpec(
        "github", "list_repositories", "GITHUB_TOKEN", "local_api_key", "read_only",
        ("repositories:read",), MAX_LOCAL_INTEGRATION_RESULTS,
    ),
}


class LocalIntegrationCancelled(RuntimeError):
    """The hosted lease was cancelled while a local adapter was running."""


def _bounded_text(value: object, limit: int = MAX_LOCAL_INTEGRATION_TEXT) -> str:
    return " ".join(str(value or "").split())[:limit]


def local_connector_catalog(configured_aliases: set[str] | None = None) -> list[dict[str, object]]:
    """Return installed connector metadata without a credential value.

    ``configured_aliases`` comes from the desktop vault caller.  Keeping the
    vault outside this module makes the public contract usable without ever
    passing a secret into it.
    """
    configured_aliases = configured_aliases or set()
    return [
        {
            **asdict(spec),
            "scopes": list(spec.scopes),
            "credential_configured": spec.credential_alias in configured_aliases,
        }
        for spec in LOCAL_CONNECTORS.values()
    ]


def _connector_metadata(spec: LocalConnectorSpec) -> dict[str, object]:
    return {
        "provider": spec.provider,
        "operation": spec.operation,
        "auth_mode": spec.auth_mode,
        "risk": spec.risk,
        "scopes": list(spec.scopes),
        "max_results": spec.max_results,
        "max_requests_per_run": spec.max_requests_per_run,
    }


def _provider_error(provider: str, response: httpx.Response) -> RuntimeError:
    if response.status_code in {401, 403}:
        return RuntimeError(f"{provider.title()} rejected its local credential. Update it in Desktop Settings.")
    if response.status_code == 429:
        return RuntimeError(f"{provider.title()} rate limit reached. Try again later.")
    return RuntimeError(f"{provider.title()} returned HTTP {response.status_code}.")


def _json_response(provider: str, response: httpx.Response) -> Any:
    if not response.is_success:
        raise _provider_error(provider, response)
    try:
        return response.json()
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{provider.title()} returned invalid JSON.") from exc


def _credential(spec: LocalConnectorSpec, payload: dict[str, Any], credentials_resolver) -> tuple[str, str]:
    expected = spec.credential_alias
    supplied = payload.get("credential_env", expected)
    if not isinstance(supplied, str) or supplied.strip().upper() != expected:
        raise RuntimeError(f"{spec.provider.title()} uses the local {expected} credential alias.")
    values = credentials_resolver([expected])
    return expected, values[expected]


def _normalise_local_integration_payload(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Repair harmless planner aliases before dispatching one local read.

    Private model tool calls are not perfectly consistent about naming a web
    search or GitHub repository listing.  Normalize only known aliases and
    clamp their bounded limits; unknown providers/operations remain unchanged
    and are rejected by ``execute_local_integration``.
    """
    if not isinstance(payload, dict):
        raise RuntimeError("Local integration payload must be an object.")
    normalized = dict(payload)
    provider = str(normalized.get("provider") or "").strip().lower()
    operation = str(normalized.get("operation") or "").strip().lower()
    query = normalized.get("query")
    if (not provider or provider in {"search", "research", "web_search", "web"}) and isinstance(query, str) and query.strip():
        provider = "tavily"
    if provider in {"tavily", "exa"} and operation in {"", "research", "web_search", "search_web"}:
        operation = "search"
    if provider == "github" and operation in {"", "repositories", "list_repos", "repos"}:
        operation = "list_repositories"
    normalized["provider"] = provider
    normalized["operation"] = operation
    if provider in {"tavily", "exa"} and operation == "search":
        raw_max = normalized.get("max_results", 5)
        try:
            max_results = int(raw_max)
        except (TypeError, ValueError):
            max_results = 5
        normalized["max_results"] = max(1, min(5, max_results))
        normalized.setdefault("credential_env", LOCAL_CONNECTORS[provider].credential_alias)
    if provider == "github" and operation == "list_repositories":
        raw_limit = normalized.get("limit", 10)
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = 10
        normalized["limit"] = max(1, min(MAX_LOCAL_INTEGRATION_RESULTS, limit))
        normalized.setdefault("credential_env", "GITHUB_TOKEN")
    return provider, normalized


def _tavily_search(client: httpx.Client, payload: dict[str, Any], secret: str, spec: LocalConnectorSpec) -> str:
    query = payload.get("query")
    if not isinstance(query, str) or not query.strip() or len(query.strip()) > 500:
        raise RuntimeError("Tavily search needs a query up to 500 characters.")
    # Accept int, float, or string and clamp to [1, 5]
    raw_max = payload.get("max_results", 5)
    try:
        max_results = max(1, min(5, int(raw_max)))
    except (TypeError, ValueError):
        max_results = 5
    include_domains = payload.get("include_domains") or []
    if not isinstance(include_domains, list):
        include_domains = []
    include_domains = [str(d).strip()[:120] for d in include_domains if isinstance(d, str) and d.strip()][:5]
    response = client.post(
        "https://api.tavily.com/search",
        json={
            "api_key": secret,
            "query": query.strip(),
            "max_results": max_results,
            "search_depth": "basic",
            "include_answer": False,
            "include_raw_content": False,
            "include_domains": include_domains,
        },
    )
    body = _json_response("tavily", response)
    rows = body.get("results", []) if isinstance(body, dict) else []
    if not isinstance(rows, list):
        rows = []
    results: list[dict[str, str]] = []
    citations: list[str] = []
    for item in rows[:max_results]:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        results.append({
            "title": _bounded_text(item.get("title"), 300),
            "url": url[:2_000],
            "snippet": _bounded_text(item.get("content"), 1_000),
        })
        citations.append(url[:2_000])
    proof = hashlib.sha256(json.dumps(results, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return json.dumps({
        "action": "local_integration", "provider": "tavily", "operation": "search",
        "query": query.strip(), "results": results, "citations": citations,
        "connector": _connector_metadata(spec),
        "proof": {"provider": "tavily", "result_sha256": proof, "results": len(results)},
    }, ensure_ascii=False)[:MAX_LOCAL_INTEGRATION_OUTPUT]


def _exa_search(client: httpx.Client, payload: dict[str, Any], secret: str, spec: LocalConnectorSpec) -> str:
    query = payload.get("query")
    if not isinstance(query, str) or not query.strip() or len(query.strip()) > 500:
        raise RuntimeError("Exa search needs a query up to 500 characters.")
    # Accept int, float, or string and clamp to the published connector bound.
    raw_max = payload.get("max_results", 5)
    try:
        max_results = max(1, min(spec.max_results, int(raw_max)))
    except (TypeError, ValueError):
        max_results = 5
    include_domains = payload.get("include_domains") or []
    if not isinstance(include_domains, list):
        include_domains = []
    include_domains = [str(d).strip()[:120] for d in include_domains if isinstance(d, str) and d.strip()][:5]

    body_data: dict[str, Any] = {
        "query": query.strip(),
        "type": "auto",
        "numResults": max_results,
        "contents": {"highlights": {"maxCharacters": 1200}},
    }
    if include_domains:
        body_data["includeDomains"] = [item.strip()[:120] for item in include_domains]

    response = client.post(
        "https://api.exa.ai/search",
        headers={"x-api-key": secret, "Content-Type": "application/json"},
        json=body_data,
    )
    body = _json_response("exa", response)
    rows = body.get("results", []) if isinstance(body, dict) else []
    if not isinstance(rows, list):
        rows = []
    results: list[dict[str, str]] = []
    citations: list[str] = []
    for item in rows[:max_results]:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        highlights = item.get("highlights") or []
        snippet = " ".join(str(v).strip() for v in highlights if str(v).strip()) or str(item.get("text") or "")
        results.append({
            "title": _bounded_text(item.get("title"), 300),
            "url": url[:2_000],
            "snippet": _bounded_text(snippet, 1_000),
        })
        citations.append(url[:2_000])
    proof = hashlib.sha256(json.dumps(results, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return json.dumps({
        "action": "local_integration", "provider": "exa", "operation": "search",
        "query": query.strip(), "results": results, "citations": citations,
        "connector": _connector_metadata(spec),
        "proof": {"provider": "exa", "result_sha256": proof, "results": len(results)},
    }, ensure_ascii=False)[:MAX_LOCAL_INTEGRATION_OUTPUT]


def _github_repositories(client: httpx.Client, payload: dict[str, Any], secret: str, spec: LocalConnectorSpec) -> str:
    limit = payload.get("limit", 10)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LOCAL_INTEGRATION_RESULTS:
        raise RuntimeError(f"GitHub limit must be between 1 and {MAX_LOCAL_INTEGRATION_RESULTS}.")
    response = client.get(
        "https://api.github.com/user/repos",
        headers={
            "Authorization": f"Bearer {secret}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        params={"per_page": limit, "sort": "updated"},
    )
    body = _json_response("github", response)
    rows = body if isinstance(body, list) else []
    repositories: list[dict[str, object]] = []
    for item in rows[:limit]:
        if not isinstance(item, dict):
            continue
        repositories.append({
            "name": _bounded_text(item.get("name"), 200),
            "full_name": _bounded_text(item.get("full_name"), 300),
            "private": bool(item.get("private")),
            "url": str(item.get("html_url") or "")[:2_000],
            "description": _bounded_text(item.get("description"), 500),
            "language": _bounded_text(item.get("language"), 100),
        })
    proof = hashlib.sha256(json.dumps(repositories, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return json.dumps({
        "action": "local_integration", "provider": "github", "operation": "list_repositories",
        "repositories": repositories,
        "connector": _connector_metadata(spec),
        "proof": {"provider": "github", "result_sha256": proof, "repositories": len(repositories)},
    }, ensure_ascii=False)[:MAX_LOCAL_INTEGRATION_OUTPUT]


def execute_local_integration(payload: dict[str, Any], credentials_resolver, *, checkpoint=None, progress_hook=None) -> str:
    """Run one explicitly approved, local-only integration read."""
    provider, payload = _normalise_local_integration_payload(payload)
    if not isinstance(provider, str) or provider.strip().lower() not in LOCAL_CONNECTORS:
        raise RuntimeError("Supported local integrations are Tavily search, Exa search, and GitHub repositories.")
    provider = provider.strip().lower()
    spec = LOCAL_CONNECTORS[provider]
    operation = payload.get("operation")
    if operation != spec.operation:
        raise RuntimeError(f"{provider.title()} supports only {spec.operation} locally.")
    if checkpoint is not None and checkpoint():
        raise LocalIntegrationCancelled("Local integration was cancelled before it started.")
    _emit = progress_hook
    if _emit is not None:
        _emit(f"Starting local {provider} adapter")
    _alias, secret = _credential(spec, payload, credentials_resolver)
    try:
        with httpx.Client(
            timeout=20,
            follow_redirects=False,
            headers={"User-Agent": "SmaraDesktop/0.1 local-integration"},
        ) as client:
            if provider == "tavily":
                result = _tavily_search(client, payload, secret, spec)
            elif provider == "exa":
                result = _exa_search(client, payload, secret, spec)
            else:
                result = _github_repositories(client, payload, secret, spec)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not reach {provider.title()} from this PC.") from exc
    finally:
        # Do not retain the secret in local adapter state longer than the
        # request.  Python cannot guarantee memory zeroing, but this avoids
        # accidental reuse and all returned data is secret-free.
        secret = ""
    if checkpoint is not None and checkpoint():
        raise LocalIntegrationCancelled("Local integration was cancelled before completion.")
    if _emit is not None:
        _emit(f"Local {provider} adapter finished")
    return result
