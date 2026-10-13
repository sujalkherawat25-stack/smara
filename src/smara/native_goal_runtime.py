"""Bounded continuation over the native app server, with durable human approvals."""
from __future__ import annotations

import contextlib
from collections import OrderedDict
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
    "provider_rate_limited": "The provider returned HTTP 429. Wait for its capacity before resuming; completed actions were retained.",
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


def turn_failure(turn):
    error = RuntimeError("Native goal turn did not complete")
    info = (turn.get("error") or {}).get("codexErrorInfo") or (turn.get("error") or {}).get("codex_error_info")
    if isinstance(info, dict) and any(isinstance(value, dict) and (value.get("httpStatusCode") or value.get("http_status_code")) == 429 for value in info.values()):
        error.smara_failure_context = "provider_rate_limited"
    return error


def checkpoint_needed(store, goal_id, root_thread_id, request_thread_id):
    if not root_thread_id or request_thread_id != root_thread_id:
        return False
    current = store.get(goal_id)
    needed = current["spec"]["max_requests"] - current["requests"] <= 1
    if needed:
        store.progress(current, "Final allowed request: recording observed progress without new tool actions")
    return needed


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
        from .native_budget import output_payload, reservation_estimate
        payload = output_payload(kwargs["json"], self.goal["spec"].get("max_output_tokens"))
        # UTF-8 bytes + reserved maximum output are a conservative estimate,
        # never a claim about actual token counts or INR billing.
        estimate = reservation_estimate(payload)
        self.store.reserve(self.goal, estimate)
        return self.client.stream(*args, **{**kwargs, "json": payload})

    def close(self):
        self.client.close()


class NativeGoalSession:
    def __init__(self, adapter, goal, store, *, binary=None):
        from .native_runtime import launch_options, native_binary, runtime_home
        self.goal, self.store = goal, store
        self.adapter = adapter
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
        self.observed_items = OrderedDict()
        self.root_thread_id = None
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
        if message.get("method") == "turn/interrupt" and self.adapter.memory is not None:
            params = message.get("params") or {}
            self.adapter.memory.cancel(params.get("threadId"), params.get("turnId"))
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
                if method == "item/completed" and message.get("params", {}).get("threadId") == self.root_thread_id:
                    self.observed_items[str(item.get("id"))] = item
                    while len(self.observed_items) > 500:
                        self.observed_items.popitem(last=False)
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
 "claims":[{"claim":"a key factual claim", "url":"source URL", "quote":"exact supporting excerpt, at least 20 characters"}],
 "coverage":[{"requirement_id":"provided requirement ID", "claim_indexes":[0], "outputs":[], "test_commands":[]}]}.
Coding/mixed checks need a plain native test command (python -m pytest, pytest, npm test,
cargo test or go test) after edits. Research/mixed checks need actual fetch_url/source_page
reads, not just search snippets, and claims with exact excerpts from those returned pages.
Include important conclusions as claims; coding claims may leave url empty, including the
coding part of a mixed task. Research claims need a source URL and exact quote. Local code
claims cannot count toward the research citation gate. Quote presence does not prove semantic
support. The user reviews correctness and answer coverage. Address saved outcome gaps.
Report HTTP failures and tool diagnostics in evidence/summary, not as claims quoting a
published source. Copy exact returned source text, not its JSON-escaped representation.
For explicit outcome requirements, include coverage entries with requirement_id and
claim_indexes (zero-based), outputs, and test_commands linking that requirement to
observed evidence. Do not mark unsupported requirements covered. Use an empty coverage
list if no explicit requirements were provided. A linkage is not semantic proof.
Use ready_for_review only when you have evidence addressing all success criteria. Use blocked
when user input or an external change is needed. The user accepts the result to finish the goal.
Never send messages, publish, pay, delete unrelated data or bypass native permission controls.
"""

CHECKPOINT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "decision": {"type": "string", "enum": ["continue", "blocked", "ready_for_review"]},
        **{k: {"type": "string"} for k in ("summary", "next_step")},
        **{k: {"type": "array", "items": {"type": "string"}} for k in ("plan", "completed_steps", "evidence", "outputs")},
        "claims": {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "properties": {k: {"type": "string"} for k in ("claim", "url", "quote")},
            "required": ["claim", "url", "quote"]}},
        "coverage": {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "properties": {"requirement_id": {"type": "string"},
                "claim_indexes": {"type": "array", "items": {"type": "integer", "minimum": 0}},
                "outputs": {"type": "array", "items": {"type": "string"}},
                "test_commands": {"type": "array", "items": {"type": "string"}}},
            "required": ["requirement_id", "claim_indexes", "outputs", "test_commands"]}},
    },
    "required": ["decision", "summary", "next_step", "plan", "completed_steps", "evidence", "outputs", "claims", "coverage"],
}


def checkpoint_json(text):
    """Recover one complete object, never repair truncated JSON or choose between objects.

    This parses text already returned; it sends no request and replays no tools.
    Schema/outputs/evidence are still validated by checkpoint_from.
    """
    if not isinstance(text, str) or len(text) > 250_000:
        raise ValueError("Checkpoint text is missing or too large")
    clean = text.strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        candidates = []
        offset = 0
        while offset < len(clean):
            start = clean.find("{", offset)
            if start < 0:
                break
            try:
                value, end = decoder.raw_decode(clean[start:])
            except json.JSONDecodeError:
                raise ValueError("Native response contains an incomplete goal checkpoint") from None
            if isinstance(value, dict) and "decision" in value:
                candidates.append(value)
            offset = start + end
        if len(candidates) != 1:
            raise ValueError("Native response must contain one unambiguous complete goal checkpoint")
        return candidates[0]


def checkpoint_from(text, workspace, task_kind="general"):
    value = checkpoint_json(text)
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
        or (task_kind == "research" and not c.get("url", "").strip())
        for c in claims
    ):
        raise ValueError("Invalid citation claims")
    value["claims"] = [{"claim": c["claim"], "url": c.get("url", ""), "quote": c["quote"]} for c in claims]
    coverage = value.get("coverage", [])
    # A prose label carries no authority or evidence. Some providers include
    # it even when only linkage fields were requested. Drop this one bounded
    # presentation field locally; never repair missing/invalid evidence links.
    if isinstance(coverage, list):
        coverage = [{k: v for k, v in entry.items() if k != "description"}
                    if isinstance(entry, dict) and isinstance(entry.get("description"), str) and len(entry["description"]) <= 4000
                    else entry for entry in coverage]
    if (not isinstance(coverage, list) or len(coverage) > 20 or any(
        not isinstance(entry, dict) or set(entry) != {"requirement_id", "claim_indexes", "outputs", "test_commands"}
        or not isinstance(entry["requirement_id"], str) or len(entry["requirement_id"]) > 48
        or not isinstance(entry["claim_indexes"], list) or len(entry["claim_indexes"]) > 100
        or any(type(index) is not int or not 0 <= index < len(claims) for index in entry["claim_indexes"])
        or any(not isinstance(entry[k], list) or len(entry[k]) > 100 or
               any(not isinstance(s, str) or len(s) > 8000 for s in entry[k]) for k in ("outputs", "test_commands"))
        for entry in coverage) or len({entry["requirement_id"] for entry in coverage}) != len(coverage)):
        raise ValueError("Invalid requirement coverage links")
    value["coverage"] = coverage
    value.pop("outcome", None)  # A model cannot submit its own verification.
    if value["decision"] == "ready_for_review" and not value["evidence"]:
        raise ValueError("Provide evidence before requesting result review")
    return value


def partial_checkpoint(session, goal, elapsed_seconds):
    """Record native observations after interruption; no model call or tool replay."""
    prior_turns = list(getattr(session, "observed_prior_transcript", {}).get("turns", []))
    current_items = list(getattr(session, "observed_items", {}).values())
    # Preserve the resumed session's actual evidence, not just events emitted
    # since reconnect. Native item IDs prevent a repeated notification from
    # moving an old edit after its passing test.
    prior_ids = {item.get("id") for turn in prior_turns for item in turn.get("items", []) if item.get("id")}
    current_items = [item for item in current_items if not item.get("id") or item["id"] not in prior_ids]
    items = [item for turn in prior_turns for item in turn.get("items", [])] + current_items
    if not items:
        return None
    from .native_outcomes import review, output_record
    outputs = []
    for path in goal["checkpoint"].get("outputs", []):
        try:
            output = output_record(goal["spec"]["workspace"], path)["path"]
            if output not in outputs: outputs.append(output)
        except (OSError, ValueError):
            continue  # Revalidate old deliverables; missing files are not proof.
    for item in items:
        if item.get("type") != "fileChange" or item.get("status") != "completed":
            continue
        for change in item.get("changes", []):
            path = change.get("path")
            if not isinstance(path, str): continue
            try:
                output = output_record(goal["spec"]["workspace"], path)["path"]
                if output not in outputs: outputs.append(output)
            except (OSError, ValueError):
                continue  # Not a deliverable; never infer a sensitive output.
    checkpoint = {"decision": "continue", "summary": "Cycle interrupted before the final checkpoint. Recorded completed native steps are retained; the full goal is not verified.",
        "next_step": "Inspect saved state and these observations before resuming; do not repeat completed actions.",
        "plan": goal["checkpoint"].get("plan", []),
        "completed_steps": [f"Observed {item['type']} · {item.get('status', 'unknown')}" for item in items if item.get("type")][-100:],
        "evidence": ["Completed native tool/command records retained in the saved session"], "outputs": outputs,
        "claims": goal["checkpoint"].get("claims", []),
        "coverage": goal["checkpoint"].get("coverage", []), "partial": True}
    transcript = {"turns": prior_turns + [{"id": "interrupted-cycle", "items": current_items}]}
    checkpoint["outcome"] = review(goal["spec"], checkpoint, transcript,
        elapsed_seconds=elapsed_seconds, cycle_turn_ids=["interrupted-cycle"])
    # Observations can remain useful while the final synthesis is absent.
    # Partial evidence must never certify an interrupted goal's completion.
    checkpoint["outcome"]["passed"] = False
    return checkpoint


def execute_cycle(store, goal, *, endpoint=None, session_factory=NativeGoalSession, provider_guard=None, checkpoint_only=False):
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
            # One bounded cycle has one visible research time allowance, not a
            # hidden four-minute child/ten-minute parent cutoff.
            adapter.research_guard.worker_seconds = goal["spec"]["cycle_seconds"]
            adapter.research_guard.shared_seconds = goal["spec"]["cycle_seconds"]
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
                session.root_thread_id = thread["id"]
                def checkpoint_required(request_thread_id):
                    if checkpoint_only and request_thread_id == thread["id"]:
                        return True
                    return checkpoint_needed(store, goal["id"], thread["id"], request_thread_id)
                adapter.checkpoint_required = checkpoint_required
                observed_prior = None
                if goal.get("session_id"):
                    # Re-read authoritative native records before asking the
                    # model to continue. An older verifier's missing-evidence
                    # report must not cause completed actions to be repeated.
                    phase = "read_session_transcript"
                    prior = session.rpc("thread/read", {"threadId": thread["id"], "includeTurns": True})["thread"]
                    session.observed_prior_transcript = prior
                    from .native_outcomes import review
                    observed_prior = review(goal["spec"], goal["checkpoint"], prior, cycle_turn_ids=[])
                prompt = json.dumps({"objective": goal["spec"]["objective"], "success_criteria": goal["spec"]["success_criteria"],
                    "saved_checkpoint": goal["checkpoint"], "feedback": [item["text"] for item in feedback],
                    "observed_prior_outcome": observed_prior,
                    "cycle": goal["cycles"], "max_cycles": goal["spec"]["max_cycles"],
                    "requests_remaining": goal["spec"]["max_requests"] - goal["requests"],
                    "token_estimate_remaining": goal["spec"]["max_token_estimate"] - goal["token_estimate"],
                    "cycle_seconds": goal["spec"]["cycle_seconds"]}, ensure_ascii=False)
                prompt = json.dumps({"task": json.loads(prompt), "outcome_requirements": {k: goal["spec"].get(k) for k in ("task_kind", "min_sources", "time_sensitive", "requirements")},
                    "execution_limits": {"max_output_tokens": goal["spec"].get("max_output_tokens") or endpoint.default_max_output_tokens or 4096,
                        "research_calls_per_worker": adapter.research_guard.worker_calls, "research_calls_shared": adapter.research_guard.shared_calls,
                        "provider_concurrent_streams": 2, "provider_requests_per_minute": 60}}, ensure_ascii=False)
                phase = "start_model_turn"
                turn = session.rpc("turn/start", {"threadId": thread["id"],
                    "input": [{"type": "text", "text": prompt, "text_elements": []}]})["turn"]
                store.attach(goal, thread["id"], turn["id"])
                session.deadline = time.monotonic() + goal["spec"]["cycle_seconds"]
                phase = "wait_for_model_turn"
                completed = session.wait_turn(turn["id"])
                if completed.get("status") != "completed":
                    raise turn_failure(completed)
                phase = "read_session_transcript"
                transcript = session.rpc("thread/read", {"threadId": thread["id"], "includeTurns": True})["thread"]
                last = next(t for t in transcript["turns"] if t["id"] == turn["id"])
                messages = [item.get("text", "") for item in last.get("items", []) if item.get("type") == "agentMessage"]
                cycle_turn_ids = [turn["id"]]
                phase = "validate_checkpoint"
                try:
                    checkpoint = checkpoint_from(messages[-1] if messages else "", goal["spec"]["workspace"], goal["spec"].get("task_kind", "general"))
                except ValueError:
                    if checkpoint_only:
                        raise  # One approved serialization request, no extra repair.
                    # One bounded format repair in the SAME native thread. It
                    # cannot replay tools and is charged to the same goal budget.
                    store.progress(goal, "Formatting observed progress; no tools or actions will be repeated")
                    adapter.checkpoint_thread_id = thread["id"]
                    try:
                        repaired = session.rpc("turn/start", {"threadId": thread["id"], "outputSchema": CHECKPOINT_SCHEMA,
                            "input": [{"type": "text", "text": "Serialize the work already observed into the required goal checkpoint, including coverage. Do not continue the task, run tools, fabricate evidence or repeat actions. If evidence is missing, say so.", "text_elements": []}]})["turn"]
                        store.attach(goal, thread["id"], repaired["id"])
                        cycle_turn_ids.append(repaired["id"])
                        if session.wait_turn(repaired["id"]).get("status") != "completed":
                            raise ValueError("Checkpoint serialization did not complete")
                        transcript = session.rpc("thread/read", {"threadId": thread["id"], "includeTurns": True})["thread"]
                        final = next(t for t in transcript["turns"] if t["id"] == repaired["id"])
                        text = [item.get("text", "") for item in final.get("items", []) if item.get("type") == "agentMessage"]
                        checkpoint = checkpoint_from(text[-1] if text else "", goal["spec"]["workspace"], goal["spec"].get("task_kind", "general"))
                    finally:
                        adapter.checkpoint_thread_id = None
                phase = "verify_outcome"
                from .native_outcomes import review
                checkpoint["outcome"] = review(goal["spec"], checkpoint, transcript, elapsed_seconds=time.monotonic() - started, cycle_turn_ids=cycle_turn_ids)
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
        store.finish(goal, partial_checkpoint(session, goal, time.monotonic() - started), "paused", "Supervisor stopped; resume explicitly")
    except Exception as exc:
        # Never expose a provider body or a prompt through raw exception text.
        failure = _failure_record(exc, phase)
        store.finish(goal, partial_checkpoint(session, goal, time.monotonic() - started), "blocked", _failure_detail(failure), failure=failure)
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
            futures = {}
            next_schedule_check, online = 0., True
            try:
                while not stop.is_set():
                    for future in tuple(futures):
                        if future.done():
                            goal = futures.pop(future)
                            try:
                                future.result()
                            except Exception as exc:
                                # No automatic retry of a possibly accepted action.
                                # Keep other projects running; release only this lease.
                                failure = _failure_record(exc, "save_checkpoint")
                                try:
                                    store.finish(goal, None, "blocked", _failure_detail(failure), failure=failure)
                                except RuntimeError:
                                    # A stale lease belongs to another generation.
                                    if store.get(goal["id"]).get("lease") == goal["lease"]:
                                        raise
                    from .native_background import enqueue_due, network_available
                    if time.monotonic() >= next_schedule_check:
                        online = network_available()
                        next_schedule_check = time.monotonic() + 5
                        with store.connect() as db:
                            store.set_meta(db, "background_health", {"online": online, "updated": time.time()})
                        if online: enqueue_due(store)
                    while online and len(futures) < MAX_CONCURRENT_GOALS and not stop.is_set():
                        goal = store.claim()
                        if goal is None:
                            break
                        futures[workers.submit(execute_cycle, store, goal)] = goal
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
