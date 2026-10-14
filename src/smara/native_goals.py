"""Durable goals shared by the native supervisor, Desktop, CLI and Telegram.

This ledger records intent and checkpoints. The source-built native app server
owns planning, tools, sessions and permissions. Model success is ready for review;
only the user's acceptance marks a goal completed.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
import re
from pathlib import Path
import secrets
import sqlite3
import sys
import threading
import time
import uuid

TERMINAL = {"completed", "stopped"}
ACTIVE = {"running", "awaiting_approval"}


def default_path():
    from .native_runtime import runtime_home
    return runtime_home() / "native-goals.sqlite"


def strip_windows_verbatim_prefix(value):
    """Convert Windows extended paths to ordinary drive/UNC spelling."""
    text = os.fspath(value)
    unc_prefix = "\\\\?\\UNC\\"
    if text[:len(unc_prefix)].casefold() == unc_prefix.casefold():
        return "\\\\" + text[len(unc_prefix):]
    drive_prefix = "\\\\?\\"
    if text.startswith(drive_prefix):
        return text[len(drive_prefix):]
    return text


def workspace_path(workspace):
    """Resolve a project and remove Rust's Windows-only verbatim prefix."""
    value = os.fspath(workspace)
    if os.name == "nt":
        value = strip_windows_verbatim_prefix(value)
    text = str(Path(value).resolve())
    return strip_windows_verbatim_prefix(text) if os.name == "nt" else text


def workspace_key(workspace):
    """Return one indexed identity for Python and Rust canonical paths."""
    return os.path.normcase(workspace_path(workspace))


def goal_spec(value):
    requested = Path(str(value.get("workspace", "")))
    if not requested.is_absolute():
        raise ValueError("Choose an absolute project folder")
    root = requested.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Choose an existing project folder")
    result = {"workspace": strip_windows_verbatim_prefix(root) if os.name == "nt" else str(root)}
    for field, limit in (("objective", 100_000), ("success_criteria", 30_000), ("profile_id", 48)):
        text = value.get(field)
        if not isinstance(text, str) or not text.strip() or len(text) > limit:
            raise ValueError(f"Provide {field.replace('_', ' ')} (up to {limit} characters)")
        result[field] = text.strip()
    if value.get("sandbox", "read-only") not in {"read-only", "workspace-write"}:
        raise ValueError("Choose read-only or project editing access")
    result["sandbox"] = value.get("sandbox", "read-only")
    origin_thread_id = value.get("origin_thread_id", "")
    if not isinstance(origin_thread_id, str) or len(origin_thread_id) > 128 or any(ord(char) < 32 for char in origin_thread_id):
        raise ValueError("Invalid conversation association")
    result["origin_thread_id"] = origin_thread_id
    from .native_outcomes import KINDS
    kind = value.get("task_kind", "general")
    if kind not in KINDS:
        raise ValueError("Choose general, coding, research or mixed outcome checks")
    result["task_kind"] = kind
    requirements = value.get("requirements", [])
    if (not isinstance(requirements, list) or len(requirements) > 20
            or any(not isinstance(r, dict) or set(r) != {"id", "description"}
                   or not isinstance(r["id"], str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", r["id"])
                   or not isinstance(r["description"], str) or not r["description"].strip()
                   or len(r["description"]) > 2000 for r in requirements)
            or len({r["id"] for r in requirements}) != len(requirements)):
        raise ValueError("Provide up to 20 distinct outcome requirements with id and description")
    result["requirements"] = requirements
    count = value.get("min_sources", 2)
    if type(count) is not int or not 1 <= count <= 20:
        raise ValueError("Minimum sources must be 1..20")
    result["min_sources"] = count
    sensitive = value.get("time_sensitive", bool(re.search(r"\b(today|latest|current|now|recent)\b", result["objective"], re.I)))
    if type(sensitive) is not bool:
        raise ValueError("Time-sensitive must be true or false")
    result["time_sensitive"] = sensitive
    allowance = value.get("max_output_tokens")
    if allowance is not None and (type(allowance) is not int or not 256 <= allowance <= 131072):
        raise ValueError("Explicit output token limit must be 256..131072, or omitted for the model default")
    if allowance is not None:
        result["max_output_tokens"] = allowance
    for field, default, lower, upper in (("max_cycles", 8, 1, 100), ("max_requests", 30, 1, 500),
            ("max_token_estimate", 500_000, 4096, 20_000_000), ("cycle_seconds", 900, 10, 3600),
            ("interval_seconds", 30, 0, 86400)):
        number = value.get(field, default)
        if type(number) is not int or not lower <= number <= upper:
            raise ValueError(f"{field} must be {lower}..{upper}")
        result[field] = number
    from .native_management import project_options
    result.update(project_options(value.get("options") or {}))
    return result


class GoalStore:
    def __init__(self, path=None):
        self.path = Path(path or default_path()).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS goals (
                    id TEXT PRIMARY KEY, spec TEXT NOT NULL, state TEXT NOT NULL,
                    workspace_key TEXT NOT NULL DEFAULT '',
                    session_id TEXT, turn_id TEXT, lease TEXT, due REAL NOT NULL,
                    created REAL NOT NULL, updated REAL NOT NULL,
                    cycles INTEGER NOT NULL DEFAULT 0, requests INTEGER NOT NULL DEFAULT 0,
                    token_estimate INTEGER NOT NULL DEFAULT 0, stalled INTEGER NOT NULL DEFAULT 0,
                    checkpoint TEXT NOT NULL DEFAULT '{}', detail TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS goal_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, goal_id TEXT NOT NULL,
                    kind TEXT NOT NULL, body TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS goal_feedback (
                    id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, text TEXT NOT NULL,
                    created REAL NOT NULL, consumed INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS goal_requests (
                    id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, lease TEXT NOT NULL,
                    native_id TEXT NOT NULL, method TEXT NOT NULL, params TEXT NOT NULL,
                    response TEXT, state TEXT NOT NULL DEFAULT 'pending', created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS goal_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS telegram_updates (
                    namespace TEXT NOT NULL, update_id INTEGER NOT NULL,
                    PRIMARY KEY(namespace, update_id));
                CREATE TABLE IF NOT EXISTS telegram_outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, namespace TEXT NOT NULL,
                    chat_id INTEGER NOT NULL, text TEXT NOT NULL, artifact TEXT,
                    state TEXT NOT NULL DEFAULT 'pending');
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(goals)")}
            if "workspace_key" not in columns:
                db.execute("ALTER TABLE goals ADD COLUMN workspace_key TEXT NOT NULL DEFAULT ''")
            for row in db.execute("SELECT id,spec FROM goals WHERE workspace_key='' ").fetchall():
                try:
                    project = json.loads(row["spec"])["workspace"]
                    canonical = workspace_key(project)
                except (KeyError, TypeError, ValueError, OSError):
                    continue
                db.execute("UPDATE goals SET workspace_key=? WHERE id=?", (canonical, row["id"]))
            db.execute("CREATE INDEX IF NOT EXISTS goals_workspace_key_idx ON goals(workspace_key,lease,state)")
            db.execute("PRAGMA journal_mode=WAL")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def decode(row):
        if row is None:
            raise ValueError("Goal not found")
        item = dict(row)
        item["spec"] = json.loads(item["spec"])
        item["checkpoint"] = json.loads(item["checkpoint"])
        return item

    @staticmethod
    def event(db, identity, kind, body):
        db.execute("INSERT INTO goal_events(goal_id,kind,body,created) VALUES(?,?,?,?)",
                   (identity, kind, json.dumps(body, ensure_ascii=False), time.time()))

    @staticmethod
    def meta(db, key, default=None):
        row = db.execute("SELECT value FROM goal_meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    @staticmethod
    def set_meta(db, key, value):
        db.execute("INSERT INTO goal_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (key, json.dumps(value)))

    def list(self, workspace=None, db=None):
        if db is None:
            with self.connect() as connection:
                return self.list(workspace, connection)
        if workspace:
            canonical = workspace_key(workspace)
            return [self.decode(row) for row in db.execute("SELECT * FROM goals WHERE workspace_key=? ORDER BY created DESC LIMIT 200", (canonical,))]
        return [self.decode(row) for row in db.execute("SELECT * FROM goals ORDER BY created DESC LIMIT 200")]

    def get(self, identity):
        with self.connect() as db:
            goal = self.decode(db.execute("SELECT * FROM goals WHERE id=?", (identity,)).fetchone())
            goal["events"] = []
            for row in db.execute(
                "SELECT * FROM (SELECT * FROM goal_events WHERE goal_id=? ORDER BY seq DESC LIMIT 100) ORDER BY seq", (identity,)
            ):
                body = json.loads(row["body"])
                failure = body.get("failure")
                safe_failure = None
                if isinstance(failure, dict):
                    stage, error_type, context = failure.get("stage"), failure.get("error_type"), failure.get("context")
                    known_stages = {
                        "load_provider_profile", "start_native_session", "initialize_native_protocol",
                        "resume_saved_session", "start_saved_session", "start_model_turn",
                        "wait_for_model_turn", "read_session_transcript", "validate_checkpoint",
                        "verify_outcome", "close_native_session", "save_checkpoint",
                    }
                    known_contexts = {
                        "workspace_missing", "native_runtime_missing", "runtime_dependency_missing",
                        "required_file_unavailable", "provider_rate_limited",
                    }
                    safe_type = (isinstance(error_type, str) and error_type.replace("_", "").isalnum())
                    if (isinstance(stage, str) and stage in known_stages and safe_type
                            and (context is None or (isinstance(context, str) and context in known_contexts))):
                        safe_failure = {"stage": stage, "error_type": error_type[:80], "context": context}
                safe_body = {"summary": str(body.get("summary", ""))[:2000]}
                if safe_failure:
                    safe_body["failure"] = safe_failure
                goal["events"].append({"seq": row["seq"], "kind": row["kind"], "created": row["created"], "body": safe_body})
            goal["approvals"] = [{**dict(row), "params": json.loads(row["params"])} for row in db.execute(
                "SELECT id,method,params,state FROM goal_requests WHERE goal_id=? AND state='pending'", (identity,))]
            return goal

    def progress(self, goal, summary):
        """Persist a short, user-safe activity update for the Desktop timeline."""
        summary = str(summary).strip()[:240]
        if not summary:
            return
        with self.connect() as db:
            changed = db.execute(
                "UPDATE goals SET detail=?,updated=? WHERE id=? AND lease=? AND state IN ('running','awaiting_approval')",
                (summary, time.time(), goal["id"], goal["lease"]),
            )
            if changed.rowcount:
                self.event(db, goal["id"], "activity", {"summary": summary})

    def control(self, identity):
        with self.connect() as db:
            row = db.execute("SELECT state,lease,session_id,turn_id FROM goals WHERE id=?", (identity,)).fetchone()
            if not row:
                raise ValueError("Goal not found")
            return dict(row)

    def action(self, request, db=None):
        if db is None:
            with self.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                return self.action(request, connection)
        action = request.get("action")
        now = time.time()
        if action == "create":
            spec = goal_spec(request)
            identity = request.get("identity") or uuid.uuid4().hex
            if not isinstance(identity, str) or not identity.isalnum() or len(identity) > 64:
                raise ValueError("Invalid goal identity")
            previous = db.execute("SELECT spec FROM goals WHERE id=?", (identity,)).fetchone()
            if previous and json.loads(previous[0]) != spec:
                raise ValueError("This creation receipt belongs to a different goal")
            inserted = db.execute("INSERT OR IGNORE INTO goals(id,spec,workspace_key,state,due,created,updated) VALUES(?,?,?,'queued',?,?,?)",
                (identity, json.dumps(spec), workspace_key(spec["workspace"]), now, now, now))
            if inserted.rowcount:
                self.event(db, identity, "created", {"summary": "Goal queued", "access": spec["sandbox"]})
            return identity
        identity = request.get("id")
        row = self.decode(db.execute("SELECT * FROM goals WHERE id=?", (identity,)).fetchone())
        state = row["state"]
        if action == "feedback":
            text = request.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > 100_000:
                raise ValueError("Provide feedback up to 100000 characters")
            if state in TERMINAL:
                raise ValueError("This goal has finished; create a new goal")
            feedback_id = request.get("feedback_id") or uuid.uuid4().hex
            added = db.execute("INSERT OR IGNORE INTO goal_feedback VALUES(?,?,?,?,0)",
                (feedback_id, identity, text.strip(), now))
            if added.rowcount:
                db.execute("UPDATE goals SET stalled=0,updated=? WHERE id=?", (now, identity))
                self.event(db, identity, "feedback", {"summary": text.strip()})
                # Feedback invalidates a result pending review. Other paused
                # states stay paused until an explicit resume.
                if state == "ready_for_review":
                    db.execute("UPDATE goals SET state='queued',due=?,updated=? WHERE id=?", (now, now, identity))
        elif action in {"pause", "stop"}:
            if state not in TERMINAL:
                target = "paused" if action == "pause" else "stopped"
                db.execute("UPDATE goals SET state=?,detail=?,updated=? WHERE id=?", (target, "Paused by user" if action == "pause" else "Stopped by user", now, identity))
                self.event(db, identity, target, {"summary": target.capitalize() + " by user"})
        elif action == "resume":
            if row["lease"]:
                raise ValueError("The current cycle is stopping; resume when it has released its session")
            if state in TERMINAL or state == "ready_for_review":
                raise ValueError("This goal has finished or is ready for review")
            if row["cycles"] >= row["spec"]["max_cycles"] or row["requests"] >= row["spec"]["max_requests"]:
                raise ValueError("Goal budget exhausted; explicitly extend its limits before resuming")
            if row["token_estimate"] >= row["spec"]["max_token_estimate"]:
                raise ValueError("Goal token estimate limit reached")
            if self.meta(db, "budget_blocked:" + identity):
                raise ValueError("Extend the cumulative budget before resuming this goal")
            if state != "queued":
                db.execute("UPDATE goals SET state='queued',due=?,detail='',updated=? WHERE id=?", (now, now, identity))
                self.event(db, identity, "resumed", {"summary": "Resume requested; inspect the saved session before continuing"})
        elif action == "extend":
            if row["lease"] or state in TERMINAL:
                raise ValueError("Pause this goal before changing its limits")
            options = {k: row["spec"][k] for k in ("tools_enabled", "workers_enabled", "browser_origins")}
            spec = goal_spec({**row["spec"], "options": options, **{k: request[k] for k in ("max_cycles", "max_requests", "max_token_estimate") if k in request}})
            db.execute("UPDATE goals SET spec=?,updated=? WHERE id=?", (json.dumps(spec), now, identity))
            blocked = self.meta(db, "budget_blocked:" + identity)
            if blocked and any(spec[k] > blocked[k] for k in blocked):
                self.set_meta(db, "budget_blocked:" + identity, None)
            self.event(db, identity, "limits", {"summary": "User updated cumulative limits"})
        elif action == "accept":
            if state != "ready_for_review" or row["lease"]:
                raise ValueError("Review the result after the goal reaches Ready for review")
            from .native_outcomes import validate_acceptance
            validate_acceptance(row)
            db.execute("UPDATE goals SET state='completed',updated=?,detail='Accepted by user' WHERE id=?", (now, identity))
            self.event(db, identity, "completed", {"summary": "Result accepted by user"})
        elif action in {"delete", "dismiss", "clear"}:
            if row["lease"] or state in ACTIVE:
                raise ValueError("This goal still owns a worker lease. Pause or stop it and wait for cleanup before dismissing it.")
            db.execute("DELETE FROM goal_requests WHERE goal_id=?", (identity,))
            db.execute("DELETE FROM goal_feedback WHERE goal_id=?", (identity,))
            db.execute("DELETE FROM goal_events WHERE goal_id=?", (identity,))
            db.execute("DELETE FROM goals WHERE id=?", (identity,))
            return identity
        else:
            raise ValueError("Unsupported goal action")
        return identity

    def claim(self, identity=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for row in db.execute("SELECT * FROM goals WHERE state='queued' AND lease IS NULL AND due<=? AND (? IS NULL OR id=?) ORDER BY due", (time.time(), identity, identity)).fetchall():
                goal = self.decode(row)
                key = workspace_key(goal["spec"]["workspace"])
                if db.execute("SELECT 1 FROM goals WHERE workspace_key=? AND lease IS NOT NULL LIMIT 1", (key,)).fetchone():
                    continue
                interactive = self.meta(db, "interactive:" + key, {})
                if interactive.get("heartbeat", 0) > time.time() - 10 and interactive.get("busy"):
                    continue
                if goal["cycles"] >= goal["spec"]["max_cycles"] or goal["requests"] >= goal["spec"]["max_requests"] or goal["token_estimate"] >= goal["spec"]["max_token_estimate"]:
                    db.execute("UPDATE goals SET state='paused',detail='Cumulative goal budget reached' WHERE id=?", (goal["id"],))
                    self.event(db, goal["id"], "paused", {"summary": "Cumulative goal budget reached"})
                    continue
                lease = uuid.uuid4().hex
                summary = f"Starting cycle {goal['cycles'] + 1} of {goal['spec']['max_cycles']}"
                db.execute("UPDATE goals SET state='running',lease=?,cycles=cycles+1,detail=?,updated=? WHERE id=?", (lease, summary, time.time(), goal["id"]))
                self.event(db, goal["id"], "cycle_started", {"summary": summary})
                return {**goal, "lease": lease, "cycles": goal["cycles"] + 1, "state": "running"}
        return None

    def attach(self, goal, session_id, turn_id=None):
        with self.connect() as db:
            changed = db.execute("UPDATE goals SET session_id=?,turn_id=?,updated=? WHERE id=? AND lease=?",
                (session_id, turn_id, time.time(), goal["id"], goal["lease"]))
            if changed.rowcount != 1:
                raise RuntimeError("Goal lease changed")

    def reserve(self, goal, estimate):
        exhausted = False
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.decode(db.execute("SELECT * FROM goals WHERE id=?", (goal["id"],)).fetchone())
            if row["lease"] != goal["lease"] or row["state"] not in ACTIVE:
                raise RuntimeError("Goal cycle stopped")
            if row["requests"] >= row["spec"]["max_requests"] or row["token_estimate"] + estimate > row["spec"]["max_token_estimate"]:
                exhausted = True
                db.execute("UPDATE goals SET state='paused',detail='Cumulative goal inference budget reached; extend its limits before resuming',updated=? WHERE id=?", (time.time(), goal["id"]))
                self.set_meta(db, "budget_blocked:" + goal["id"], {"max_requests": row["spec"]["max_requests"], "max_token_estimate": row["spec"]["max_token_estimate"]})
            else:
                db.execute("UPDATE goals SET requests=requests+1,token_estimate=token_estimate+? WHERE id=?", (estimate, goal["id"]))
        if exhausted:
            from .native_budget import BudgetLimitError
            raise BudgetLimitError("Cumulative goal inference budget reached")

    def feedback(self, identity):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT id,text FROM goal_feedback WHERE goal_id=? AND consumed=0 ORDER BY created", (identity,))]

    def pending(self, goal, message):
        identity = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO goal_requests(id,goal_id,lease,native_id,method,params,created) VALUES(?,?,?,?,?,?,?)",
                (identity, goal["id"], goal["lease"], json.dumps(message["id"]), message["method"], json.dumps(message.get("params") or {}), time.time()))
            db.execute("UPDATE goals SET state='awaiting_approval',detail='Waiting for your approval in the chat',updated=? WHERE id=? AND lease=? AND state='running'", (time.time(), goal["id"], goal["lease"]))
            self.event(db, goal["id"], "awaiting_approval", {"summary": "Respond to the approval in Smara Desktop", "request_id": identity})
        return identity

    def respond(self, identity, request_id, allow=None, answers=None):
        from .native_goal_runtime import approval_response, user_input_response
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT r.* FROM goal_requests r JOIN goals g ON g.id=r.goal_id WHERE r.id=? AND r.goal_id=? AND r.lease=g.lease AND r.state='pending' AND g.state='awaiting_approval'", (request_id, identity)).fetchone()
            if not row:
                raise ValueError("This approval has expired or was already answered")
            params = json.loads(row["params"])
            if row["method"] == "item/tool/requestUserInput":
                if answers is None:
                    raise ValueError("Answer every requested question before continuing")
                response = user_input_response(params, answers)
            else:
                if type(allow) is not bool:
                    raise ValueError("Choose Allow or Deny for this request")
                response = approval_response(row["method"], params, allow)
            db.execute("UPDATE goal_requests SET response=?,state='answered' WHERE id=?", (json.dumps(response), request_id))
            summary = "Answered" if answers is not None else "Allowed" if allow else "Denied"
            self.event(db, identity, "approval_answered", {"summary": summary, "request_id": request_id})

    def consume_response(self, goal, request_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT r.response,r.native_id FROM goal_requests r JOIN goals g ON g.id=r.goal_id WHERE r.id=? AND r.lease=? AND r.lease=g.lease AND r.state='answered' AND g.state IN ('running','awaiting_approval')", (request_id, goal["lease"])).fetchone()
            if not row:
                return None
            # User answers can contain sensitive values. Keep them only until
            # they are handed to the native request, not in the durable ledger.
            db.execute("UPDATE goal_requests SET state='delivered',response=NULL WHERE id=?", (request_id,))
            remaining = db.execute("SELECT 1 FROM goal_requests WHERE lease=? AND state IN ('pending','answered') LIMIT 1", (goal["lease"],)).fetchone()
            if not remaining:
                db.execute("UPDATE goals SET state='running',detail='' WHERE id=? AND lease=? AND state='awaiting_approval'", (goal["id"], goal["lease"]))
            return {"id": json.loads(row["native_id"]), "result": json.loads(row["response"])}

    def finish(self, goal, checkpoint, state, detail, feedback_ids=(), failure=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.decode(db.execute("SELECT * FROM goals WHERE id=?", (goal["id"],)).fetchone())
            if row["lease"] != goal["lease"]:
                raise RuntimeError("Stale goal lease")
            if row["state"] in {"paused", "stopped"}:
                state, detail = row["state"], row["detail"]
            stalled = row["stalled"]
            if checkpoint:
                old = row["checkpoint"]
                keys = ("completed_steps", "evidence", "outputs")
                stalled = stalled + 1 if old and all(old.get(k) == checkpoint.get(k) for k in keys) else 0
                if state == "queued" and stalled >= 2:
                    state, detail = "blocked", "Two cycles produced no new recorded progress; add feedback before resuming"
            else:
                checkpoint = row["checkpoint"]
            for feedback_id in feedback_ids:
                db.execute("UPDATE goal_feedback SET consumed=1 WHERE id=? AND goal_id=?", (feedback_id, goal["id"]))
            if state == "ready_for_review" and db.execute("SELECT 1 FROM goal_feedback WHERE goal_id=? AND consumed=0 LIMIT 1", (goal["id"],)).fetchone():
                state, detail = "queued", "New user feedback arrived during this cycle; continuing with that feedback"
            db.execute("UPDATE goals SET lease=NULL,turn_id=NULL,state=?,checkpoint=?,stalled=?,detail=?,due=?,updated=? WHERE id=?",
                (state, json.dumps(checkpoint), stalled, detail, time.time() + row["spec"]["interval_seconds"], time.time(), goal["id"]))
            db.execute("UPDATE goal_requests SET state='expired' WHERE lease=? AND state IN ('pending','answered')", (goal["lease"],))
            body = {"summary": detail, "checkpoint": checkpoint}
            if failure:
                body["failure"] = failure
            self.event(db, goal["id"], state, body)

    def interactive(self, workspace, owner, busy):
        key = "interactive:" + workspace_key(workspace)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = self.meta(db, key, {})
            if busy:
                canonical = workspace_key(workspace)
                if db.execute("SELECT 1 FROM goals WHERE lease IS NOT NULL AND workspace_key=? LIMIT 1", (canonical,)).fetchone():
                    raise ValueError("A goal is working in this project; pause it before starting another turn or shell")
                if previous.get("owner") != owner and previous.get("busy") and previous.get("heartbeat", 0) > time.time() - 10:
                    raise ValueError("Another Smara session is working in this project")
                self.set_meta(db, key, {"owner": owner, "busy": True, "heartbeat": time.time()})
            elif previous.get("owner") == owner:
                self.set_meta(db, key, {"owner": owner, "busy": False, "heartbeat": time.time()})

    def recover(self):
        # Only the process holding supervisor_lock may recover abandoned leases.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT id,state FROM goals WHERE lease IS NOT NULL").fetchall()
            for row in rows:
                self.event(db, row["id"], "recovery", {"summary": "Supervisor interrupted; saved session retained. Resume explicitly."})
            db.execute("UPDATE goals SET state=CASE WHEN state='stopped' THEN state ELSE 'paused' END,lease=NULL,turn_id=NULL,detail='Interrupted supervisor; inspect the saved session before resuming' WHERE lease IS NOT NULL")
            db.execute("UPDATE goal_requests SET state='expired' WHERE state IN ('pending','answered')")
            # Telegram sends accepted before a crash have uncertain delivery.
            db.execute("UPDATE telegram_outbox SET state='unknown' WHERE state='sending'")

    def service(self):
        with self.connect() as db:
            value = self.meta(db, "service", {})
            health = self.meta(db, "background_health", {})
        return {**value, "running": bool(value.get("heartbeat", 0) > time.time() - 10), "online": health.get("online")}

    def pairing_code(self):
        code = secrets.token_urlsafe(18)
        with self.connect() as db:
            self.set_meta(db, "pairing", {"digest": hashlib.sha256(code.encode()).hexdigest(), "expires": time.time() + 600})
        return code


@contextmanager
def project_activity(workspace):
    """Exclude concurrent goal cycles while a local CLI/review action owns files."""
    store = GoalStore()
    owner = uuid.uuid4().hex
    store.interactive(workspace, owner, True)
    stopped = threading.Event()
    def pulse():
        while not stopped.wait(2):
            store.interactive(workspace, owner, True)
    thread = threading.Thread(target=pulse, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=3)
        store.interactive(workspace, owner, False)


def operate(request, *, store=None):
    store = store or GoalStore()
    action = request.get("action", "list")
    if action == "list":
        raw_goals = store.list(request.get("workspace"))
        goals = [{**g, "spec": {**g["spec"], "objective": g["spec"]["objective"][:500], "success_criteria": g["spec"]["success_criteria"][:500]},
            "checkpoint": {k: str(g["checkpoint"].get(k, ""))[:2000] for k in ("summary", "next_step")}} for g in raw_goals]
        selected = next((goal for goal in raw_goals if goal["lease"]),
            next((goal for goal in raw_goals if goal["state"] not in TERMINAL), None))
        current = store.get(selected["id"]) if selected else None
        if current:
            current["spec"] = {**current["spec"],
                "objective": current["spec"]["objective"][:1200],
                "success_criteria": current["spec"]["success_criteria"][:2000]}
            current["checkpoint"] = {k: str(current["checkpoint"].get(k, ""))[:5000 if k == "summary" else 1200]
                for k in ("summary", "next_step")}
        return {"goals": goals, "current_goal": current, "service": store.service()}
    if action == "get":
        return store.get(request.get("id"))
    if action in {"delete", "dismiss", "clear"}:
        store.action(request)
        return {"ok": True, "id": request.get("id")}
    if action in {"start_service", "stop_service", "telegram_status", "pair", "unlink"}:
        from .native_goal_runtime import service_control
        return service_control(store, request)
    if action == "save_telegram":
        from .native_management import preferences
        from .native_profiles import load_profiles
        project = next((p for p in preferences()["projects"] if p["workspace"] == request.get("workspace")), None)
        profiles, _, _ = load_profiles()
        if not project or not any(p["id"] == request.get("profile_id") for p in profiles):
            raise ValueError("Choose a registered project and model for Telegram")
        spec = goal_spec({**request, "objective": "Telegram goal defaults", "success_criteria": "User supplied criteria", "options": project.get("options") or {}})
        with store.connect() as db:
            store.set_meta(db, "telegram_options", {k: v for k, v in spec.items() if k not in {"objective", "success_criteria"}} | {"options": project.get("options") or {}})
        from .native_goal_runtime import service_control
        return service_control(store, {"action": "telegram_status"})
    if action == "respond":
        store.respond(request.get("id"), request.get("request_id"), request.get("allow"), request.get("answers"))
        return store.get(request.get("id"))
    if action == "create":
        from .native_management import preferences
        from .native_profiles import load_profiles
        spec = goal_spec(request)
        projects = preferences()["projects"]
        project = next((p for p in projects if os.path.normcase(p["workspace"]) == os.path.normcase(spec["workspace"])), None)
        if not project:
            raise ValueError("Add this project in Smara Settings first")
        profiles, _, _ = load_profiles()
        if not any(p["id"] == spec["profile_id"] for p in profiles):
            raise ValueError("Choose a configured model")
        request = {**spec, "action": "create", "options": project.get("options") or {}, **({"identity": request["identity"]} if request.get("identity") else {})}
    identity = store.action(request)
    return store.get(identity)


def main(argv=None):
    from .native_runtime import configure_protocol_stdio
    configure_protocol_stdio(sys.stdin, sys.stdout)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    create = commands.add_parser("add")
    create.add_argument("--workspace", required=True)
    create.add_argument("--profile", required=True)
    create.add_argument("--objective", required=True)
    create.add_argument("--success-criteria", required=True)
    create.add_argument("--sandbox", choices=["read-only", "workspace-write"], default="read-only")
    create.add_argument("--task-kind", choices=["general", "coding", "research", "mixed"], default="general")
    create.add_argument("--min-sources", type=int, default=2)
    for name, default in (("max-cycles", 8), ("max-requests", 30), ("max-token-estimate", 500000), ("cycle-seconds", 300), ("interval-seconds", 30)):
        create.add_argument("--" + name, type=int, default=default)
    for action in ("show", "pause", "stop", "resume", "accept", "feedback", "extend"):
        command = commands.add_parser(action)
        command.add_argument("id")
        if action == "feedback":
            command.add_argument("text")
        if action == "extend":
            for name in ("max-cycles", "max-requests", "max-token-estimate"):
                command.add_argument("--" + name, type=int)
    commands.add_parser("start", help="Start the background goal supervisor and configured Telegram connector")
    commands.add_parser("shutdown", help="Stop the supervisor and interrupt its active cycle")
    commands.add_parser("run", help="Run the supervisor in this terminal")
    telegram = commands.add_parser("telegram")
    telegram.add_argument("action", choices=["status", "pair", "unlink", "key", "configure"])
    telegram.add_argument("--workspace")
    telegram.add_argument("--profile")
    telegram.add_argument("--sandbox", choices=["read-only", "workspace-write"], default="read-only")
    args = vars(parser.parse_args(argv))
    store = GoalStore(args.pop("store"))
    command = args.pop("command")
    if command == "run":
        from .native_goal_runtime import run
        return run(store)
    if command == "telegram":
        if args["action"] == "key":
            import getpass
            from .native_management import manage
            manage({"operation": "save_credential", "name": "SMARA_TELEGRAM_BOT_TOKEN", "provider": "telegram", "secret": getpass.getpass("Telegram bot token: ")})
            print("Telegram bot token saved in the protected credential store.")
            return 0
        args["action"] = {"status": "telegram_status", "pair": "pair", "unlink": "unlink", "configure": "save_telegram"}[args["action"]]
    else:
        args["action"] = {"add": "create", "show": "get", "start": "start_service", "shutdown": "stop_service"}.get(command, command)
    if "profile" in args:
        args["profile_id"] = args.pop("profile")
    result = operate({k: v for k, v in args.items() if v is not None}, store=store)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
