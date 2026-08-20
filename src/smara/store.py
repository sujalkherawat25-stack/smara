from __future__ import annotations

import hashlib
import json
import secrets
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
              requires_approval INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              cancel_requested INTEGER NOT NULL DEFAULT 0);
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
              created_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
              max_attempts INTEGER NOT NULL DEFAULT 3, retry_at TEXT, last_error TEXT,
              required_capability TEXT, executor_kind TEXT NOT NULL DEFAULT 'hosted',
              executor_payload TEXT NOT NULL DEFAULT '{}',
              UNIQUE(task_run_id,ordinal));
            CREATE TABLE IF NOT EXISTS task_step_dependencies (
              step_id TEXT NOT NULL, depends_on_step_id TEXT NOT NULL,
              PRIMARY KEY(step_id,depends_on_step_id), CHECK(step_id <> depends_on_step_id));
            CREATE TABLE IF NOT EXISTS research_evidence (
              id TEXT PRIMARY KEY, task_id TEXT NOT NULL, url TEXT NOT NULL,
              title TEXT, status TEXT NOT NULL, retrieved_at TEXT,
              content_sha256 TEXT, excerpt TEXT, claim TEXT, confidence REAL,
              citation_label TEXT, error TEXT, created_at TEXT NOT NULL,
              UNIQUE(task_id,url));
            CREATE TABLE IF NOT EXISTS artifacts (
              id TEXT PRIMARY KEY, task_id TEXT NOT NULL, kind TEXT NOT NULL,
              name TEXT NOT NULL, uri TEXT NOT NULL, sha256 TEXT, content TEXT,
              created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS desktop_executors (
              id TEXT PRIMARY KEY, account_id TEXT NOT NULL, name TEXT NOT NULL,
              capabilities TEXT NOT NULL, token_hash TEXT NOT NULL, status TEXT NOT NULL,
              last_seen_at TEXT, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS executor_pairings (
              code_hash TEXT PRIMARY KEY, account_id TEXT NOT NULL, name TEXT NOT NULL,
              capabilities TEXT NOT NULL, expires_at TEXT NOT NULL, consumed_at TEXT);
            CREATE TABLE IF NOT EXISTS executor_leases (
              id TEXT PRIMARY KEY, step_id TEXT NOT NULL UNIQUE, executor_id TEXT NOT NULL,
              expires_at TEXT NOT NULL, completed_at TEXT, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS integration_connections (
              id TEXT PRIMARY KEY, account_id TEXT NOT NULL, provider TEXT NOT NULL,
              display_name TEXT NOT NULL DEFAULT '', policy TEXT NOT NULL,
              granted_scopes TEXT NOT NULL DEFAULT '[]', health TEXT NOT NULL,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(account_id,provider));
            CREATE TABLE IF NOT EXISTS integration_action_log (
              id TEXT PRIMARY KEY, account_id TEXT NOT NULL, connection_id TEXT NOT NULL,
              action TEXT NOT NULL, preview TEXT NOT NULL, idempotency_key TEXT NOT NULL,
              risk TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}',
              approval_note TEXT NOT NULL DEFAULT '', lease_owner TEXT, lease_expires_at TEXT,
              attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, result_summary TEXT, created_at TEXT NOT NULL,
              UNIQUE(account_id,idempotency_key));
            CREATE TABLE IF NOT EXISTS integration_credentials (
              connection_id TEXT PRIMARY KEY, kind TEXT NOT NULL, encrypted_secret TEXT NOT NULL,
              updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS integration_oauth_states (
              state_hash TEXT PRIMARY KEY, account_id TEXT NOT NULL, provider TEXT NOT NULL,
              code_verifier TEXT NOT NULL, expires_at TEXT NOT NULL, consumed_at TEXT);
            CREATE TABLE IF NOT EXISTS push_subscriptions (
              endpoint TEXT PRIMARY KEY, account_id TEXT NOT NULL, p256dh TEXT NOT NULL,
              auth TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS task_dead_letters (
              id TEXT PRIMARY KEY, task_id TEXT NOT NULL, step_id TEXT NOT NULL,
              account_id TEXT NOT NULL, error TEXT NOT NULL, attempts INTEGER NOT NULL,
              created_at TEXT NOT NULL, resolved_at TEXT);
            """)
            # Backward-compatible local development upgrades for databases
            # created before retry state existed.
            columns = {row[1] for row in c.execute("PRAGMA table_info(task_steps)")}
            for name, definition in (("attempts", "INTEGER NOT NULL DEFAULT 0"), ("max_attempts", "INTEGER NOT NULL DEFAULT 3"), ("retry_at", "TEXT"), ("last_error", "TEXT")):
                if name not in columns:
                    c.execute(f"ALTER TABLE task_steps ADD COLUMN {name} {definition}")
            for name, definition in (("required_capability", "TEXT"), ("executor_kind", "TEXT NOT NULL DEFAULT 'hosted'"), ("executor_payload", "TEXT NOT NULL DEFAULT '{}'")):
                if name not in columns:
                    c.execute(f"ALTER TABLE task_steps ADD COLUMN {name} {definition}")
            task_columns = {row[1] for row in c.execute("PRAGMA table_info(tasks)")}
            if "cancel_requested" not in task_columns:
                c.execute("ALTER TABLE tasks ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0")
            action_columns = {row[1] for row in c.execute("PRAGMA table_info(integration_action_log)")}
            for name, definition in (("payload", "TEXT NOT NULL DEFAULT '{}'"), ("approval_note", "TEXT NOT NULL DEFAULT ''"), ("lease_owner", "TEXT"), ("lease_expires_at", "TEXT"), ("attempts", "INTEGER NOT NULL DEFAULT 0"), ("last_error", "TEXT"), ("result_summary", "TEXT")):
                if name not in action_columns:
                    c.execute(f"ALTER TABLE integration_action_log ADD COLUMN {name} {definition}")

    def create(self, account_id: str, workspace_id: str, title: str, objective: str, requires_approval: bool, steps: list[dict] | None = None) -> dict:
        task_id, now = f"task_{uuid.uuid4().hex}", _now()
        steps = steps or [{"name": "execute_task", "depends_on": []}]
        with self._connect() as c:
            c.execute("INSERT INTO tasks(id,account_id,workspace_id,title,objective,status,requires_approval,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)", (task_id, account_id, workspace_id, title, objective, "queued", requires_approval, now, now))
            run_id = f"run_{uuid.uuid4().hex}"
            c.execute("INSERT INTO task_runs(id,task_id,attempt,status,created_at) VALUES(?,?,?,?,?)", (run_id, task_id, 1, "queued", now))
            step_ids: list[str] = []
            for ordinal, step in enumerate(steps, start=1):
                step_id = f"step_{uuid.uuid4().hex}"; step_ids.append(step_id)
                c.execute("""INSERT INTO task_steps(id,task_id,task_run_id,ordinal,name,status,idempotency_key,lease_owner,lease_expires_at,created_at,required_capability,executor_kind,executor_payload)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (step_id, task_id, run_id, ordinal, step["name"], "queued", f"task:{task_id}:step:{ordinal}", None, None, now, step.get("required_capability"), step.get("executor_kind", "hosted"), json.dumps(step.get("executor_payload", {}))))
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

    def task_is_approved(self, task_id: str, account_id: str) -> bool:
        self.get(task_id, account_id)
        with self._connect() as c:
            row = c.execute("SELECT status FROM approvals WHERE task_id=?", (task_id,)).fetchone()
        return bool(row and row["status"] == "approved")

    def decide(self, task_id: str, account_id: str, approved: bool, note: str) -> dict:
        self.get(task_id, account_id); now = _now(); status = "approved" if approved else "denied"
        with self._connect() as c:
            c.execute("INSERT INTO approvals(task_id,status,note,decided_at) VALUES(?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET status=excluded.status,note=excluded.note,decided_at=excluded.decided_at", (task_id, status, note, now))
            next_status = "queued" if approved else "cancelled"
            c.execute("UPDATE tasks SET status=?,requires_approval=?,updated_at=? WHERE id=?", (next_status, not approved, now, task_id))
            c.execute("INSERT INTO task_events VALUES(?,?,?,?,?)", (f"evt_{uuid.uuid4().hex}", task_id, f"approval.{status}", '{"source":"user"}', now))
        return self.get(task_id, account_id)

    def cancel(self, task_id: str, account_id: str) -> dict:
        task = self.get(task_id, account_id)
        if task["status"] in {"completed", "failed", "cancelled"}:
            return task
        now = _now()
        with self._connect() as c:
            c.execute("UPDATE tasks SET status='cancelling',cancel_requested=?,updated_at=? WHERE id=?", (True, now, task_id))
            c.execute("UPDATE task_steps SET status='cancelled' WHERE task_id=? AND status='queued'", (task_id,))
            running = c.execute("SELECT COUNT(*) AS count FROM task_steps WHERE task_id=? AND status='running'", (task_id,)).fetchone()["count"]
            if running == 0:
                c.execute("UPDATE tasks SET status='cancelled',updated_at=? WHERE id=?", (now, task_id))
                c.execute("UPDATE task_runs SET status='cancelled' WHERE task_id=? AND status!='completed'", (task_id,))
            c.execute("INSERT INTO task_events VALUES(?,?,?,?,?)", (f"evt_{uuid.uuid4().hex}", task_id, "task.cancel_requested", '{"source":"user"}', now))
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
            row = c.execute("""SELECT t.*, s.id AS step_id, s.task_run_id, s.idempotency_key, s.name, s.executor_kind, s.executor_payload
              FROM tasks t JOIN task_steps s ON s.task_id=t.id
              WHERE t.status IN ('queued','running') AND s.status='queued' AND s.executor_kind IN ('hosted','sandbox') AND (s.retry_at IS NULL OR s.retry_at <= ?) AND NOT EXISTS (
                SELECT 1 FROM task_step_dependencies d JOIN task_steps parent ON parent.id=d.depends_on_step_id
                WHERE d.step_id=s.id AND parent.status!='completed')
              ORDER BY t.created_at,s.ordinal LIMIT 1""", (now,)).fetchone()
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
                c.execute("UPDATE task_steps SET status='running',lease_owner=?,lease_expires_at=?,attempts=attempts+1,retry_at=NULL WHERE id=?", (worker_id, until, task["step_id"]))
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
            cancelled = c.execute("SELECT cancel_requested FROM tasks WHERE id=?", (task_id,)).fetchone()["cancel_requested"]
            if cancelled:
                c.execute("UPDATE task_runs SET status='cancelled' WHERE id=?", (run_id,))
                c.execute("UPDATE tasks SET status='cancelled',updated_at=? WHERE id=?", (now, task_id))
                c.execute("INSERT INTO task_events VALUES(?,?,?,?,?)", (f"evt_{uuid.uuid4().hex}", task_id, "task.cancelled", '{"source":"worker"}', now))
                return
            pending = c.execute("SELECT COUNT(*) AS count FROM task_steps WHERE task_run_id=? AND status!='completed'", (run_id,)).fetchone()["count"]
            if pending == 0:
                c.execute("UPDATE task_runs SET status='completed' WHERE id=?", (run_id,))
                c.execute("UPDATE tasks SET status='completed',updated_at=? WHERE id=?", (now, task_id))
                c.execute("INSERT INTO task_events VALUES(?,?,?,?,?)", (f"evt_{uuid.uuid4().hex}", task_id, "task.completed", '{"result":"recorded"}', now))

    def fail_step(self, step_id: str, account_id: str, error: str, retry_delay_seconds: int = 5) -> str:
        """Record a bounded failure. Returns `retrying` or terminal `failed`."""
        now = _now()
        with self._connect() as c:
            row = c.execute("SELECT task_id,task_run_id,attempts,max_attempts FROM task_steps WHERE id=? AND task_id IN (SELECT id FROM tasks WHERE account_id=?)", (step_id, account_id)).fetchone()
            if not row: raise KeyError(step_id)
            retry = row["attempts"] < row["max_attempts"]
            if retry:
                retry_at = (datetime.now(timezone.utc) + timedelta(seconds=retry_delay_seconds)).isoformat()
                c.execute("UPDATE task_steps SET status='queued',lease_owner=NULL,lease_expires_at=NULL,retry_at=?,last_error=? WHERE id=?", (retry_at, error[:2000], step_id))
                c.execute("UPDATE tasks SET status='queued',updated_at=? WHERE id=?", (now, row["task_id"]))
                event_type, outcome = "step.retry_scheduled", "retrying"
            else:
                c.execute("UPDATE task_steps SET status='failed',lease_owner=NULL,lease_expires_at=NULL,last_error=? WHERE id=?", (error[:2000], step_id))
                c.execute("UPDATE task_runs SET status='failed' WHERE id=?", (row["task_run_id"],))
                c.execute("UPDATE tasks SET status='failed',updated_at=? WHERE id=?", (now, row["task_id"]))
                c.execute("INSERT INTO task_dead_letters(id,task_id,step_id,account_id,error,attempts,created_at,resolved_at) VALUES(?,?,?,?,?,?,?,NULL)", (f"dlq_{uuid.uuid4().hex}", row["task_id"], step_id, account_id, error[:2000], row["attempts"], now))
                event_type, outcome = "step.failed", "failed"
            c.execute("INSERT INTO task_events VALUES(?,?,?,?,?)", (f"evt_{uuid.uuid4().hex}", row["task_id"], event_type, '{"source":"worker"}', now))
            return outcome

    def dead_letters(self, account_id: str) -> list[dict]:
        with self._connect() as c:
            return [dict(row) for row in c.execute(
                "SELECT * FROM task_dead_letters WHERE account_id=? ORDER BY created_at DESC", (account_id,)
            )]

    def audit_export(self, account_id: str) -> dict:
        """Portable account data export; deliberately excludes encrypted secrets."""
        with self._connect() as c:
            tasks = [dict(row) for row in c.execute("SELECT * FROM tasks WHERE account_id=? ORDER BY created_at", (account_id,))]
            task_ids = [task["id"] for task in tasks]
            placeholders = ",".join("?" for _ in task_ids) or "NULL"
            return {
                "schema_version": 1, "account_id": account_id, "tasks": tasks,
                "events": [dict(row) for row in c.execute(f"SELECT * FROM task_events WHERE task_id IN ({placeholders}) ORDER BY created_at", task_ids)],
                "steps": [dict(row) for row in c.execute(f"SELECT * FROM task_steps WHERE task_id IN ({placeholders}) ORDER BY ordinal", task_ids)],
                "artifacts": [dict(row) for row in c.execute(f"SELECT * FROM artifacts WHERE task_id IN ({placeholders}) ORDER BY created_at", task_ids)],
                "dead_letters": self.dead_letters(account_id),
                "integrations": [dict(row) for row in c.execute("SELECT id,account_id,provider,display_name,policy,granted_scopes,health,created_at,updated_at FROM integration_connections WHERE account_id=?", (account_id,))],
                "integration_actions": [dict(row) for row in c.execute("SELECT * FROM integration_action_log WHERE account_id=? ORDER BY created_at", (account_id,))],
            }

    def delete_account(self, account_id: str) -> None:
        """Delete Smara-owned account data and credentials; never touch Syntarus."""
        with self._connect() as c:
            c.execute("DELETE FROM integration_credentials WHERE connection_id IN (SELECT id FROM integration_connections WHERE account_id=?)", (account_id,))
            c.execute("DELETE FROM integration_action_log WHERE account_id=?", (account_id,))
            c.execute("DELETE FROM integration_oauth_states WHERE account_id=?", (account_id,))
            c.execute("DELETE FROM integration_connections WHERE account_id=?", (account_id,))
            c.execute("DELETE FROM push_subscriptions WHERE account_id=?", (account_id,))
            c.execute("DELETE FROM desktop_executors WHERE account_id=?", (account_id,))
            c.execute("DELETE FROM executor_pairings WHERE account_id=?", (account_id,))
            c.execute("DELETE FROM task_dead_letters WHERE account_id=?", (account_id,))
            c.execute("DELETE FROM tasks WHERE account_id=?", (account_id,))

    def create_research(self, account_id: str, workspace_id: str, title: str, question: str, sources: list[str]) -> dict:
        task_id, run_id, now = f"task_{uuid.uuid4().hex}", f"run_{uuid.uuid4().hex}", _now()
        step_ids = [f"step_{uuid.uuid4().hex}" for _ in range(3)]
        steps = ("research.fetch_sources", "research.verify_evidence", "research.write_report")
        with self._connect() as c:
            c.execute("INSERT INTO tasks(id,account_id,workspace_id,title,objective,status,requires_approval,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)", (task_id, account_id, workspace_id, title, question, "queued", False, now, now))
            c.execute("INSERT INTO task_runs(id,task_id,attempt,status,created_at) VALUES(?,?,?,?,?)", (run_id, task_id, 1, "queued", now))
            for ordinal, (step_id, name) in enumerate(zip(step_ids, steps), start=1):
                c.execute("INSERT INTO task_steps(id,task_id,task_run_id,ordinal,name,status,idempotency_key,lease_owner,lease_expires_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (step_id, task_id, run_id, ordinal, name, "queued", f"task:{task_id}:step:{ordinal}", None, None, now))
            c.execute("INSERT INTO task_step_dependencies VALUES(?,?)", (step_ids[1], step_ids[0]))
            c.execute("INSERT INTO task_step_dependencies VALUES(?,?)", (step_ids[2], step_ids[1]))
            for source in sources:
                c.execute(
                    "INSERT INTO research_evidence(id,task_id,url,status,created_at) VALUES(?,?,?,?,?)",
                    (f"evidence_{uuid.uuid4().hex}", task_id, source, "pending", now),
                )
            c.execute("INSERT INTO task_events VALUES(?,?,?,?,?)", (f"evt_{uuid.uuid4().hex}", task_id, "task.created", '{"source":"research_api"}', now))
            c.execute("INSERT INTO task_events VALUES(?,?,?,?,?)", (f"evt_{uuid.uuid4().hex}", task_id, "research.planned", f'{{"source_count":{len(sources)}}}', now))
        return self.get(task_id, account_id)

    def evidence(self, task_id: str, account_id: str) -> list[dict]:
        self.get(task_id, account_id)
        with self._connect() as c:
            return [dict(row) for row in c.execute("SELECT * FROM research_evidence WHERE task_id=? ORDER BY created_at,id", (task_id,))]

    def update_evidence(self, evidence_id: str, task_id: str, *, status: str, title: str | None = None, retrieved_at: str | None = None, content_sha256: str | None = None, excerpt: str | None = None, claim: str | None = None, confidence: float | None = None, citation_label: str | None = None, error: str | None = None) -> None:
        with self._connect() as c:
            c.execute("""UPDATE research_evidence SET status=?,title=?,retrieved_at=?,content_sha256=?,excerpt=?,claim=?,confidence=?,citation_label=?,error=?
              WHERE id=? AND task_id=?""", (status, title, retrieved_at, content_sha256, excerpt, claim, confidence, citation_label, error, evidence_id, task_id))

    def append_event(self, task_id: str, event_type: str, payload: str = "{}") -> None:
        with self._connect() as c:
            c.execute("INSERT INTO task_events VALUES(?,?,?,?,?)", (f"evt_{uuid.uuid4().hex}", task_id, event_type, payload, _now()))

    def create_artifact(self, task_id: str, account_id: str, *, kind: str, name: str, content: str) -> dict:
        self.get(task_id, account_id)
        artifact = {
            "id": f"artifact_{uuid.uuid4().hex}", "task_id": task_id, "kind": kind,
            "name": name, "uri": "", "sha256": hashlib.sha256(content.encode()).hexdigest(),
            "content": content, "created_at": _now(),
        }
        artifact["uri"] = f"inline://artifacts/{artifact['id']}"
        with self._connect() as c:
            c.execute("INSERT INTO artifacts(id,task_id,kind,name,uri,sha256,content,created_at) VALUES(?,?,?,?,?,?,?,?)", tuple(artifact[key] for key in ("id", "task_id", "kind", "name", "uri", "sha256", "content", "created_at")))
        self.append_event(task_id, "artifact.created", '{"kind":"research_report"}')
        return artifact

    def artifacts(self, task_id: str, account_id: str) -> list[dict]:
        self.get(task_id, account_id)
        with self._connect() as c:
            return [dict(row) for row in c.execute("SELECT * FROM artifacts WHERE task_id=? ORDER BY created_at,id", (task_id,))]

    def create_executor_pairing(self, account_id: str, name: str, capabilities: list[str]) -> dict:
        code = secrets.token_hex(4).upper()
        now = _now(); expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        with self._connect() as c:
            c.execute("INSERT INTO executor_pairings(code_hash,account_id,name,capabilities,expires_at,consumed_at) VALUES(?,?,?,?,?,?)", (hashlib.sha256(code.encode()).hexdigest(), account_id, name, json.dumps(sorted(set(capabilities))), expires_at, None))
        return {"code": code, "expires_at": expires_at}

    def pair_executor(self, code: str) -> dict:
        now = _now(); code_hash = hashlib.sha256(code.encode()).hexdigest()
        with self._connect() as c:
            pairing = c.execute("SELECT * FROM executor_pairings WHERE code_hash=? AND consumed_at IS NULL AND expires_at>?", (code_hash, now)).fetchone()
            if not pairing: raise KeyError("pairing")
            pairing = dict(pairing); executor_id, token = f"desktop_{uuid.uuid4().hex}", secrets.token_urlsafe(32)
            capabilities = pairing["capabilities"] if isinstance(pairing["capabilities"], str) else json.dumps(pairing["capabilities"])
            c.execute("INSERT INTO desktop_executors(id,account_id,name,capabilities,token_hash,status,last_seen_at,created_at) VALUES(?,?,?,?,?,?,?,?)", (executor_id, pairing["account_id"], pairing["name"], capabilities, hashlib.sha256(token.encode()).hexdigest(), "active", now, now))
            c.execute("UPDATE executor_pairings SET consumed_at=? WHERE code_hash=?", (now, code_hash))
        return {"executor_id": executor_id, "token": token, "account_id": pairing["account_id"], "capabilities": json.loads(pairing["capabilities"]) if isinstance(pairing["capabilities"], str) else pairing["capabilities"]}

    def executor(self, executor_id: str, token: str) -> dict:
        with self._connect() as c:
            row = c.execute("SELECT * FROM desktop_executors WHERE id=? AND token_hash=? AND status='active'", (executor_id, hashlib.sha256(token.encode()).hexdigest())).fetchone()
        if not row: raise KeyError("executor")
        result = dict(row); result["capabilities"] = json.loads(result["capabilities"]) if isinstance(result["capabilities"], str) else result["capabilities"]
        return result

    def heartbeat_executor(self, executor_id: str, token: str, capabilities: list[str]) -> dict:
        executor = self.executor(executor_id, token); now = _now()
        with self._connect() as c:
            c.execute("UPDATE desktop_executors SET last_seen_at=?,capabilities=? WHERE id=?", (now, json.dumps(sorted(set(capabilities))), executor_id))
        executor.update({"last_seen_at": now, "capabilities": sorted(set(capabilities))})
        return executor

    def executors(self, account_id: str) -> list[dict]:
        with self._connect() as c:
            rows = c.execute("SELECT id,name,capabilities,status,last_seen_at,created_at FROM desktop_executors WHERE account_id=? ORDER BY created_at DESC", (account_id,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["capabilities"] = json.loads(item["capabilities"]) if isinstance(item["capabilities"], str) else item["capabilities"]
            result.append(item)
        return result

    def claim_for_executor(self, executor_id: str, token: str, lease_seconds: int = 60) -> dict | None:
        executor = self.executor(executor_id, token); now = _now(); capabilities = set(executor["capabilities"])
        with self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            rows = c.execute("""SELECT t.*,s.id AS step_id,s.task_run_id,s.idempotency_key,s.name,s.required_capability,s.executor_payload
              FROM tasks t JOIN task_steps s ON s.task_id=t.id
              WHERE t.account_id=? AND t.status IN ('queued','running') AND s.status='queued' AND s.executor_kind='desktop' AND (s.retry_at IS NULL OR s.retry_at<=?) AND NOT EXISTS (
                SELECT 1 FROM task_step_dependencies d JOIN task_steps parent ON parent.id=d.depends_on_step_id WHERE d.step_id=s.id AND parent.status!='completed')
              ORDER BY t.created_at,s.ordinal""", (executor["account_id"], now)).fetchall()
            row = next((dict(item) for item in rows if item["required_capability"] in capabilities), None)
            if not row: return None
            until = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()
            c.execute("UPDATE tasks SET status='running',updated_at=? WHERE id=?", (now, row["id"]))
            c.execute("UPDATE task_runs SET status='running' WHERE id=?", (row["task_run_id"],))
            c.execute("UPDATE task_steps SET status='running',lease_owner=?,lease_expires_at=?,attempts=attempts+1,retry_at=NULL WHERE id=?", (executor_id, until, row["step_id"]))
            c.execute("INSERT INTO executor_leases(id,step_id,executor_id,expires_at,created_at) VALUES(?,?,?,?,?)", (f"lease_{uuid.uuid4().hex}", row["step_id"], executor_id, until, now))
            c.execute("INSERT INTO task_events VALUES(?,?,?,?,?)", (f"evt_{uuid.uuid4().hex}", row["id"], "executor.step_claimed", f'{{"executor_id":"{executor_id}"}}', now))
            row["executor_payload"] = json.loads(row["executor_payload"]) if isinstance(row["executor_payload"], str) else row["executor_payload"]
            row["lease_owner"] = executor_id; row["lease_expires_at"] = until; row["status"] = "running"
            return row

    def complete_executor_step(self, executor_id: str, token: str, step_id: str, result: str) -> None:
        self.executor(executor_id, token)
        with self._connect() as c:
            row = c.execute("SELECT task_id FROM task_steps WHERE id=? AND lease_owner=? AND status='running'", (step_id, executor_id)).fetchone()
            if not row: raise KeyError("lease")
            task_id = row["task_id"]
            c.execute("UPDATE executor_leases SET completed_at=? WHERE step_id=? AND executor_id=?", (_now(), step_id, executor_id))
        task = self.get(task_id, self.executor(executor_id, token)["account_id"])
        self.complete_step(step_id, task["account_id"], result)

    def fail_executor_step(self, executor_id: str, token: str, step_id: str, error: str) -> str:
        executor = self.executor(executor_id, token)
        with self._connect() as c:
            row = c.execute("SELECT task_id FROM task_steps WHERE id=? AND lease_owner=? AND status='running'", (step_id, executor_id)).fetchone()
            if not row:
                raise KeyError("lease")
            c.execute("UPDATE executor_leases SET completed_at=? WHERE step_id=? AND executor_id=?", (_now(), step_id, executor_id))
        return self.fail_step(step_id, executor["account_id"], error, retry_delay_seconds=30)

    def configure_integration(self, account_id: str, provider: str, *, display_name: str, policy: str, granted_scopes: list[str], health: str) -> dict:
        now = _now()
        with self._connect() as c:
            existing = c.execute("SELECT id FROM integration_connections WHERE account_id=? AND provider=?", (account_id, provider)).fetchone()
            if existing:
                connection_id = existing["id"]
                c.execute("UPDATE integration_connections SET display_name=?,policy=?,granted_scopes=?,health=?,updated_at=? WHERE id=?", (display_name, policy, json.dumps(sorted(set(granted_scopes))), health, now, connection_id))
            else:
                connection_id = f"integration_{uuid.uuid4().hex}"
                c.execute("INSERT INTO integration_connections(id,account_id,provider,display_name,policy,granted_scopes,health,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)", (connection_id, account_id, provider, display_name, policy, json.dumps(sorted(set(granted_scopes))), health, now, now))
        return self.integration(account_id, provider)

    def integration(self, account_id: str, provider: str) -> dict:
        with self._connect() as c:
            row = c.execute("SELECT * FROM integration_connections WHERE account_id=? AND provider=?", (account_id, provider)).fetchone()
        if not row:
            raise KeyError(provider)
        result = dict(row)
        result["granted_scopes"] = json.loads(result["granted_scopes"]) if isinstance(result["granted_scopes"], str) else result["granted_scopes"]
        return result

    def integrations(self, account_id: str) -> list[dict]:
        with self._connect() as c:
            rows = c.execute("SELECT * FROM integration_connections WHERE account_id=? ORDER BY provider", (account_id,)).fetchall()
        return [dict(row) | {"granted_scopes": json.loads(row["granted_scopes"]) if isinstance(row["granted_scopes"], str) else row["granted_scopes"]} for row in rows]

    def request_integration_action(self, account_id: str, provider: str, action: str, preview: str, idempotency_key: str, payload: dict | None = None) -> dict:
        connection = self.integration(account_id, provider)
        # The registry, not the caller, classifies a small fixed action set.
        read_actions = {"gmail.search", "calendar.list", "telegram.search", "github.list", "drive.search"}
        risk = "read" if action in read_actions else "external"
        if connection["policy"] == "blocked" or (connection["policy"] == "observe" and risk == "external"):
            status = "blocked"
        elif connection["policy"] == "draft" and risk == "external":
            status = "draft"
        else:
            # Even trusted integrations remain approval-gated until an explicit,
            # bounded workflow template and executor exist.
            status = "awaiting_approval" if risk == "external" else "draft"
        now = _now(); action_id = f"iact_{uuid.uuid4().hex}"
        with self._connect() as c:
            prior = c.execute("SELECT * FROM integration_action_log WHERE account_id=? AND idempotency_key=?", (account_id, idempotency_key)).fetchone()
            if prior:
                return dict(prior)
            c.execute("INSERT INTO integration_action_log(id,account_id,connection_id,action,preview,idempotency_key,risk,status,payload,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (action_id, account_id, connection["id"], action, preview, idempotency_key, risk, status, json.dumps(payload or {}), now))
        return {"id": action_id, "account_id": account_id, "connection_id": connection["id"], "action": action, "preview": preview, "idempotency_key": idempotency_key, "risk": risk, "status": status, "payload": payload or {}, "created_at": now}

    def integration_actions(self, account_id: str) -> list[dict]:
        with self._connect() as c:
            return [dict(row) for row in c.execute("SELECT * FROM integration_action_log WHERE account_id=? ORDER BY created_at DESC", (account_id,))]

    def store_integration_credential(self, account_id: str, provider: str, kind: str, encrypted_secret: str) -> None:
        connection = self.integration(account_id, provider); now = _now()
        with self._connect() as c:
            c.execute("INSERT INTO integration_credentials(connection_id,kind,encrypted_secret,updated_at) VALUES(?,?,?,?) ON CONFLICT(connection_id) DO UPDATE SET kind=excluded.kind,encrypted_secret=excluded.encrypted_secret,updated_at=excluded.updated_at", (connection["id"], kind, encrypted_secret, now))
            c.execute("UPDATE integration_connections SET health='healthy',updated_at=? WHERE id=?", (now, connection["id"]))

    def encrypted_integration_credential(self, connection_id: str) -> dict:
        with self._connect() as c:
            row = c.execute("SELECT * FROM integration_credentials WHERE connection_id=?", (connection_id,)).fetchone()
        if not row:
            raise KeyError("credential")
        return dict(row)

    def integration_by_id(self, connection_id: str) -> dict:
        with self._connect() as c:
            row = c.execute("SELECT * FROM integration_connections WHERE id=?", (connection_id,)).fetchone()
        if not row:
            raise KeyError("integration")
        return dict(row)

    def create_oauth_state(self, account_id: str, provider: str, state: str, code_verifier: str) -> None:
        now = _now(); expires = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        with self._connect() as c:
            c.execute("INSERT INTO integration_oauth_states(state_hash,account_id,provider,code_verifier,expires_at,consumed_at) VALUES(?,?,?,?,?,?)", (hashlib.sha256(state.encode()).hexdigest(), account_id, provider, code_verifier, expires, None))

    def consume_oauth_state(self, state: str, provider: str) -> dict:
        now = _now(); state_hash = hashlib.sha256(state.encode()).hexdigest()
        with self._connect() as c:
            row = c.execute("SELECT * FROM integration_oauth_states WHERE state_hash=? AND provider=? AND consumed_at IS NULL AND expires_at>?", (state_hash, provider, now)).fetchone()
            if not row:
                raise KeyError("oauth state")
            c.execute("UPDATE integration_oauth_states SET consumed_at=? WHERE state_hash=?", (now, state_hash))
        return dict(row)

    def decide_integration_action(self, account_id: str, action_id: str, approved: bool, note: str, edited_preview: str | None = None, edited_payload: dict | None = None) -> dict:
        with self._connect() as c:
            row = c.execute("SELECT * FROM integration_action_log WHERE id=? AND account_id=?", (action_id, account_id)).fetchone()
            if not row:
                raise KeyError(action_id)
            result = dict(row)
            if result["status"] != "awaiting_approval":
                raise ValueError("action is not awaiting approval")
            status = "approved" if approved else "denied"
            preview = edited_preview if edited_preview is not None else result["preview"]
            payload = edited_payload if edited_payload is not None else (json.loads(result["payload"]) if isinstance(result["payload"], str) else result["payload"])
            c.execute("UPDATE integration_action_log SET status=?,approval_note=?,preview=?,payload=? WHERE id=?", (status, note, preview, json.dumps(payload), action_id))
            result.update({"status": status, "approval_note": note, "preview": preview, "payload": payload})
            return result

    def claim_integration_action(self, worker_id: str = "integration-worker", lease_seconds: int = 60) -> dict | None:
        now = _now()
        with self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("UPDATE integration_action_log SET status='approved',lease_owner=NULL,lease_expires_at=NULL WHERE status='running' AND lease_expires_at<?", (now,))
            row = c.execute("SELECT * FROM integration_action_log WHERE status='approved' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return None
            result = dict(row); until = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()
            c.execute("UPDATE integration_action_log SET status='running',lease_owner=?,lease_expires_at=?,attempts=attempts+1 WHERE id=?", (worker_id, until, result["id"]))
            result.update({"status": "running", "lease_owner": worker_id, "lease_expires_at": until, "attempts": result["attempts"] + 1})
            result["payload"] = json.loads(result["payload"]) if isinstance(result["payload"], str) else result["payload"]
            return result

    def complete_integration_action(self, action_id: str, worker_id: str, *, result: str | None = None, error: str | None = None) -> None:
        with self._connect() as c:
            row = c.execute("SELECT id FROM integration_action_log WHERE id=? AND status='running' AND lease_owner=?", (action_id, worker_id)).fetchone()
            if not row:
                raise KeyError("integration lease")
            if error:
                c.execute("UPDATE integration_action_log SET status='failed',lease_owner=NULL,lease_expires_at=NULL,last_error=? WHERE id=?", (error[:2000], action_id))
            else:
                c.execute("UPDATE integration_action_log SET status='completed',lease_owner=NULL,lease_expires_at=NULL,result_summary=? WHERE id=?", ((result or "completed")[:2000], action_id))

    def create_capture(self, account_id: str, kind: str, title: str, content: str, mime_type: str = "text/plain") -> dict:
        task = self.create(account_id, "inbox", title, content[:20_000] or f"{kind} capture", False, [{"name": "capture.received"}])
        artifact = self.create_artifact(task["id"], account_id, kind=f"capture:{kind}:{mime_type}", name=title, content=content)
        return {"task": task, "artifact": artifact}

    def save_push_subscription(self, account_id: str, endpoint: str, p256dh: str, auth: str) -> None:
        now = _now()
        with self._connect() as c:
            c.execute("INSERT INTO push_subscriptions(endpoint,account_id,p256dh,auth,created_at,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(endpoint) DO UPDATE SET account_id=excluded.account_id,p256dh=excluded.p256dh,auth=excluded.auth,updated_at=excluded.updated_at", (endpoint, account_id, p256dh, auth, now, now))

    def push_subscriptions(self, account_id: str) -> list[dict]:
        with self._connect() as c:
            return [dict(row) for row in c.execute("SELECT endpoint,p256dh,auth FROM push_subscriptions WHERE account_id=?", (account_id,))]

    def delete_push_subscription(self, endpoint: str) -> None:
        with self._connect() as c:
            c.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,))


class PostgresTaskStore(TaskStore):
    """Production implementation of the same task-store contract.

    Migrations own schema evolution; this class deliberately inherits the task
    graph state-machine methods so SQLite tests and the live worker cannot drift.
    """
    def __init__(self, database_url: str):
        self.database_url = database_url
        self._init()

    def _init(self):
        from .migrations import apply_postgres_migrations
        apply_postgres_migrations(self.database_url)

    def _connect(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("Postgres runtime requires `psycopg[binary]`; install Smara dependencies.") from exc
        return _PostgresConnection(psycopg.connect(self.database_url, row_factory=dict_row))

    def claim_one(self, worker_id: str = "worker", lease_seconds: int = 60) -> dict | None:
        """Atomically lease one ready step without competing workers colliding.

        The SQLite implementation uses ``BEGIN IMMEDIATE``. In Postgres the
        equivalent contract is a row lock with ``SKIP LOCKED``: a second worker
        simply keeps looking rather than observing the same ready step.
        """
        with self._connect() as c:
            now = _now()
            expired = c.execute(
                "SELECT id,task_id FROM task_steps WHERE status='running' AND lease_expires_at < %s FOR UPDATE SKIP LOCKED",
                (now,),
            ).fetchall()
            for item in expired:
                c.execute("UPDATE task_steps SET status='queued',lease_owner=NULL,lease_expires_at=NULL WHERE id=%s", (item["id"],))
                c.execute("UPDATE tasks SET status='queued',updated_at=%s WHERE id=%s AND status='running'", (now, item["task_id"]))
                c.execute("INSERT INTO task_events VALUES(%s,%s,%s,%s,%s)", (f"evt_{uuid.uuid4().hex}", item["task_id"], "step.lease_expired", '{"recovered":true}', now))

            row = c.execute("""SELECT t.*, s.id AS step_id, s.task_run_id, s.idempotency_key, s.name, s.executor_kind, s.executor_payload
              FROM tasks t JOIN task_steps s ON s.task_id=t.id
              WHERE t.status IN ('queued','running') AND s.status='queued' AND s.executor_kind='hosted' AND (s.retry_at IS NULL OR s.retry_at <= %s) AND NOT EXISTS (
                SELECT 1 FROM task_step_dependencies d JOIN task_steps parent ON parent.id=d.depends_on_step_id
                WHERE d.step_id=s.id AND parent.status!='completed')
              ORDER BY t.created_at,s.ordinal
              LIMIT 1 FOR UPDATE OF s SKIP LOCKED""", (now,)).fetchone()
            if not row:
                return None
            task = dict(row)
            if task["requires_approval"] and task["status"] == "queued":
                c.execute("UPDATE tasks SET status='waiting_approval',updated_at=%s WHERE id=%s", (now, task["id"]))
                event_type = "approval.requested"
                task["status"] = "waiting_approval"
            else:
                until = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()
                c.execute("UPDATE tasks SET status='running',updated_at=%s WHERE id=%s", (now, task["id"]))
                c.execute("UPDATE task_runs SET status='running' WHERE id=%s", (task["task_run_id"],))
                c.execute("UPDATE task_steps SET status='running',lease_owner=%s,lease_expires_at=%s,attempts=attempts+1,retry_at=NULL WHERE id=%s", (worker_id, until, task["step_id"]))
                event_type = "task.started"
                task["status"] = "running"
                task["lease_owner"] = worker_id
            c.execute("INSERT INTO task_events VALUES(%s,%s,%s,%s,%s)", (f"evt_{uuid.uuid4().hex}", task["id"], event_type, '{"source":"worker"}', now))
            task["updated_at"] = now
            return task

    def claim_for_executor(self, executor_id: str, token: str, lease_seconds: int = 60) -> dict | None:
        """Postgres desktop claim with the same SKIP LOCKED lease guarantee."""
        executor = self.executor(executor_id, token); now = _now(); capabilities = set(executor["capabilities"])
        with self._connect() as c:
            rows = c.execute("""SELECT t.*,s.id AS step_id,s.task_run_id,s.idempotency_key,s.name,s.required_capability,s.executor_payload
              FROM tasks t JOIN task_steps s ON s.task_id=t.id
              WHERE t.account_id=%s AND t.status IN ('queued','running') AND s.status='queued' AND s.executor_kind='desktop' AND (s.retry_at IS NULL OR s.retry_at<=%s) AND NOT EXISTS (
                SELECT 1 FROM task_step_dependencies d JOIN task_steps parent ON parent.id=d.depends_on_step_id WHERE d.step_id=s.id AND parent.status!='completed')
              ORDER BY t.created_at,s.ordinal FOR UPDATE OF s SKIP LOCKED""", (executor["account_id"], now)).fetchall()
            row = next((dict(item) for item in rows if item["required_capability"] in capabilities), None)
            if not row: return None
            until = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()
            c.execute("UPDATE tasks SET status='running',updated_at=%s WHERE id=%s", (now, row["id"]))
            c.execute("UPDATE task_runs SET status='running' WHERE id=%s", (row["task_run_id"],))
            c.execute("UPDATE task_steps SET status='running',lease_owner=%s,lease_expires_at=%s,attempts=attempts+1,retry_at=NULL WHERE id=%s", (executor_id, until, row["step_id"]))
            c.execute("INSERT INTO executor_leases(id,step_id,executor_id,expires_at,created_at) VALUES(%s,%s,%s,%s,%s)", (f"lease_{uuid.uuid4().hex}", row["step_id"], executor_id, until, now))
            c.execute("INSERT INTO task_events VALUES(%s,%s,%s,%s,%s)", (f"evt_{uuid.uuid4().hex}", row["id"], "executor.step_claimed", f'{{"executor_id":"{executor_id}"}}', now))
            row["executor_payload"] = json.loads(row["executor_payload"]) if isinstance(row["executor_payload"], str) else row["executor_payload"]
            row.update({"lease_owner": executor_id, "lease_expires_at": until, "status": "running"})
            return row

    def claim_integration_action(self, worker_id: str = "integration-worker", lease_seconds: int = 60) -> dict | None:
        now = _now()
        with self._connect() as c:
            c.execute("UPDATE integration_action_log SET status='approved',lease_owner=NULL,lease_expires_at=NULL WHERE status='running' AND lease_expires_at<%s", (now,))
            row = c.execute("SELECT * FROM integration_action_log WHERE status='approved' ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED").fetchone()
            if not row:
                return None
            result = dict(row); until = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()
            c.execute("UPDATE integration_action_log SET status='running',lease_owner=%s,lease_expires_at=%s,attempts=attempts+1 WHERE id=%s", (worker_id, until, result["id"]))
            result.update({"status": "running", "lease_owner": worker_id, "lease_expires_at": until, "attempts": result["attempts"] + 1})
            result["payload"] = json.loads(result["payload"]) if isinstance(result["payload"], str) else result["payload"]
            return result


def open_task_store(*, database_url: str, database_path: str):
    """Select Postgres in every configured live deployment; SQLite is dev/test only."""
    return PostgresTaskStore(database_url) if database_url else TaskStore(database_path)


class _PostgresConnection:
    """Translate the small common SQL subset used by the shared state machine."""
    def __init__(self, connection): self._connection = connection
    def __enter__(self): self._connection.__enter__(); return self
    def __exit__(self, *args): return self._connection.__exit__(*args)
    def execute(self, sql: str, params=None):
        normalized = "BEGIN" if sql == "BEGIN IMMEDIATE" else sql.replace("?", "%s")
        return self._connection.execute(normalized, params)
