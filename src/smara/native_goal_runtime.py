"""Bounded continuation over the native app server, with durable human approvals."""
from __future__ import annotations

import contextlib
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

from .native_goals import ACTIVE, GoalStore


MAX_CONCURRENT_GOALS = 2

_FAILURE_STAGE_LABELS = {
    "load_provider_profile": "loading the model profile",
    "start_native_session": "starting the native session",
    "initialize_native_protocol": "initializing the native session",
    "resume_saved_session": "resuming the saved session",
    "start_saved_session": "creating the native session",
    "start_model_turn": "starting the model turn",
    "wait_for_model_turn": "waiting for the model turn",
    "read_session_transcript": "reading the completed session",
    "validate_checkpoint": "validating the progress checkpoint",
    "verify_outcome": "checking outcome evidence",
    "close_native_session": "closing the native session",
    "save_checkpoint": "saving progress",
}
_FAILURE_CONTEXT_LABELS = {
    "workspace_missing": "The selected project folder is unavailable.",
    "native_runtime_missing": "The bundled Smara native executable is unavailable.",
    "runtime_dependency_missing": "Windows could not start a required native runtime file.",
    "required_file_unavailable": "A file required during this phase was unavailable.",
}


def _tag_file_failure(error, context):
    """Attach only a fixed, non-sensitive diagnostic code to a file error."""
    if context in _FAILURE_CONTEXT_LABELS:
        error.smara_failure_context = context
    return error


def _failure_record(error, stage):
    error_type = type(error).__name__
    if not error_type.replace("_", "").isalnum():
        error_type = "Exception"
    context = getattr(error, "smara_failure_context", None)
    if context not in _FAILURE_CONTEXT_LABELS:
        context = "required_file_unavailable" if isinstance(error, FileNotFoundError) else None
    return {
        "stage": stage if stage in _FAILURE_STAGE_LABELS else "start_native_session",
        "error_type": error_type[:80],
        "context": context,
    }


def _failure_detail(failure):
    stage = _FAILURE_STAGE_LABELS[failure["stage"]]
    detail = f"Cycle interrupted while {stage} ({failure['error_type']})."
    context = _FAILURE_CONTEXT_LABELS.get(failure.get("context"))
    if context:
        detail += " " + context
    return detail + " The saved session was retained; inspect this failure before resuming."


def approval_response(method, params, allow):
    if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
        return {"decision": "accept" if allow else "decline"}
    if method == "item/permissions/requestApproval":
        permissions = params.get("permissions")
        if not isinstance(permissions, dict) or set(permissions) - {"network", "fileSystem"}:
            raise ValueError("Unsupported native permissions")
        return {"permissions": permissions if allow else {}, "scope": "turn"}
    if method == "mcpServer/elicitation/request":
        schema = params.get("requestedSchema") or {}
        meta = params.get("_meta") or {}
        if allow and not (params.get("mode") == "form" and schema.get("type") == "object"
                and schema.get("properties") == {} and not schema.get("required")
                and meta.get("codex_approval_kind") == "mcp_tool_call"):
            raise ValueError("This specialized request needs its native client")
        return {"action": "accept" if allow else "decline", "content": {} if allow else None, "_meta": None}
    raise ValueError("This request needs user input; pause and provide goal feedback")


def user_input_response(params, answers):
    """Validate and shape a native user-input response without guessing."""
    questions = params.get("questions")
    if not isinstance(questions, list) or not 1 <= len(questions) <= 100:
        raise ValueError("The native request did not contain valid questions")
    if not isinstance(answers, dict):
        raise ValueError("Provide an answer for every question")
    expected = []
    for question in questions:
        identity = question.get("id") if isinstance(question, dict) else None
        if not isinstance(identity, str) or not identity or len(identity) > 128 or identity in expected:
            raise ValueError("The native request contains an invalid question identifier")
        expected.append(identity)
    if set(answers) != set(expected):
        raise ValueError("Answer every requested question and no others")
    result = {}
    for identity in expected:
        entry = answers[identity]
        values = entry.get("answers") if isinstance(entry, dict) else None
        if (not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], str)
                or not values[0].strip() or len(values[0]) > 10_000):
            raise ValueError("Each answer must be non-empty and at most 10000 characters")
        result[identity] = {"answers": [values[0]]}
    return {"answers": result}


class GoalBudget:
    """Reserve provider requests before sending; reservations survive a crash."""
    def __init__(self, client, store, goal):
        self.client, self.store, self.goal = client, store, goal

    def stream(self, *args, **kwargs):
        payload = dict(kwargs["json"])
        payload["max_tokens"] = min(payload.get("max_tokens", 4096), 4096)
        # UTF-8 bytes + reserved maximum output are a conservative estimate,
        # never a claim about actual token counts or INR billing.
        estimate = len(json.dumps(payload, ensure_ascii=False).encode()) + payload["max_tokens"]
        self.store.reserve(self.goal, estimate)
        return self.client.stream(*args, **{**kwargs, "json": payload})

    def close(self):
        self.client.close()


class NativeGoalSession:
    def __init__(self, adapter, goal, store, *, binary=None):
        from .native_runtime import launch_options, native_binary, runtime_home
        self.goal, self.store = goal, store
        spec = goal["spec"]
        try:
            executable = Path(binary) if binary is not None else native_binary()
        except RuntimeError as exc:
            raise _tag_file_failure(exc, "native_runtime_missing") from None
        workspace = Path(spec["workspace"])
        if not workspace.is_dir():
            raise _tag_file_failure(FileNotFoundError("Project workspace is unavailable"), "workspace_missing")
        if not executable.is_file():
            raise _tag_file_failure(FileNotFoundError("Native executable is unavailable"), "native_runtime_missing")
        options, env = launch_options(adapter, home=runtime_home(), workspace=workspace,
            tools_enabled=spec["tools_enabled"], search_enabled=not spec["tools_enabled"],
            workers_enabled=spec["workers_enabled"], browser_origins=spec["browser_origins"])
        try:
            self.process = subprocess.Popen([str(executable), *options, "app-server", "--listen", "stdio://"],
                cwd=workspace, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except FileNotFoundError as exc:
            if not workspace.is_dir():
                context = "workspace_missing"
            elif not executable.is_file():
                context = "native_runtime_missing"
            else:
                context = "runtime_dependency_missing"
            raise _tag_file_failure(exc, context) from None
        self.messages = queue.Queue()
        self.counter = 0
        self.events = []
        self.pending_requests = []
        self.last_progress_write = 0.0
        self.deadline = time.monotonic() + spec["cycle_seconds"]
        self.last_poll = time.monotonic()

        def read():
            try:
                for line in self.process.stdout:
                    self.messages.put(json.loads(line))
            except (ValueError, OSError):
                pass  # EOF below is an explicit transport failure.
            finally:
                self.messages.put({"transport_exited": True})

        def drain():
            for _line in self.process.stderr:
                pass  # Native diagnostics remain native; provider details stay private.

        threading.Thread(target=read, daemon=True).start()
        threading.Thread(target=drain, daemon=True).start()

    def send(self, message):
        self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def next(self):
        while True:
            current = self.store.control(self.goal["id"])
            with self.store.connect() as db:
                stopping = self.store.meta(db, "service_stop", False)
            if current["state"] not in ACTIVE or stopping:
                if current.get("session_id") and current.get("turn_id"):
                    self.send({"id": "goal-interrupt", "method": "turn/interrupt", "params": {"threadId": current["session_id"], "turnId": current["turn_id"]}})
                raise InterruptedError("Goal paused or supervisor stopped")
            now = time.monotonic()
            if current["state"] == "awaiting_approval":
                self.deadline += now - self.last_poll
            self.last_poll = now
            if now > self.deadline:
                raise TimeoutError("Goal cycle time limit reached")
            for request_id in list(self.pending_requests):
                response = self.store.consume_response(self.goal, request_id)
                if response:
                    self.send(response)
                    self.pending_requests.remove(request_id)
            try:
                message = self.messages.get(timeout=.2)
            except queue.Empty:
                continue
            if message.get("transport_exited"):
                raise RuntimeError("Native goal transport exited")
            method = message.get("method")
            if method == "turn/completed":
                self.events.append(message)
                self.events = self.events[-10:]
            if message.get("id") is not None and message.get("method"):
                # Human decisions are returned verbatim to this native request.
                # Their IDs/lease cannot survive a disconnected generation.
                self.pending_requests.append(self.store.pending(self.goal, message))
            elif method == "turn/started":
                self.store.progress(self.goal, "Model is working on this cycle")
            elif method == "turn/completed":
                self.store.progress(self.goal, "Cycle response received; checking evidence and outcome")
            elif method in {"item/started", "item/completed"}:
                item = message.get("params", {}).get("item", {})
                kind = item.get("type")
                verb = "Started" if method == "item/started" else "Finished"
                labels = {
                    "commandExecution": "project command",
                    "fileChange": "project file update",
                    "mcpToolCall": "connected tool action",
                    "webSearch": "web search",
                    "webFetch": "source page read",
                    "browser": "browser action",
                    "collabAgentToolCall": "parallel worker coordination",
                }
                summary = f"{verb} {labels.get(kind, 'agent step')}"
                self.store.progress(self.goal, summary)
            elif method == "item/agentMessage/delta":
                now = time.monotonic()
                if now - self.last_progress_write >= 8:
                    self.last_progress_write = now
                    self.store.progress(self.goal, "Smara is preparing a progress update")
            return message

    def rpc(self, method, params=None):
        self.counter += 1
        identity = self.counter
        self.send({"id": identity, "method": method, "params": params or {}})
        while True:
            message = self.next()
            if message.get("id") == identity and not message.get("method"):
                if "error" in message:
                    raise RuntimeError("Native goal request failed: " + method)
                return message["result"]

    def wait_turn(self, turn_id):
        while True:
            for message in self.events:
                params = message.get("params", {})
                if message.get("method") == "turn/completed" and params.get("turn", {}).get("id") == turn_id:
                    return params["turn"]
            self.next()

    def close(self):
        if self.process.poll() is None:
            with contextlib.suppress(OSError):
                self.process.stdin.close()
            try:
                self.process.wait(timeout=45)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        for stream in (self.process.stdout, self.process.stderr):
            stream.close()


CHECKPOINT_INSTRUCTIONS = """You are Smara pursuing a persistent user goal. Use the native tools and permissions.
At the beginning inspect saved progress and user feedback. Continue from the actual state;
an interrupted action may already have happened, so inspect before repeating it. Work on one
bounded cycle. Do not claim tests, citations or results you did not observe. Ask for native
approval when more authority is required. Never inspect credentials or ask for keys in chat.
For time-sensitive research read the clock, search, read source pages, and cite sources.
Finish your response with one JSON object (no surrounding prose), using this schema:
{"decision":"continue|blocked|ready_for_review", "summary":"what happened and what remains",
 "plan":["steps"], "completed_steps":["finished steps"], "next_step":"next action",
 "evidence":["observed results or source URLs"], "outputs":["project-relative output file paths"],
 "claims":[{"claim":"a key factual claim", "url":"source URL", "quote":"exact supporting excerpt, at least 20 characters"}]}.
Coding/mixed checks need a plain native test command (python -m pytest, pytest, npm test,
cargo test or go test) after edits. Research/mixed checks need actual fetch_url/source_page
reads, not just search snippets, and claims with exact excerpts from those returned pages.
Include important conclusions as claims; coding-only claims may leave url empty, while every
research/mixed claim needs a source URL and exact quote. Quote presence does not prove semantic
support. The user reviews correctness and answer coverage. Address saved outcome gaps.
Use ready_for_review only when you have evidence addressing all success criteria. Use blocked
when user input or an external change is needed. The user accepts the result to finish the goal.
Never send messages, publish, pay, delete unrelated data or bypass native permission controls.
"""


def checkpoint_from(text, workspace, task_kind="general"):
    clean = text.strip()
    if clean.startswith("```") and clean.endswith("```"):
        clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    value = json.loads(clean)
    if not isinstance(value, dict) or value.get("decision") not in {"continue", "blocked", "ready_for_review"}:
        raise ValueError("Native response did not contain a valid goal checkpoint")
    for key in ("summary", "next_step"):
        if not isinstance(value.get(key), str) or len(value[key]) > 30_000:
            raise ValueError("Invalid goal checkpoint text")
    for key in ("plan", "completed_steps", "evidence", "outputs"):
        entries = value.get(key)
        if not isinstance(entries, list) or len(entries) > 100 or any(not isinstance(entry, str) or len(entry) > 8000 for entry in entries):
            raise ValueError("Invalid goal checkpoint list")
    root = Path(workspace).resolve()
    outputs = []
    for name in value["outputs"]:
        from .native_outcomes import output_record
        outputs.append(output_record(root, name)["path"])
    value["outputs"] = outputs
    claims = value.get("claims", [])
    if not isinstance(claims, list) or len(claims) > 100 or any(
        not isinstance(c, dict)
        or any(not isinstance(c.get(k), str) or not c[k].strip() or len(c[k]) > 4000 for k in ("claim", "quote"))
        or not isinstance(c.get("url", ""), str)
        or len(c.get("url", "")) > 4000
        or (task_kind in {"research", "mixed"} and not c.get("url", "").strip())
        for c in claims
    ):
        raise ValueError("Invalid citation claims")
    value["claims"] = [{"claim": c["claim"], "url": c.get("url", ""), "quote": c["quote"]} for c in claims]
    value.pop("outcome", None)  # A model cannot submit its own verification.
    if value["decision"] == "ready_for_review" and not value["evidence"]:
        raise ValueError("Provide evidence before requesting result review")
    return value


def execute_cycle(store, goal, *, endpoint=None, session_factory=NativeGoalSession, provider_guard=None):
    from .native_provider import ChatEndpoint, ResponsesAdapter
    from .native_profiles import load_profiles, resolve_profile_key
    from .native_runtime import active_profile
    feedback = store.feedback(goal["id"])
    session = None
    started = time.monotonic()
    phase = "load_provider_profile"
    try:
        if endpoint is None:
            profiles, _, credentials = load_profiles()
            profile = active_profile(profiles, goal["spec"]["profile_id"])
            endpoint = ChatEndpoint(profile["base_url"], profile["model"], resolve_profile_key(profile, credentials, allow_environment=False), profile.get("auth_header", "authorization"), profile.get("context_window"))
        with ResponsesAdapter(endpoint) as adapter:
            adapter.client = GoalBudget(provider_guard(adapter.client) if provider_guard else adapter.client, store, goal)
            phase = "start_native_session"
            session = session_factory(adapter, goal, store)
            try:
                phase = "initialize_native_protocol"
                session.rpc("initialize", {"clientInfo": {"name": "smara_goal_supervisor", "version": "1"}, "capabilities": None})
                session.send({"method": "initialized"})
                params = {"cwd": goal["spec"]["workspace"], "approvalPolicy": "on-request", "sandbox": goal["spec"]["sandbox"], "developerInstructions": CHECKPOINT_INSTRUCTIONS}
                if goal.get("session_id"):
                    phase = "resume_saved_session"
                    params["threadId"] = goal["session_id"]
                    thread = session.rpc("thread/resume", params)["thread"]
                    if any(turn.get("status") == "inProgress" for turn in thread.get("turns", [])):
                        raise RuntimeError("Saved session is still active; inspect it in Desktop")
                else:
                    phase = "start_saved_session"
                    thread = session.rpc("thread/start", params)["thread"]
                store.attach(goal, thread["id"])
                prompt = json.dumps({"objective": goal["spec"]["objective"], "success_criteria": goal["spec"]["success_criteria"],
                    "saved_checkpoint": goal["checkpoint"], "feedback": [item["text"] for item in feedback],
                    "cycle": goal["cycles"], "max_cycles": goal["spec"]["max_cycles"],
                    "requests_remaining": goal["spec"]["max_requests"] - goal["requests"],
                    "token_estimate_remaining": goal["spec"]["max_token_estimate"] - goal["token_estimate"],
                    "cycle_seconds": goal["spec"]["cycle_seconds"]}, ensure_ascii=False)
                prompt = json.dumps({"task": json.loads(prompt), "outcome_requirements": {k: goal["spec"].get(k) for k in ("task_kind", "min_sources", "time_sensitive")}}, ensure_ascii=False)
                phase = "start_model_turn"
                turn = session.rpc("turn/start", {"threadId": thread["id"], "input": [{"type": "text", "text": prompt, "text_elements": []}]})["turn"]
                store.attach(goal, thread["id"], turn["id"])
                session.deadline = time.monotonic() + goal["spec"]["cycle_seconds"]
                phase = "wait_for_model_turn"
                completed = session.wait_turn(turn["id"])
                if completed.get("status") != "completed":
                    raise RuntimeError("Native goal turn did not complete")
                phase = "read_session_transcript"
                transcript = session.rpc("thread/read", {"threadId": thread["id"], "includeTurns": True})["thread"]
                last = next(t for t in transcript["turns"] if t["id"] == turn["id"])
                messages = [item.get("text", "") for item in last.get("items", []) if item.get("type") == "agentMessage"]
                phase = "validate_checkpoint"
                checkpoint = checkpoint_from(messages[-1] if messages else "", goal["spec"]["workspace"], goal["spec"].get("task_kind", "general"))
                phase = "verify_outcome"
                from .native_outcomes import review
                checkpoint["outcome"] = review(goal["spec"], checkpoint, transcript, elapsed_seconds=time.monotonic() - started)
                if checkpoint["decision"] == "ready_for_review" and not checkpoint["outcome"]["passed"]:
                    checkpoint["decision"] = "continue"
                    checkpoint["next_step"] = "Resolve outcome evidence gaps: " + "; ".join(c["name"] for c in checkpoint["outcome"]["checks"] if not c["passed"])
                state = {"continue": "queued", "blocked": "blocked", "ready_for_review": "ready_for_review"}[checkpoint["decision"]]
            finally:
                if sys.exc_info()[0] is None:
                    phase = "close_native_session"
                session.close()
        # Keep project ownership until native child/tool cleanup has finished.
        phase = "save_checkpoint"
        store.finish(goal, checkpoint, state, checkpoint["summary"], [item["id"] for item in feedback])
    except InterruptedError:
        store.finish(goal, None, "paused", "Supervisor stopped; resume explicitly")
    except Exception as exc:
        # Never expose a provider body or a prompt through raw exception text.
        failure = _failure_record(exc, phase)
        store.finish(goal, None, "blocked", _failure_detail(failure), failure=failure)
    return store.get(goal["id"])


def service_control(store, request):
    action = request["action"]
    if action == "start_service":
        current = store.service()
        if current.get("running"):
            return current
        args = ([sys.executable, "--native-goals"] if getattr(sys, "frozen", False)
                else [sys.executable, "-m", "smara.native_goal_runtime"])
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            process = subprocess.Popen([*args, "--store", str(store.path)], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags,
                start_new_session=os.name != "nt")
        except OSError:
            raise ValueError("The background worker could not be launched. Restart Smara and try again.") from None
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            current = store.service()
            if current.get("running"):
                return current
            exit_code = process.poll()
            if exit_code is not None:
                raise ValueError(f"The background worker exited before becoming ready (exit code {exit_code}). Restart Smara and retry.")
            time.sleep(.2)
        current = store.service()
        return {**current, "starting": not current.get("running"), "pid": process.pid}
    if action == "stop_service":
        with store.connect() as db:
            store.set_meta(db, "service_stop", True)
        return {"stopping": True}
    if action == "pair":
        return {"code": store.pairing_code(), "expires_seconds": 600}
    with store.connect() as db:
        if action == "unlink":
            store.set_meta(db, "telegram_binding", None)
        binding = store.meta(db, "telegram_binding")
        health = store.meta(db, "telegram_health", {})
    from .native_profiles import resolve_credential
    token = resolve_credential("SMARA_TELEGRAM_BOT_TOKEN", allow_environment=False)
    with store.connect() as db:
        options = store.meta(db, "telegram_options", {})
    namespace = hashlib.sha256(token.encode()).hexdigest()[:24] if token else None
    with store.connect() as db:
        uncertain = bool(db.execute("SELECT 1 FROM telegram_outbox WHERE namespace=? AND state='unknown' LIMIT 1", (namespace,)).fetchone())
    if uncertain:
        health = {**health, "delivery_unknown": True, "detail": "An earlier message or file delivery is uncertain. It was not repeated automatically; request status or that result again if needed."}
    return {"configured": bool(token), "paired": bool(binding and binding.get("namespace") == namespace), "service": store.service(), "health": health,
            "options": {k: options.get(k) for k in ("workspace", "profile_id", "sandbox")}}


def run(store):
    from .native_schedule import supervisor_lock
    stop = threading.Event()
    with supervisor_lock(store.path):
        store.recover()
        with store.connect() as db:
            store.set_meta(db, "service_stop", False)
        def heartbeat():
            while not stop.is_set():
                with store.connect() as db:
                    store.set_meta(db, "service", {"pid": os.getpid(), "heartbeat": time.time()})
                    if store.meta(db, "service_stop", False):
                        stop.set()
                stop.wait(1)
        pulse = threading.Thread(target=heartbeat, daemon=True)
        pulse.start()
        from .native_telegram import run_connector
        telegram = threading.Thread(target=run_connector, args=(store, stop), daemon=True)
        telegram.start()
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_GOALS, thread_name_prefix="smara-goal") as workers:
            futures = set()
            try:
                while not stop.is_set():
                    for future in tuple(futures):
                        if future.done():
                            futures.remove(future)
                            future.result()  # Surface unexpected supervisor faults; do not orphan leases.
                    while len(futures) < MAX_CONCURRENT_GOALS and not stop.is_set():
                        goal = store.claim()
                        if goal is None:
                            break
                        futures.add(workers.submit(execute_cycle, store, goal))
                    stop.wait(.25)
            except KeyboardInterrupt:
                with store.connect() as db:
                    store.set_meta(db, "service_stop", True)
            finally:
                stop.set()
                with store.connect() as db:
                    store.set_meta(db, "service_stop", True)
                workers.shutdown(wait=True, cancel_futures=False)
                pulse.join(timeout=3)
                telegram.join(timeout=3)
                with store.connect() as db:
                    store.set_meta(db, "service", {"pid": os.getpid(), "heartbeat": 0})
    return 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path)
    raise SystemExit(run(GoalStore(parser.parse_args().store)))
