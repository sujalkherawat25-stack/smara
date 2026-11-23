"""Tasks from the owner's phone (WhatsApp/Telegram, through hosted Smara) run here, on the owner's own PC.

The background worker (``--native-goals``) runs this connector next to its goal loop. It uses the device token that
Smara Desktop's sign-in stored for the browser helper (``~/.smara-bridge/config.json``), so the PC only ever connects
out and only sees its own shop's tasks:

- ``/bridge/tasks/next``: a task the owner sent becomes a normal Smara Desktop goal in the current project, with the
  owner's active model, project-editing access and the PC's apps and browser;
- every approval that goal needs goes to the owner's chat as Allow / Deny, and a question it asks (such as a one-time
  code) is asked there too; the answers come back through ``/bridge/tasks/answers``;
- when the goal finishes, its summary goes back to the chat.

Everything the owner sees on the PC (the goal, its activity and outputs) stays in Smara Desktop as usual."""
from __future__ import annotations

import json
import re
import time
import uuid

import httpx

POLL_S = 20
CREDS_RETRY_S = 5
DONE = {"ready_for_review": "done", "completed": "done", "blocked": "failed", "stopped": "failed", "paused": "failed",
        "failed": "failed"}
CODE_WORDS = re.compile(r"\b(otp|one[- ]time|verification code|code|pin)\b", re.I)
# A phone task's work allowance. The goal budget counts request BYTES (a conservative upper bound: one desktop model step is
# about 100 KB, roughly 25-30k real tokens), so a step is budgeted at 120 KB. When it runs out, the owner is asked on the
# phone whether to keep going (EXTRA more steps each time, at most EXTENSIONS times) instead of the task just stopping.
STEPS, STEP_BYTES, CYCLES = 40, 120_000, 8
EXTRA, EXTENSIONS = 20, 2
BUDGET = "budget:"


def credentials() -> tuple[str, str] | None:
    """The hosted server and this PC's device token, or None while not signed in (or signed out by the server)."""
    from .hosted_login import _home, _refused_by_server
    try:
        cfg = json.loads((_home() / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    token, server = str(cfg.get("token") or ""), str(cfg.get("server") or "").rstrip("/")
    if not token or not server.startswith("https://") or _refused_by_server(token):
        return None
    return server, token


def describe(method: str, params: dict) -> str:
    """One line the owner can decide on from their phone."""
    if method == "item/commandExecution/requestApproval":
        command = params.get("command")
        if isinstance(command, list):
            command = " ".join(str(c) for c in command)
        reason = str(params.get("reason") or "").strip()
        return f"run a command on your PC: {str(command)[:600]}" + (f" ({reason[:200]})" if reason else "")
    if method == "item/fileChange/requestApproval":
        reason = str(params.get("reason") or "").strip()
        return "change files in your project" + (f": {reason[:400]}" if reason else "")
    if method == "item/permissions/requestApproval":
        wanted = ", ".join(sorted((params.get("permissions") or {}).keys())) or "more access"
        return f"use {wanted} for this task" + (f" ({str(params.get('reason'))[:300]})" if params.get("reason") else "")
    if method == "mcpServer/elicitation/request":
        return "use an app: " + str(params.get("message") or params.get("serverName") or "")[:600]
    return "continue with a step that needs your OK"


PHONE_PROJECT = "Phone tasks"


def phone_project() -> str:
    """The project folder tasks from the phone run in (Documents/Smara phone tasks). A task there can still read and use
    the rest of the PC, but it never locks the owner's own project, so their Desktop chat keeps working while it runs.
    Created and registered once, with the browsing/tool options of the owner's current project; the owner's selected
    project stays selected."""
    from pathlib import Path
    from .native_management import manage, preferences
    root = Path.home() / "Documents" / "Smara phone tasks"
    root.mkdir(parents=True, exist_ok=True)
    prefs = preferences()
    known = next((p for p in prefs["projects"] if Path(p["workspace"]).resolve() == root.resolve()), None)
    if known:
        return known["workspace"]
    current = prefs.get("workspace") or ""
    source = next((p for p in prefs["projects"] if p["workspace"] == current), prefs["projects"][0] if prefs["projects"] else None)
    out = manage({"operation": "add_project", "workspace": str(root), "label": PHONE_PROJECT})
    workspace = out["preferences"]["workspace"]
    # The owner's own options, but always with tools and the browser: a phone task is useless without them (any public
    # site; local and private addresses stay closed, and every step that changes a page still asks on the phone).
    options = {"tools_enabled": True, "workers_enabled": False, **((source or {}).get("options") or {})}
    options["tools_enabled"] = True
    options["browser_origins"] = options.get("browser_origins") or ["*"]
    manage({"operation": "save_project_options", "workspace": workspace, "options": options})
    if current:
        manage({"operation": "select_project", "workspace": current})
    return workspace


def questions(params: dict) -> list[dict]:
    return [q for q in (params.get("questions") or []) if isinstance(q, dict) and isinstance(q.get("id"), str)]


class HostedConnector:
    def __init__(self, store, http: httpx.Client, server: str, token: str):
        self.store, self.http, self.server = store, http, server
        self.headers = {"Authorization": f"Bearer {token}"}

    def post(self, path: str, body: dict | None = None, timeout: float = 15) -> dict:
        reply = self.http.post(self.server + path, json=body or {}, headers=self.headers, timeout=timeout)
        if reply.status_code == 401:
            raise PermissionError("signed out")
        reply.raise_for_status()
        return reply.json()

    # ---- tasks in
    def defaults(self) -> dict | None:
        from .native_profiles import load_profiles
        profiles, active, _ = load_profiles()
        if not any(p.get("id") == active for p in profiles):
            return None
        return {"workspace": phone_project(), "profile_id": active}

    def pick_up(self, wait: int = POLL_S) -> bool:
        got = self.post(f"/bridge/tasks/next?wait={wait}", timeout=wait + 15)
        if not got.get("task_id"):
            return False
        task_id, task = got["task_id"], got["task"]
        config = self.defaults()
        if config is None:
            self.post(f"/bridge/tasks/{task_id}/result", {"state": "failed", "summary":
                "Smara Desktop has no project folder or model chosen yet. Open Smara Desktop, pick a project and a model "
                "(\"Smara (included)\" works), then ask again."})
            return True
        identity = uuid.uuid5(uuid.NAMESPACE_URL, f"smara-hosted:{task_id}").hex
        from .native_goals import operate
        try:
            operate({**config, "action": "create", "identity": identity, "task_kind": "general", "sandbox": "workspace-write",
                     "use_apps": True, "max_cycles": CYCLES, "max_requests": STEPS, "max_token_estimate": STEPS * STEP_BYTES,
                     "objective": f"{task}\n\n(Sent by the owner from their phone. Do it on this PC by yourself. If a site needs "
                                  "a login, use browser_login (the owner's saved login for that site). If it asks for a one-time "
                                  "code, ask the owner with ONE question that says 'OTP' and then use browser_fill_code; never "
                                  "guess a code. A captcha or a payment is the owner's: use browser_handoff and say so. Save any "
                                  "file you make in this project folder. Your final answer is exactly what the owner reads on "
                                  "their phone: give the actual result (the list, the numbers, the answer, up to about 40 lines; "
                                  "more goes into a file here), not a description of your work, then where any file is.)",
                     "success_criteria": "The owner's request is done on this PC, or it is clear exactly what blocked it."},
                    store=self.store)
        except ValueError as err:
            self.post(f"/bridge/tasks/{task_id}/result", {"state": "failed", "summary": f"Smara Desktop could not start it: {err}"})
            return True
        with self.store.connect() as db:
            mapping = self.store.meta(db, "hosted_tasks", {})
            mapping[identity] = task_id
            self.store.set_meta(db, "hosted_tasks", mapping)
        self.post(f"/bridge/tasks/{task_id}/started", {"goal_id": identity})
        return True

    # ---- what the goals ask and finish with goes out
    def relay(self) -> None:
        with self.store.connect() as db:
            mapping = self.store.meta(db, "hosted_tasks", {})
            cursor = self.store.meta(db, "hosted_event_cursor", 0)
            rows = db.execute("SELECT * FROM goal_events WHERE seq>? ORDER BY seq LIMIT 100", (cursor,)).fetchall()
        for row in rows:
            task_id = mapping.get(row["goal_id"])
            if task_id:
                body = json.loads(row["body"])
                if row["kind"] == "awaiting_approval" and body.get("request_id"):
                    self.ask(task_id, row["goal_id"], body["request_id"])
                elif row["kind"] == "paused" and self.out_of_allowance(row["goal_id"]):
                    self.ask_for_more(task_id, row["goal_id"])
                elif row["kind"] in DONE:
                    self.finish(task_id, row["goal_id"], row["kind"], body)
            with self.store.connect() as db:          # one event at a time, so a crash repeats at most one (the server ignores repeats)
                self.store.set_meta(db, "hosted_event_cursor", row["seq"])

    def ask(self, task_id: str, goal_id: str, request_id: str) -> None:
        with self.store.connect() as db:
            req = db.execute("SELECT method, params FROM goal_requests WHERE id=? AND goal_id=?", (request_id, goal_id)).fetchone()
        if req is None:
            return
        params = json.loads(req["params"])
        if req["method"] == "item/tool/requestUserInput":
            asked = " / ".join(str(q.get("question") or q.get("header") or "") for q in questions(params))[:900]
            kind = "otp" if CODE_WORDS.search(asked) and len(questions(params)) == 1 else "approval"
            if kind == "approval":     # a free-text question cannot be answered with Allow/Deny: ask the owner on the PC
                self.post(f"/bridge/tasks/{task_id}/ask", {"request_id": request_id, "summary":
                          f"answer a question in Smara Desktop: {asked}. (Allow lets you answer it on the PC; Deny stops this step.)"})
                return
            self.post(f"/bridge/tasks/{task_id}/ask", {"request_id": request_id, "kind": "otp", "summary": asked})
            return
        self.post(f"/bridge/tasks/{task_id}/ask", {"request_id": request_id, "summary": describe(req["method"], params)})

    # ---- a task that used up its work allowance asks the owner whether to keep going
    def out_of_allowance(self, goal_id: str) -> bool:
        goal = self.store.get(goal_id) or {}
        spec = goal.get("spec") or {}
        with self.store.connect() as db:
            blocked = self.store.meta(db, "budget_blocked:" + goal_id)
        return bool(blocked) or "budget" in str(goal.get("detail") or "").lower() or (
            bool(spec) and (goal.get("requests", 0) >= spec.get("max_requests", 1) or goal.get("cycles", 0) >= spec.get("max_cycles", 1)
                            or goal.get("token_estimate", 0) >= spec.get("max_token_estimate", 1)))

    def ask_for_more(self, task_id: str, goal_id: str) -> None:
        with self.store.connect() as db:
            asked = self.store.meta(db, "hosted_extensions", {})
            n = int(asked.get(goal_id, 0))
            if n < EXTENSIONS:
                asked[goal_id] = n + 1
                self.store.set_meta(db, "hosted_extensions", asked)
        goal = self.store.get(goal_id) or {}
        if n >= EXTENSIONS:
            self.finish(task_id, goal_id, "paused", {}, stopped=f"it used all its work ({goal.get('requests', 0)} model steps) "
                                                                "without finishing")
            return
        self.post(f"/bridge/tasks/{task_id}/ask", {"request_id": f"{BUDGET}{goal_id}:{n + 1}", "summary":
                  f"keep working on this task. It has used its work allowance ({goal.get('requests', 0)} model steps) and is "
                  f"not finished yet. Allow gives it {EXTRA} more steps; Deny stops it here."})

    def more(self, goal_id: str, allow: bool, task_id: str) -> None:
        if not allow:
            self.finish(task_id, goal_id, "paused", {}, stopped="you chose to stop it when it ran out of work allowance")
            return
        goal = self.store.get(goal_id) or {}
        spec = goal.get("spec") or {}
        if goal.get("state") != "paused":
            return                                     # already resumed (a repeat answer) or finished meanwhile
        room = (spec.get("max_requests", 0) - goal.get("requests", 0) >= EXTRA // 2
                and spec.get("max_token_estimate", 0) - goal.get("token_estimate", 0) >= EXTRA // 2 * STEP_BYTES
                and spec.get("max_cycles", 0) > goal.get("cycles", 0))
        if not room:                                   # on a retry the limits may already be extended
            self.store.action({"action": "extend", "id": goal_id,
                               "max_requests": max(spec.get("max_requests", STEPS), goal.get("requests", 0)) + EXTRA,
                               "max_token_estimate": max(spec.get("max_token_estimate", 0), goal.get("token_estimate", 0)) + EXTRA * STEP_BYTES,
                               "max_cycles": max(spec.get("max_cycles", CYCLES), goal.get("cycles", 0)) + 2})
        self.store.action({"action": "resume", "id": goal_id})

    def finish(self, task_id: str, goal_id: str, state: str, body: dict, stopped: str = "") -> None:
        goal = self.store.get(goal_id) or {}
        checkpoint = goal.get("checkpoint") or {}
        summary = str(checkpoint.get("summary") or body.get("summary") or goal.get("detail") or "").strip()
        if DONE[state] == "failed" and (stopped or not summary or summary.startswith("Cycle interrupted")):
            # The runtime's own wording ("Cycle interrupted before the final checkpoint ...") means nothing on a phone.
            reason = stopped or str(goal.get("detail") or "it stopped before finishing").strip()
            known = "" if summary.startswith("Cycle interrupted") else summary
            summary = (f"It stopped because {reason}." + (f"\n\nSo far: {known}" if known else "") +
                       "\n\nThe steps it took are in Smara Desktop (Goals) on your PC. Ask again with a narrower request if needed.")
        outputs = checkpoint.get("outputs") or []
        if outputs:
            summary += "\n\nFiles on your PC: " + ", ".join(str(o) for o in outputs[:10])
        self.post(f"/bridge/tasks/{task_id}/result", {"state": DONE[state], "summary": summary[:5900]})
        with self.store.connect() as db:
            mapping = self.store.meta(db, "hosted_tasks", {})
            mapping.pop(goal_id, None)
            self.store.set_meta(db, "hosted_tasks", mapping)
            asked = self.store.meta(db, "hosted_extensions", {})
            if asked.pop(goal_id, None) is not None:
                self.store.set_meta(db, "hosted_extensions", asked)

    # ---- the owner's answers come in
    def apply_answers(self) -> int:
        got = self.post("/bridge/tasks/answers").get("answers") or []
        with self.store.connect() as db:
            reverse = {t: g for g, t in self.store.meta(db, "hosted_tasks", {}).items()}
        with self.store.connect() as db:
            waiting = self.store.meta(db, "hosted_more_pending", [])      # "keep going" answers whose goal was still stopping
        got = waiting + got
        later = []
        for a in got:
            goal_id = reverse.get(a["task_id"])
            if not goal_id:
                continue
            try:
                if str(a.get("request_id") or "").startswith(BUDGET):
                    try:
                        self.more(goal_id, a.get("answer") == "allow", a["task_id"])
                    except ValueError:
                        if a.get("tries", 0) < 30:                         # the cycle still holds its lease: try next round
                            later.append({**a, "tries": a.get("tries", 0) + 1})
                    continue
                if a["kind"] == "otp":
                    with self.store.connect() as db:
                        req = db.execute("SELECT params FROM goal_requests WHERE id=?", (a["request_id"],)).fetchone()
                    qs = questions(json.loads(req["params"])) if req else []
                    if len(qs) == 1:
                        self.store.respond(goal_id, a["request_id"], answers={qs[0]["id"]: {"answers": [a["answer"] or ""]}})
                else:
                    with self.store.connect() as db:
                        req = db.execute("SELECT method FROM goal_requests WHERE id=?", (a["request_id"],)).fetchone()
                    if req and req["method"] == "item/tool/requestUserInput":
                        continue                       # Allow here means "I will answer it on the PC": nothing to send
                    self.store.respond(goal_id, a["request_id"], allow=a["answer"] == "allow")
            except ValueError:
                pass                                   # already answered on the PC, or expired: the first answer wins
        with self.store.connect() as db:
            self.store.set_meta(db, "hosted_more_pending", later)
        return len(got) - len(waiting)


def run_connector(store, stop) -> None:
    """Started by the background worker. Waits quietly while the PC is not signed in."""
    while not stop.is_set():
        creds = credentials()
        if creds is None:
            stop.wait(CREDS_RETRY_S)
            continue
        server, token = creds
        try:
            with httpx.Client(timeout=httpx.Timeout(POLL_S + 15, connect=10), follow_redirects=False) as http:
                connector = HostedConnector(store, http, server, token)
                while not stop.is_set() and credentials() == creds:
                    connector.relay()
                    connector.apply_answers()
                    connector.pick_up(wait=5)
                    with store.connect() as db:
                        store.set_meta(db, "hosted_health", {"status": "connected", "last_poll": time.time()})
        except PermissionError:
            with store.connect() as db:
                store.set_meta(db, "hosted_health", {"status": "signed_out", "detail": "Sign in to Smara in Smara Desktop."})
            stop.wait(30)
        except (httpx.HTTPError, OSError, ValueError, RuntimeError):
            with store.connect() as db:
                store.set_meta(db, "hosted_health", {"status": "unavailable", "detail": "Smara could not be reached; retrying."})
            stop.wait(5)
