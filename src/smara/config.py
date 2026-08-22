from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("SMARA_DATABASE_URL", "")
    database_path: str = os.getenv("SMARA_DATABASE_PATH", "./data/smara.db")
    syntarus_api_key: str = os.getenv("SYNTARUS_API_KEY", "")
    syntarus_base_url: str = os.getenv("SYNTARUS_BASE_URL", "https://ai.syntarus.com/v1")
    dev_mode: bool = os.getenv("SMARA_DEV_MODE", "false").lower() == "true"
    gateway_signing_secret: str = os.getenv("SMARA_GATEWAY_SIGNING_SECRET", "")
    control_bridge_secret: str = os.getenv("SMARA_CONTROL_BRIDGE_SECRET", "")
    allowed_origins: str = os.getenv(
        "SMARA_ALLOWED_ORIGINS",
        "https://ai.syntarus.com,https://control-staging.syntarus.com",
    )
    integration_master_key: str = os.getenv("SMARA_INTEGRATION_MASTER_KEY", "")
    integration_master_keys: str = os.getenv("SMARA_INTEGRATION_MASTER_KEYS", os.getenv("SMARA_INTEGRATION_MASTER_KEY", ""))
    public_base_url: str = os.getenv("SMARA_PUBLIC_BASE_URL", "http://127.0.0.1:8080")
    google_client_id: str = os.getenv("SMARA_GOOGLE_CLIENT_ID", "")
    google_client_secret: str = os.getenv("SMARA_GOOGLE_CLIENT_SECRET", "")
    github_client_id: str = os.getenv("SMARA_GITHUB_CLIENT_ID", "")
    github_client_secret: str = os.getenv("SMARA_GITHUB_CLIENT_SECRET", "")
    vapid_public_key: str = os.getenv("SMARA_VAPID_PUBLIC_KEY", "")
    vapid_private_key: str = os.getenv("SMARA_VAPID_PRIVATE_KEY", "")
    vapid_subject: str = os.getenv("SMARA_VAPID_SUBJECT", "mailto:admin@example.com")
    rate_limit_per_minute: int = int(os.getenv("SMARA_RATE_LIMIT_PER_MINUTE", "120"))
    sentry_dsn: str = os.getenv("SMARA_SENTRY_DSN", "")
    redis_url: str = os.getenv("SMARA_REDIS_URL", "")
    llm_base_url: str = os.getenv("SMARA_LLM_BASE_URL", "")
    llm_api_key: str = os.getenv("SMARA_LLM_API_KEY", "")
    llm_model: str = os.getenv("SMARA_LLM_MODEL", "")
    llm_provider: str = os.getenv("SMARA_LLM_PROVIDER", "configured model provider")
    llm_profiles: str = os.getenv("SMARA_LLM_PROFILES", "")
    llm_default_profile: str = os.getenv("SMARA_LLM_DEFAULT_PROFILE", "")
    research_synthesis_enabled: bool = os.getenv("SMARA_RESEARCH_SYNTHESIS_ENABLED", "false").lower() == "true"
    cli_token_secret: str = os.getenv("SMARA_CLI_TOKEN_SECRET", "")
    cli_token_ttl_days: int = int(os.getenv("SMARA_CLI_TOKEN_TTL_DAYS", "30"))
    search_provider: str = os.getenv("SMARA_SEARCH_PROVIDER", "brave")
    search_api_key: str = os.getenv("SMARA_SEARCH_API_KEY", "")
    search_url: str = os.getenv("SMARA_SEARCH_URL", "")
    search_timeout_seconds: float = float(os.getenv("SMARA_SEARCH_TIMEOUT_SECONDS", "12"))
    research_allowed_domains: str = os.getenv("SMARA_RESEARCH_ALLOWED_DOMAINS", "")
    research_blocked_domains: str = os.getenv("SMARA_RESEARCH_BLOCKED_DOMAINS", "")
    capture_transcription_base_url: str = os.getenv("SMARA_CAPTURE_TRANSCRIPTION_BASE_URL", "")
    capture_transcription_api_key: str = os.getenv("SMARA_CAPTURE_TRANSCRIPTION_API_KEY", "")
    capture_transcription_model: str = os.getenv("SMARA_CAPTURE_TRANSCRIPTION_MODEL", "")
    capture_vision_base_url: str = os.getenv("SMARA_CAPTURE_VISION_BASE_URL", "")
    capture_vision_api_key: str = os.getenv("SMARA_CAPTURE_VISION_API_KEY", "")
    capture_vision_model: str = os.getenv("SMARA_CAPTURE_VISION_MODEL", "")
    # Disabled until a separately isolated sandbox service is deployed.
    sandbox_enabled: bool = os.getenv("SMARA_SANDBOX_ENABLED", "false").lower() == "true"


settings = Settings()
