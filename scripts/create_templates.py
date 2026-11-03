"""Submit Smara's WhatsApp message templates to Meta for approval (UTILITY category).

    python scripts/create_templates.py --waba <WhatsApp Business Account ID>

Reads WA_ACCESS_TOKEN from .env. Approval usually takes minutes to a day; check
status in WhatsApp Manager > Message templates. Once approved, set in .env:
    WA_REMINDER_TEMPLATE=smara_payment_reminder
    WA_OWNER_TEMPLATE=smara_owner_waiting
"""
import argparse
import os
import sys
from pathlib import Path

import httpx

GRAPH = "https://graph.facebook.com/v25.0"

TEMPLATES = [
    {   # to a debtor who has not written in 24h; variables match src/loop/reminders.py
        "name": "smara_payment_reminder", "language": "en", "category": "UTILITY",
        "components": [{
            "type": "BODY",
            "text": "Namaste {{1}}, this is a reminder that invoice {{2}} for {{3}} was due on {{4}}. "
                    "Please reply here if you have already paid or need more time.",
            "example": {"body_text": [["Ravi Kirana Store", "INV-101", "Rs 12,500", "22 Oct"]]},
        }],
    },
    {   # to the owner when their 24h window is closed; variable is the number of items waiting
        "name": "smara_owner_waiting", "language": "en", "category": "UTILITY",
        "components": [{
            "type": "BODY",
            "text": "You have {{1}} item(s) waiting for your approval in Smara. Reply to this message to see them.",
            "example": {"body_text": [["3"]]},
        }],
    },
]


def load_env() -> None:
    for line in (Path(__file__).resolve().parents[1] / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--waba", required=True)
    a = ap.parse_args()
    load_env()
    token = os.environ["WA_ACCESS_TOKEN"]
    ok = True
    for t in TEMPLATES:
        r = httpx.post(f"{GRAPH}/{a.waba}/message_templates", headers={"Authorization": "Bearer " + token},
                       json=t, timeout=30)
        print(t["name"], r.status_code, r.text[:200].replace(token, "<token>"))
        ok &= r.status_code == 200 or "already exists" in r.text
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
