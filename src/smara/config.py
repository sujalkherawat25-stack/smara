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
    integration_master_key: str = os.getenv("SMARA_INTEGRATION_MASTER_KEY", "")
    public_base_url: str = os.getenv("SMARA_PUBLIC_BASE_URL", "http://127.0.0.1:8080")
    google_client_id: str = os.getenv("SMARA_GOOGLE_CLIENT_ID", "")
    google_client_secret: str = os.getenv("SMARA_GOOGLE_CLIENT_SECRET", "")
    github_client_id: str = os.getenv("SMARA_GITHUB_CLIENT_ID", "")
    github_client_secret: str = os.getenv("SMARA_GITHUB_CLIENT_SECRET", "")


settings = Settings()
