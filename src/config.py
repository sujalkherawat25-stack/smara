import os

from src.clock import parse_quiet_hours
from dataclasses import dataclass, field


def normalize_phone(raw: str) -> str:
    return "".join(ch for ch in raw if ch.isdigit())


@dataclass(frozen=True)
class Settings:
    database_url: str = ""
    continuum_url: str = "http://localhost:8000"
    continuum_api_key: str = ""
    wa_verify_token: str = ""
    wa_app_secret: str = ""
    wa_access_token: str = ""
    wa_phone_number_id: str = ""
    # Approved Meta template for reminders sent outside the 24h reply window ("" = text only)
    wa_reminder_template: str = ""
    wa_reminder_lang: str = "en"
    # Approved template that tells the owner "N items waiting" when their 24h window is closed
    wa_owner_template: str = ""
    telegram_bot_token: str = ""
    # Python sandbox: container (prod, isolated munim_sandbox), local (dev/tests only), off
    sandbox_mode: str = "local"
    sandbox_jobs_dir: str = "./data/sandbox-jobs"
    telegram_webhook_secret: str = ""
    # Public https origin of this server; enables tappable pay links in reminders
    public_base_url: str = ""
    # OpenAI-compatible chat endpoint; fast model by default, reasoning model on escalation
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "glm5.3-flash"
    llm_reasoning_model: str = ""
    llm_agent_model: str = ""
    llm_max_concurrency: int = 4
    sarvam_api_key: str = ""
    tick_seconds: int = 60
    digest_hour_ist: int = 9
    # Nothing proactive (cards, digests, reminders) goes out between these IST hours
    # 0/0 = never quiet for a bare Settings(); production gets 21-8 from load_settings (QUIET_HOURS_IST)
    quiet_start_hour: int = 0
    quiet_end_hour: int = 0


def load_settings() -> Settings:
    env = os.environ
    return Settings(
        database_url=env.get("DATABASE_URL", ""),
        continuum_url=env.get("CONTINUUM_URL", "http://localhost:8000"),
        continuum_api_key=env.get("CONTINUUM_API_KEY", ""),
        wa_verify_token=env.get("WA_VERIFY_TOKEN", ""),
        wa_app_secret=env.get("WA_APP_SECRET", ""),
        wa_access_token=env.get("WA_ACCESS_TOKEN", ""),
        wa_phone_number_id=env.get("WA_PHONE_NUMBER_ID", ""),
        wa_reminder_template=env.get("WA_REMINDER_TEMPLATE", ""),
        wa_reminder_lang=env.get("WA_REMINDER_LANG", "en"),
        wa_owner_template=env.get("WA_OWNER_TEMPLATE", ""),
        telegram_bot_token=env.get("TELEGRAM_BOT_TOKEN", ""),
        sandbox_mode=env.get("SANDBOX_MODE", "local"),
        sandbox_jobs_dir=env.get("SANDBOX_JOBS_DIR", "./data/sandbox-jobs"),
        telegram_webhook_secret=env.get("TELEGRAM_WEBHOOK_SECRET", ""),
        public_base_url=env.get("PUBLIC_BASE_URL", ""),
        llm_base_url=env.get("LLM_BASE_URL", ""),
        llm_api_key=env.get("LLM_API_KEY", ""),
        llm_model=env.get("LLM_MODEL", "glm5.3-flash"),
        llm_reasoning_model=env.get("LLM_REASONING_MODEL", ""),
        llm_agent_model=env.get("LLM_AGENT_MODEL", ""),
        llm_max_concurrency=int(env.get("LLM_MAX_CONCURRENCY", "4")),
        sarvam_api_key=env.get("SARVAM_API_KEY", ""),
        tick_seconds=int(env.get("TICK_SECONDS", "60")),
        digest_hour_ist=int(env.get("DIGEST_HOUR_IST", "9")),
        quiet_start_hour=parse_quiet_hours(env.get("QUIET_HOURS_IST", "21-8"))[0],
        quiet_end_hour=parse_quiet_hours(env.get("QUIET_HOURS_IST", "21-8"))[1],
    )
