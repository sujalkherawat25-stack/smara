"""Shared durable runtime-session contract.

The Desktop, CLI, and hosted API historically persisted slightly different
shapes of a conversation.  This module is deliberately small: it stores only
the session envelope and append-only lifecycle events.  Existing task and
conversation stores remain the source for their specialised data, while this
contract gives every entry point one resume/status protocol.

The implementation uses SQLite from the standard library so the local Desktop
does not need a server dependency.  A caller can point it at a path beside its
existing state file; hosted deployments can use the same schema in their
private data directory and mirror events into their normal task store.
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping


SESSION_SCHEMA_VERSION = 1
SESSION_STATUSES = frozenset(
    {"created", "running", "waiting_approval", "completed", "failed", "cancelled", "needs_input"}
)
TERMINAL_SESSION_STATUSES = frozenset({"completed", "failed", "cancelled", "needs_input"})


def _now() -> float:
    return time.time()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _decode(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


@dataclass(frozen=True)
class SessionEvent:
    session_id: str
    sequence: int
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RuntimeSession:
    session_id: str
    workspace_id: str = "default"
    account_id: str = "local"
    mode: str = "local"
    status: str = "created"
    request: str = ""
    model_profile: str | None = None
    tool_profile: str | None = None
    research_mode: str | None = None
    result: dict[str, Any] = field(default_factory=dict)
    unresolved: list[str] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0
    revision: int = 0
    cancel_requested: bool = False
    cancel_reason: str | None = None

    def __post_init__(self) -> None:
        if self.status not in SESSION_STATUSES:
            raise ValueError(f"invalid runtime session status: {self.status}")
        self.session_id = str(self.session_id).strip()[:160]
        if not self.session_id:
            raise ValueError("session_id is required")
        self.workspace_id = str(self.workspace_id or "default")[:512]
        self.account_id = str(self.account_id or "local")[:256]
        self.mode = str(self.mode or "local")[:64]
        self.request = str(self.request or "")[:20_000]
        self.cancel_requested = bool(self.cancel_requested)
        if self.cancel_reason is not None:
            self.cancel_reason = str(self.cancel_reason)[:500]
        if self.created_at <= 0:
            self.created_at = _now()
        if self.updated_at <= 0:
            self.updated_at = self.created_at

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RuntimeSession":
        payload = dict(value)
        payload["result"] = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        payload["unresolved"] = payload.get("unresolved") if isinstance(payload.get("unresolved"), list) else []
        # Only pass fields present in the serialized envelope.  Passing
        # missing optional fields as ``None`` would bypass dataclass defaults
        # and make old session records fail validation on resume.
        allowed = set(cls.__dataclass_fields__)
        return cls(**{key: payload[key] for key in allowed if key in payload})


class SQLiteRuntimeSessionStore:
    """Atomic, restart-safe session envelope and event ledger."""

    def __init__(self, path: Path | str, *, max_events: int = 512, timeout: float = 8.0):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_events = max(32, min(int(max_events), 10_000))
        self.timeout = max(1.0, float(timeout))
        self._init()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=self.timeout)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={int(self.timeout * 1000)}")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _init(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runtime_sessions (
                    session_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    request TEXT NOT NULL,
                    model_profile TEXT,
                    tool_profile TEXT,
                    research_mode TEXT,
                    result_json TEXT NOT NULL,
                    unresolved_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    cancel_reason TEXT,
                    schema_version INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS runtime_session_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(session_id, sequence),
                    FOREIGN KEY(session_id) REFERENCES runtime_sessions(session_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS runtime_session_events_lookup
                    ON runtime_session_events(session_id, sequence DESC);
                CREATE INDEX IF NOT EXISTS runtime_sessions_updated
                    ON runtime_sessions(workspace_id, updated_at DESC);
                """
            )
            # Older Desktop/CLI databases predate cooperative cancellation.
            # Migrate in place so a reconnect never silently loses the flag.
            columns = {row[1] for row in connection.execute("PRAGMA table_info(runtime_sessions)")}
            if "cancel_requested" not in columns:
                connection.execute("ALTER TABLE runtime_sessions ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0")
            if "cancel_reason" not in columns:
                connection.execute("ALTER TABLE runtime_sessions ADD COLUMN cancel_reason TEXT")

    @staticmethod
    def _row_to_session(row: sqlite3.Row | None) -> RuntimeSession | None:
        if row is None:
            return None
        session = RuntimeSession(
            session_id=row["session_id"], workspace_id=row["workspace_id"], account_id=row["account_id"],
            mode=row["mode"], status=row["status"], request=row["request"],
            model_profile=row["model_profile"], tool_profile=row["tool_profile"],
            research_mode=row["research_mode"], result=_decode(row["result_json"], {}),
            unresolved=_decode(row["unresolved_json"], []), created_at=row["created_at"],
            updated_at=row["updated_at"], revision=row["revision"],
            cancel_requested=bool(row["cancel_requested"]) if "cancel_requested" in row.keys() else False,
            cancel_reason=row["cancel_reason"] if "cancel_reason" in row.keys() else None,
        )
        if "latest_turn_json" in row.keys() and row["latest_turn_json"]:
            latest = _decode(row["latest_turn_json"], {})
            latest_request = str(latest.get("request") or "") if isinstance(latest, dict) else ""
            # New rows store the full request in the envelope and a bounded
            # prefix in the event. Only reconcile legacy rows whose request
            # actually disagrees with the latest turn.
            if latest_request and not session.request.startswith(latest_request):
                session.request = latest_request
        return session

    def create_or_get(
        self,
        session_id: str | None = None,
        *,
        request: str = "",
        workspace_id: str = "default",
        account_id: str = "local",
        mode: str = "local",
        model_profile: str | None = None,
        tool_profile: str | None = None,
        research_mode: str | None = None,
    ) -> RuntimeSession:
        sid = str(session_id or f"session_{uuid.uuid4().hex}")[:160]
        now = _now()
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO runtime_sessions "
                "(session_id,workspace_id,account_id,mode,status,request,model_profile,tool_profile,research_mode,result_json,unresolved_json,created_at,updated_at,revision,schema_version) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, str(workspace_id or "default")[:512], str(account_id or "local")[:256], str(mode or "local")[:64],
                 "created", str(request or "")[:20_000], model_profile, tool_profile, research_mode, "{}", "[]", now, now, 0, SESSION_SCHEMA_VERSION),
            )
            row = connection.execute("SELECT * FROM runtime_sessions WHERE session_id=?", (sid,)).fetchone()
        session = self._row_to_session(row)
        if session is None:  # pragma: no cover - defensive sqlite failure guard
            raise RuntimeError("runtime session could not be created")
        return session

    def get(self, session_id: str) -> RuntimeSession | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT s.*, (SELECT e.payload_json FROM runtime_session_events e "
                "WHERE e.session_id=s.session_id AND e.kind='turn.started' ORDER BY e.sequence DESC LIMIT 1) AS latest_turn_json "
                "FROM runtime_sessions s WHERE s.session_id=?",
                (str(session_id),),
            ).fetchone()
        return self._row_to_session(row)

    def start_turn(
        self,
        session_id: str | None = None,
        *,
        request: str = "",
        workspace_id: str = "default",
        account_id: str = "local",
        mode: str = "local",
        model_profile: str | None = None,
        tool_profile: str | None = None,
        research_mode: str | None = None,
    ) -> RuntimeSession:
        """Create a session or refresh its envelope for a new conversation turn.

        Conversation IDs are intentionally stable across Desktop turns. A new
        turn must replace the prior request/result metadata and clear a prior
        cancellation without discarding the append-only event history.
        """
        session = self.create_or_get(
            session_id,
            request=request,
            workspace_id=workspace_id,
            account_id=account_id,
            mode=mode,
            model_profile=model_profile,
            tool_profile=tool_profile,
            research_mode=research_mode,
        )
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE runtime_sessions SET workspace_id=?,account_id=?,mode=?,status='created',request=?,"
                "model_profile=?,tool_profile=?,research_mode=?,result_json='{}',unresolved_json='[]',"
                "cancel_requested=0,cancel_reason=NULL,updated_at=? WHERE session_id=?",
                (
                    str(workspace_id or "default")[:512], str(account_id or "local")[:256], str(mode or "local")[:64],
                    str(request or "")[:20_000], model_profile, tool_profile, research_mode, now, session.session_id,
                ),
            )
            row = connection.execute("SELECT * FROM runtime_sessions WHERE session_id=?", (session.session_id,)).fetchone()
        refreshed = self._row_to_session(row)
        if refreshed is None:  # pragma: no cover - defensive sqlite failure guard
            raise RuntimeError("runtime session disappeared while starting a turn")
        return refreshed

    def list(self, *, workspace_id: str | None = None, limit: int = 50) -> list[RuntimeSession]:
        limit = max(1, min(int(limit), 500))
        query = (
            "SELECT s.*, (SELECT e.payload_json FROM runtime_session_events e "
            "WHERE e.session_id=s.session_id AND e.kind='turn.started' ORDER BY e.sequence DESC LIMIT 1) AS latest_turn_json "
            "FROM runtime_sessions s"
        )
        args: list[Any] = []
        if workspace_id is not None:
            query += " WHERE s.workspace_id=?"
            args.append(str(workspace_id))
        query += " ORDER BY s.updated_at DESC LIMIT ?"
        args.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, args).fetchall()
        return [item for row in rows if (item := self._row_to_session(row)) is not None]

    def append_event(self, session_id: str, kind: str, payload: Mapping[str, Any] | None = None) -> SessionEvent:
        kind = str(kind or "event").strip()[:120]
        if not kind:
            raise ValueError("event kind is required")
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT revision FROM runtime_sessions WHERE session_id=?", (session_id,)).fetchone()
            if row is None:
                raise KeyError(session_id)
            sequence = int(row["revision"]) + 1
            connection.execute(
                "INSERT INTO runtime_session_events(session_id,sequence,kind,payload_json,created_at) VALUES(?,?,?,?,?)",
                (session_id, sequence, kind, _json(dict(payload or {})), now),
            )
            connection.execute(
                "UPDATE runtime_sessions SET revision=?, updated_at=? WHERE session_id=?",
                (sequence, now, session_id),
            )
            connection.execute(
                "DELETE FROM runtime_session_events WHERE session_id=? AND sequence <= ?",
                (session_id, max(0, sequence - self.max_events)),
            )
        return SessionEvent(session_id, sequence, kind, dict(payload or {}), now)

    def events(self, session_id: str, *, after: int = 0, limit: int = 100) -> list[SessionEvent]:
        limit = max(1, min(int(limit), self.max_events))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT session_id,sequence,kind,payload_json,created_at FROM runtime_session_events "
                "WHERE session_id=? AND sequence>? ORDER BY sequence ASC LIMIT ?",
                (session_id, max(0, int(after)), limit),
            ).fetchall()
        return [SessionEvent(row["session_id"], row["sequence"], row["kind"], _decode(row["payload_json"], {}), row["created_at"]) for row in rows]

    def checkpoint(
        self,
        session_id: str,
        *,
        status: str | None = None,
        result: Mapping[str, Any] | None = None,
        unresolved: Iterable[str] | None = None,
        model_profile: str | None = None,
        tool_profile: str | None = None,
        research_mode: str | None = None,
        event: str | None = None,
        event_payload: Mapping[str, Any] | None = None,
    ) -> RuntimeSession:
        if status is not None and status not in SESSION_STATUSES:
            raise ValueError(f"invalid runtime session status: {status}")
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM runtime_sessions WHERE session_id=?", (session_id,)).fetchone()
            if row is None:
                raise KeyError(session_id)
            revision = int(row["revision"])
            cancel_requested = bool(row["cancel_requested"]) if "cancel_requested" in row.keys() else False
            next_status = status or row["status"]
            next_result = dict(result) if result is not None else _decode(row["result_json"], {})
            next_unresolved = [str(item)[:500] for item in unresolved] if unresolved is not None else _decode(row["unresolved_json"], [])
            # Cancellation wins races with a provider response or a late
            # completion callback.  A cancelled session can never be reported
            # as successfully completed by a stale writer.
            if cancel_requested and next_status != "cancelled":
                next_status = "cancelled"
                reason = str(row["cancel_reason"] or "cancelled by user")[:500]
                if reason not in next_unresolved:
                    next_unresolved.append(reason)
                if event and not event.endswith("cancelled"):
                    event = "turn.cancelled"
            connection.execute(
                "UPDATE runtime_sessions SET status=?, result_json=?, unresolved_json=?, model_profile=COALESCE(?,model_profile), tool_profile=COALESCE(?,tool_profile), research_mode=COALESCE(?,research_mode), updated_at=? WHERE session_id=?",
                (next_status, _json(next_result), _json(next_unresolved), model_profile, tool_profile, research_mode, now, session_id),
            )
            if event:
                revision += 1
                connection.execute(
                    "INSERT INTO runtime_session_events(session_id,sequence,kind,payload_json,created_at) VALUES(?,?,?,?,?)",
                    (session_id, revision, str(event)[:120], _json(dict(event_payload or {})), now),
                )
                connection.execute("UPDATE runtime_sessions SET revision=? WHERE session_id=?", (revision, session_id))
                connection.execute("DELETE FROM runtime_session_events WHERE session_id=? AND sequence <= ?", (session_id, max(0, revision - self.max_events)))
            final_row = connection.execute("SELECT * FROM runtime_sessions WHERE session_id=?", (session_id,)).fetchone()
        session = self._row_to_session(final_row)
        if session is None:  # pragma: no cover
            raise RuntimeError("runtime session disappeared during checkpoint")
        return session

    def request_cancel(self, session_id: str, reason: str = "cancelled by user") -> RuntimeSession:
        """Atomically request cancellation and publish a replayable event.

        The status is moved to ``cancelled`` immediately.  Running callers
        still poll ``should_cancel`` and terminate their provider/process work;
        the checkpoint guard above prevents a late completion from winning the
        race.  Repeated cancellation is idempotent and only adds no duplicate
        event.
        """
        reason = str(reason or "cancelled by user")[:500]
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM runtime_sessions WHERE session_id=?", (session_id,)).fetchone()
            if row is None:
                raise KeyError(session_id)
            status = str(row["status"])
            revision = int(row["revision"])
            if status in TERMINAL_SESSION_STATUSES and status != "cancelled":
                kind = "cancel.ignored"
                payload = {"reason": reason, "status": status}
                # Do not mutate a completed/failed/needs-input result.
                revision += 1
                connection.execute(
                    "UPDATE runtime_sessions SET revision=?, updated_at=? WHERE session_id=?", (revision, now, session_id)
                )
            elif bool(row["cancel_requested"]) and status == "cancelled":
                kind = None
                payload = {}
            else:
                status = "cancelled"
                revision += 1
                kind = "turn.cancel_requested"
                payload = {"reason": reason}
                connection.execute(
                    "UPDATE runtime_sessions SET status=?, cancel_requested=1, cancel_reason=?, unresolved_json=?, revision=?, updated_at=? WHERE session_id=?",
                    (status, reason, _json([reason]), revision, now, session_id),
                )
            if kind:
                connection.execute(
                    "INSERT INTO runtime_session_events(session_id,sequence,kind,payload_json,created_at) VALUES(?,?,?,?,?)",
                    (session_id, revision, kind, _json(payload), now),
                )
                connection.execute(
                    "DELETE FROM runtime_session_events WHERE session_id=? AND sequence <= ?",
                    (session_id, max(0, revision - self.max_events)),
                )
            final_row = connection.execute("SELECT * FROM runtime_sessions WHERE session_id=?", (session_id,)).fetchone()
        session = self._row_to_session(final_row)
        if session is None:  # pragma: no cover
            raise RuntimeError("runtime session disappeared during cancellation")
        return session

    def should_cancel(self, session_id: str) -> bool:
        session = self.get(session_id)
        return bool(session and session.cancel_requested)

    def snapshot(self, session_id: str, *, after: int = 0, limit: int = 100) -> dict[str, Any]:
        """Return a reconnect-safe envelope with a monotonic event cursor."""
        session = self.get(session_id)
        if session is None:
            raise KeyError(session_id)
        requested = max(0, int(after))
        events = self.events(session_id, after=requested, limit=limit)
        first = self.first_sequence(session_id)
        if requested and first and requested < first - 1:
            raise ValueError(f"event cursor {requested} is no longer replayable; earliest is {first}")
        next_cursor = events[-1].sequence if events else requested
        return {
            "version": SESSION_SCHEMA_VERSION,
            "session": session.to_dict(),
            "events": [event.to_dict() for event in events],
            "cursor": next_cursor,
            "next_cursor": next_cursor,
            "earliest_cursor": max(0, first - 1) if first else next_cursor,
            "has_more": bool(events and events[-1].sequence < session.revision),
            "reconnectable": session.status not in {"failed", "cancelled"} or bool(events),
        }

    def first_sequence(self, session_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT MIN(sequence) AS first FROM runtime_session_events WHERE session_id=?", (session_id,)).fetchone()
        return int(row["first"] or 0) if row else 0

    def resume(self, session_id: str) -> RuntimeSession:
        session = self.get(session_id)
        if session is None:
            raise KeyError(session_id)
        if session.status == "cancelled" or session.cancel_requested:
            raise ValueError("cancelled runtime sessions cannot be resumed")
        if session.status in {"completed", "failed"}:
            # Inspecting a terminal result is idempotent; a new turn should
            # explicitly call the owning run/chat endpoint with a new request.
            return session
        return self.checkpoint(session_id, event="turn.resumed", event_payload={"from_status": session.status})


def session_store_for_state(state_path: Path | str) -> SQLiteRuntimeSessionStore:
    """Use a stable sibling DB for both the CLI and packaged Desktop."""
    path = Path(state_path).expanduser().resolve()
    return SQLiteRuntimeSessionStore(path.with_name("runtime-sessions.sqlite3"))


def session_store_for_workspace(workspace: Path | str) -> SQLiteRuntimeSessionStore:
    """Return the canonical runtime ledger for a CLI/server workspace.

    Desktop uses :func:`session_store_for_state` because its packaged
    executor owns an AppData state file.  Both stores expose the same schema
    and protocol; workspace callers get a stable `.smara` location that is
    easy to back up and survives CLI restarts.
    """
    root = Path(workspace).expanduser().resolve()
    return SQLiteRuntimeSessionStore(root / ".smara" / "runtime-sessions.sqlite3")


__all__ = [
    "SESSION_SCHEMA_VERSION", "SESSION_STATUSES", "TERMINAL_SESSION_STATUSES",
    "SessionEvent", "RuntimeSession", "SQLiteRuntimeSessionStore", "session_store_for_state", "session_store_for_workspace",
]
