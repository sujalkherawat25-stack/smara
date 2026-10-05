"""Local JSONL app server: concurrent turns, replay and approval responses."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from .runtime_session import session_store_for_workspace
from .session_protocol import SessionProtocol


def serve(workspace: Path, source, sink, *, runner=None, model_profile: str | None = None) -> None:
    from .app_adapter import run_canonical_task
    root = workspace.resolve()
    protocol = SessionProtocol(session_store_for_workspace(root))
    runner = runner or run_canonical_task
    output_lock = threading.Lock()
    workers: dict[str, threading.Thread] = {}
    initialized = False

    def write(value):
        with output_lock:
            sink.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
            sink.flush()

    def notify(value):
        write({"method": "session/event", "params": value["event"]})

    def execute(turn, request, params, context, recovery=None):
        try:
            model_options = {}
            if model_profile:
                from .cli import _load_local_profiles, _resolve_profile_key
                profiles, _active, credentials = _load_local_profiles()
                profile = next((item for item in profiles if item.get("id") == model_profile), None)
                if profile is None:
                    raise ValueError("Requested model profile is not configured")
                model_options["model_settings"] = {**profile, "api_key": _resolve_profile_key(profile, credentials)}
            if recovery:
                model_options.update(session_id=recovery["session_id"], resume=True)
            result = runner(request, root, tool_profile=params.get("tool_profile", "full"),
                            research_mode=params.get("research_mode", "auto"), protocol_turn=turn,
                            context_history=context,
                            **model_options,
                            cancel_requested=lambda: protocol.store.should_cancel(turn.thread_id))
            status = str(result.get("status") or "needs_input")
            if status not in {"completed", "failed", "cancelled", "needs_input"}:
                status = "failed"
            protocol.store.checkpoint(turn.thread_id, status=status, result=result,
                                      unresolved=result.get("unresolved_work", []), expected_turn_id=turn.turn_id)
            turn.finish(status, str(result.get("answer") or ""))
        except Exception as exc:
            protocol.store.checkpoint(turn.thread_id, status="failed", unresolved=[f"Agent turn failed ({type(exc).__name__}); check provider and workspace settings"], expected_turn_id=turn.turn_id)
            turn.finish("failed")

    try:
        for line in source:
            request_id = None
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("Request must be an object")
                request_id = request.get("id")
                method = str(request.get("method") or "")
                params = request.get("params") or {}
                if not isinstance(params, dict):
                    raise ValueError("params must be an object")
                if method == "initialize":
                    initialized = True
                elif not initialized:
                    raise ValueError("Call initialize before other methods")
                if method in {"turn/start", "turn/resume"}:
                    if sum(worker.is_alive() for worker in workers.values()) >= 4:
                        raise ValueError("Four turns are already running; wait or interrupt one")
                    thread_id = str(params.get("thread_id") or "")
                    if not thread_id or protocol.store.get(thread_id) is None:
                        raise ValueError("Create a thread before starting a turn")
                    recovery = protocol.recovery(thread_id, root) if method == "turn/resume" else None
                    if recovery:
                        params = {**params, "tool_profile": recovery["tool_profile"], "research_mode": recovery["research_mode"]}
                    objective = recovery["request"] if recovery else params.get("request")
                    if not isinstance(objective, str) or not objective.strip():
                        raise ValueError("request must be nonempty text")
                    context = protocol.history(thread_id)
                    turn = protocol.begin(thread_id, objective, notify=notify,
                                          approval_mode=str(params.get("approval_mode") or "auto"))
                    if recovery:
                        turn.bind_execution(recovery["session_id"], root)
                    protocol.store.start_turn(thread_id, request=objective, workspace_id=str(root), mode="app-server")
                    protocol.store.checkpoint(thread_id, status="running", expected_turn_id=turn.turn_id)
                    worker = threading.Thread(target=execute, args=(turn, objective, params, context, recovery), daemon=True)
                    workers[thread_id] = worker
                    write({"id": request_id, "result": {"thread_id": thread_id, "turn_id": turn.turn_id}})
                    worker.start()
                    continue
                result = protocol.dispatch(method, params)
                write({"id": request_id, "result": result})
            except (ValueError, KeyError, TypeError) as exc:
                write({"id": request_id, "error": {"code": "invalid_request", "message": str(exc)}})
    finally:
        # Closing the owning stdio connection explicitly interrupts its work.
        for thread_id, worker in workers.items():
            if worker.is_alive():
                protocol.interrupt(thread_id)
