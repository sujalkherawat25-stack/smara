"""Export or erase everything Smara holds for one shop (DPDP: access and erasure on request).

    python scripts/tenant_data.py export --tenant "Ravi Traders" > ravi.json
    python scripts/tenant_data.py erase  --tenant "Ravi Traders"                       # dry run: shows what would go
    python scripts/tenant_data.py erase  --tenant "Ravi Traders" --execute --confirm "Ravi Traders"

Erase deletes the shop's rows in Postgres (ledger, parties, messages log, agent sessions, usage, device pairings) and
asks Continuum to delete the shop's memory scopes. Backups age out on their own (7 days of nightly dumps), so tell the
shop that. Device tokens and link codes are never part of an export.
"""
import argparse
import json
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

SECRET_COLUMNS = {"token_hash", "telegram_link_code", "code_hash", "ciphertext", "wrapped_key", "nonce"}
# deleted first, in this order, because rows below depend on them
VIA_OBLIGATION = ("proposed_adjustments", "obligation_events")
LAST = ("tenant_secrets", "tenant_keys", "obligations", "parties")


def _plain(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, (UUID, Decimal)):
        return str(v)
    return v


def tenant_tables(conn) -> list[str]:
    rows = conn.execute("""SELECT table_name FROM information_schema.columns
                            WHERE table_schema = 'public' AND column_name = 'tenant_id' ORDER BY table_name""").fetchall()
    return [r["table_name"] for r in rows]


def find_tenant(conn, name: str) -> dict:
    row = conn.execute("SELECT id, name FROM tenants WHERE name = %s", (name,)).fetchall()
    if len(row) != 1:
        raise SystemExit(f"expected exactly one shop named {name!r}, found {len(row)}")
    return row[0]


def export_tenant(conn, tenant_id) -> dict:
    out: dict = {"tenant": None, "tables": {}}
    t = conn.execute("SELECT * FROM tenants WHERE id = %s", (tenant_id,)).fetchone()
    out["tenant"] = {k: _plain(v) for k, v in t.items() if k not in SECRET_COLUMNS}
    for table in tenant_tables(conn):
        if table == "tenants":
            continue
        rows = conn.execute(f'SELECT * FROM "{table}" WHERE tenant_id = %s', (tenant_id,)).fetchall()
        out["tables"][table] = [{k: _plain(v) for k, v in r.items() if k not in SECRET_COLUMNS} for r in rows]
    for table in VIA_OBLIGATION:
        rows = conn.execute(f'SELECT x.* FROM "{table}" x JOIN obligations o ON o.id = x.obligation_id WHERE o.tenant_id = %s',
                            (tenant_id,)).fetchall()
        out["tables"][table] = [{k: _plain(v) for k, v in r.items() if k not in SECRET_COLUMNS} for r in rows]
    return out


def erase_tenant(conn, tenant_id, execute: bool = False) -> dict[str, int]:
    """Row counts per table; deletes only when execute=True (inside the caller's transaction)."""
    counts: dict[str, int] = {}

    def run(label, sql, args):
        if execute:
            counts[label] = conn.execute(sql, args).rowcount
        else:
            counts[label] = conn.execute(f"SELECT count(*) AS n FROM ({sql.replace('DELETE FROM', 'SELECT 1 FROM', 1)}) q", args).fetchone()["n"]

    for table in VIA_OBLIGATION:
        run(table, f'DELETE FROM "{table}" WHERE obligation_id IN (SELECT id FROM obligations WHERE tenant_id = %s)', (tenant_id,))
    middle = [t for t in tenant_tables(conn) if t not in LAST and t != "tenants"]
    for table in middle:
        run(table, f'DELETE FROM "{table}" WHERE tenant_id = %s', (tenant_id,))
    for table in LAST:
        run(table, f'DELETE FROM "{table}" WHERE tenant_id = %s', (tenant_id,))
    run("tenants", "DELETE FROM tenants WHERE id = %s", (tenant_id,))
    return counts


def continuum_scopes(conn, tenant_id) -> list[str]:
    ids = [str(r["id"]) for r in conn.execute("SELECT id FROM parties WHERE tenant_id = %s", (tenant_id,)).fetchall()]
    return [f"{tenant_id}_{p}" for p in ids] + [f"{tenant_id}_shop"]


def delete_continuum(scopes: list[str]) -> int:
    import httpx
    base, key = os.environ.get("CONTINUUM_URL", ""), os.environ.get("CONTINUUM_API_KEY", "")
    if not base:
        print("CONTINUUM_URL not set: memory was NOT deleted", file=sys.stderr)
        return 0
    done = 0
    with httpx.Client(base_url=base, headers={"Authorization": f"Bearer {key}"}, timeout=30) as h:
        for s in scopes:
            if h.delete("/memories", params={"user_id": s}).status_code in (200, 202, 204):
                done += 1
    return done


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["export", "erase"])
    ap.add_argument("--tenant", required=True, help="the shop's exact name")
    ap.add_argument("--execute", action="store_true", help="erase: really delete (default is a dry run)")
    ap.add_argument("--confirm", default="", help="erase: repeat the exact shop name")
    ap.add_argument("--db", default=os.environ.get("DATABASE_URL", ""))
    a = ap.parse_args()
    with psycopg.connect(a.db, row_factory=dict_row) as conn:
        t = find_tenant(conn, a.tenant)
        if a.action == "export":
            print(json.dumps(export_tenant(conn, t["id"]), indent=1, ensure_ascii=False))
            return 0
        if a.execute and a.confirm != t["name"]:
            raise SystemExit("refusing to erase: --confirm must repeat the shop name exactly")
        scopes = continuum_scopes(conn, t["id"])
        counts = erase_tenant(conn, t["id"], execute=a.execute)
        if a.execute:
            conn.commit()
            print(f"Erased {t['name']}: {sum(counts.values())} rows; memory scopes deleted: {delete_continuum(scopes)}/{len(scopes)}")
        else:
            conn.rollback()
            print(f"DRY RUN for {t['name']} (nothing deleted). Rows that would go:")
        for k, v in counts.items():
            if v:
                print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
