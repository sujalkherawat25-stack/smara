"""Vault administration.

    python scripts/vault_admin.py new-key            # print a fresh master key (put it in the server's secret file)
    docker exec munim_app python scripts/vault_admin.py status
    docker exec munim_app python scripts/vault_admin.py rotate   # after setting the new key as VAULT_MASTER_KEY(_FILE)
                                                                  # and the old one as VAULT_MASTER_KEY_OLD
"""
import asyncio
import os
import selectors
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import vault  # noqa: E402


async def _status() -> int:
    from src.db import open_pool
    keys = vault.master_keys()
    print("master key configured:", bool(keys), "| versions:", sorted(keys))
    pool = await open_pool(os.environ["DATABASE_URL"])
    async with pool.connection() as conn:
        k = await (await conn.execute("SELECT master_version, count(*) n FROM tenant_keys GROUP BY 1 ORDER BY 1")).fetchall()
        s = await (await conn.execute("SELECT count(*) n, count(DISTINCT tenant_id) t FROM tenant_secrets")).fetchone()
    await pool.close()
    print("shop keys by master version:", {r["master_version"]: r["n"] for r in k} or "none")
    print(f"secrets stored: {s['n']} across {s['t']} shops (values are never printed)")
    return 0


async def _rotate() -> int:
    from src.db import open_pool
    pool = await open_pool(os.environ["DATABASE_URL"])
    n = await vault.rewrap_all(pool)
    await pool.close()
    print(f"re-wrapped {n} shop keys under master version {vault.current_version()}. Remove VAULT_MASTER_KEY_OLD afterwards.")
    return 0


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "new-key":
        print(vault.new_master_key())
        return 0
    run = {"status": _status, "rotate": _rotate}.get(cmd)
    if run is None:
        print(__doc__)
        return 2
    return asyncio.run(run(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))


if __name__ == "__main__":
    sys.exit(main())
