"""Small durable H1 execution spine for local CLI and benchmark adapters."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    status: str
    text: str = ""
    exit_code: int | None = None
    changed_paths: tuple[str, ...] = ()


class SessionEngine:
    """Journaled sequential executor; H2 supplies policy/process isolation."""
    VERSION = "h1-local-1"

    def __init__(self, workspace: Path | str, session_id: str | None = None):
        self.workspace = Path(workspace).resolve()
        self.session_id = session_id or uuid.uuid4().hex
        root = self.workspace / ".smara" / "sessions"
        root.mkdir(parents=True, exist_ok=True)
        self.db_path = root / f"{self.session_id}.sqlite3"
        self.artifacts = root / self.session_id / "artifacts"
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.db_path)
        self._db.execute("CREATE TABLE IF NOT EXISTS events (sequence INTEGER PRIMARY KEY, type TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL)")
        self._db.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self._db.commit()

    def _event(self, kind: str, payload: dict[str, Any]) -> None:
        sequence = self._db.execute("SELECT COALESCE(MAX(sequence), 0) + 1 FROM events").fetchone()[0]
        self._db.execute("INSERT INTO events VALUES (?, ?, ?, ?)", (sequence, kind, json.dumps(payload, sort_keys=True), time.time()))
        self._db.commit()

    def _set(self, key: str, value: Any) -> None:
        self._db.execute("INSERT OR REPLACE INTO state VALUES (?, ?)", (key, json.dumps(value, sort_keys=True)))
        self._db.commit()

    def inspect(self) -> dict[str, Any]:
        state = {k: json.loads(v) for k, v in self._db.execute("SELECT key, value FROM state")}
        events = [{"sequence": row[0], "type": row[1], "payload": json.loads(row[2])} for row in self._db.execute("SELECT sequence, type, payload FROM events ORDER BY sequence")]
        return {"engine_version": self.VERSION, "session_id": self.session_id, "workspace": str(self.workspace), "state": state, "events": events}

    def cancel(self) -> None:
        self._set("cancelled", True); self._event("cancelled", {})

    def close(self) -> None:
        self._db.close()

    def run(self, request: str, calls: list[dict[str, Any]], executor: Callable[[dict[str, Any]], ToolResult], *, resume: bool = False) -> dict[str, Any]:
        prior = self.inspect()["state"]
        if prior.get("cancelled"):
            return self._finish("cancelled", "", [])
        if not resume:
            self._set("request", request); self._set("next_call", 0); self._event("started", {"request": request})
        start = int(self.inspect()["state"].get("next_call", 0))
        receipts: list[dict[str, Any]] = []
        for index in range(start, len(calls)):
            if self.inspect()["state"].get("cancelled"):
                return self._finish("cancelled", "", receipts)
            call = dict(calls[index]); call.setdefault("call_id", f"{self.session_id}-{index}")
            self._event("tool_admitted", {"call_id": call["call_id"], "name": call.get("name")})
            result = executor(call)
            receipt = asdict(result); receipts.append(receipt)
            artifact = self.artifacts / f"{result.call_id}.json"
            artifact.write_text(json.dumps(receipt, sort_keys=True), encoding="utf-8")
            self._event("tool_result", {**receipt, "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()})
            self._set("next_call", index + 1)
            if result.status != "ok" or result.exit_code not in (None, 0):
                return self._finish("tool_error", "", receipts)
        return self._finish("completed", "", receipts)

    def _finish(self, status: str, answer: str, receipts: list[dict[str, Any]]) -> dict[str, Any]:
        result = {"status": status, "answer": answer, "session_id": self.session_id, "engine_version": self.VERSION, "verification": receipts, "resume_token": self.session_id}
        self._set("result", result); self._event("finished", {"status": status})
        return result
