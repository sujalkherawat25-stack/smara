"""Bounded recurring wakes into the single native goal ledger.

No model loop, replay or second scheduler service. New schedules start paused;
each wake has a durable goal receipt and awaits user acceptance before another.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid
from .native_goals import GoalStore, goal_spec, workspace_key


def initialize(store):
    with store.connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS goal_schedules (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, spec TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL,
            interval INTEGER NOT NULL, max_runs INTEGER NOT NULL,
            runs INTEGER NOT NULL DEFAULT 0, goal_id TEXT,
            detail TEXT NOT NULL DEFAULT 'Paused until explicitly enabled')""")


def add(store, request):
    initialize(store)
    name = request.get("name", "")
    interval, maximum = request.get("interval_seconds", 3600), request.get("max_runs", 10)
    if (not isinstance(name, str) or not name.strip() or len(name) > 160
            or type(interval) is not int or not 60 <= interval <= 31_536_000
            or type(maximum) is not int or not 1 <= maximum <= 100):
        raise ValueError("Provide a name, interval 60..31536000 seconds and 1..100 wakes")
    spec = goal_spec(request.get("spec") or {})
    # Background recurrence is initially read-only. Editing goals can still be
    # created explicitly with approvals; a schedule is not standing write consent.
    if spec["sandbox"] != "read-only":
        raise ValueError("Recurring wakes require read-only scope")
    if spec.get("browser_origins"):
        raise ValueError("Interactive browser sessions require a foreground human")
    identity = uuid.uuid4().hex
    with store.connect() as db:
        db.execute("INSERT INTO goal_schedules(id,name,spec,due,interval,max_runs) VALUES(?,?,?,?,?,?)",
                   (identity, name.strip(), json.dumps(spec), time.time(), interval, maximum))
    return identity


def schedules(store, workspace=None):
    initialize(store)
    with store.connect() as db:
        rows = db.execute("SELECT * FROM goal_schedules ORDER BY due LIMIT 200").fetchall()
        result = []
        for row in rows:
            item = dict(row); item["spec"] = json.loads(item["spec"])
            if workspace and workspace_key(item["spec"]["workspace"]) != workspace_key(workspace): continue
            if item["goal_id"]:
                goal = db.execute("SELECT state FROM goals WHERE id=?", (item["goal_id"],)).fetchone()
                item["goal_state"] = goal["state"] if goal else "missing"
            result.append(item)
        return result


def enable(store, identity, enabled):
    initialize(store)
    if type(enabled) is not bool: raise ValueError("Enabled must be true or false")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM goal_schedules WHERE id=?", (identity,)).fetchone()
        if not row: raise ValueError("Schedule not found")
        if enabled and row["runs"] >= row["max_runs"]: raise ValueError("Wake allowance exhausted; create a new schedule")
        db.execute("UPDATE goal_schedules SET enabled=?,detail=? WHERE id=?",
                   (int(enabled), "Waiting for next wake / prior result review" if enabled else "Paused by user", identity))


def enqueue_due(store, now=None):
    """Atomic occurrence receipt; skip backlog, never reset a goal's budget."""
    initialize(store)
    now = time.time() if now is None else now
    created = []
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        for row in db.execute("SELECT * FROM goal_schedules WHERE enabled=1 AND due<=? ORDER BY due", (now,)).fetchall():
            if row["runs"] >= row["max_runs"]:
                db.execute("UPDATE goal_schedules SET enabled=0,detail='Wake allowance exhausted' WHERE id=?", (row["id"],)); continue
            if row["goal_id"]:
                prior = db.execute("SELECT state FROM goals WHERE id=?", (row["goal_id"],)).fetchone()
                if not prior or prior["state"] != "completed":
                    db.execute("UPDATE goal_schedules SET detail='Waiting for prior goal acceptance; no automatic replay' WHERE id=?", (row["id"],)); continue
            spec = json.loads(row["spec"])
            identity = "wake" + row["id"] + str(row["runs"] + 1)
            options = {k: spec[k] for k in ("tools_enabled", "workers_enabled", "browser_origins")}
            store.action({**spec, "action": "create", "identity": identity, "options": options}, db)
            db.execute("UPDATE goal_schedules SET runs=runs+1,goal_id=?,due=?,detail='Goal queued in shared ledger' WHERE id=?",
                       (identity, now + row["interval"], row["id"]))
            created.append(identity)
    return created


def network_available():
    """Read local OS connectivity, without probing or sending data to a service."""
    if os.name != "nt": return True
    import ctypes
    flags = ctypes.c_ulong()
    return bool(ctypes.windll.wininet.InternetGetConnectedState(ctypes.byref(flags), 0))


def import_legacy(store, path):
    """Explicit, paused configuration migration; preserve old runs and sessions."""
    from .native_schedule import supervisor_lock
    path = Path(path).resolve(strict=True)
    if path == store.path: raise ValueError("Choose the old schedule database, not the goal ledger")
    initialize(store)
    with supervisor_lock(path), store.connect() as db:
        db.execute("ATTACH DATABASE ? AS legacy_schedule", (str(path),))
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute("SELECT * FROM legacy_schedule.native_jobs").fetchall()
        if any(row["running"] for row in rows): raise ValueError("Old run ownership is uncertain; stop/reconcile it before importing")
        for row in rows:
            old = json.loads(row["spec"])
            spec = goal_spec({"workspace": old["workspace"], "profile_id": old["profile_id"], "objective": old["prompt"],
                "success_criteria": "Provide observed evidence for human review", "sandbox": "read-only",
                "max_requests": old["max_requests"], "max_cycles": 2, "max_token_estimate": old.get("max_token_estimate", 500000),
                "cycle_seconds": old.get("cycle_seconds", 900)})
            identity = hashlib.sha256((str(path) + ":" + row["id"]).encode()).hexdigest()[:32]
            db.execute("INSERT OR IGNORE INTO goal_schedules(id,name,spec,due,interval,max_runs) VALUES(?,?,?,?,?,10)",
                       (identity, row["name"], json.dumps(spec), time.time(), old["interval_seconds"]))
            db.execute("UPDATE legacy_schedule.native_jobs SET enabled=0 WHERE id=?", (row["id"],))
    # Both databases committed together; imported configurations remain paused.
    return schedules(store)


def startup_command(enabled):
    """Explicit user opt-in only; called from settings, never from bootstrap."""
    if os.name != "nt": raise ValueError("Login startup is available only on Windows")
    if enabled and not getattr(sys, "frozen", False):
        raise ValueError("Login startup requires an installed, verified Smara companion")
    desktop = Path(sys.executable).resolve().parent.parent / "smara-desktop.exe"
    if enabled and (Path(sys.executable).resolve().parent.name.casefold() != "resources" or not desktop.is_file()):
        raise ValueError("Login startup requires the installed Desktop companion, not a console CLI")
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
        if enabled:
            import subprocess
            winreg.SetValueEx(key, "SmaraBackground", 0, winreg.REG_SZ,
                             subprocess.list2cmdline([str(desktop), "--background-worker"]))
        else:
            try: winreg.DeleteValue(key, "SmaraBackground")
            except FileNotFoundError: pass  # Already disabled; no other startup entry touched.


def manage(request, store=None):
    supplied = store is not None
    store = store or GoalStore()
    action = request.get("action", "status")
    if action == "add" and not supplied:
        from .native_management import preferences
        from .native_profiles import load_profiles
        spec = request.get("spec") or {}
        if not any(workspace_key(p["workspace"]) == workspace_key(spec.get("workspace", "")) for p in preferences()["projects"]):
            raise ValueError("Choose a registered project")
        if not any(p["id"] == spec.get("profile_id") for p in load_profiles()[0]):
            raise ValueError("Choose a configured model")
    if action == "add": add(store, request)
    elif action in {"enable", "pause"}: enable(store, request.get("id"), action == "enable")
    elif action == "startup":
        if type(request.get("enabled")) is not bool: raise ValueError("Startup choice must be explicit")
        startup_command(request["enabled"])
        with store.connect() as db: store.set_meta(db, "background_startup", request["enabled"])
    elif action not in {"status", "start_service", "stop_service"}: raise ValueError("Unsupported background action")
    if action in {"start_service", "stop_service"}:
        from .native_goal_runtime import service_control
        service_control(store, {"action": action})
    with store.connect() as db: startup = store.meta(db, "background_startup", False)
    return {"service": store.service(), "startup": startup, "schedules": schedules(store, request.get("workspace")),
            "detail": "One goal worker; missed wakes coalesce. Prior results require acceptance; interrupted work stays paused."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["status", "start_service", "stop_service", "enable", "pause", "add", "import-legacy"])
    parser.add_argument("--id"); parser.add_argument("--workspace")
    parser.add_argument("--name"); parser.add_argument("--profile")
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--interval-seconds", type=int, default=3600)
    parser.add_argument("--max-runs", type=int, default=10)
    parser.add_argument("--max-requests", type=int, default=4)
    parser.add_argument("--legacy-store", type=Path)
    args = vars(parser.parse_args(argv))
    if args["action"] == "import-legacy":
        if not args["legacy_store"]: parser.error("import-legacy requires --legacy-store; nothing migrates automatically")
        print(json.dumps(import_legacy(GoalStore(), args["legacy_store"]))); return 0
    if args["action"] == "add":
        if not args["prompt_file"]: parser.error("add requires --prompt-file")
        args["spec"] = {"workspace": args["workspace"], "profile_id": args["profile"],
            "objective": args["prompt_file"].read_text(encoding="utf-8"),
            "success_criteria": "Produce observed evidence for user review", "max_requests": args["max_requests"]}
    print(json.dumps(manage(args)))
    return 0


if __name__ == "__main__": raise SystemExit(main())
