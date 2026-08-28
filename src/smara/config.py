from __future__ import annotations

import os
from dataclasses import dataclass


def _secret(name: str, default: str = "") -> str:
    """Read an environment secret or a Docker/Kubernetes-style *_FILE value."""
    direct = os.getenv(name)
    if direct:
        return direct
    path = os.getenv(f"{name}_FILE")
    if path:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                value = handle.read().strip()
        except OSError:
            return default
        return value or default
    return default


def _secret_with_fallback(name: str, fallback_name: str = "") -> str:
    """Read a dedicated secret, falling back to one shared provider secret.

    Capture pipelines intentionally reuse the operator's Sarvam key by
    default, while still allowing a deployment to override each capability
    with a separate secret when required.
    """
    value = _secret(name)
    return value or (_secret(fallback_name) if fallback_name else "")


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("SMARA_DATABASE_URL", "")
    database_path: str = os.getenv("SMARA_DATABASE_PATH", "./data/smara.db")
    syntarus_api_key: str = _secret("SYNTARUS_API_KEY")
    syntarus_base_url: str = os.getenv("SYNTARUS_BASE_URL", "https://ai.syntarus.com/v1")
    dev_mode: bool = os.getenv("SMARA_DEV_MODE", "false").lower() == "true"
    gateway_signing_secret: str = _secret("SMARA_GATEWAY_SIGNING_SECRET")
    control_bridge_secret: str = _secret("SMARA_CONTROL_BRIDGE_SECRET")
    allowed_origins: str = os.getenv(
        "SMARA_ALLOWED_ORIGINS",
        "https://ai.syntarus.com",
    )
    integration_master_key: str = _secret("SMARA_INTEGRATION_MASTER_KEY")
    integration_master_keys: str = _secret("SMARA_INTEGRATION_MASTER_KEYS", _secret("SMARA_INTEGRATION_MASTER_KEY"))
    # Personal-account integrations (Gmail, Calendar, Drive, GitHub, Telegram)
    # are disabled on the hosted control plane by default. User credentials
    # and browser sessions belong on the paired local device; the VM should
    # only run operator-owned LLM/public-research services and coordination.
    hosted_user_integrations_enabled: bool = os.getenv("SMARA_HOSTED_USER_INTEGRATIONS_ENABLED", "false").lower() == "true"
    public_base_url: str = os.getenv("SMARA_PUBLIC_BASE_URL", "http://127.0.0.1:8080")
    google_client_id: str = os.getenv("SMARA_GOOGLE_CLIENT_ID", "")
    google_client_secret: str = _secret("SMARA_GOOGLE_CLIENT_SECRET")
    github_client_id: str = os.getenv("SMARA_GITHUB_CLIENT_ID", "")
    github_client_secret: str = _secret("SMARA_GITHUB_CLIENT_SECRET")
    vapid_public_key: str = os.getenv("SMARA_VAPID_PUBLIC_KEY", "")
    vapid_private_key: str = _secret("SMARA_VAPID_PRIVATE_KEY")
    vapid_subject: str = os.getenv("SMARA_VAPID_SUBJECT", "mailto:admin@example.com")
    rate_limit_per_minute: int = int(os.getenv("SMARA_RATE_LIMIT_PER_MINUTE", "120"))
    sentry_dsn: str = _secret("SMARA_SENTRY_DSN")
    redis_url: str = os.getenv("SMARA_REDIS_URL", "")
    llm_base_url: str = os.getenv("SMARA_LLM_BASE_URL", "")
    llm_api_key: str = _secret("SMARA_LLM_API_KEY")
    llm_model: str = os.getenv("SMARA_LLM_MODEL", "")
    llm_provider: str = os.getenv("SMARA_LLM_PROVIDER", "configured model provider")
    llm_profiles: str = os.getenv("SMARA_LLM_PROFILES", "")
    llm_default_profile: str = os.getenv("SMARA_LLM_DEFAULT_PROFILE", "")
    plugin_manifests: str = os.getenv("SMARA_PLUGIN_MANIFESTS", "")
    research_synthesis_enabled: bool = os.getenv("SMARA_RESEARCH_SYNTHESIS_ENABLED", "false").lower() == "true"
    cli_token_secret: str = _secret("SMARA_CLI_TOKEN_SECRET")
    cli_token_ttl_days: int = int(os.getenv("SMARA_CLI_TOKEN_TTL_DAYS", "30"))
    search_provider: str = os.getenv("SMARA_SEARCH_PROVIDER", "brave")
    search_api_key: str = _secret("SMARA_SEARCH_API_KEY")
    search_url: str = os.getenv("SMARA_SEARCH_URL", "")
    # Advanced Tavily retrieval returns stronger page-level leads for research
    # while remaining provider-neutral. Operators may choose "basic" when
    # latency/cost matters more than recall.
    search_depth: str = os.getenv("SMARA_SEARCH_DEPTH", "advanced").lower()
    search_timeout_seconds: float = float(os.getenv("SMARA_SEARCH_TIMEOUT_SECONDS", "12"))
    research_allowed_domains: str = os.getenv("SMARA_RESEARCH_ALLOWED_DOMAINS", "")
    research_blocked_domains: str = os.getenv("SMARA_RESEARCH_BLOCKED_DOMAINS", "")
    capture_transcription_base_url: str = os.getenv("SMARA_CAPTURE_TRANSCRIPTION_BASE_URL", "")
    capture_transcription_api_key: str = _secret_with_fallback("SMARA_CAPTURE_TRANSCRIPTION_API_KEY", "SMARA_SARVAM_KEY")
    capture_transcription_model: str = os.getenv("SMARA_CAPTURE_TRANSCRIPTION_MODEL", "")
    capture_transcription_auth_header: str = os.getenv("SMARA_CAPTURE_TRANSCRIPTION_AUTH_HEADER", "Authorization")
    capture_vision_base_url: str = os.getenv("SMARA_CAPTURE_VISION_BASE_URL", "")
    capture_vision_api_key: str = _secret_with_fallback("SMARA_CAPTURE_VISION_API_KEY", "SMARA_SARVAM_KEY")
    capture_vision_model: str = os.getenv("SMARA_CAPTURE_VISION_MODEL", "")
    capture_vision_auth_header: str = os.getenv("SMARA_CAPTURE_VISION_AUTH_HEADER", "Authorization")
    # Sarvam Document AI is an asynchronous OCR service, not a chat model.
    # Keep it on its own bounded capture path rather than exposing it as a
    # selectable chat profile.
    capture_ocr_base_url: str = os.getenv("SMARA_CAPTURE_OCR_BASE_URL", "")
    capture_ocr_api_key: str = _secret_with_fallback("SMARA_CAPTURE_OCR_API_KEY", "SMARA_SARVAM_KEY")
    capture_ocr_model: str = os.getenv("SMARA_CAPTURE_OCR_MODEL", "sarvam-vision-v1")
    capture_ocr_language: str = os.getenv("SMARA_CAPTURE_OCR_LANGUAGE", "en-IN")
    capture_ocr_auth_header: str = os.getenv("SMARA_CAPTURE_OCR_AUTH_HEADER", "api-subscription-key")
    # Disabled until a separately isolated sandbox service is deployed.
    sandbox_enabled: bool = os.getenv("SMARA_SANDBOX_ENABLED", "false").lower() == "true"
    sandbox_url: str = os.getenv("SMARA_SANDBOX_URL", "")
    sandbox_token: str = _secret("SMARA_SANDBOX_TOKEN")


settings = Settings()
