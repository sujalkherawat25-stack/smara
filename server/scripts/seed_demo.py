"""Seed the 2-phone demo: one owner phone, one debtor phone.

    python scripts/seed_demo.py --owner 919811100001 --debtor 919811100002 --vpa shop@upi

Idempotent: the tenant id is derived from the owner phone, so rerunning updates
the same rows. Registers the tenant in Postgres. Run the server afterwards;
the scheduler picks up the overdue invoices within one tick and asks the owner
to approve each reminder before anything reaches the debtor phone.
"""
import argparse
import asyncio
import os
import selectors
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import tenants  # noqa: E402
from src.config import normalize_phone  # noqa: E402
from src.db import migrate, open_pool  # noqa: E402
from src.ingest.importer import import_rows  # noqa: E402
from src.ingest.sheet_mapper import SheetRow  # noqa: E402

NAMESPACE = uuid.UUID("5a3f0c1e-0000-4000-8000-000000000000")


def demo_rows(debtor: str, today: date) -> list[SheetRow]:
    return [
        SheetRow("Ravi Kirana Store", debtor, "DEMO-101", 12_500_000, today - timedelta(days=12)),
        SheetRow("Ravi Kirana Store", debtor, "DEMO-102", 4_075_000, today - timedelta(days=3)),
        SheetRow("Ravi Kirana Store", debtor, "DEMO-103", 2_000_000, today + timedelta(days=9)),
    ]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--owner", required=True, help="owner WhatsApp number")
    ap.add_argument("--debtor", required=True, help="debtor WhatsApp number")
    ap.add_argument("--vpa", default="demoshop@upi")
    ap.add_argument("--db", default=os.environ.get("DATABASE_URL", "postgresql://munim:munim@localhost:5433/munim"))
    args = ap.parse_args()
    owner, debtor = normalize_phone(args.owner), normalize_phone(args.debtor)
    if owner == debtor:
        sys.exit("owner and debtor must be different phones")
    tenant_id = str(uuid.uuid5(NAMESPACE, owner))

    pool = await open_pool(args.db)
    await migrate(pool)
    t = await tenants.create(pool, name="Demo Shop", owner_phone=owner, upi_vpa=args.vpa, tenant_id=tenant_id)
    result = await import_rows(pool, t.id, demo_rows(debtor, date.today()))
    await pool.close()

    print(f"Seeded tenant {t.id}: {result}")
    print("\nDemo script:\n"
          "  1. Start the server; owner phone gets reminder cards for DEMO-101/102.\n"
          "  2. Tap Send on one -> debtor phone receives it with a UPI link.\n"
          "  3. Debtor replies 'kal tak bhej dunga' or '10% discount de do'.\n"
          "  4. Owner phone gets an Approve/Reject card; only Approve changes the invoice.")


if __name__ == "__main__":
    asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
