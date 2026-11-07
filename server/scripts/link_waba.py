"""Link a WhatsApp Business Account to the app behind WA_ACCESS_TOKEN, so Meta
delivers that account's messages to the app's webhook. Needed once per account.

    python scripts/link_waba.py --waba 1686113662850842

Reads WA_ACCESS_TOKEN from .env. Prints the accounts' subscribed apps afterwards.
"""
import argparse
import os
import sys
from pathlib import Path

import httpx

GRAPH = "https://graph.facebook.com/v25.0"


def load_env() -> None:
    path = Path(__file__).resolve().parents[1] / ".env"
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--waba", required=True, help="WhatsApp Business Account ID (Meta calls it Messaging account ID)")
    a = ap.parse_args()
    load_env()
    token = os.environ["WA_ACCESS_TOKEN"]
    h = {"Authorization": "Bearer " + token}
    r = httpx.post(f"{GRAPH}/{a.waba}/subscribed_apps", headers=h, timeout=30)
    print("link:", r.status_code, r.text[:300].replace(token, "<token>"))
    r = httpx.get(f"{GRAPH}/{a.waba}/subscribed_apps", headers=h, timeout=30)
    apps = [x["whatsapp_business_api_data"]["name"] for x in r.json().get("data", [])]
    print("subscribed apps:", apps)
    sys.exit(0 if r.status_code == 200 else 1)


if __name__ == "__main__":
    main()
