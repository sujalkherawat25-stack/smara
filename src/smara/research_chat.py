"""Desktop conversation plumbing around the canonical research engine."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .app_adapter import run_canonical_task
from .local_conversation_memory import SQLiteConversationMemory
from .runtime_session import session_store_for_state


def run_research_chat(*, prompt: str, state_path: Path, workspace: Path,
                      model: dict[str, Any], context: list[dict[str, Any]],
                      conversation_id: str, research_mode: str,
                      event_callback: Any = None, approval_mode: str = "auto") -> dict[str, Any]:
    store = session_store_for_state(state_path)
    from .session_protocol import SessionProtocol
    store.create_or_get(conversation_id, request=prompt, workspace_id=str(workspace))
    turn = SessionProtocol(store).begin(conversation_id, prompt, notify=event_callback, approval_mode=approval_mode)
    store.start_turn(conversation_id, request=prompt, workspace_id=str(workspace),
                     mode="local", model_profile=str(model.get("label") or model["model"]),
                     tool_profile="research_web", research_mode=research_mode)
    store.checkpoint(conversation_id, status="running", event="turn.started",
                     event_payload={"request": prompt[:500]})

    def progress(kind: str, data: dict[str, Any]) -> None:
        if not event_callback:
            return
        if kind == "thought":
            event_callback({"type": "status", "label": "Model response received",
                            "text": "Preparing the next research action."})
        elif kind == "context_packed":
            event_callback({"type": "status", "label": "Research context prepared",
                            "text": f"{data.get('input_tokens', 0):,} estimated input tokens."})
        elif kind == "model_request":
            event_callback({"type": "status", "label": "Waiting for model response",
                            "text": f"Attempt {data.get('attempt', 1)} of 3; request deadline {data.get('timeout_seconds', 0):.0f}s."})
        elif kind == "model_retry":
            event_callback({"type": "status", "label": "Retrying model request",
                            "text": "The provider request failed; retrying within the original budget."})
        elif kind == "answer" and data.get("answer"):
            event_callback({"type": "phase", "phase": "answer"})
            event_callback({"type": "token", "text": str(data["answer"])})
        elif kind in {"tool_start", "tool_end"}:
            observation = str(data.get("observation") or "")
            ok = kind == "tool_end" and not observation.startswith(("Error", "Denied:", "File Read Error:"))
            if kind == "tool_end":
                try:
                    import json
                    result = json.loads(observation)
                    if isinstance(result, dict):
                        ok = ok and result.get("status") != "error" and result.get("passed") is not False
                except (ValueError, TypeError):
                    pass
            event_callback({
                "type": "tool_call" if kind == "tool_start" else "tool_result",
                "name": data.get("tool"), "preview": str(data.get("observation") or data.get("args") or "")[:500],
                "ok": ok,
            })

    try:
        envelope = run_canonical_task(
            prompt, workspace, tool_profile="research_web", research_mode=research_mode,
            model_settings=model, context_history=context, on_progress=progress,
            cancel_requested=lambda: store.should_cancel(conversation_id),
            protocol_turn=turn,
        )
        cancelled = store.should_cancel(conversation_id)
        raw_status = str(envelope.get("status") or "failed")
        if cancelled:
            status = "cancelled"
        elif raw_status in {"completed", "failed", "cancelled", "needs_input", "waiting_approval"}:
            status = raw_status
        else:
            status = "failed" if raw_status in {"budget_exhausted", "tool_error", "interrupted"} else "needs_input"
        result = {
            "answer": envelope["answer"], "status": status,
            "completed": status == "completed", "session_id": conversation_id,
            "research_session_id": envelope["session_id"],
            "research_review": envelope["research_review"],
            "research_mode": envelope["research_mode"],
            "unresolved_items": ["cancelled by user"] if cancelled else envelope["unresolved_work"],
        }
        SQLiteConversationMemory.for_state(state_path).append_exchange(
            conversation_id=conversation_id, workspace_id=str(workspace),
            user_message=prompt, assistant_message=result["answer"],
        )
        store.checkpoint(conversation_id, status=status, result=result,
                         unresolved=result["unresolved_items"], event=f"turn.{status}", expected_turn_id=turn.turn_id)
        turn.finish(status, result["answer"])
        result["turn_id"] = turn.turn_id
        result["protocol"] = turn.protocol.snapshot(conversation_id)
        result["event_cursor"] = store.snapshot(conversation_id)["cursor"]
        return result
    except Exception:
        # Exceptions can contain provider input; keep secrets out of the journal.
        store.checkpoint(conversation_id, status="failed", unresolved=["Research turn failed"],
                         event="turn.failed", expected_turn_id=turn.turn_id)
        turn.finish("failed")
        raise
