"""Stable Server-Sent Event frames for Smara clients.

Adapted from Memento's event contract. Events describe safe observable work;
they never include private chain-of-thought or raw provider errors.
"""
from __future__ import annotations

import json
import time
from typing import Any


def frame(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def phase(name: str) -> str:
    return frame({"type": "phase", "phase": name})


def status(label: str, *, detail: str | None = None) -> str:
    payload: dict[str, Any] = {"type": "status", "label": label}
    if detail:
        payload["detail"] = detail
    return frame(payload)


def token(text: str) -> str:
    return frame({"type": "token", "text": text})


def tool_call(name: str) -> str:
    return frame({"type": "tool_call", "name": name})


def tool_result(
    name: str,
    *,
    ok: bool,
    preview: str = "",
    citations: list[str] | None = None,
) -> str:
    payload: dict[str, Any] = {"type": "tool_result", "name": name, "ok": ok}
    if preview:
        payload["preview"] = preview[:500]
    if citations:
        payload["citations"] = [str(value)[:2_000] for value in citations[:20] if str(value).strip()]
    return frame(payload)


def error(message: str, *, kind: str) -> str:
    return frame({"type": "error", "message": message, "kind": kind, "recoverable": False})


def done(
    *,
    memory_used: bool,
    total_ms: int,
    tools_used: int = 0,
    request_id: str | None = None,
    timings: dict[str, object] | None = None,
) -> str:
    payload: dict[str, object] = {
        "type": "done",
        "memories_used": int(memory_used),
        "tools_used": max(0, tools_used),
        "total_ms": max(0, total_ms),
    }
    if request_id:
        payload["request_id"] = request_id
    if timings:
        payload["timings"] = timings
    return frame(payload)


def elapsed_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)
