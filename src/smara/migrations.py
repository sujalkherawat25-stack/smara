"""Minimal explicit Postgres migration runner for the independent control plane."""
from __future__ import annotations

from pathlib import Path

from .config import settings


def apply_postgres_migrations(database_url: str | None = None) -> None:
    url = database_url or settings.database_url
    if not url.startswith(("postgres://", "postgresql://")):
        raise ValueError("SMARA_DATABASE_URL must be a PostgreSQL URL")
    import psycopg
    migration_dir = Path(__file__).resolve().parents[2] / "migrations"
    with psycopg.connect(url, autocommit=True) as con:
        with con.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS smara_schema_migrations (version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())")
            applied = {row[0] for row in cur.execute("SELECT version FROM smara_schema_migrations")}
            for migration in sorted(migration_dir.glob("*.sql")):
                if migration.name in applied:
                    continue
                cur.execute(migration.read_text(encoding="utf-8"))
                cur.execute("INSERT INTO smara_schema_migrations(version) VALUES(%s)", (migration.name,))


if __name__ == "__main__":
    apply_postgres_migrations()
