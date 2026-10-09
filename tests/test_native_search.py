import asyncio
import json
import httpx
import pytest
from smara import native_search
from smara.native_tools import NativeTools, serve


@pytest.fixture(autouse=True)
def isolated_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARA_DESKTOP_STATE", str(tmp_path / "desktop.json"))
    monkeypatch.delenv("SMARA_SEARCH_PROVIDER", raising=False)
    monkeypatch.delenv("SMARA_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("SMARA_SEARCH_URL", raising=False)
    monkeypatch.setattr(native_search, "resolve_credential", lambda alias: "")


def test_selected_provider_never_uses_another_providers_key(monkeypatch):
    monkeypatch.setenv("SMARA_SEARCH_PROVIDER", "brave")
    monkeypatch.setattr(native_search, "resolve_credential", lambda alias: "synthetic-exa" if alias == "EXA_API_KEY" else "")
    assert native_search.search_configuration() == ("brave", native_search.PROVIDERS["brave"][0], "BRAVE_SEARCH_API_KEY", "")
    with pytest.raises(native_search.SearchError, match="No fallback"):
        asyncio.run(native_search.web_search("synthetic query"))


@pytest.mark.parametrize("provider,status,message", [("tavily", 401, "authentication"), ("brave", 429, "quota/rate"), ("exa", 302, "not forwarded"), ("serper", 500, "HTTP error")])
def test_provider_errors_are_actionable_redacted_and_no_retries(monkeypatch, provider, status, message):
    monkeypatch.setenv("SMARA_SEARCH_PROVIDER", provider)
    monkeypatch.setattr(native_search, "resolve_credential", lambda alias: "synthetic-sensitive")
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(status, text="synthetic-sensitive raw provider response", headers={"location": "https://wrong.example"})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(native_search.SearchError, match=message) as error:
        asyncio.run(native_search.web_search("synthetic", client=client))
    assert "synthetic-sensitive" not in str(error.value)
    assert len(requests) == 1 and str(requests[0].url).startswith(native_search.PROVIDERS[provider][0])
    asyncio.run(client.aclose())


@pytest.mark.parametrize("provider,payload", [("tavily", {"results": [{"url": "https://docs.python.org/3/", "title": "Python", "content": "docs"}]}), ("brave", {"web": {"results": [{"url": "https://docs.python.org/3/", "title": "Python", "description": "docs"}]}}), ("serper", {"organic": [{"link": "https://docs.python.org/3/", "title": "Python", "snippet": "docs"}]}), ("exa", {"results": [{"url": "https://docs.python.org/3/", "title": "Python", "highlights": ["docs"]}]})])
def test_real_native_tool_dispatch_uses_correct_adapter_shape(tmp_path, monkeypatch, provider, payload):
    monkeypatch.setenv("SMARA_SEARCH_PROVIDER", provider)
    monkeypatch.setattr(native_search, "resolve_credential", lambda alias: "synthetic-sensitive")
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))
    result = asyncio.run(NativeTools(tmp_path, client).call("web_search", {"query": "Python"}))
    assert result["provider"] == provider and result["discovery_only"]
    assert result["results"][0]["snippet"] == "docs"
    asyncio.run(client.aclose())


def test_endpoint_mismatch_is_rejected_before_any_network(monkeypatch):
    monkeypatch.setenv("SMARA_SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("SMARA_SEARCH_URL", "https://wrong.example/search")
    with pytest.raises(native_search.SearchError, match="never sent"):
        asyncio.run(native_search.web_search("synthetic query"))


def test_mcp_reports_missing_key_not_opaque_exception(tmp_path):
    import io
    requests = [{"jsonrpc": "2.0", "id": 1, "method": "initialize"}, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "web_search", "arguments": {"query": "synthetic"}}}]
    out = io.StringIO()
    serve(tmp_path, io.StringIO("\n".join(json.dumps(r) for r in requests) + "\n"), out)
    result = json.loads(out.getvalue().splitlines()[-1])["result"]
    assert result["isError"] and "Settings" in result["content"][0]["text"]
