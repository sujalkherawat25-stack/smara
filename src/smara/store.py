from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
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
            CREATE TABLE IF NOT EXISTS task_runs (
              id TEXT PRIMARY KEY, task_id TEXT NOT NULL, attempt INTEGER NOT NULL,
              status TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(task_id,attempt));
            CREATE TABLE IF NOT EXISTS task_steps (
              id TEXT PRIMARY KEY, task_id TEXT NOT NULL, task_run_id TEXT NOT NULL,
              ordinal INTEGER NOT NULL, name TEXT NOT NULL, status TEXT NOT NULL,
              idempotency_key TEXT NOT NULL UNIQUE, lease_owner TEXT, lease_expires_at TEXT,
              created_at TEXT NOT NULL, UNIQUE(task_run_id,ordinal));
            CREATE TABLE IF NOT EXISTS task_step_dependencies (
              step_id TEXT NOT NULL, depends_on_step_id TEXT NOT NULL,
              PRIMARY KEY(step_id,depends_on_step_id), CHECK(step_id <> depends_on_step_id));
            """)

    def create(self, account_id: str, workspace_id: str, title: str, objective: str, requires_approval: bool, steps: list[dict] | None = None) -> dict:
        task_id, now = f"task_{uuid.uuid4().hex}", _now()
        steps = steps or [{"name": "execute_task", "depends_on": []}]
        with self._connect() as c:
            c.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)", (task_id, account_id, workspace_id, title, objective, "queued", int(requires_approval), now, now))
            run_id = f"run_{uuid.uuid4().hex}"
            c.execute("INSERT INTO task_runs VALUES(?,?,?,?,?)", (run_id, task_id, 1, "queued", now))
            step_ids: list[str] = []
            for ordinal, step in enumerate(steps, start=1):
                step_id = f"step_{uuid.uuid4().hex}"; step_ids.append(step_id)
                c.execute("INSERT INTO task_steps VALUES(?,?,?,?,?,?,?,?,?,?)", (step_id, task_id, run_id, ordinal, step["name"], "queued", f"task:{task_id}:step:{ordinal}", None, None, now))
            for ordinal, step in enumerate(steps):
                for parent in step.get("depends_on", []):
                    c.execute("INSERT INTO task_step_dependencies VALUES(?,?)", (step_ids[ordinal], step_ids[parent]))
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

    def steps(self, task_id: str, account_id: str) -> list[dict]:
        self.get(task_id, account_id)
        with self._connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM task_steps WHERE task_id=? ORDER BY ordinal", (task_id,))]

    def decide(self, task_id: str, account_id: str, approved: bool, note: str) -> dict:
        self.get(task_id, account_id); now = _now(); status = "approved" if approved else "denied"
        with self._connect() as c:
            c.execute("INSERT INTO approvals(task_id,status,note,decided_at) VALUES(?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET status=excluded.status,note=excluded.note,decided_at=excluded.decided_at", (task_id, status, note, now))
            next_status = "queued" if approved else "cancelled"
            c.execute("UPDATE tasks SET status=?,requires_approval=?,updated_at=? WHERE id=?", (next_status, 0 if approved else 1, now, task_id))
            c.execute("INSERT INTO task_events VALUES(?,?,?,?,?)", (f"evt_{uuid.uuid4().hex}", task_id, f"approval.{status}", '{"source":"user"}', now))
        return self.get(task_id, account_id)

    def claim_one(self, worker_id: str = "worker", lease_seconds: int = 60) -> dict | None:
        with self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            now = _now()
            # A dead worker never owns work forever. Recovery requeues only the
            # step; idempotency remains attached to the external action.
            expired = c.execute("SELECT id,task_id FROM task_steps WHERE status='running' AND lease_expires_at < ?", (now,)).fetchall()
            for item in expired:
                c.execute("UPDATE task_steps SET status='queued',lease_owner=NULL,lease_expires_at=NULL WHERE id=?", (item["id"],))
                c.execute("UPDATE tasks SET status='queued',updated_at=? WHERE id=? AND status='running'", (now, item["task_id"]))
                c.execute("INSERT INTO task_events VALUES(?,?,?,?,?)", (f"evt_{uuid.uuid4().hex}", item["task_id"], "step.lease_expired", '{"recovered":true}', now))
            row = c.execute("""SELECT t.*, s.id AS step_id, s.task_run_id, s.idempotency_key, s.name
              FROM tasks t JOIN task_steps s ON s.task_id=t.id
              WHERE t.status IN ('queued','running') AND s.status='queued' AND NOT EXISTS (
                SELECT 1 FROM task_step_dependencies d JOIN task_steps parent ON parent.id=d.depends_on_step_id
                WHERE d.step_id=s.id AND parent.status!='completed')
              ORDER BY t.created_at,s.ordinal LIMIT 1""").fetchone()
            if not row: return None
            task = dict(row)
            if task["requires_approval"] and task["status"] == "queued":
                c.execute("UPDATE tasks SET status='waiting_approval',updated_at=? WHERE id=?", (now, task["id"]))
                typ = "approval.requested"
                task["status"] = "waiting_approval"
            else:
                until = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()
                c.execute("UPDATE tasks SET status='running',updated_at=? WHERE id=?", (now, task["id"]))
                c.execute("UPDATE task_runs SET status='running' WHERE id=?", (task["task_run_id"],))
                c.execute("UPDATE task_steps SET status='running',lease_owner=?,lease_expires_at=? WHERE id=?", (worker_id, until, task["step_id"]))
                typ = "task.started"
                task["status"] = "running"
                task["lease_owner"] = worker_id
            c.execute("INSERT INTO task_events VALUES(?,?,?,?,?)", (f"evt_{uuid.uuid4().hex}", task["id"], typ, '{"source":"worker"}', now))
            task["updated_at"] = now
            return task

    def complete_step(self, step_id: str, account_id: str, result: str) -> None:
        now = _now()
        with self._connect() as c:
            row = c.execute("SELECT task_id,task_run_id FROM task_steps WHERE id=? AND task_id IN (SELECT id FROM tasks WHERE account_id=?)", (step_id, account_id)).fetchone()
            if not row: raise KeyError(step_id)
            task_id, run_id = row["task_id"], row["task_run_id"]
            c.execute("UPDATE task_steps SET status='completed',lease_owner=NULL,lease_expires_at=NULL WHERE id=? AND status='running'", (step_id,))
            c.execute("INSERT INTO task_events VALUES(?,?,?,?,?)", (f"evt_{uuid.uuid4().hex}", task_id, "step.completed", '{"result":"recorded"}', now))
            pending = c.execute("SELECT COUNT(*) FROM task_steps WHERE task_run_id=? AND status!='completed'", (run_id,)).fetchone()[0]
            if pending == 0:
                c.execute("UPDATE task_runs SET status='completed' WHERE id=?", (run_id,))
                c.execute("UPDATE tasks SET status='completed',updated_at=? WHERE id=?", (now, task_id))
                c.execute("INSERT INTO task_events VALUES(?,?,?,?,?)", (f"evt_{uuid.uuid4().hex}", task_id, "task.completed", '{"result":"recorded"}', now))
