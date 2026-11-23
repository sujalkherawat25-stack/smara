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
        from .native_management import preferences
        from .native_profiles import load_profiles
        prefs = preferences()
        workspace = prefs.get("workspace") or next((p["workspace"] for p in prefs.get("projects") or []), "")
        profiles, active, _ = load_profiles()
        if not workspace or not any(p.get("id") == active for p in profiles):
            return None
        return {"workspace": workspace, "profile_id": active}

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
                     "use_apps": True,
                     "objective": f"{task}\n\n(Sent by the owner from their phone. Do it on this PC. Finish with a short plain "
                                  "summary of what you did and where any file is.)",
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

    def finish(self, task_id: str, goal_id: str, state: str, body: dict) -> None:
        goal = self.store.get(goal_id) or {}
        checkpoint = goal.get("checkpoint") or {}
        summary = str(checkpoint.get("summary") or body.get("summary") or goal.get("detail") or "").strip()
        outputs = checkpoint.get("outputs") or []
        if outputs:
            summary += "\n\nFiles on your PC: " + ", ".join(str(o) for o in outputs[:10])
        self.post(f"/bridge/tasks/{task_id}/result", {"state": DONE[state], "summary": summary[:5900]})
        with self.store.connect() as db:
            mapping = self.store.meta(db, "hosted_tasks", {})
            mapping.pop(goal_id, None)
            self.store.set_meta(db, "hosted_tasks", mapping)

    # ---- the owner's answers come in
    def apply_answers(self) -> int:
        got = self.post("/bridge/tasks/answers").get("answers") or []
        with self.store.connect() as db:
            reverse = {t: g for g, t in self.store.meta(db, "hosted_tasks", {}).items()}
        for a in got:
            goal_id = reverse.get(a["task_id"])
            if not goal_id:
                continue
            try:
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
        return len(got)


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
