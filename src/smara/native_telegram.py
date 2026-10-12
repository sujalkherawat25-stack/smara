"""Private Telegram controls for the local native goal ledger."""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import time
import uuid

import httpx


HELP = """Smara on your laptop
/goal objective | success criteria — start a goal
/goal coding: objective | criteria — require native test evidence (also research: or mixed:)
/status [goal ID] — progress and remaining work
/feedback goal-ID your feedback
/pause goal-ID · /resume goal-ID · /stop goal-ID
/result goal-ID — summary and output list
/result goal-ID 1 — send that output file
/project — selected project
Plain messages give feedback to your last goal, or start a new goal.
Approvals and result acceptance are available in Desktop → Goals.
The laptop must be awake and online, with its goal supervisor running."""


class TelegramError(RuntimeError):
    pass


def pieces(text):
    # Telegram's limit uses UTF-16 character counting for non-BMP content.
    result, current, count = [], [], 0
    for char in text or "Smara has no result yet.":
        width = len(char.encode("utf-16-le")) // 2
        if count + width > 4000:
            result.append("".join(current))
            current, count = [], 0
        current.append(char)
        count += width
    if current:
        result.append("".join(current))
    return result


class TelegramConnector:
    def __init__(self, store, token, http):
        self.store, self.http = store, http
        self.base = "https://api.telegram.org/bot" + token
        self.namespace = hashlib.sha256(token.encode()).hexdigest()[:24]

    def api(self, method, payload, *, files=None):
        try:
            response = self.http.post(self.base + "/" + method, **({"data": payload, "files": files} if files else {"json": payload}))
            if response.status_code != 200:
                raise TelegramError(f"Telegram request rejected (HTTP {response.status_code})")
            value = response.json()
            if not isinstance(value, dict) or value.get("ok") is not True:
                raise TelegramError("Telegram did not accept the request")
            return value["result"]
        except (httpx.HTTPError, ValueError, KeyError):
            # HTTP exceptions contain the token-bearing URL; never propagate it.
            raise TelegramError("Telegram connection or response failed") from None

    def enqueue(self, db, chat_id, text, artifact=None):
        for part in pieces(text):
            db.execute("INSERT INTO telegram_outbox(namespace,chat_id,text,artifact) VALUES(?,?,?,?)",
                (self.namespace, chat_id, part, json.dumps(artifact) if artifact else None))
            artifact = None

    def handle(self, update):
        if not isinstance(update, dict):
            return
        update_id = update.get("update_id")
        if type(update_id) is not int:
            return
        message = update.get("message")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            inserted = db.execute("INSERT OR IGNORE INTO telegram_updates VALUES(?,?)", (self.namespace, update_id))
            if not inserted.rowcount:
                return
            if isinstance(message, dict) and message.get("chat", {}).get("type") == "private":
                sender = message.get("from") or {}
                text = message.get("text")
                if type(sender.get("id")) is int and type(message["chat"].get("id")) is int and not sender.get("is_bot") and isinstance(text, str):
                    # Savepoints keep the inbox receipt atomic even when a
                    # rejected command must roll back a partial mutation.
                    db.execute("SAVEPOINT telegram_command")
                    try:
                        self.command(db, update_id, sender["id"], message["chat"]["id"], text.strip(), message.get("date", 0))
                        db.execute("RELEASE telegram_command")
                    except (ValueError, OSError) as exc:
                        db.execute("ROLLBACK TO telegram_command")
                        db.execute("RELEASE telegram_command")
                        detail = str(exc) if isinstance(exc, ValueError) else "The selected local project is unavailable"
                        self.enqueue(db, message["chat"]["id"], detail)
            previous = self.store.meta(db, "telegram_offset:" + self.namespace, 0)
            self.store.set_meta(db, "telegram_offset:" + self.namespace, max(previous, update_id + 1))

    def command(self, db, update_id, user_id, chat_id, text, date):
        command, _, args = text.partition(" ")
        command = command.lower().split("@", 1)[0]
        binding = self.store.meta(db, "telegram_binding")
        if command == "/link":
            pairing = self.store.meta(db, "pairing", {}) or {}
            digest = hashlib.sha256(args.strip().encode()).hexdigest()
            if pairing.get("expires", 0) < time.time() or not hmac.compare_digest(pairing.get("digest", ""), digest):
                return  # Avoid revealing pairing information to strangers.
            self.store.set_meta(db, "pairing", None)
            self.store.set_meta(db, "telegram_binding", {"namespace": self.namespace, "user_id": user_id, "chat_id": chat_id})
            self.store.set_meta(db, "telegram_event_cursor", db.execute("SELECT COALESCE(MAX(seq),0) FROM goal_events").fetchone()[0])
            self.enqueue(db, chat_id, "Connected to Smara on your laptop.\n" + HELP)
            return
        if not binding or binding != {"namespace": self.namespace, "user_id": user_id, "chat_id": chat_id}:
            return
        if command in {"/start", "/help"}:
            self.enqueue(db, chat_id, HELP)
            return
        configuration = self.store.meta(db, "telegram_options", {})
        if command == "/project":
            self.enqueue(db, chat_id, configuration.get("workspace") or "Choose the Telegram project in Desktop Settings → Integrations.")
            return
        if command == "/status" and not args.strip():
            goals = self.store.list(db=db)
            self.enqueue(db, chat_id, "\n".join(f"{g['id'][:8]} · {g['state']} · {g['spec']['objective'][:150]}" for g in goals[:15]) or "No goals yet. Send /goal followed by your objective.")
            return
        identity, _, rest = args.partition(" ")
        if command in {"/status", "/result", "/pause", "/stop", "/resume", "/feedback"}:
            goals = self.store.list(db=db)
            matches = [g for g in goals if identity and g["id"].startswith(identity)]
            if len(matches) != 1:
                raise ValueError("Use one unique goal ID from /status")
            goal = matches[0]
            identity = goal["id"]
            if command in {"/status", "/result"}:
                checkpoint = goal["checkpoint"]
                from .native_outcomes import progress_text
                output = f"{identity[:8]} · {goal['state']}\n{goal['spec']['objective']}\n{progress_text(goal)}\nCycles: {goal['cycles']}/{goal['spec']['max_cycles']} · Requests: {goal['requests']}/{goal['spec']['max_requests']}"
                outputs = checkpoint.get("outputs", [])
                if outputs:
                    output += "\nOutputs:\n" + "\n".join(f"{i+1}. {path}" for i, path in enumerate(outputs))
                artifact = None
                if command == "/result" and rest.strip():
                    if not rest.strip().isdigit() or not 1 <= int(rest) <= len(outputs):
                        raise ValueError("Choose an output number from /result")
                    artifact = {"goal_id": identity, "path": outputs[int(rest) - 1]}
                self.enqueue(db, chat_id, output, artifact)
                return
            if command in {"/resume", "/feedback"} and time.time() - date > 1800:
                raise ValueError("This command was queued while the laptop was offline. Send it again to confirm it is still wanted.")
            request = {"action": command[1:], "id": identity}
            if command == "/feedback":
                request.update(text=rest, feedback_id=f"tg:{self.namespace}:{update_id}")
            self.store.action(request, db)
            self.enqueue(db, chat_id, f"{identity[:8]} · {command[1:]} recorded")
            return
        if command.startswith("/") and command != "/goal":
            raise ValueError("Use /help for supported commands")
        if time.time() - date > 1800:
            raise ValueError("This request arrived while the laptop was offline. Send it again when you want it to start.")
        last_goal = self.store.meta(db, "telegram_last_goal")
        last = next((g for g in self.store.list(db=db) if g["id"] == last_goal), None)
        if command != "/goal" and last and last["state"] not in {"completed", "stopped"}:
            self.store.action({"action": "feedback", "id": last_goal, "text": text, "feedback_id": f"tg:{self.namespace}:{update_id}"}, db)
            self.enqueue(db, chat_id, f"Feedback saved for {last_goal[:8]}. It will be used in the next cycle.")
            return
        objective, _, criteria = (args if command == "/goal" else text).partition("|")
        kind = "general"
        for prefix in ("coding", "research", "mixed"):
            if objective.strip().lower().startswith(prefix + ":"):
                kind, objective = prefix, objective.strip()[len(prefix) + 1:].strip()
                break
        if not configuration.get("workspace") or not configuration.get("profile_id"):
            raise ValueError("Choose the Telegram project and model in Desktop Settings → Integrations")
        # Goal creation and receipt/offset/outbox are one SQLite transaction.
        identity = uuid.uuid5(uuid.NAMESPACE_URL, f"smara:{self.namespace}:{update_id}").hex
        self.store.action({**configuration, "action": "create", "identity": identity,
            "task_kind": kind,
            "objective": objective, "success_criteria": criteria.strip() or "Deliver the requested result with supporting evidence and explain unresolved gaps."}, db)
        self.store.set_meta(db, "telegram_last_goal", identity)
        self.enqueue(db, chat_id, f"Goal {identity[:8]} queued.\n{objective.strip()}\nUse /status {identity[:8]} or send feedback.")

    def notifications(self):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            binding = self.store.meta(db, "telegram_binding")
            if not binding or binding.get("namespace") != self.namespace:
                return
            cursor = self.store.meta(db, "telegram_event_cursor", 0)
            rows = db.execute("SELECT * FROM goal_events WHERE seq>? ORDER BY seq LIMIT 100", (cursor,)).fetchall()
            for row in rows:
                if row["kind"] in {"queued", "blocked", "ready_for_review", "completed", "awaiting_approval", "paused", "stopped", "recovery"}:
                    body = json.loads(row["body"])
                    self.enqueue(db, binding["chat_id"], f"{row['goal_id'][:8]} · {row['kind']}\n{body.get('summary', '')[:2000]}")
            if rows:
                self.store.set_meta(db, "telegram_event_cursor", rows[-1]["seq"])

    def flush(self):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM telegram_outbox WHERE namespace=? AND state='pending' ORDER BY id LIMIT 1", (self.namespace,)).fetchone()
            if not row:
                return False
            binding = self.store.meta(db, "telegram_binding")
            if not binding or binding.get("namespace") != self.namespace or binding.get("chat_id") != row["chat_id"]:
                db.execute("UPDATE telegram_outbox SET state='cancelled' WHERE id=?", (row["id"],))
                return True
            db.execute("UPDATE telegram_outbox SET state='sending' WHERE id=?", (row["id"],))
        state = "sent"
        try:
            if row["artifact"]:
                artifact = json.loads(row["artifact"])
                goal = self.store.get(artifact["goal_id"])
                name = artifact["path"]
                root = Path(goal["spec"]["workspace"]).resolve()
                if name not in goal["checkpoint"].get("outputs", []):
                    raise TelegramError("The selected output is not recorded")
                from .native_outcomes import output_record
                checked = output_record(root, name)
                saved = next((item for item in goal["checkpoint"].get("outcome", {}).get("outputs", []) if item["path"] == checked["path"]), None)
                if saved and saved != checked:
                    raise TelegramError("Output changed since verification; request a fresh review")
                path = (root / checked["path"]).resolve(strict=True)
                if not path.is_relative_to(root):
                    raise TelegramError("Output moved outside the project")
                with path.open("rb") as handle:
                    data = handle.read(25 * 1024 * 1024 + 1)
                if len(data) != checked["bytes"] or hashlib.sha256(data).hexdigest() != checked["sha256"]:
                    raise TelegramError("Output changed during retrieval")
                # Upload exactly the checked bytes, not a mutable open file.
                self.api("sendDocument", {"chat_id": str(row["chat_id"]), "caption": f"Smara result · {goal['id'][:8]}"}, files={"document": (path.name, data)})
            else:
                self.api("sendMessage", {"chat_id": row["chat_id"], "text": row["text"], "link_preview_options": {"is_disabled": True}})
        except (TelegramError, OSError, ValueError):
            state = "unknown"  # An accepted send may have lost its response.
        with self.store.connect() as db:
            db.execute("UPDATE telegram_outbox SET state=? WHERE id=?", (state, row["id"]))
            if state == "unknown":
                self.store.set_meta(db, "telegram_health", {"status": "delivery_unknown", "detail": "A message or file delivery is uncertain; it was not repeated automatically."})
        return True


def run_connector(store, stop):
    from .native_profiles import resolve_credential
    while not stop.is_set():
        token = resolve_credential("SMARA_TELEGRAM_BOT_TOKEN", allow_environment=False)
        if not token:
            stop.wait(3)
            continue
        try:
            with httpx.Client(timeout=httpx.Timeout(35, connect=10), follow_redirects=False, trust_env=False) as http:
                connector = TelegramConnector(store, token, http)
                if connector.api("getWebhookInfo", {}).get("url"):
                    raise TelegramError("This bot uses a webhook. Configure a dedicated bot for this laptop.")
                while not stop.is_set():
                    if resolve_credential("SMARA_TELEGRAM_BOT_TOKEN", allow_environment=False) != token:
                        break
                    connector.notifications()
                    if not stop.is_set():
                        connector.flush()
                    with store.connect() as db:
                        offset = store.meta(db, "telegram_offset:" + connector.namespace, 0)
                    updates = connector.api("getUpdates", {"offset": offset, "timeout": 3, "allowed_updates": ["message"]})
                    if not isinstance(updates, list):
                        raise TelegramError("Telegram returned an invalid updates list")
                    for update in updates:
                        connector.handle(update)
                    with store.connect() as db:
                        store.set_meta(db, "telegram_health", {"status": "connected", "last_poll": time.time()})
        except (TelegramError, OSError, ValueError):
            with store.connect() as db:
                store.set_meta(db, "telegram_health", {"status": "unavailable", "detail": "Check the bot token, internet connection, and whether another bot worker/webhook is active."})
            stop.wait(5)
