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


def test_escaped_dotenv_json_profiles_are_accepted():
    profiles = load_profiles(
        r'{\"grok\":{\"base_url\":\"https://api.x.ai/v1\",\"model\":\"grok-test\",\"api_key\":\"secret\"}}',
        fallback_base_url="",
        fallback_key="",
        fallback_model="",
    )
    assert profiles["grok"].base_url == "https://api.x.ai/v1"
    assert profiles["grok"].model == "grok-test"
