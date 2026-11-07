"""Invite codes, the waitlist and open data requests for the public Smara number.

    docker exec munim_app python scripts/invites.py create --label "Ramesh Hardware" [--uses 1] [--days 14] [--prefix BSW]
    docker exec munim_app python scripts/invites.py list
    docker exec munim_app python scripts/invites.py revoke BSW-7Q2K
    docker exec munim_app python scripts/invites.py waitlist          # people who wrote without a code
    docker exec munim_app python scripts/invites.py requests          # shops that asked to delete their data

Give the owner the code; they send "JOIN BSW-7Q2K" to the Smara WhatsApp number.
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.ingest.onboarding import new_code  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--label", default="")
    c.add_argument("--uses", type=int, default=1)
    c.add_argument("--days", type=int, default=14)
    c.add_argument("--prefix", default="SMA")
    sub.add_parser("list")
    r = sub.add_parser("revoke")
    r.add_argument("code")
    sub.add_parser("waitlist")
    sub.add_parser("requests")
    a = ap.parse_args()
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row, autocommit=True) as conn:
        if a.cmd == "create":
            code = new_code(a.prefix)
            conn.execute("INSERT INTO invite_codes (code, label, max_uses, expires_at) VALUES (%s, %s, %s, %s)",
                         (code, a.label, a.uses, datetime.now(timezone.utc) + timedelta(days=a.days)))
            print(f"{code}   ({a.label or 'no label'}, {a.uses} use(s), {a.days} days)\nTell them to send:  JOIN {code}")
        elif a.cmd == "list":
            for r_ in conn.execute("SELECT * FROM invite_codes ORDER BY created_at DESC LIMIT 50").fetchall():
                state = "revoked" if r_["revoked_at"] else f"{r_['uses']}/{r_['max_uses']} used"
                print(f"{r_['code']:<10} {state:<12} {r_['label']}")
        elif a.cmd == "revoke":
            n = conn.execute("UPDATE invite_codes SET revoked_at = now() WHERE code = %s", (a.code.upper(),)).rowcount
            print("revoked" if n else "no such code")
        elif a.cmd == "waitlist":
            for r_ in conn.execute("SELECT * FROM waitlist WHERE invited_at IS NULL ORDER BY created_at LIMIT 100").fetchall():
                print(f"+{r_['phone']:<14} {r_['messages']:>2} msg  {r_['first_text'][:80]!r}")
        elif a.cmd == "requests":
            for r_ in conn.execute("""SELECT d.created_at, t.name FROM data_requests d JOIN tenants t ON t.id = d.tenant_id
                                       WHERE d.status = 'OPEN' ORDER BY d.created_at""").fetchall():
                print(f"{r_['created_at']:%Y-%m-%d}  {r_['name']}   -> python scripts/tenant_data.py erase --tenant \"{r_['name']}\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
