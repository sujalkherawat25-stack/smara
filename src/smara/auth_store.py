"""Small native Smara identity store.

Smara owns the session and Telegram edge after the cutover, but it reuses the
existing account database when ``SMARA_ACCOUNTS_DATABASE_URL`` is configured.
That preserves the account ids used by Syntarus memory without importing the
legacy Memento package.  The store intentionally has a SQLite fallback for
desktop/tests; production should always use Postgres.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


class AccountStore:
    def __init__(self, database_url: str = "", database_path: str = "./data/smara.db"):
        self.database_url = database_url.strip()
        self.database_path = database_path
        if not self.database_url:
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)

    def _connect(self):
        if self.database_url:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:  # pragma: no cover - dependency packaging
                raise RuntimeError("Postgres identity storage requires psycopg.") from exc
            return psycopg.connect(self.database_url, row_factory=dict_row)
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _execute(self, connection: Any, sql: str, params: tuple = ()):
        return connection.execute(sql.replace("%s", "?") if not self.database_url else sql, params)

    def ensure_schema(self) -> None:
        """Create only native additions; existing account tables are reused."""
        if self.database_url:
            ddl = """
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY, email TEXT UNIQUE, google_sub TEXT UNIQUE,
                display_name TEXT, avatar_url TEXT, plan TEXT NOT NULL DEFAULT 'free',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), last_login_at TIMESTAMPTZ
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), expires_at TIMESTAMPTZ NOT NULL,
                user_agent TEXT
            );
            CREATE INDEX IF NOT EXISTS smara_sessions_account ON sessions(account_id);
            CREATE TABLE IF NOT EXISTS channel_links (
                channel TEXT NOT NULL, channel_user_id TEXT NOT NULL, chat_id TEXT,
                account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                linked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), PRIMARY KEY(channel, channel_user_id)
            );
            CREATE INDEX IF NOT EXISTS smara_channel_links_account ON channel_links(account_id);
            CREATE TABLE IF NOT EXISTS smara_telegram_link_codes (
                code_hash TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                expires_at TIMESTAMPTZ NOT NULL, consumed_at TIMESTAMPTZ
            );
            CREATE INDEX IF NOT EXISTS smara_telegram_codes_expiry ON smara_telegram_link_codes(expires_at);
            """
        else:
            ddl = """
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY, email TEXT UNIQUE, google_sub TEXT UNIQUE,
                display_name TEXT, avatar_url TEXT, plan TEXT NOT NULL DEFAULT 'free',
                created_at TEXT NOT NULL, last_login_at TEXT
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL, expires_at TEXT NOT NULL, user_agent TEXT
            );
            CREATE TABLE IF NOT EXISTS channel_links (
                channel TEXT NOT NULL, channel_user_id TEXT NOT NULL, chat_id TEXT,
                account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                linked_at TEXT NOT NULL, PRIMARY KEY(channel, channel_user_id)
            );
            CREATE TABLE IF NOT EXISTS smara_telegram_link_codes (
                code_hash TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL, consumed_at TEXT
            );
            """
        with self._connect() as connection:
            connection.executescript(ddl) if not self.database_url else connection.execute(ddl)

    @staticmethod
    def _dict(row: Any) -> dict[str, Any] | None:
        return dict(row) if row else None

    def account_by_google_sub(self, google_sub: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            return self._dict(self._execute(connection, "SELECT * FROM accounts WHERE google_sub=%s", (google_sub,)).fetchone())

    def account_by_id(self, account_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            return self._dict(self._execute(connection, "SELECT * FROM accounts WHERE id=%s", (account_id,)).fetchone())

    def upsert_google_account(self, *, google_sub: str, email: str, display_name: str | None, avatar_url: str | None) -> dict[str, Any]:
        now = _now()
        existing = self.account_by_google_sub(google_sub)
        if existing:
            with self._connect() as connection:
                self._execute(connection, "UPDATE accounts SET email=%s,display_name=%s,avatar_url=%s,last_login_at=%s WHERE id=%s", (email, display_name, avatar_url, now if self.database_url else _iso(now), existing["id"]))
            return self.account_by_id(existing["id"]) or existing
        account_id = f"acct_{secrets.token_urlsafe(9).replace('-', '_').replace('.', '_')}"
        created = now if self.database_url else _iso(now)
        with self._connect() as connection:
            if self.database_url:
                self._execute(connection, "INSERT INTO accounts (id,email,google_sub,display_name,avatar_url,plan,created_at,last_login_at) VALUES (%s,%s,%s,%s,%s,'free',%s,%s)", (account_id, email, google_sub, display_name, avatar_url, created, created))
            else:
                self._execute(connection, "INSERT INTO accounts (id,email,google_sub,display_name,avatar_url,plan,created_at,last_login_at) VALUES (%s,%s,%s,%s,%s,'free',%s,%s)", (account_id, email, google_sub, display_name, avatar_url, created, created))
        return self.account_by_id(account_id) or {"id": account_id, "email": email, "google_sub": google_sub, "display_name": display_name, "avatar_url": avatar_url, "plan": "free"}

    def create_session(self, token_id: str, account_id: str, expires_at: datetime, user_agent: str | None = None) -> None:
        with self._connect() as connection:
            self._execute(connection, "INSERT INTO sessions (token_id,account_id,created_at,expires_at,user_agent) VALUES (%s,%s,%s,%s,%s)", (token_id, account_id, _now() if self.database_url else _iso(_now()), expires_at if self.database_url else _iso(expires_at), (user_agent or "")[:300]))

    def session_account(self, token_id: str, account_id: str, now: datetime | None = None) -> bool:
        now = now or _now()
        with self._connect() as connection:
            row = self._execute(connection, "SELECT 1 FROM sessions WHERE token_id=%s AND account_id=%s AND expires_at>%s", (token_id, account_id, now if self.database_url else _iso(now))).fetchone()
        return bool(row)

    def delete_session(self, token_id: str) -> None:
        with self._connect() as connection:
            self._execute(connection, "DELETE FROM sessions WHERE token_id=%s", (token_id,))

    def create_telegram_code(self, account_id: str, ttl_seconds: int = 600) -> dict[str, Any]:
        code = f"{secrets.randbelow(1_000_000):06d}"
        expires = _now() + timedelta(seconds=max(60, min(ttl_seconds, 3600)))
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        with self._connect() as connection:
            self._execute(connection, "DELETE FROM smara_telegram_link_codes WHERE account_id=%s OR expires_at<=%s", (account_id, _now() if self.database_url else _iso(_now())))
            self._execute(connection, "INSERT INTO smara_telegram_link_codes (code_hash,account_id,expires_at,consumed_at) VALUES (%s,%s,%s,NULL)", (code_hash, account_id, expires if self.database_url else _iso(expires)))
        return {"code": code, "expires_in_seconds": int((expires - _now()).total_seconds())}

    def redeem_telegram_code(self, code: str, telegram_user_id: int | str, chat_id: int | str | None = None) -> str | None:
        code_hash = hashlib.sha256(str(code).strip().encode()).hexdigest()
        now = _now()
        with self._connect() as connection:
            row = self._execute(connection, "SELECT account_id FROM smara_telegram_link_codes WHERE code_hash=%s AND consumed_at IS NULL AND expires_at>%s", (code_hash, now if self.database_url else _iso(now))).fetchone()
            if not row:
                return None
            account_id = row["account_id"] if isinstance(row, dict) else row[0]
            updated = self._execute(connection, "UPDATE smara_telegram_link_codes SET consumed_at=%s WHERE code_hash=%s AND consumed_at IS NULL", (now if self.database_url else _iso(now), code_hash))
            if updated.rowcount != 1:
                return None
            if self.database_url:
                self._execute(connection, "INSERT INTO channel_links (channel,channel_user_id,chat_id,account_id,linked_at) VALUES ('telegram',%s,%s,%s,%s) ON CONFLICT (channel,channel_user_id) DO UPDATE SET chat_id=EXCLUDED.chat_id,account_id=EXCLUDED.account_id,linked_at=EXCLUDED.linked_at", (str(telegram_user_id), str(chat_id) if chat_id is not None else None, account_id, now))
            else:
                self._execute(connection, "INSERT INTO channel_links (channel,channel_user_id,chat_id,account_id,linked_at) VALUES ('telegram',%s,%s,%s,%s) ON CONFLICT(channel,channel_user_id) DO UPDATE SET chat_id=excluded.chat_id,account_id=excluded.account_id,linked_at=excluded.linked_at", (str(telegram_user_id), str(chat_id) if chat_id is not None else None, account_id, _iso(now)))
        return str(account_id)

    def telegram_account(self, telegram_user_id: int | str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = self._execute(connection, "SELECT a.*,c.chat_id,c.linked_at FROM channel_links c JOIN accounts a ON a.id=c.account_id WHERE c.channel='telegram' AND c.channel_user_id=%s", (str(telegram_user_id),)).fetchone()
        return self._dict(row)

    def telegram_status(self, account_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = self._execute(connection, "SELECT channel_user_id,linked_at FROM channel_links WHERE channel='telegram' AND account_id=%s ORDER BY linked_at DESC LIMIT 1", (account_id,)).fetchone()
        if not row:
            return {"linked": False, "linked_at": None, "channel_user_preview": None}
        value = row["channel_user_id"] if isinstance(row, dict) else row[0]
        linked_at = row["linked_at"] if isinstance(row, dict) else row[1]
        return {"linked": True, "linked_at": linked_at.isoformat() if hasattr(linked_at, "isoformat") else linked_at, "channel_user_preview": f"…{str(value)[-4:]}"}

    def unlink_telegram(self, account_id: str) -> None:
        with self._connect() as connection:
            self._execute(connection, "DELETE FROM channel_links WHERE channel='telegram' AND account_id=%s", (account_id,))

