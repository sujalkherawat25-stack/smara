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
                      event_callback: Any = None) -> dict[str, Any]:
    store = session_store_for_state(state_path)
    store.start_turn(conversation_id, request=prompt, workspace_id=str(workspace),
                     mode="local", model_profile=str(model.get("label") or model["model"]),
                     tool_profile="research_web", research_mode=research_mode)
    store.checkpoint(conversation_id, status="running", event="turn.started",
                     event_payload={"request": prompt[:500]})

    def progress(kind: str, data: dict[str, Any]) -> None:
        if not event_callback:
            return
        if kind == "thought":
            event_callback({"type": "thought", "text": str(data.get("thought") or "")[:2000]})
        elif kind in {"tool_start", "tool_end"}:
            event_callback({
                "type": "tool_call" if kind == "tool_start" else "tool_result",
                "name": data.get("tool"), "preview": str(data.get("observation") or data.get("args") or "")[:500],
                "ok": kind == "tool_end" and '"status": "error"' not in str(data.get("observation") or ""),
            })

    try:
        envelope = run_canonical_task(
            prompt, workspace, tool_profile="research_web", research_mode=research_mode,
            model_settings=model, context_history=context, on_progress=progress,
            cancel_requested=lambda: store.should_cancel(conversation_id),
        )
        cancelled = store.should_cancel(conversation_id)
        status = "cancelled" if cancelled else str(envelope["status"])
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
                         unresolved=result["unresolved_items"], event=f"turn.{status}")
        result["event_cursor"] = store.snapshot(conversation_id)["cursor"]
        return result
    except Exception:
        # Exceptions can contain provider input; keep secrets out of the journal.
        store.checkpoint(conversation_id, status="failed", unresolved=["Research turn failed"],
                         event="turn.failed")
        raise
