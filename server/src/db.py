import os
from pathlib import Path

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


async def open_pool(database_url: str) -> AsyncConnectionPool:
    pool = AsyncConnectionPool(
        database_url, kwargs={"row_factory": dict_row}, open=False, min_size=2, max_size=int(os.environ.get("DB_POOL_MAX", "16")), timeout=20
    )
    await pool.open()
    return pool


# The agent's read-only SQL runs as smara_agent_ro with a row filter keyed on the setting smara.tenant_id. A query that could call
# set_config() could point that filter at another shop, so nobody but the app's own role may call it (and the other functions that
# act on the server or on other sessions). The app sets the filter BEFORE it drops to the read-only role (see tools.query_ledger_sql).
AGENT_LOCKDOWN = """
REVOKE EXECUTE ON FUNCTION pg_catalog.set_config(text, text, boolean) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION pg_catalog.pg_terminate_backend(integer, bigint) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION pg_catalog.pg_cancel_backend(integer) FROM PUBLIC;
"""


async def migrate(pool: AsyncConnectionPool) -> list[str]:
    """Apply unapplied migrations/*.sql in filename order; return applied names."""
    applied: list[str] = []
    async with pool.connection() as conn:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        # Serialize concurrent starters.
        await conn.execute("SELECT pg_advisory_xact_lock(727001)")
        cur = await conn.execute("SELECT name FROM schema_migrations")
        done = {row["name"] for row in await cur.fetchall()}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in done:
                continue
            await conn.execute(path.read_text(encoding="utf-8"))
            await conn.execute(
                "INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,)
            )
            applied.append(path.name)
        await conn.execute(AGENT_LOCKDOWN)       # every start, not once: it must hold even after a restore or an upgrade
    return applied
