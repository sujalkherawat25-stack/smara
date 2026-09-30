"""Durable recurring refreshes for canonical, citation-backed research."""
from __future__ import annotations

import json
from contextlib import contextmanager
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Callable


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _review_snapshot(value: dict[str, Any]) -> dict[str, Any]:
    review = value.get("research_review") or {}
    return {
        "answer": str(value.get("answer") or ""),
        "claims": [
            {"claim": str(item.get("claim") or ""), "supported": bool(item.get("supported")),
             "citations": list(item.get("citations") or [])}
            for item in review.get("claims", []) if isinstance(item, dict)
        ],
        "evidence": [
            {"url": str(item.get("url") or ""), "sha256": str(item.get("sha256") or ""),
             "cited": bool(item.get("cited")), "retrieved_at": str(item.get("retrieved_at") or "")}
            for item in review.get("evidence", []) if isinstance(item, dict)
        ],
        "failures": list(review.get("failures") or []),
    }


def compare_refreshes(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    """Compare saved claims and source fingerprints without overstating staleness."""
    current = _review_snapshot(current)
    if not previous:
        return {"baseline": True, "summary": "Initial refresh recorded as baseline; future runs will show changes.",
                "new_claims": [], "unsupported_claims": [], "not_repeated_claims": [],
                "sources_added": [], "sources_changed": [], "sources_not_seen": [],
                "supporting_sources": sorted({citation.get("url", "") for claim in current["claims"]
                    if claim["supported"] for citation in claim["citations"] if citation.get("url")})}

    def claim_map(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {re.sub(r"\s+", " ", str(item.get("claim") or "")).strip().casefold(): item
                for item in snapshot.get("claims", []) if str(item.get("claim") or "").strip()}

    before_claims, after_claims = claim_map(previous), claim_map(current)
    new_claims = [item["claim"] for key, item in after_claims.items() if key not in before_claims]
    unsupported = [item["claim"] for key, item in after_claims.items()
                   if key in before_claims and before_claims[key].get("supported") and not item.get("supported")]
    not_repeated = [item["claim"] for key, item in before_claims.items() if key not in after_claims]
    before_sources = {item["url"]: item for item in previous.get("evidence", []) if item.get("url")}
    after_sources = {item["url"]: item for item in current.get("evidence", []) if item.get("url")}
    added = sorted(set(after_sources) - set(before_sources))
    changed = sorted(url for url in set(after_sources) & set(before_sources)
                     if before_sources[url].get("sha256") and after_sources[url].get("sha256")
                     and before_sources[url]["sha256"] != after_sources[url]["sha256"])
    missing = sorted(set(before_sources) - set(after_sources))
    supporting = sorted({citation.get("url", "") for item in after_claims.values() if item.get("supported")
                         for citation in item.get("citations", []) if citation.get("url")})
    changed_count = len(new_claims) + len(unsupported) + len(added) + len(changed)
    return {
        "baseline": False,
        "summary": f"{changed_count} material change(s); {len(not_repeated)} prior claim(s) were not repeated and need review.",
        "new_claims": new_claims,
        "unsupported_claims": unsupported,
        "not_repeated_claims": not_repeated,
        "sources_added": added,
        "sources_changed": changed,
        "sources_not_seen": missing,
        "supporting_sources": supporting,
        "staleness_note": "A claim omitted in a refresh is a review candidate, not proof it became false. A source not fetched again is unverified, not necessarily stale.",
    }


class ResearchWatchStore:
    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()

    def __init__(self, workspace: str | Path):
        self.workspace = Path(workspace).expanduser().resolve()
        if not self.workspace.is_dir():
            raise ValueError("research watch workspace must exist")
        self.path = self.workspace / ".smara" / "research_watch.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS research_watches (
                id TEXT PRIMARY KEY, topic TEXT NOT NULL, workspace TEXT NOT NULL,
                interval_seconds INTEGER NOT NULL, next_run_at REAL NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1, running INTEGER NOT NULL DEFAULT 0,
                lease_until REAL NOT NULL DEFAULT 0, last_run_at REAL, last_error TEXT,
                created_at REAL NOT NULL, updated_at REAL NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS research_refreshes (
                id TEXT PRIMARY KEY, watch_id TEXT NOT NULL, status TEXT NOT NULL,
                started_at REAL NOT NULL, completed_at REAL, session_id TEXT,
                snapshot_json TEXT NOT NULL DEFAULT '{}', diff_json TEXT NOT NULL DEFAULT '{}',
                error TEXT, FOREIGN KEY(watch_id) REFERENCES research_watches(id))""")
            db.execute("CREATE INDEX IF NOT EXISTS research_refreshes_watch_started ON research_refreshes(watch_id, started_at DESC)")

    def add(self, topic: str, interval_hours: float = 24, *, baseline: dict[str, Any] | None = None) -> dict[str, Any]:
        topic = str(topic).strip()
        seconds = int(float(interval_hours) * 3600)
        if not topic or len(topic) > 20_000 or not 3600 <= seconds <= 31_536_000:
            raise ValueError("topic and refresh interval (1 hour to 1 year) are required")
        now = time.time()
        watch_id = f"watch_{uuid.uuid4().hex}"
        next_run = now + seconds if baseline else now
        with self._connect() as db:
            db.execute("INSERT INTO research_watches(id,topic,workspace,interval_seconds,next_run_at,enabled,running,lease_until,last_run_at,last_error,created_at,updated_at) VALUES(?,?,?,?,?,1,0,0,NULL,NULL,?,?)",
                       (watch_id, topic, str(self.workspace), seconds, next_run, now, now))
            if baseline:
                snapshot = _review_snapshot(baseline)
                diff = compare_refreshes(None, baseline)
                db.execute("INSERT INTO research_refreshes(id,watch_id,status,started_at,completed_at,session_id,snapshot_json,diff_json,error) VALUES(?,?, 'baseline', ?, ?, ?, ?, ?, NULL)",
                           (f"refresh_{uuid.uuid4().hex}", watch_id, now, now, baseline.get("session_id"), _json(snapshot), _json(diff)))
        return next(item for item in self.list() if item["id"] == watch_id)

    def list(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM research_watches ORDER BY enabled DESC,next_run_at ASC").fetchall()
        return [self._watch(row) for row in rows]

    @staticmethod
    def _watch(row: sqlite3.Row) -> dict[str, Any]:
        return {"id": row["id"], "topic": row["topic"], "workspace": row["workspace"],
                "interval_seconds": row["interval_seconds"], "next_run_at": row["next_run_at"],
                "enabled": bool(row["enabled"]), "running": bool(row["running"]),
                "last_run_at": row["last_run_at"], "last_error": row["last_error"]}

    def get(self, watch_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM research_watches WHERE id=?", (watch_id,)).fetchone()
        return self._watch(row) if row else None

    def set_enabled(self, watch_id: str, enabled: bool) -> None:
        with self._connect() as db:
            cur = db.execute("UPDATE research_watches SET enabled=?,updated_at=? WHERE id=?",
                             (int(enabled), time.time(), watch_id))
            if not cur.rowcount:
                raise KeyError(f"unknown research watch: {watch_id}")

    def remove(self, watch_id: str) -> None:
        # Keep refresh history for auditability; removing a watch only disables it.
        self.set_enabled(watch_id, False)

    def history(self, watch_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM research_refreshes WHERE watch_id=? ORDER BY started_at DESC LIMIT ?",
                              (watch_id, max(1, min(int(limit), 100)))).fetchall()
        return [{"id": row["id"], "watch_id": row["watch_id"], "status": row["status"],
                 "started_at": row["started_at"], "completed_at": row["completed_at"],
                 "session_id": row["session_id"], "snapshot": json.loads(row["snapshot_json"]),
                 "diff": json.loads(row["diff_json"]), "error": row["error"]} for row in rows]

    def _claim(self, watch_id: str, *, force: bool) -> dict[str, Any] | None:
        now = time.time()
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM research_watches WHERE id=?", (watch_id,)).fetchone()
            if row is None or not row["enabled"] or (row["running"] and row["lease_until"] > now) or (not force and row["next_run_at"] > now):
                return None
            db.execute("UPDATE research_watches SET running=1,lease_until=?,updated_at=? WHERE id=?",
                       (now + 1800, now, watch_id))
            item = self._watch(row)
            item["running"] = True
            return item

    def _previous_snapshot(self, watch_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT snapshot_json FROM research_refreshes WHERE watch_id=? "
                "AND status IN ('completed','baseline') ORDER BY started_at DESC LIMIT 1",
                (watch_id,),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def _finish(self, watch: dict[str, Any], refresh_id: str, snapshot: dict[str, Any], diff: dict[str, Any],
                status: str, error: str | None = None, session_id: str | None = None,
                started_at: float | None = None) -> dict[str, Any]:
        now = time.time()
        with self._connect() as db:
            db.execute("INSERT INTO research_refreshes(id,watch_id,status,started_at,completed_at,session_id,snapshot_json,diff_json,error) VALUES(?,?,?,?,?,?,?,?,?)",
                       (refresh_id, watch["id"], status, started_at if started_at is not None else now, now, session_id, _json(snapshot), _json(diff), error))
            db.execute("UPDATE research_watches SET running=0,lease_until=0,next_run_at=?,last_run_at=?,last_error=?,updated_at=? WHERE id=?",
                       (now + min(watch["interval_seconds"], 3600) if error else now + watch["interval_seconds"], now, error, now, watch["id"]))
        return {"id": refresh_id, "watch_id": watch["id"], "status": status, "snapshot": snapshot, "diff": diff, "error": error}

    def run(self, watch_id: str, *, force: bool = True,
            runner: Callable[[str, str], dict[str, Any]] | None = None) -> dict[str, Any] | None:
        watch = self._claim(watch_id, force=force)
        if watch is None:
            return None
        started_at = time.time()
        refresh_id = f"refresh_{uuid.uuid4().hex}"
        previous = self._previous_snapshot(watch_id)
        try:
            if runner is None:
                from .app_adapter import run_canonical_task
                result = run_canonical_task(watch["topic"], workspace=watch["workspace"],
                    budget_profile="research_quick", tool_profile="research_web", research_mode="quick")
            else:
                result = runner(watch["topic"], watch["workspace"])
            snapshot = _review_snapshot(result)
            diff = compare_refreshes(previous, result)
            status = "completed" if result.get("status") == "completed" else "failed"
            error = None if status == "completed" else "; ".join(str(item) for item in result.get("unresolved_work", [])[:8]) or "canonical research did not complete"
            return self._finish(watch, refresh_id, {**snapshot, "status": result.get("status"), "session_id": result.get("session_id")},
                                diff, status, error, result.get("session_id"), started_at)
        except Exception as exc:
            empty = {"answer": "", "claims": [], "evidence": [], "failures": []}
            return self._finish(watch, refresh_id, empty, compare_refreshes(previous, empty), "failed",
                                f"{type(exc).__name__}: {str(exc)[:500]}", started_at=started_at)

    def run_due(self, *, limit: int = 5, runner: Callable[[str, str], dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        now = time.time()
        with self._connect() as db:
            rows = db.execute("SELECT id FROM research_watches WHERE enabled=1 AND (running=0 OR lease_until<=?) AND next_run_at<=? ORDER BY next_run_at LIMIT ?",
                              (now, now, max(1, min(int(limit), 20)))).fetchall()
        output = []
        for (watch_id,) in rows:
            result = self.run(watch_id, force=False, runner=runner)
            if result is not None:
                output.append(result)
        return output
