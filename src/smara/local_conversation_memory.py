"""Private, restart-safe conversation memory backed by SQLite FTS5.

The local Desktop and CLI keep their own conversation index.  It is deliberately
separate from hosted/Syntarus memory: the database never leaves the machine and
the query boundary always includes the approved workspace identifier.  FTS5 is
used for deterministic, low-latency lexical recall; callers can layer model
reasoning over the bounded snippets without treating them as authoritative.
"""
from __future__ import annotations

import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any


MAX_TURN_CHARS = 20_000
MAX_SEARCH_CHARS = 4_000
MAX_RESULTS = 20
MAX_QUERY_CHARS = 400
_ROLES = {"user", "assistant", "tool", "system"}
_TOKEN_RE = re.compile(r"[\w][\w.-]{1,63}", re.UNICODE)
_SECRET_RE = re.compile(
    r"(?i)(?:bearer\s+[A-Za-z0-9._-]{12,}|(?:sk|gh[pousr]|xox[baprs])_[A-Za-z0-9_-]{12,})"
)


def memory_path_for_state(state_path: Path | str) -> Path:
    """Return the local database path adjacent to a Desktop state file."""
    path = Path(state_path).expanduser().resolve()
    return path.with_name("conversation-memory.sqlite3")


def _redact(text: str) -> str:
    return _SECRET_RE.sub("[REDACTED]", str(text)[:MAX_TURN_CHARS])


def _workspace_key(value: object) -> str:
    text = str(value or "default").strip()
    return text[:512] or "default"


class SQLiteConversationMemory:
    """A small concurrent-safe FTS5 conversation index."""

    def __init__(self, path: Path | str):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    @classmethod
    def for_state(cls, state_path: Path | str) -> "SQLiteConversationMemory":
        return cls(memory_path_for_state(state_path))

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.path), timeout=8.0)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=8000")
        con.execute("PRAGMA foreign_keys=ON")
        return con

    def _init(self) -> None:
        with self._connect() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=NORMAL")
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS local_conversation_turns (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(workspace_id, conversation_id, sequence)
                );
                CREATE INDEX IF NOT EXISTS local_turns_workspace_created
                    ON local_conversation_turns(workspace_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS local_turns_conversation_sequence
                    ON local_conversation_turns(conversation_id, sequence);
                CREATE VIRTUAL TABLE IF NOT EXISTS local_conversation_fts USING fts5(
                    content,
                    turn_id UNINDEXED,
                    conversation_id UNINDEXED,
                    workspace_id UNINDEXED,
                    role UNINDEXED,
                    created_at UNINDEXED,
                    tokenize = 'unicode61'
                );
                """
            )

    def append_turn(
        self,
        *,
        conversation_id: str,
        workspace_id: str = "default",
        role: str,
        content: str,
    ) -> dict[str, Any]:
        conversation_id = str(conversation_id or "local-default")[:240]
        workspace_id = _workspace_key(workspace_id)
        role = str(role or "assistant").strip().lower()
        if role not in _ROLES:
            raise ValueError("local conversation role is invalid")
        text = _redact(str(content or "").strip())
        if not text:
            return {"stored": False, "reason": "empty"}
        now = time.time()
        turn_id = f"turn_{uuid.uuid4().hex}"
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT COALESCE(MAX(sequence), -1) + 1 AS next_sequence "
                "FROM local_conversation_turns WHERE conversation_id=? AND workspace_id=?",
                (conversation_id, workspace_id),
            ).fetchone()
            sequence = int(row["next_sequence"] if row else 0)
            con.execute(
                "INSERT INTO local_conversation_turns "
                "(id,conversation_id,workspace_id,sequence,role,content,created_at) VALUES(?,?,?,?,?,?,?)",
                (turn_id, conversation_id, workspace_id, sequence, role, text, now),
            )
            con.execute(
                "INSERT INTO local_conversation_fts "
                "(content,turn_id,conversation_id,workspace_id,role,created_at) VALUES(?,?,?,?,?,?)",
                (text, turn_id, conversation_id, workspace_id, role, now),
            )
        return {
            "stored": True,
            "id": turn_id,
            "conversation_id": conversation_id,
            "workspace_id": workspace_id,
            "sequence": sequence,
        }

    def append_exchange(
        self,
        *,
        conversation_id: str,
        workspace_id: str = "default",
        user_message: str,
        assistant_message: str,
    ) -> dict[str, Any]:
        """Persist one user/assistant exchange atomically."""
        conversation_id = str(conversation_id or "local-default")[:240]
        workspace_id = _workspace_key(workspace_id)
        user_text = _redact(str(user_message or "").strip())
        assistant_text = _redact(str(assistant_message or "").strip())
        if not user_text and not assistant_text:
            return {"stored": False, "reason": "empty"}
        now = time.time()
        inserted: list[dict[str, Any]] = []
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT COALESCE(MAX(sequence), -1) + 1 AS next_sequence "
                "FROM local_conversation_turns WHERE conversation_id=? AND workspace_id=?",
                (conversation_id, workspace_id),
            ).fetchone()
            sequence = int(row["next_sequence"] if row else 0)
            for role, text in (("user", user_text), ("assistant", assistant_text)):
                if not text:
                    continue
                turn_id = f"turn_{uuid.uuid4().hex}"
                con.execute(
                    "INSERT INTO local_conversation_turns "
                    "(id,conversation_id,workspace_id,sequence,role,content,created_at) VALUES(?,?,?,?,?,?,?)",
                    (turn_id, conversation_id, workspace_id, sequence, role, text, now),
                )
                con.execute(
                    "INSERT INTO local_conversation_fts "
                    "(content,turn_id,conversation_id,workspace_id,role,created_at) VALUES(?,?,?,?,?,?)",
                    (text, turn_id, conversation_id, workspace_id, role, now),
                )
                inserted.append({"id": turn_id, "role": role, "sequence": sequence})
                sequence += 1
        return {"stored": bool(inserted), "turns": inserted}

    @staticmethod
    def _match_query(query: str) -> str:
        tokens = _TOKEN_RE.findall(str(query or "")[:MAX_QUERY_CHARS].lower())
        # FTS5 syntax is built only from regex-validated tokens. Prefix search
        # keeps normal inflections useful while excluding punctuation operators.
        return " AND ".join(f'"{token}"*' for token in tokens[:32])

    def search(
        self,
        query: str,
        *,
        workspace_id: str = "default",
        conversation_id: str | None = None,
        limit: int = 6,
    ) -> list[dict[str, Any]]:
        match = self._match_query(query)
        if not match:
            return []
        limit = max(1, min(int(limit), MAX_RESULTS))
        workspace_id = _workspace_key(workspace_id)
        params: list[Any] = [match, workspace_id]
        where = "workspace_id=?"
        if conversation_id:
            where += " AND conversation_id=?"
            params.append(str(conversation_id)[:240])
        params.append(limit)
        try:
            with self._connect() as con:
                rows = con.execute(
                    "SELECT turn_id,conversation_id,workspace_id,role,created_at, "
                    "snippet(local_conversation_fts,0,'','', ' … ', 20) AS snippet "
                    "FROM local_conversation_fts "
                    "WHERE local_conversation_fts MATCH ? AND " + where + " "
                    "ORDER BY bm25(local_conversation_fts), created_at DESC LIMIT ?",
                    params,
                ).fetchall()
        except sqlite3.OperationalError:
            # FTS5 is present in supported Python builds, but a vendor Python
            # without the extension should still provide deterministic recall.
            like_terms = [token.strip('"*') for token in match.split(" AND ")]
            sql = "SELECT id AS turn_id,conversation_id,workspace_id,role,created_at,content FROM local_conversation_turns WHERE workspace_id=?"
            args: list[Any] = [workspace_id]
            if conversation_id:
                sql += " AND conversation_id=?"
                args.append(str(conversation_id)[:240])
            sql += " AND " + " AND ".join("LOWER(content) LIKE ?" for _ in like_terms)
            args.extend(f"%{term.lower()}%" for term in like_terms)
            sql += " ORDER BY created_at DESC LIMIT ?"
            args.append(limit)
            with self._connect() as con:
                rows = con.execute(sql, args).fetchall()
            return [
                {
                    "turn_id": row["turn_id"],
                    "conversation_id": row["conversation_id"],
                    "workspace_id": row["workspace_id"],
                    "role": row["role"],
                    "created_at": row["created_at"],
                    "content": str(row["content"] or "")[:MAX_SEARCH_CHARS],
                    "score": 0.0,
                }
                for row in rows
            ]
        result: list[dict[str, Any]] = []
        for row in rows:
            result.append(
                {
                    "turn_id": row["turn_id"],
                    "conversation_id": row["conversation_id"],
                    "workspace_id": row["workspace_id"],
                    "role": row["role"],
                    "created_at": row["created_at"],
                    "content": str(row["snippet"] or "")[:MAX_SEARCH_CHARS],
                    "score": 0.0,
                }
            )
        return result

    def recent(self, *, workspace_id: str = "default", limit: int = 16) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), MAX_RESULTS))
        with self._connect() as con:
            rows = con.execute(
                "SELECT id AS turn_id,conversation_id,workspace_id,sequence,role,content,created_at "
                "FROM local_conversation_turns WHERE workspace_id=? ORDER BY created_at DESC, sequence DESC LIMIT ?",
                (_workspace_key(workspace_id), limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def status(self) -> dict[str, Any]:
        with self._connect() as con:
            turns = int(con.execute("SELECT COUNT(*) FROM local_conversation_turns").fetchone()[0])
            conversations = int(con.execute("SELECT COUNT(DISTINCT conversation_id) FROM local_conversation_turns").fetchone()[0])
        return {"path": str(self.path), "turns": turns, "conversations": conversations, "fts5": True}


__all__ = ["MAX_TURN_CHARS", "MAX_SEARCH_CHARS", "SQLiteConversationMemory", "memory_path_for_state"]
