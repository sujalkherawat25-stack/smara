"""Point a Telegram bot at this server's webhook (run on the server, or anywhere with the .env).

    python scripts/telegram_setup.py --url https://ai.syntarus.com/munim/telegram/webhook

Reads TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_SECRET from .env (creates and
prints a new secret line if missing — add it to .env and restart the app).
Only private-chat messages and button presses are subscribed.
"""
import argparse
import os
import secrets
import sys
from pathlib import Path

import httpx

ENV = Path(__file__).resolve().parents[1] / ".env"


def load_env() -> None:
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    a = ap.parse_args()
    load_env()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        sys.exit("TELEGRAM_BOT_TOKEN is not set in .env (get it from @BotFather).")
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
    if not secret:
        secret = secrets.token_urlsafe(32)
        with ENV.open("a", encoding="utf-8", newline="\n") as f:
            f.write(f"\nTELEGRAM_WEBHOOK_SECRET={secret}\n")
        print("Added TELEGRAM_WEBHOOK_SECRET to .env — restart the app so it picks it up.")
    api = f"https://api.telegram.org/bot{token}"
    me = httpx.get(f"{api}/getMe", timeout=20).json()
    r = httpx.post(f"{api}/setWebhook", json={"url": a.url, "secret_token": secret,
                                             "allowed_updates": ["message", "callback_query"],
                                             "drop_pending_updates": True}, timeout=20)
    print("bot:", (me.get("result") or {}).get("username"), "| setWebhook:", r.status_code, r.json().get("description"))
    info = httpx.get(f"{api}/getWebhookInfo", timeout=20).json().get("result", {})
    print("webhook url:", info.get("url"), "| pending:", info.get("pending_update_count"), "| last error:", info.get("last_error_message"))


if __name__ == "__main__":
    main()
