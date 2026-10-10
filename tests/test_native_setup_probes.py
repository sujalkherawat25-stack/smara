from __future__ import annotations

import json

import httpx
import pytest

from smara import native_management, native_profiles
from smara import native_search
from smara.native_provider import ChatEndpoint, probe_endpoint


def test_model_probe_sends_one_fixed_tiny_request_and_redacts_provider_errors():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    endpoint = ChatEndpoint("https://model.example/v1", "synthetic-model", "synthetic-secret")
    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as client:
        result = probe_endpoint(endpoint, client=client)
    assert result["ok"] is True
    assert result["model"] == "synthetic-model"
    assert "synthetic-secret" not in json.dumps(result)
    assert len(requests) == 1
    assert requests[0].headers["authorization"] == "Bearer synthetic-secret"
    payload = json.loads(requests[0].content)
    assert payload["messages"] == [{"role": "user", "content": "Reply with OK."}]
    assert payload["max_tokens"] == 1 and payload["stream"] is False

    def unauthorized(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="synthetic-secret rejected")

    with httpx.Client(transport=httpx.MockTransport(unauthorized), trust_env=False) as client:
        with pytest.raises(ValueError, match="rejected the saved credential") as error:
            probe_endpoint(endpoint, client=client)
    assert "synthetic-secret" not in str(error.value)


@pytest.fixture
def native_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARA_DESKTOP_STATE", str(tmp_path / "desktop.json"))
    monkeypatch.setenv("SMARA_DESKTOP_CREDENTIALS", str(tmp_path / "credentials.json"))
    monkeypatch.delenv("SMARA_SEARCH_PROVIDER", raising=False)
    monkeypatch.delenv("SMARA_SEARCH_URL", raising=False)
    monkeypatch.setattr(native_management, "protect", lambda value: "protected:" + value)
    monkeypatch.setattr(native_profiles, "unprotect", lambda value: value.removeprefix("protected:"))
    return tmp_path


def test_settings_model_probe_uses_only_saved_credential(native_settings, monkeypatch):
    native_management.manage({"operation": "save_model", "profile": {
        "id": "fixture", "label": "Fixture", "provider": "fixture", "model": "fixture-model",
        "base_url": "https://model.example/v1", "api_key": "synthetic-saved-key",
    }})
    monkeypatch.setenv("SMARA_MODEL_FIXTURE_API_KEY", "synthetic-environment-key")
    observed = {}

    def probe(endpoint):
        observed["endpoint"] = endpoint
        return {"ok": True, "model": endpoint.model, "elapsed_ms": 1}

    monkeypatch.setattr(native_management, "probe_endpoint", probe)
    result = native_management.manage({"operation": "test_model", "id": "fixture"})
    assert observed["endpoint"].api_key == "synthetic-saved-key"
    assert result["ok"] is True
    assert "synthetic-saved-key" not in json.dumps(result)


def test_search_probe_sends_only_a_fixed_single_result_query(native_settings, monkeypatch):
    monkeypatch.setattr(native_management, "search_status", lambda **_: {"configured": True, "provider": "tavily"})
    calls = []

    def search(query, *, count):
        calls.append((query, count))
        return {"provider": "tavily", "result_count": 1}

    monkeypatch.setattr(native_management, "probe_search", search)
    result = native_management.manage({"operation": "test_search"})
    assert calls == [("Smara web search connectivity test", 1)]
    assert result == {
        "kind": "search", "ok": True, "provider": "tavily",
        "query": "Smara web search connectivity test", "result_count": 1,
    }


def test_search_probe_does_not_call_provider_without_saved_key(native_settings, monkeypatch):
    monkeypatch.setattr(native_management, "search_status", lambda **_: {"configured": False})
    monkeypatch.setattr(native_management, "probe_search", lambda *args, **kwargs: pytest.fail("must not send request"))
    with pytest.raises(ValueError, match="No readable search key"):
        native_management.manage({"operation": "test_search"})


def test_search_probe_uses_protected_provider_and_only_returns_result_count(monkeypatch):
    requests = []

    def configuration(*, allow_environment):
        assert allow_environment is False
        return "tavily", "https://api.tavily.com/search", "TAVILY_API_KEY", "synthetic-search-key"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"results": [{"url": "https://example.test", "content": "private-looking snippet"}]})

    monkeypatch.setattr(native_search, "search_configuration", configuration)
    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as client:
        result = native_search.probe_search("Smara web search connectivity test", count=1, client=client)
    assert result == {"provider": "tavily", "result_count": 1}
    assert len(requests) == 1
    assert requests[0].url.host == "api.tavily.com"
    assert requests[0].headers["authorization"] == "Bearer synthetic-search-key"
    payload = json.loads(requests[0].content)
    assert payload["query"] == "Smara web search connectivity test"
    assert payload["max_results"] == 1
    assert "private-looking snippet" not in json.dumps(result)
