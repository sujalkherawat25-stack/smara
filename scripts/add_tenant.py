"""Onboard (or update) an MSME owner. Idempotent on the owner's WhatsApp number.

    python scripts/add_tenant.py --name "Ravi Traders" --owner 919811100001 \
        --vpa ravi@upi [--number-id <Meta phone_number_id>]

--number-id binds a dedicated WhatsApp number to this tenant; without it the
tenant uses the shared number and debtors are matched by their own phone.
"""
import argparse
import asyncio
import os
import selectors
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import tenants  # noqa: E402
from src.config import normalize_phone  # noqa: E402
from src.db import migrate, open_pool  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--owner", required=True, help="owner WhatsApp number")
    ap.add_argument("--vpa")
    ap.add_argument("--upi-name")
    ap.add_argument("--number-id")
    ap.add_argument("--db", default=os.environ.get("DATABASE_URL", "postgresql://munim:munim@localhost:5433/munim"))
    a = ap.parse_args()
    pool = await open_pool(a.db)
    await migrate(pool)
    t = await tenants.create(pool, name=a.name, owner_phone=normalize_phone(a.owner), upi_vpa=a.vpa,
                             upi_name=a.upi_name, wa_phone_number_id=a.number_id)
    await pool.close()
    print(f"tenant {t.id}  owner={t.owner_phone}  vpa={t.upi_vpa}  number_id={t.wa_phone_number_id}")


if __name__ == "__main__":
    asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
