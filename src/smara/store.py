from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskStore:
    """Small durable store. One database owns tasks, events and approvals."""
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._init()

    def _connect(self):
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    def _init(self):
        with self._connect() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
              id TEXT PRIMARY KEY, account_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
              title TEXT NOT NULL, objective TEXT NOT NULL, status TEXT NOT NULL,
              requires_approval INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS task_events (
              id TEXT PRIMARY KEY, task_id TEXT NOT NULL, type TEXT NOT NULL,
              payload TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS approvals (
              task_id TEXT PRIMARY KEY, status TEXT NOT NULL, note TEXT NOT NULL,
              decided_at TEXT, FOREIGN KEY(task_id) REFERENCES tasks(id));
            """)

    def create(self, account_id: str, workspace_id: str, title: str, objective: str, requires_approval: bool) -> dict:
        task_id, now = f"task_{uuid.uuid4().hex}", _now()
        with self._connect() as c:
            c.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)", (task_id, account_id, workspace_id, title, objective, "queued", int(requires_approval), now, now))
            c.execute("INSERT INTO task_events VALUES(?,?,?,?,?)", (f"evt_{uuid.uuid4().hex}", task_id, "task.created", '{"source":"api"}', now))
        return self.get(task_id, account_id)

    def get(self, task_id: str, account_id: str) -> dict:
        with self._connect() as c:
            row = c.execute("SELECT * FROM tasks WHERE id=? AND account_id=?", (task_id, account_id)).fetchone()
        if not row: raise KeyError(task_id)
        return dict(row)

    def list(self, account_id: str) -> list[dict]:
        with self._connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM tasks WHERE account_id=? ORDER BY created_at DESC", (account_id,))]

    def events(self, task_id: str, account_id: str) -> list[dict]:
        self.get(task_id, account_id)
        with self._connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM task_events WHERE task_id=? ORDER BY created_at", (task_id,))]

    def decide(self, task_id: str, account_id: str, approved: bool, note: str) -> dict:
        self.get(task_id, account_id); now = _now(); status = "approved" if approved else "denied"
        with self._connect() as c:
            c.execute("INSERT INTO approvals(task_id,status,note,decided_at) VALUES(?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET status=excluded.status,note=excluded.note,decided_at=excluded.decided_at", (task_id, status, note, now))
            next_status = "queued" if approved else "cancelled"
            c.execute("UPDATE tasks SET status=?,updated_at=? WHERE id=?", (next_status, now, task_id))
            c.execute("INSERT INTO task_events VALUES(?,?,?,?,?)", (f"evt_{uuid.uuid4().hex}", task_id, f"approval.{status}", '{"source":"user"}', now))
        return self.get(task_id, account_id)

    def claim_one(self) -> dict | None:
        with self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM tasks WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if not row: return None
            task = dict(row)
            if task["requires_approval"]:
                c.execute("UPDATE tasks SET status='waiting_approval',updated_at=? WHERE id=?", (_now(), task["id"]))
                typ = "approval.requested"
                task["status"] = "waiting_approval"
            else:
                c.execute("UPDATE tasks SET status='running',updated_at=? WHERE id=?", (_now(), task["id"]))
                typ = "task.started"
                task["status"] = "running"
            c.execute("INSERT INTO task_events VALUES(?,?,?,?,?)", (f"evt_{uuid.uuid4().hex}", task["id"], typ, '{"source":"worker"}', _now()))
            task["updated_at"] = _now()
            return task

    def complete(self, task_id: str, account_id: str, result: str) -> None:
        now = _now()
        with self._connect() as c:
            c.execute("UPDATE tasks SET status='completed',updated_at=? WHERE id=? AND account_id=?", (now, task_id, account_id))
            c.execute("INSERT INTO task_events VALUES(?,?,?,?,?)", (f"evt_{uuid.uuid4().hex}", task_id, "task.completed", '{"result":"recorded"}', now))
