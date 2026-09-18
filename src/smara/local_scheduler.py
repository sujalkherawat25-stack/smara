"""Small durable scheduler used by local CLI/Desktop gateway deployments."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
import threading
from pathlib import Path
from typing import Any, Callable


class LocalScheduleStore:
    def __init__(self, path: Path | str):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS schedules (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, payload_json TEXT NOT NULL,
                interval_seconds INTEGER NOT NULL, next_run_at REAL NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1, running INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, updated_at REAL NOT NULL
            )""")

    def add(self, name: str, payload: dict[str, Any], interval_seconds: int, *, schedule_id: str | None = None, next_run_at: float | None = None) -> dict[str, Any]:
        if not str(name).strip() or not 5 <= int(interval_seconds) <= 31_536_000:
            raise ValueError("schedule name and interval are invalid")
        item = {"id": schedule_id or f"sched_{uuid.uuid4().hex}", "name": str(name)[:160], "payload": payload,
                "interval_seconds": int(interval_seconds), "next_run_at": float(time.time() if next_run_at is None else next_run_at), "enabled": True}
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO schedules(id,name,payload_json,interval_seconds,next_run_at,enabled,running,attempts,last_error,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (item["id"], item["name"], json.dumps(payload, ensure_ascii=False), item["interval_seconds"], item["next_run_at"], 1, 0, 0, None, time.time()))
        return item

    def list(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM schedules ORDER BY next_run_at ASC").fetchall()
        return [self._row(row) for row in rows]

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        return {"id": row["id"], "name": row["name"], "payload": json.loads(row["payload_json"]), "interval_seconds": row["interval_seconds"], "next_run_at": row["next_run_at"], "enabled": bool(row["enabled"]), "running": bool(row["running"]), "attempts": row["attempts"], "last_error": row["last_error"]}

    def set_enabled(self, schedule_id: str, enabled: bool) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE schedules SET enabled=?, running=0, updated_at=? WHERE id=?", (1 if enabled else 0, time.time(), schedule_id))

    def remove(self, schedule_id: str) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))

    def tick(self, callback: Callable[[dict[str, Any]], Any], *, now: float | None = None, limit: int = 20) -> list[dict[str, Any]]:
        now = float(now or time.time()); claimed: list[dict[str, Any]] = []
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM schedules WHERE enabled=1 AND running=0 AND next_run_at<=? ORDER BY next_run_at LIMIT ?", (now, max(1, min(int(limit), 100)))).fetchall()
            for row in rows:
                db.execute("UPDATE schedules SET running=1, attempts=attempts+1, updated_at=? WHERE id=?", (now, row["id"]))
                claimed.append(self._row(row))
        completed: list[dict[str, Any]] = []
        for item in claimed:
            try:
                callback(item)
                with sqlite3.connect(self.path) as db:
                    db.execute("UPDATE schedules SET running=0, next_run_at=?, last_error=NULL, updated_at=? WHERE id=?", (now + item["interval_seconds"], time.time(), item["id"]))
                completed.append({**item, "status": "completed"})
            except Exception as exc:
                with sqlite3.connect(self.path) as db:
                    db.execute("UPDATE schedules SET running=0, next_run_at=?, last_error=?, updated_at=? WHERE id=?", (now + min(item["interval_seconds"], 300), str(exc)[:1_000], time.time(), item["id"]))
                completed.append({**item, "status": "failed", "error": str(exc)[:1_000]})
        return completed


class LocalScheduler:
    """Restart-safe background tick loop; callback remains the policy boundary."""
    def __init__(self, store: LocalScheduleStore, callback: Callable[[dict[str, Any]], Any], *, poll_seconds: float = 5.0):
        self.store, self.callback, self.poll_seconds = store, callback, max(1.0, float(poll_seconds)); self._stop = threading.Event(); self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive(): return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="smara-scheduler", daemon=True); self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.poll_seconds):
            self.store.tick(self.callback)

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread(): self._thread.join(timeout=3)
        self._thread = None
