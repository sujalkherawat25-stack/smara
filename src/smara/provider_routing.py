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
    # Most providers use Authorization: Bearer.  Sarvam also supports its
    # native api-subscription-key header, which avoids an unnecessary auth
    # translation at the hosted boundary.
    auth_header: str = "Authorization"
    capability: str = "chat"


def _auth_header(value: object) -> str:
    if value is None or value == "":
        return "Authorization"
    if not isinstance(value, str):
        raise ValueError("Model profile auth_header must be a string.")
    normalized = value.strip().lower()
    if normalized in {"authorization", "bearer", "authorization: bearer"}:
        return "Authorization"
    if normalized in {"api-subscription-key", "api_subscription_key"}:
        return "api-subscription-key"
    raise ValueError("Model profile auth_header must be Authorization or api-subscription-key.")


def _capability(value: object) -> str:
    if value is None or value == "":
        return "chat"
    if not isinstance(value, str) or value.strip().lower() not in {"chat", "reasoning", "vision"}:
        raise ValueError("Model profile capability must be chat, reasoning, or vision.")
    return value.strip().lower()


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
            # Some dotenv/Compose setups preserve escaped quotes when a JSON
            # object is supplied as an environment value (for example,
            # {\"grok\":{\"model\":\"...\"}}).  Treat that representation
            # as the same profile document rather than making hosted chat
            # fail while health and task endpoints continue to work.  We only
            # remove the quote escape sequence; all other input validation
            # remains unchanged below.
            if r'\"' in raw:
                try:
                    data = json.loads(raw.replace(r'\"', '"'))
                except json.JSONDecodeError:
                    raise ValueError("SMARA_LLM_PROFILES must be valid JSON.") from exc
            elif '"' not in raw and "\\" in raw:
                # Docker Compose also accepts a double-quoted dotenv value
                # such as {\"grok\":...}; in that form it can remove the
                # quote characters but leave their escape markers behind,
                # producing a document where every JSON quote is `\\`.  The
                # profile schema does not allow backslashes in names, URLs, or
                # environment-variable names, so restoring those markers is
                # unambiguous and keeps the deployment backward-compatible.
                try:
                    data = json.loads(raw.replace("\\", '"'))
                except json.JSONDecodeError:
                    raise ValueError("SMARA_LLM_PROFILES must be valid JSON.") from exc
            else:
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
            # A profile may intentionally share the legacy provider secret.
            # This is important during the staged migration: older deployments
            # have SMARA_LLM_API_KEY populated while a newer profile document
            # names a not-yet-created provider-specific variable.  Only inherit
            # when the endpoint is exactly the configured fallback endpoint;
            # never copy a key across providers or arbitrary URLs.
            profile_base_url = _valid_base_url(value.get("base_url"))
            if not key.strip() and fallback_key.strip() and profile_base_url.lower() == fallback_base_url.strip().rstrip("/").lower():
                key = fallback_key
            model = value.get("model")
            if not isinstance(model, str) or not model.strip():
                raise ValueError(f"Model profile '{name}' is missing model.")
            profiles[name] = ModelProfile(
                name,
                profile_base_url,
                model.strip(),
                key,
                _auth_header(value.get("auth_header")),
                _capability(value.get("capability")),
            )
    if fallback_base_url and fallback_model:
        profiles.setdefault(
            fallback_provider or "default",
            ModelProfile(fallback_provider or "default", fallback_base_url.rstrip("/"), fallback_model, fallback_key),
        )
    return profiles


def resolve_profile(*, raw: str, requested: str | None, fallback_base_url: str, fallback_key: str, fallback_model: str, fallback_provider: str = "configured") -> ModelProfile:
    profiles = load_profiles(raw, fallback_base_url=fallback_base_url, fallback_key=fallback_key, fallback_model=fallback_model, fallback_provider=fallback_provider)
    name = requested or fallback_provider or "default"
    profile = profiles.get(name)
    if profile is None and requested is None and len(profiles) == 1:
        profile = next(iter(profiles.values()))
    if profile is None and requested is None and "default" in profiles:
        profile = profiles["default"]
    if profile is None:
        raise ValueError(f"Unknown model profile '{name}'. Choose one configured by the operator.")
    return profile
