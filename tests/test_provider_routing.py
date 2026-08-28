import os

import pytest

from smara.provider_routing import load_profiles, resolve_profile


def test_profiles_reference_secret_environment_without_exposing_it(monkeypatch):
    monkeypatch.setenv("TEST_XAI_KEY", "secret-value")
    profiles = load_profiles('{"cheap":{"base_url":"https://api.example/v1","model":"small","api_key_env":"TEST_XAI_KEY"}}', fallback_base_url="", fallback_key="", fallback_model="")
    assert profiles["cheap"].api_key == "secret-value"
    assert "secret-value" not in repr(profiles["cheap"].name)


def test_unknown_profile_is_rejected():
    with pytest.raises(ValueError, match="Unknown model profile"):
        resolve_profile(raw='{"cheap":{"base_url":"https://api.example/v1","model":"small","api_key":"x"}}', requested="missing", fallback_base_url="", fallback_key="", fallback_model="")


def test_legacy_single_provider_remains_valid():
    profile = resolve_profile(raw="", requested=None, fallback_base_url="https://api.example/v1", fallback_key="key", fallback_model="small", fallback_provider="xai")
    assert (profile.base_url, profile.model, profile.api_key) == ("https://api.example/v1", "small", "key")


def test_profile_inherits_legacy_key_only_for_same_endpoint(monkeypatch):
    monkeypatch.delenv("MISSING_XAI_KEY", raising=False)
    profiles = load_profiles(
        '{"grok":{"base_url":"https://api.x.ai/v1","model":"grok-4.6","api_key_env":"MISSING_XAI_KEY"}}',
        fallback_base_url="https://api.x.ai/v1",
        fallback_key="legacy-xai-key",
        fallback_model="grok-4.3",
        fallback_provider="xAI",
    )
    assert profiles["grok"].api_key == "legacy-xai-key"


def test_profile_does_not_inherit_key_for_different_endpoint():
    profiles = load_profiles(
        '{"sarvam":{"base_url":"https://api.sarvam.ai/v1","model":"sarvam-105b","api_key_env":"MISSING_SARVAM_KEY"}}',
        fallback_base_url="https://api.x.ai/v1",
        fallback_key="legacy-xai-key",
        fallback_model="grok-4.3",
        fallback_provider="xAI",
    )
    assert profiles["sarvam"].api_key == ""


def test_escaped_dotenv_json_profiles_are_accepted():
    profiles = load_profiles(
        r'{\"grok\":{\"base_url\":\"https://api.x.ai/v1\",\"model\":\"grok-test\",\"api_key\":\"secret\"}}',
        fallback_base_url="",
        fallback_key="",
        fallback_model="",
    )
    assert profiles["grok"].base_url == "https://api.x.ai/v1"
    assert profiles["grok"].model == "grok-test"


def test_compose_quote_markers_without_quotes_are_accepted():
    profiles = load_profiles(
        r'{\grok\:{\base_url\:\https://api.x.ai/v1\,\model\:\grok-test\,\api_key_env\:\XAI_API_KEY\}}',
        fallback_base_url="",
        fallback_key="",
        fallback_model="",
    )
    assert profiles["grok"].base_url == "https://api.x.ai/v1"
    assert profiles["grok"].model == "grok-test"


def test_sarvam_profile_preserves_native_auth_and_capability(monkeypatch):
    monkeypatch.setenv("SARVAM_TEST_KEY", "sarvam-secret")
    profiles = load_profiles(
        '{"reasoning":{"base_url":"https://api.sarvam.ai/v2","model":"glm5.2","api_key_env":"SARVAM_TEST_KEY","auth_header":"api-subscription-key","capability":"reasoning"}}',
        fallback_base_url="",
        fallback_key="",
        fallback_model="",
    )
    profile = profiles["reasoning"]
    assert profile.api_key == "sarvam-secret"
    assert profile.auth_header == "api-subscription-key"
    assert profile.capability == "reasoning"


def test_unknown_profile_auth_header_is_rejected():
    with pytest.raises(ValueError, match="auth_header"):
        load_profiles(
            '{"bad":{"base_url":"https://api.example/v1","model":"small","auth_header":"X-Secret"}}',
            fallback_base_url="",
            fallback_key="",
            fallback_model="",
        )
