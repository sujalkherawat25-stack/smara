"""Minimal explicit Postgres migration runner for the independent control plane."""
from __future__ import annotations

import argparse
from pathlib import Path

from .config import settings


def apply_postgres_migrations(database_url: str | None = None) -> None:
    url = database_url or settings.database_url
    if not url.startswith(("postgres://", "postgresql://")):
        raise ValueError("SMARA_DATABASE_URL must be a PostgreSQL URL")
    import psycopg
    # Source checkouts keep migrations at the project root; the production
    # image copies that directory to /app because installed wheels contain only
    # Python packages.
    source_dir = Path(__file__).resolve().parents[2] / "migrations"
    migration_dir = source_dir if source_dir.is_dir() else Path("/app/migrations")
    if not migration_dir.is_dir():
        raise RuntimeError(f"Smara migration directory is missing: {migration_dir}")
    with psycopg.connect(url, autocommit=True) as con:
        with con.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS smara_schema_migrations (version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())")
            applied = {row[0] for row in cur.execute("SELECT version FROM smara_schema_migrations")}
            for migration in sorted(migration_dir.glob("*.sql")):
                if migration.name in applied:
                    continue
                cur.execute(migration.read_text(encoding="utf-8"))
                cur.execute("INSERT INTO smara_schema_migrations(version) VALUES(%s)", (migration.name,))


def migration_status(database_url: str | None = None) -> dict[str, list[str]]:
    """Return applied and pending migration names without exposing credentials."""
    url = database_url or settings.database_url
    if not url.startswith(("postgres://", "postgresql://")):
        raise ValueError("SMARA_DATABASE_URL must be a PostgreSQL URL")
    import psycopg

    source_dir = Path(__file__).resolve().parents[2] / "migrations"
    migration_dir = source_dir if source_dir.is_dir() else Path("/app/migrations")
    expected = sorted(path.name for path in migration_dir.glob("*.sql"))
    with psycopg.connect(url) as con:
        with con.cursor() as cur:
            cur.execute("SELECT to_regclass('public.smara_schema_migrations')")
            if cur.fetchone()[0] is None:
                applied: set[str] = set()
            else:
                cur.execute("SELECT version FROM smara_schema_migrations")
                applied = {row[0] for row in cur.fetchall()}
    return {"applied": [name for name in expected if name in applied], "pending": [name for name in expected if name not in applied]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect or apply Smara PostgreSQL migrations")
    parser.add_argument("--apply", action="store_true", help="apply pending migrations")
    args = parser.parse_args(argv)
    before = migration_status()
    if args.apply and before["pending"]:
        apply_postgres_migrations()
    after = migration_status()
    print(f"Applied: {len(after['applied'])}; pending: {len(after['pending'])}")
    if after["pending"]:
        print("Pending migrations: " + ", ".join(after["pending"]))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
