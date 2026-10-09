"""Explicit, bounded read-only wakes into native sessions. No second agent loop.

Jobs start paused. A failed/unknown run pauses the job and is never retried
automatically. This supervisor does not grant interactive permissions or
schedule company writes. It must be started explicitly, not on app startup.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import os
import sqlite3
import subprocess
import sys
import time
import threading
import uuid


class ScheduleStore:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS native_jobs (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, spec TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 0, next_due REAL NOT NULL, running TEXT,
                    last_status TEXT);
                CREATE TABLE IF NOT EXISTS native_runs (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL, started REAL NOT NULL,
                    finished REAL, status TEXT NOT NULL, evidence TEXT);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self, name, spec):
        workspace = Path(spec["workspace"]).resolve(strict=True)
        if not workspace.is_dir() or not spec.get("prompt", "").strip() or not spec.get("profile_id"):
            raise ValueError("Explicit workspace, prompt and profile are required")
        if not 60 <= spec["interval_seconds"] <= 31_536_000 or not 1 <= spec["max_requests"] <= 100:
            raise ValueError("Invalid interval/request budget")
        spec = {**spec, "workspace": str(workspace)}
        identity = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO native_jobs (id,name,spec,next_due) VALUES (?,?,?,?)", (identity, name, json.dumps(spec), time.time()))
        return identity

    def list(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT id,name,enabled,next_due,running,last_status FROM native_jobs ORDER BY next_due")]

    def enable(self, identity, enabled):
        with self.connect() as db:
            row = db.execute("SELECT running FROM native_jobs WHERE id=?", (identity,)).fetchone()
            if row is None or (enabled and row["running"]):
                raise ValueError("Job absent or running; cannot change its lease")
            db.execute("UPDATE native_jobs SET enabled=? WHERE id=?", (int(enabled), identity))

    def claim(self, now=None):
        now = time.time() if now is None else now
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM native_jobs WHERE enabled=1 AND running IS NULL AND next_due<=? ORDER BY next_due LIMIT 1", (now,)).fetchone()
            if not row:
                return None
            run_id = uuid.uuid4().hex
            db.execute("UPDATE native_jobs SET running=? WHERE id=?", (run_id, row["id"]))
            db.execute("INSERT INTO native_runs (id,job_id,started,status) VALUES (?,?,?,?)", (run_id, row["id"], now, "running"))
            return {"id": row["id"], "run_id": run_id, **json.loads(row["spec"])}

    def finish(self, job, status, evidence):
        if status not in {"completed", "failed", "unknown"}:
            raise ValueError("Invalid native run status")
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            updated = db.execute("UPDATE native_jobs SET running=NULL,last_status=?,enabled=CASE WHEN ?='completed' THEN enabled ELSE 0 END,next_due=? WHERE id=? AND running=?",
                (status, status, now + job["interval_seconds"], job["id"], job["run_id"]))
            if updated.rowcount != 1:
                raise ValueError("Stale scheduler lease")
            db.execute("UPDATE native_runs SET status=?,finished=?,evidence=? WHERE id=?", (status, now, json.dumps(evidence), job["run_id"]))

    def recover_unknown(self):
        # Caller MUST hold supervisor_lock. A lost supervisor's previous run
        # may have performed work: never erase the lease and rerun silently.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE native_runs SET status='unknown',finished=? WHERE status='running'", (time.time(),))
            db.execute("UPDATE native_jobs SET enabled=0,running=NULL,last_status='unknown' WHERE running IS NOT NULL")


@contextmanager
def supervisor_lock(path: Path):
    with path.with_suffix(".lock").open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Another native schedule supervisor owns this store") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class RequestBudget:
    def __init__(self, client, ceiling):
        self.client, self.ceiling, self.requests = client, ceiling, 0
        self.estimated_input_tokens_upper_bound = 0
        self.lock = threading.Lock()

    def stream(self, *args, **kwargs):
        with self.lock:
            if self.requests >= self.ceiling:
                raise RuntimeError("Scheduled inference request ceiling reached")
            # Count UTF-8 bytes as a conservative bound, not actual billing.
            estimate = len(json.dumps(kwargs["json"], ensure_ascii=False).encode("utf-8"))
            if self.estimated_input_tokens_upper_bound + estimate > 200_000:
                raise RuntimeError("Scheduled cumulative input estimate ceiling reached")
            self.estimated_input_tokens_upper_bound += estimate
            self.requests += 1
        kwargs["json"]["max_tokens"] = min(kwargs["json"].get("max_tokens", 2048), 2048)
        return self.client.stream(*args, **kwargs)

    def close(self):
        self.client.close()


def execute(job, runs_root):
    from .native_provider import ChatEndpoint, ResponsesAdapter
    from .native_runtime import active_profile, launch_options, native_binary
    from .native_profiles import load_profiles, resolve_profile_key
    profiles, _selected, credentials = load_profiles()
    profile = active_profile(profiles, job["profile_id"])
    home = runs_root / job["run_id"]
    home.mkdir(parents=True)
    evidence = {"native_home": str(home), "sandbox": "read-only", "approval_policy": "never", "profile_id": job["profile_id"]}
    with ResponsesAdapter(ChatEndpoint(profile["base_url"], profile["model"], resolve_profile_key(profile, credentials), profile.get("auth_header", "authorization"), profile.get("context_window"))) as adapter:
        budget = RequestBudget(adapter.client, job["max_requests"])
        adapter.client = budget
        options, env = launch_options(adapter, home=home, workspace=Path(job["workspace"]), tools_enabled=job.get("public_readers") is True)
        try:
            result = subprocess.run([str(native_binary()), *options, "-c", 'sandbox_mode="read-only"', "-c", 'approval_policy="never"',
                "-c", "agents.max_concurrent_threads_per_session=1", "--no-daemon", "exec", "--skip-git-repo-check", "--json", "-"],
                input=job["prompt"], cwd=job["workspace"], env=env, text=True, encoding="utf-8", capture_output=True, timeout=180,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            trace = home / "schedule-events.jsonl"
            trace.write_text(result.stdout, encoding="utf-8")
            events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
            finished = any(event.get("type") == "turn.completed" for event in events)
            failures = any(event.get("type") in {"turn.failed", "error"} for event in events)
            evidence.update(trace=str(trace), exit_code=result.returncode,
                thread_ids=[event["thread_id"] for event in events if event.get("type") == "thread.started"])
            status = "completed" if finished and not failures and result.returncode == 0 else "failed" if failures else "unknown"
        except subprocess.TimeoutExpired:
            status = "unknown"  # Do not retry a possibly accepted operation.
        evidence.update(provider_requests=budget.requests, request_ceiling=budget.ceiling,
            estimated_input_tokens_upper_bound=budget.estimated_input_tokens_upper_bound, max_output_tokens_per_request=2048)
    return status, evidence


def tick(store, runner=execute):
    job = store.claim()
    if job is None:
        return None
    try:
        status, evidence = runner(job, store.path.parent / "schedule-runs")
    except Exception as exc:
        status, evidence = "unknown", {"error_type": type(exc).__name__}
    store.finish(job, status, evidence)
    return {"job_id": job["id"], "run_id": job["run_id"], "status": status, **evidence}


def main(argv=None):
    from .native_runtime import runtime_home
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=runtime_home() / "native-schedules.sqlite")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add", help="Register a paused, read-only job")
    add.add_argument("--name", required=True)
    add.add_argument("--workspace", required=True)
    add.add_argument("--prompt-file", type=Path, required=True)
    add.add_argument("--profile", required=True)
    add.add_argument("--interval-seconds", type=int, default=3600)
    add.add_argument("--max-requests", type=int, default=4)
    add.add_argument("--public-readers", action="store_true")
    commands.add_parser("list")
    for name in ("enable", "pause"):
        command = commands.add_parser(name)
        command.add_argument("id")
    commands.add_parser("tick", help="Run one due job, explicitly")
    commands.add_parser("run", help="Run the foreground supervisor; does not install OS auto-start")
    args = parser.parse_args(argv)
    store = ScheduleStore(args.store)
    if args.command == "add":
        identity = store.add(args.name, {"workspace": args.workspace, "prompt": args.prompt_file.read_text(encoding="utf-8"),
            "profile_id": args.profile, "interval_seconds": args.interval_seconds, "max_requests": args.max_requests, "public_readers": args.public_readers})
        print(json.dumps({"id": identity, "enabled": False, "scope": "read-only"}))
    elif args.command == "list":
        print(json.dumps(store.list()))
    elif args.command in {"enable", "pause"}:
        store.enable(args.id, args.command == "enable")
    else:
        with supervisor_lock(store.path):
            store.recover_unknown()
            while True:
                result = tick(store)
                if result:
                    print(json.dumps(result), flush=True)
                if args.command == "tick":
                    break
                time.sleep(5)
    return 0
