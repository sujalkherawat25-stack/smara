"""Small, allowlisted model-provider router.

Profiles are operator configuration, not user-supplied endpoints.  The JSON
value may reference an environment variable for each secret, so API keys never
enter task payloads, browser storage, or model-selection responses.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class ModelProfile:
    name: str
    base_url: str
    model: str
    api_key: str


def _valid_base_url(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Model profile base_url is required.")
    url = value.strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise ValueError("Model profile base_url must be an HTTP(S) URL.")
    return url


def load_profiles(raw: str, *, fallback_base_url: str, fallback_key: str, fallback_model: str, fallback_provider: str = "configured") -> dict[str, ModelProfile]:
    profiles: dict[str, ModelProfile] = {}
    if raw.strip():
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("SMARA_LLM_PROFILES must be valid JSON.") from exc
        if not isinstance(data, dict) or len(data) > 12:
            raise ValueError("SMARA_LLM_PROFILES must be an object with at most 12 profiles.")
        for name, value in data.items():
            if not isinstance(name, str) or not name or len(name) > 64 or not isinstance(value, dict):
                raise ValueError("Each model profile needs a short name and object value.")
            key_env = value.get("api_key_env")
            key = os.getenv(key_env, "") if isinstance(key_env, str) else value.get("api_key", "")
            if not isinstance(key, str):
                key = ""
            model = value.get("model")
            if not isinstance(model, str) or not model.strip():
                raise ValueError(f"Model profile '{name}' is missing model.")
            profiles[name] = ModelProfile(name, _valid_base_url(value.get("base_url")), model.strip(), key)
    if fallback_base_url and fallback_model:
        profiles.setdefault(fallback_provider or "default", ModelProfile(fallback_provider or "default", fallback_base_url.rstrip("/"), fallback_model, fallback_key))
    return profiles


def resolve_profile(*, raw: str, requested: str | None, fallback_base_url: str, fallback_key: str, fallback_model: str, fallback_provider: str = "configured") -> ModelProfile:
    profiles = load_profiles(raw, fallback_base_url=fallback_base_url, fallback_key=fallback_key, fallback_model=fallback_model, fallback_provider=fallback_provider)
    name = requested or fallback_provider or "default"
    profile = profiles.get(name)
    if profile is None:
        raise ValueError(f"Unknown model profile '{name}'. Choose one configured by the operator.")
    return profile
