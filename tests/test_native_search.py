import asyncio
import json
import httpx
import pytest
from smara import native_search
from smara.native_tools import NativeTools, serve


@pytest.fixture(autouse=True)
def isolated_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARA_DESKTOP_STATE", str(tmp_path / "desktop.json"))
    monkeypatch.delenv("SMARA_DESKTOP_CREDENTIALS", raising=False)
    monkeypatch.delenv("SMARA_SEARCH_PROVIDER", raising=False)
    monkeypatch.delenv("SMARA_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("SMARA_SEARCH_URL", raising=False)


def test_selected_provider_never_uses_another_providers_key(monkeypatch):
    monkeypatch.setenv("SMARA_SEARCH_PROVIDER", "brave")
    monkeypatch.setattr(native_search, "resolve_credential", lambda alias, **kwargs: "synthetic-exa" if alias == "EXA_API_KEY" else "")
    assert native_search.search_configuration() == ("brave", native_search.PROVIDERS["brave"][0], "BRAVE_SEARCH_API_KEY", "")
    with pytest.raises(native_search.SearchError, match="No fallback"):
        asyncio.run(native_search.web_search("synthetic query"))


@pytest.mark.parametrize("provider,status,message", [("tavily", 401, "authentication"), ("brave", 429, "quota/rate"), ("exa", 302, "not forwarded"), ("serper", 500, "HTTP error")])
def test_provider_errors_are_actionable_redacted_and_no_retries(monkeypatch, provider, status, message):
    monkeypatch.setenv("SMARA_SEARCH_PROVIDER", provider)
    monkeypatch.setattr(native_search, "resolve_credential", lambda alias, **kwargs: "synthetic-sensitive")
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
    monkeypatch.setattr(native_search, "resolve_credential", lambda alias, **kwargs: "synthetic-sensitive")
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


def test_missing_key_directs_to_protected_settings_not_chat(monkeypatch):
    monkeypatch.setenv("SMARA_SEARCH_PROVIDER", "tavily")
    with pytest.raises(native_search.SearchError, match="never paste API keys into chat") as error:
        asyncio.run(native_search.web_search("synthetic query"))
    assert "Settings → Integrations → Web search" in str(error.value)


def test_tavily_uses_documented_bearer_header_without_putting_key_in_body(monkeypatch):
    monkeypatch.setenv("SMARA_SEARCH_PROVIDER", "tavily")
    monkeypatch.setattr(native_search, "resolve_credential", lambda alias, **kwargs: "synthetic-tavily-key")
    requests = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: (requests.append(request) or httpx.Response(200, json={"results": []}))))
    try:
        asyncio.run(native_search.web_search("synthetic query", client=client))
        assert len(requests) == 1
        assert requests[0].headers["Authorization"] == "Bearer synthetic-tavily-key"
        assert "synthetic-tavily-key" not in requests[0].content.decode("utf-8")
        assert "api_key" not in json.loads(requests[0].content)
    finally:
        asyncio.run(client.aclose())


def test_saved_search_provider_and_key_win_over_ambient_environment(tmp_path, monkeypatch):
    state = tmp_path / "desktop.json"
    (tmp_path / "native-settings.json").write_text(json.dumps({"search_provider": "tavily"}), encoding="utf-8")
    (tmp_path / "credentials.json").write_text(json.dumps({"TAVILY_API_KEY": {"protected": "synthetic-saved-key"}}), encoding="utf-8")
    monkeypatch.setenv("SMARA_DESKTOP_STATE", str(state))
    monkeypatch.setenv("SMARA_DESKTOP_CREDENTIALS", str(tmp_path / "credentials.json"))
    monkeypatch.setenv("SMARA_SEARCH_PROVIDER", "brave")
    monkeypatch.setenv("TAVILY_API_KEY", "synthetic-ambient-key")
    monkeypatch.setattr("smara.native_profiles.unprotect", lambda value: value)
    assert native_search.search_configuration() == (
        "tavily", native_search.PROVIDERS["tavily"][0], "TAVILY_API_KEY", "synthetic-saved-key")
    assert native_search.search_status(protected_only=True)["configured"] is True


def test_ambient_only_key_does_not_report_desktop_runtime_ready(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "synthetic-ambient-key")
    monkeypatch.setenv("SMARA_SEARCH_PROVIDER", "tavily")
    assert native_search.search_status(protected_only=True)["configured"] is False


def test_generic_search_environment_key_is_supported_only_for_explicit_reader(monkeypatch):
    monkeypatch.setenv("SMARA_SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("SMARA_SEARCH_API_KEY", "synthetic-generic-key")
    assert native_search.search_configuration()[-1] == "synthetic-generic-key"
    assert native_search.search_status(protected_only=True)["configured"] is False


def test_mcp_reports_missing_key_not_opaque_exception(tmp_path):
    import io
    requests = [{"jsonrpc": "2.0", "id": 1, "method": "initialize"}, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "web_search", "arguments": {"query": "synthetic"}}}]
    out = io.StringIO()
    serve(tmp_path, io.StringIO("\n".join(json.dumps(r) for r in requests) + "\n"), out)
    result = json.loads(out.getvalue().splitlines()[-1])["result"]
    assert result["isError"] and "Settings" in result["content"][0]["text"]
