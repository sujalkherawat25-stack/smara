"""Versioned thread/turn/item protocol over the existing local SQLite ledger.

The protocol journal is retained independently of bounded legacy progress.
Approval records are receipts for the exact in-memory action, never commands
to execute on replay. Clients can reconnect through a monotonically increasing
thread cursor without re-running tools.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from typing import Any, Callable

from .runtime_session import SQLiteRuntimeSessionStore

PROTOCOL_VERSION = 1
ACTIVE = {"running", "waiting_approval"}
READ_TOOLS = frozenset({
    "file_read", "list_directory", "search_files", "code_graph", "calculate",
    "web_search", "web_extract", "skills_list", "skill_view", "todo",
    "local_file_read", "local_file_search", "local_system_info", "local_time",
    "process_poll", "browser_observe", "browser_tabs", "browser_scroll",
    "research_plan", "research_search", "research_fetch", "research_gather", "research_inspect",
    "research_analyze", "research_resolve", "research_validate", "research_ingest_file",
    "research_ingest_pdf_collection", "academic_search", "academic_fulltext",
    "browser_open", "browser_navigate", "browser_switch", "browser_close",
})


def _encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): "[redacted]" if re.search(r"secret|password|api_key|authorization|access_token|refresh_token", str(key), re.I)
                else _safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe(item) for item in value]
    if isinstance(value, str):
        return re.sub(r"\bsk_[A-Za-z0-9_\-]{16,}", "[redacted]", value)
    return value


def _owner_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5  # Access denied is not evidence of death.
        try:
            code = wintypes.DWORD()
            kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class SessionProtocol:
    def __init__(self, store: SQLiteRuntimeSessionStore):
        self.store = store
        with store._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS protocol_turns (
                  turn_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES runtime_sessions(session_id),
                  status TEXT NOT NULL, created_at REAL NOT NULL, ended_at REAL, owner_pid INTEGER);
                CREATE UNIQUE INDEX IF NOT EXISTS protocol_single_turn ON protocol_turns(thread_id)
                  WHERE status IN ('running','waiting_approval');
                CREATE TABLE IF NOT EXISTS protocol_items (
                  item_id TEXT PRIMARY KEY, turn_id TEXT NOT NULL REFERENCES protocol_turns(turn_id),
                  kind TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS protocol_events (
                  sequence INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT NOT NULL,
                  turn_id TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS protocol_replay ON protocol_events(thread_id,sequence);
                CREATE TABLE IF NOT EXISTS protocol_approvals (
                  approval_id TEXT PRIMARY KEY, turn_id TEXT NOT NULL REFERENCES protocol_turns(turn_id),
                  item_id TEXT NOT NULL, action_sha256 TEXT NOT NULL, action TEXT NOT NULL,
                  decision TEXT NOT NULL DEFAULT 'pending', created_at REAL NOT NULL);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(protocol_turns)")}
            if "owner_pid" not in columns:
                db.execute("ALTER TABLE protocol_turns ADD COLUMN owner_pid INTEGER")

    @staticmethod
    def _event(db, thread_id: str, turn_id: str, kind: str, payload: dict) -> dict:
        now = time.time()
        cursor = db.execute("INSERT INTO protocol_events(thread_id,turn_id,kind,payload,created_at) VALUES(?,?,?,?,?)",
                            (thread_id, turn_id, kind, _encode(payload), now)).lastrowid
        return {"version": PROTOCOL_VERSION, "thread_id": thread_id, "turn_id": turn_id,
                "sequence": cursor, "kind": kind, "payload": payload, "created_at": now}

    def begin(self, thread_id: str, request: str, *, notify=None, approval_mode="auto", approval_handler=None) -> "ProtocolTurn":
        if approval_mode not in {"auto", "ask"}:
            raise ValueError("approval_mode must be auto or ask")
        if not isinstance(request, str) or not request.strip():
            raise ValueError("request must be nonempty text")
        turn_id = f"turn_{uuid.uuid4().hex}"
        with self.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM runtime_sessions WHERE session_id=?", (thread_id,)).fetchone():
                raise KeyError(thread_id)
            active = db.execute("SELECT turn_id,owner_pid FROM protocol_turns WHERE thread_id=? AND status IN ('running','waiting_approval')", (thread_id,)).fetchone()
            if active:
                if not active["owner_pid"] or _owner_alive(active["owner_pid"]):
                    raise ValueError("This thread already has an active turn. Interrupt it before starting another.")
                self._end(db, thread_id, active["turn_id"], "needs_input")
                self._event(db, thread_id, active["turn_id"], "turn.interrupted", {"reason": "owning process exited; unfinished tools are not replayed"})
            db.execute("INSERT INTO protocol_turns VALUES(?,?,?, ?,NULL,?)", (turn_id, thread_id, "running", time.time(), os.getpid()))
            event = self._event(db, thread_id, turn_id, "turn.started", {"approval_mode": approval_mode})
        turn = ProtocolTurn(self, thread_id, turn_id, notify, approval_mode, approval_handler)
        turn.emit(event)
        item = turn.item("user_message", {"text": _safe(request)})
        turn.complete_item(item, {"text": _safe(request)})
        return turn

    def snapshot(self, thread_id: str, *, after: int = 0, limit: int = 100) -> dict:
        if self.store.get(thread_id) is None:
            raise KeyError(thread_id)
        after, limit = max(0, int(after)), max(1, min(int(limit), 500))
        with self.store._connect() as db:
            db.execute("BEGIN")
            latest = db.execute("SELECT COALESCE(MAX(sequence),0) FROM protocol_events WHERE thread_id=?", (thread_id,)).fetchone()[0]
            if after > latest:
                raise ValueError("Event cursor is ahead of this thread's journal")
            rows = db.execute("SELECT * FROM protocol_events WHERE thread_id=? AND sequence>? ORDER BY sequence LIMIT ?", (thread_id, after, limit)).fetchall()
            turns = [dict(row) for row in db.execute("SELECT * FROM protocol_turns WHERE thread_id=? ORDER BY created_at DESC LIMIT 50", (thread_id,))]
            current = next((turn for turn in turns if turn["status"] in ACTIVE), turns[0] if turns else None)
            items, approvals = [], []
            if current:
                items = [{**dict(row), "payload": json.loads(row["payload"])} for row in db.execute("SELECT * FROM protocol_items WHERE turn_id=? ORDER BY rowid LIMIT 500", (current["turn_id"],))]
                approvals = [{**dict(row), "action": json.loads(row["action"])} for row in db.execute("SELECT * FROM protocol_approvals WHERE turn_id=? AND decision='pending'", (current["turn_id"],))]
            events = [{"version": PROTOCOL_VERSION, **dict(row), "payload": json.loads(row["payload"])} for row in rows]
            cursor = rows[-1]["sequence"] if rows else after
        can_resume = bool(current and current["status"] not in {"completed", "cancelled"}
                          and not (current["status"] in ACTIVE and (not current["owner_pid"] or _owner_alive(current["owner_pid"])))
                          and any(item["kind"] == "execution_checkpoint" for item in items))
        return {"version": PROTOCOL_VERSION, "thread_id": thread_id, "turns": turns, "items": items, "can_resume": can_resume,
                "pending_approvals": approvals, "events": events, "cursor": cursor, "has_more": cursor < latest}

    def respond(self, thread_id: str, approval_id: str, turn_id: str, action_sha256: str, decision: str) -> dict:
        if decision not in {"allow", "deny"}:
            raise ValueError("decision must be allow or deny")
        with self.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT a.*,t.thread_id,t.status,t.owner_pid FROM protocol_approvals a JOIN protocol_turns t USING(turn_id) WHERE approval_id=?", (approval_id,)).fetchone()
            if not row or row["thread_id"] != thread_id or row["turn_id"] != turn_id or row["action_sha256"] != action_sha256:
                raise ValueError("Approval does not match this thread, turn and action")
            session = db.execute("SELECT cancel_requested FROM runtime_sessions WHERE session_id=?", (thread_id,)).fetchone()
            if row["status"] not in ACTIVE or session[0] or (row["owner_pid"] and not _owner_alive(row["owner_pid"])):
                raise ValueError("Approval is no longer active")
            if row["decision"] != "pending":
                if row["decision"] != decision:
                    raise ValueError("Approval already has a different decision")
                return {"approval_id": approval_id, "decision": decision}
            db.execute("UPDATE protocol_approvals SET decision=? WHERE approval_id=?", (decision, approval_id))
            self._event(db, thread_id, turn_id, "approval.resolved", {"approval_id": approval_id, "decision": decision})
        return {"approval_id": approval_id, "decision": decision}

    def history(self, thread_id: str, *, limit: int = 32) -> list[dict]:
        with self.store._connect() as db:
            rows = db.execute("SELECT i.kind,i.payload FROM protocol_items i JOIN protocol_turns t USING(turn_id) WHERE t.thread_id=? AND i.status='completed' AND i.kind IN ('user_message','agent_message') ORDER BY i.rowid DESC LIMIT ?", (thread_id, max(1, min(limit, 100)))).fetchall()
        return [{"role": "user" if row["kind"] == "user_message" else "assistant", "content": json.loads(row["payload"]).get("text", "")} for row in reversed(rows)]

    def recovery(self, thread_id: str, workspace) -> dict:
        """Resolve the latest turn's execution checkpoint, not a new task."""
        from pathlib import Path
        from .harness import SessionEngine
        with self.store._connect() as db:
            turn = db.execute("SELECT * FROM protocol_turns WHERE thread_id=? ORDER BY created_at DESC LIMIT 1", (thread_id,)).fetchone()
            if not turn:
                raise ValueError("This thread has no turn to resume")
            if turn["status"] in ACTIVE and (not turn["owner_pid"] or _owner_alive(turn["owner_pid"])):
                raise ValueError("The turn is still running; reconnect instead of resuming")
            item = db.execute("SELECT payload FROM protocol_items WHERE turn_id=? AND kind='execution_checkpoint' AND status='completed' ORDER BY rowid DESC LIMIT 1", (turn["turn_id"],)).fetchone()
        if not item:
            raise ValueError("This turn has no canonical checkpoint; use Retry as a new turn")
        checkpoint = json.loads(item[0])
        root = Path(workspace).resolve()
        if root != Path(checkpoint["workspace"]).resolve():
            raise ValueError("Resume workspace does not match the original execution")
        session_id = str(checkpoint["session_id"])
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id) or not (root / ".smara" / "sessions" / f"{session_id}.sqlite3").is_file():
            raise ValueError("Execution checkpoint is missing")
        session = SessionEngine(root, session_id)
        try:
            return {**checkpoint, "request": session.get("request"),
                    "tool_profile": session.get("tool_profile") or "full",
                    "research_mode": session.get("research_mode") or "auto"}
        finally:
            session.close()

    def interrupt(self, thread_id: str) -> dict:
        self.store.request_cancel(thread_id)
        with self.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT turn_id FROM protocol_turns WHERE thread_id=? AND status IN ('running','waiting_approval')", (thread_id,)).fetchall()
            for row in rows:
                self._end(db, thread_id, row[0], "cancelled")
        return self.snapshot(thread_id)

    def dispatch(self, method: str, params: dict | None = None) -> dict:
        params = params or {}
        if not isinstance(params, dict):
            raise ValueError("params must be an object")
        if method == "initialize":
            return {"version": PROTOCOL_VERSION, "capabilities": ["threads", "turns", "items", "event_replay", "approvals", "interrupt", "checkpoint_resume"]}
        if method == "thread/create":
            session = self.store.create_or_get(params.get("thread_id"), workspace_id=str(params.get("workspace") or "default"))
            return {"thread": session.to_dict()}
        if method == "thread/list":
            return {"threads": [{**session.to_dict(), "request": session.request[:600], "result": {}}
                                for session in self.store.list(limit=int(params.get("limit", 50)))]}
        thread_id = str(params.get("thread_id") or "")
        if not thread_id:
            raise ValueError("thread_id is required")
        if method == "approval/list":
            with self.store._connect() as db:
                row = db.execute("SELECT cancel_requested FROM runtime_sessions WHERE session_id=?", (thread_id,)).fetchone()
                if row is None:
                    raise KeyError(thread_id)
                approvals = [] if row[0] else [{**dict(item), "action": json.loads(item["action"])} for item in db.execute("SELECT a.* FROM protocol_approvals a JOIN protocol_turns t USING(turn_id) WHERE t.thread_id=? AND t.status IN ('running','waiting_approval') AND a.decision='pending'", (thread_id,))]
            return {"thread_id": thread_id, "pending_approvals": approvals}
        if method in {"thread/read", "thread/resume", "thread/events"}:
            return {"thread": self.store.get(thread_id).to_dict() if self.store.get(thread_id) else None,
                    **self.snapshot(thread_id, after=int(params.get("after", 0)), limit=int(params.get("limit", 100)))}
        if method == "turn/interrupt":
            return self.interrupt(thread_id)
        if method == "approval/respond":
            return self.respond(thread_id, str(params.get("approval_id") or ""), str(params.get("turn_id") or ""),
                                str(params.get("action_sha256") or ""), str(params.get("decision") or ""))
        raise ValueError(f"Unknown session method: {method}")

    @classmethod
    def _end(cls, db, thread_id, turn_id, status):
        db.execute("UPDATE protocol_turns SET status=?,ended_at=? WHERE turn_id=?", (status, time.time(), turn_id))
        db.execute("UPDATE protocol_approvals SET decision='expired' WHERE turn_id=? AND decision='pending'", (turn_id,))
        for row in db.execute("SELECT item_id FROM protocol_items WHERE turn_id=? AND status='running'", (turn_id,)).fetchall():
            db.execute("UPDATE protocol_items SET status=? WHERE item_id=?", ("cancelled" if status == "cancelled" else "failed", row[0]))
            cls._event(db, thread_id, turn_id, "item.completed", {"item_id": row[0], "status": "cancelled" if status == "cancelled" else "failed"})
        return cls._event(db, thread_id, turn_id, "turn.completed", {"status": status})


class ProtocolTurn:
    def __init__(self, protocol, thread_id, turn_id, notify, approval_mode, approval_handler):
        self.protocol, self.thread_id, self.turn_id = protocol, thread_id, turn_id
        self.notify, self.approval_mode, self.approval_handler = notify, approval_mode, approval_handler
        self.lock = threading.RLock()
        self.message_id = None

    def emit(self, event):
        if self.notify:
            self.notify({"type": "session_event", "event": event})

    def bind_execution(self, session_id, workspace):
        item = self.item("execution_checkpoint", {"session_id": session_id, "workspace": str(workspace)})
        self.complete_item(item, {"session_id": session_id, "workspace": str(workspace)})

    def item(self, kind: str, payload: dict) -> str:
        item_id = f"item_{uuid.uuid4().hex}"
        with self.protocol.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT status FROM protocol_turns WHERE turn_id=?", (self.turn_id,)).fetchone()[0] not in ACTIVE:
                raise RuntimeError("Turn is no longer active")
            db.execute("INSERT INTO protocol_items VALUES(?,?,?,?,?)", (item_id, self.turn_id, kind, "running", _encode(_safe(payload))))
            event = self.protocol._event(db, self.thread_id, self.turn_id, "item.started", {"item_id": item_id, "kind": kind, **_safe(payload)})
        self.emit(event)
        return item_id

    def complete_item(self, item_id, payload, status="completed"):
        with self.protocol.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            updated = db.execute("UPDATE protocol_items SET status=?,payload=? WHERE item_id=? AND turn_id=? AND status='running'", (status, _encode(_safe(payload)), item_id, self.turn_id)).rowcount
            if not updated:
                return
            event = self.protocol._event(db, self.thread_id, self.turn_id, "item.completed", {"item_id": item_id, "status": status, **_safe(payload)})
        self.emit(event)

    def execute(self, name: str, arguments: dict, action: Callable[[], Any]):
        item_id = self.item("tool_execution", {"name": name, "arguments": arguments})
        try:
            if self.approval_mode == "ask" and name not in READ_TOOLS:
                self._approve(item_id, name, arguments)
            if self.protocol.store.should_cancel(self.thread_id):
                raise RuntimeError("Turn cancelled before tool execution")
            result = action()
            failed = isinstance(result, dict) and (result.get("error") or result.get("ok") is False or result.get("success") is False)
            if isinstance(result, str):
                failed = result.startswith(("Error:", "Denied:", "File Read Error:"))
            self.complete_item(item_id, {"name": name, "result": result}, "failed" if failed else "completed")
            return result
        except Exception as exc:
            self.complete_item(item_id, {"name": name, "error": str(exc)}, "failed")
            raise

    def _approve(self, item_id, name, arguments, *, timeout=300.0):
        action = {"name": name, "arguments": arguments}
        digest = hashlib.sha256(_encode(action).encode()).hexdigest()
        approval_id = f"approval_{uuid.uuid4().hex}"
        payload = {"approval_id": approval_id, "item_id": item_id, "turn_id": self.turn_id,
                   "action_sha256": digest, "action": _safe(action)}
        with self.protocol.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT status FROM protocol_turns WHERE turn_id=?", (self.turn_id,)).fetchone()[0] not in ACTIVE:
                raise RuntimeError("Turn is no longer active")
            db.execute("INSERT INTO protocol_approvals VALUES(?,?,?,?,?,'pending',?)", (approval_id, self.turn_id, item_id, digest, _encode(_safe(action)), time.time()))
            db.execute("UPDATE protocol_turns SET status='waiting_approval' WHERE turn_id=?", (self.turn_id,))
            event = self.protocol._event(db, self.thread_id, self.turn_id, "approval.requested", payload)
        self.protocol.store.checkpoint(self.thread_id, status="waiting_approval", expected_turn_id=self.turn_id)
        self.emit(event)
        if self.approval_handler:
            decision = self.approval_handler(payload)
            self.protocol.respond(self.thread_id, approval_id, self.turn_id, digest, decision)
        deadline = time.monotonic() + timeout
        while True:
            if self.protocol.store.should_cancel(self.thread_id):
                raise RuntimeError("Turn cancelled while awaiting approval")
            with self.protocol.store._connect() as db:
                if db.execute("SELECT status FROM protocol_turns WHERE turn_id=?", (self.turn_id,)).fetchone()[0] not in ACTIVE:
                    raise RuntimeError("Turn is no longer active")
                decision = db.execute("SELECT decision FROM protocol_approvals WHERE approval_id=?", (approval_id,)).fetchone()[0]
            if decision != "pending":
                break
            if time.monotonic() >= deadline:
                with self.protocol.store._connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    changed = db.execute("UPDATE protocol_approvals SET decision='expired' WHERE approval_id=? AND decision='pending'", (approval_id,)).rowcount
                    if changed:
                        self.protocol._event(db, self.thread_id, self.turn_id, "approval.resolved", {"approval_id": approval_id, "decision": "expired"})
                    decision = db.execute("SELECT decision FROM protocol_approvals WHERE approval_id=?", (approval_id,)).fetchone()[0]
                break
            time.sleep(0.1)
        with self.protocol.store._connect() as db:
            pending = db.execute("SELECT COUNT(*) FROM protocol_approvals WHERE turn_id=? AND decision='pending'", (self.turn_id,)).fetchone()[0]
            if not pending:
                db.execute("UPDATE protocol_turns SET status='running' WHERE turn_id=? AND status='waiting_approval'", (self.turn_id,))
        if not pending:
            self.protocol.store.checkpoint(self.thread_id, status="running", expected_turn_id=self.turn_id)
        if decision != "allow":
            raise RuntimeError(f"Action approval {decision}; tool was not executed")

    def finish(self, status: str, answer: str = ""):
        if status in ACTIVE or status == "created":
            status = "needs_input"
        if status not in {"completed", "failed", "cancelled", "needs_input"}:
            status = "failed"
        with self.lock:
            events = []
            with self.protocol.store._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT status FROM protocol_turns WHERE turn_id=?", (self.turn_id,)).fetchone()
                if row[0] not in ACTIVE:
                    return
                if answer:
                    payload = {"text": _safe(answer)}
                    if self.message_id is None:
                        self.message_id = f"item_{uuid.uuid4().hex}"
                        db.execute("INSERT INTO protocol_items VALUES(?,?,?,?,?)", (self.message_id, self.turn_id, "agent_message", "running", _encode(payload)))
                        events.append(self.protocol._event(db, self.thread_id, self.turn_id, "item.started", {"item_id": self.message_id, "kind": "agent_message", **payload}))
                    db.execute("UPDATE protocol_items SET status='completed',payload=? WHERE item_id=?", (_encode(payload), self.message_id))
                    events.append(self.protocol._event(db, self.thread_id, self.turn_id, "item.completed", {"item_id": self.message_id, "status": "completed", **payload}))
                cancelled = db.execute("SELECT cancel_requested FROM runtime_sessions WHERE session_id=?", (self.thread_id,)).fetchone()[0]
                events.append(self.protocol._end(db, self.thread_id, self.turn_id, "cancelled" if cancelled else status))
            for event in events:
                self.emit(event)

    def progress(self, kind: str, data: dict):
        if kind == "answer" and data.get("answer"):
            self.text_delta(str(data["answer"]))
            return
        # Only user-visible progress; provider reasoning is not journaled.
        if kind not in {"context_packed", "model_request", "model_retry"}:
            return
        with self.protocol.store._connect() as db:
            event = self.protocol._event(db, self.thread_id, self.turn_id,
                                        "turn.context" if kind == "context_packed" else "turn.progress",
                                        {"phase": kind, **_safe(data)})
        self.emit(event)

    def text_delta(self, text: str):
        with self.lock:
            if self.message_id is None:
                self.message_id = self.item("agent_message", {"text": ""})
            with self.protocol.store._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT payload,status FROM protocol_items WHERE item_id=?", (self.message_id,)).fetchone()
                if row["status"] != "running":
                    return
                payload = json.loads(row["payload"])
                payload["text"] = payload.get("text", "") + _safe(text)
                db.execute("UPDATE protocol_items SET payload=? WHERE item_id=?", (_encode(payload), self.message_id))
                event = self.protocol._event(db, self.thread_id, self.turn_id, "item.delta", {"item_id": self.message_id, "text": _safe(text)})
            self.emit(event)
