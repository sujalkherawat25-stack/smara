from pathlib import Path

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


async def open_pool(database_url: str) -> AsyncConnectionPool:
    pool = AsyncConnectionPool(
        database_url, kwargs={"row_factory": dict_row}, open=False
    )
    await pool.open()
    return pool


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
    return applied
