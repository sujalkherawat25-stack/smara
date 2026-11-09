import os

from src.clock import parse_quiet_hours
from dataclasses import dataclass, field


def normalize_phone(raw: str) -> str:
    return "".join(ch for ch in raw if ch.isdigit())


def reminder_mode(raw: str) -> str:
    """'own' or 'smara'. Anything else falls back to the default, 'own'."""
    value = (raw or "").strip().lower()
    return value if value in ("own", "smara") else "own"


@dataclass(frozen=True)
class Settings:
    database_url: str = ""
    continuum_url: str = "http://localhost:8000"
    continuum_api_key: str = ""
    wa_verify_token: str = ""
    wa_app_secret: str = ""
    signup_mode: str = "invite"       # invite = only people with a code; open = anyone who writes can start
    owner_daily_msgs: int = 300
    owner_daily_files: int = 30
    owner_daily_tokens: int = 4_000_000
    meta_app_id: str = ""            # Meta app used for Embedded Signup (shops connecting their own numbers)
    meta_es_config_id: str = ""      # Embedded Signup configuration id
    wa_access_token: str = ""
    wa_phone_number_id: str = ""
    # Approved Meta template for reminders sent outside the 24h reply window ("" = text only)
    wa_reminder_template: str = ""
    wa_reminder_lang: str = "en"
    # order of the reminder template's variables; "shop" lets one shared number say which business is writing
    wa_reminder_params: str = "customer,invoice,amount,due"
    # How a reminder reaches the customer after the owner taps Send: "own" = the owner sends it from their own
    # WhatsApp via a wa.me link (no template or shared-number dependency); "smara" = from Smara's number
    reminder_send_mode: str = "own"
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
    # Comma-separated pool of keys for the same endpoint (each key adds its own requests-per-minute budget).
    # When set it replaces llm_api_key; see Settings.llm_keys().
    llm_api_keys: str = ""
    llm_model: str = "glm5.3-flash"
    llm_reasoning_model: str = ""
    llm_agent_model: str = ""
    llm_max_concurrency: int = 4
    llm_agent_rpm: float = 0
    brave_search_api_key: str = ""
    llm_fallback_model: str = ""
    sarvam_api_key: str = ""
    tick_seconds: int = 60
    digest_hour_ist: int = 9
    # Nothing proactive (cards, digests, reminders) goes out between these IST hours
    # 0/0 = never quiet for a bare Settings(); production gets 21-8 from load_settings (QUIET_HOURS_IST)
    quiet_start_hour: int = 0
    quiet_end_hour: int = 0
    # The shop's own WhatsApp number through the Baileys link sidecar (server/wa_link). Unofficial: see README there.
    # Empty wa_link_url = the link is not used at all, and only the Cloud API paths run.
    wa_link_url: str = ""
    wa_link_token: str = ""
    wa_link_daily_cap: int = 40          # sends per shop per IST day, customers and STOP acknowledgements included
    wa_link_send_gap_s: float = 6.0      # pause between two sends from the same link


    def llm_keys(self) -> list[str]:
        """The LLM key pool: LLM_API_KEYS if set (comma-separated), else the single LLM_API_KEY.
        Duplicates are dropped. Keys are never logged; the LLM client refers to them as key#1, key#2."""
        pool = [k.strip() for k in self.llm_api_keys.split(",") if k.strip()] if self.llm_api_keys else []
        if not pool and self.llm_api_key:
            pool = [self.llm_api_key.strip()]
        return list(dict.fromkeys(pool))


def _secret(env, name: str) -> str:
    """NAME, or the contents of the file named by NAME_FILE (a Docker secret). Never printed."""
    path = env.get(f"{name}_FILE", "")
    if path:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    return env.get(name, "")

def load_settings() -> Settings:
    env = os.environ
    return Settings(
        database_url=env.get("DATABASE_URL", ""),
        continuum_url=env.get("CONTINUUM_URL", "http://localhost:8000"),
        continuum_api_key=env.get("CONTINUUM_API_KEY", ""),
        wa_verify_token=env.get("WA_VERIFY_TOKEN", ""),
        wa_app_secret=env.get("WA_APP_SECRET", ""),
        signup_mode=env.get("SIGNUP_MODE", "invite"),
        owner_daily_msgs=int(env.get("OWNER_DAILY_MSGS", "300")),
        owner_daily_files=int(env.get("OWNER_DAILY_FILES", "30")),
        owner_daily_tokens=int(env.get("OWNER_DAILY_TOKENS", "4000000")),
        meta_app_id=env.get("META_APP_ID", ""),
        meta_es_config_id=env.get("META_ES_CONFIG_ID", ""),
        wa_access_token=env.get("WA_ACCESS_TOKEN", ""),
        wa_phone_number_id=env.get("WA_PHONE_NUMBER_ID", ""),
        wa_reminder_template=env.get("WA_REMINDER_TEMPLATE", ""),
        wa_reminder_lang=env.get("WA_REMINDER_LANG", "en"),
        wa_reminder_params=env.get("WA_REMINDER_PARAMS", "customer,invoice,amount,due"),
        wa_owner_template=env.get("WA_OWNER_TEMPLATE", ""),
        reminder_send_mode=reminder_mode(env.get("REMINDER_SEND_MODE", "own")),
        telegram_bot_token=env.get("TELEGRAM_BOT_TOKEN", ""),
        sandbox_mode=env.get("SANDBOX_MODE", "local"),
        sandbox_jobs_dir=env.get("SANDBOX_JOBS_DIR", "./data/sandbox-jobs"),
        telegram_webhook_secret=env.get("TELEGRAM_WEBHOOK_SECRET", ""),
        public_base_url=env.get("PUBLIC_BASE_URL", ""),
        llm_base_url=env.get("LLM_BASE_URL", ""),
        llm_api_key=env.get("LLM_API_KEY", ""),
        llm_api_keys=env.get("LLM_API_KEYS", ""),
        llm_model=env.get("LLM_MODEL", "glm5.3-flash"),
        llm_reasoning_model=env.get("LLM_REASONING_MODEL", ""),
        llm_agent_model=env.get("LLM_AGENT_MODEL", ""),
        llm_max_concurrency=int(env.get("LLM_MAX_CONCURRENCY", "4")),
        llm_agent_rpm=float(env.get("LLM_AGENT_RPM", "0")),
        brave_search_api_key=env.get("BRAVE_SEARCH_API_KEY", ""),
        llm_fallback_model=env.get("LLM_FALLBACK_MODEL", ""),
        sarvam_api_key=env.get("SARVAM_API_KEY", ""),
        tick_seconds=int(env.get("TICK_SECONDS", "60")),
        digest_hour_ist=int(env.get("DIGEST_HOUR_IST", "9")),
        quiet_start_hour=parse_quiet_hours(env.get("QUIET_HOURS_IST", "21-8"))[0],
        quiet_end_hour=parse_quiet_hours(env.get("QUIET_HOURS_IST", "21-8"))[1],
        wa_link_url=env.get("WA_LINK_URL", ""),
        wa_link_token=_secret(env, "WA_LINK_TOKEN"),
        wa_link_daily_cap=int(env.get("WA_LINK_DAILY_CAP", "40")),
        wa_link_send_gap_s=float(env.get("WA_LINK_SEND_GAP_S", "6")),
    )
