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


settings = Settings()
