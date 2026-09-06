"""Bounded programmatic tool-calling for the local Smara agent.

The kernel lets the model collapse independent, read-only operations into one
agent turn without introducing an arbitrary code runner.  It deliberately
accepts a dispatcher callback rather than importing tools itself; this keeps
the normal tool registry as the single execution path and makes the boundary
easy to test.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List


PTC_MAX_CALLS = 8
PTC_MAX_ARGS_BYTES = 16_000
PTC_MAX_RESULT_CHARS = 4_000
PTC_MAX_TOTAL_RESULT_CHARS = 24_000

# These tools are retrieval/calculation only.  Mutating, credential, shell,
# browser-control, delegation, and memory-write tools must never be batched.
PTC_SAFE_TOOLS = frozenset(
    {
        "web_search",
        "web_extract",
        "web_reader_dynamic",
        "wayback_extract",
        "wikipedia_page",
        "file_read",
        "pdf_search",
        "zip_extract_and_read",
        "calculate",
        "skills_list",
        "skill_view",
    }
)


@dataclass(frozen=True)
class PTCCallResult:
    """One truthful result from the normal tool dispatcher."""

    name: str
    ok: bool
    content: str = ""
    error: str | None = None

    def as_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "name": self.name,
            "ok": self.ok,
            "content": self.content,
        }
        if self.error:
            result["error"] = self.error
        return result


@dataclass
class PTCExecutionResult:
    """Aggregate returned to the model after a bounded batch."""

    ok: bool
    calls: List[PTCCallResult] = field(default_factory=list)
    error: str | None = None

    def as_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "ok": self.ok,
            "calls": [call.as_dict() for call in self.calls],
        }
        if self.error:
            result["error"] = self.error
        return result

    def to_model_json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, separators=(",", ":"))


class ProgrammaticToolKernel:
    """Validate and execute a small batch of safe, independent tool calls.

    This is intentionally sequential.  The optimization is one model/tool
    turn and one compact observation; the underlying tools retain their own
    network and filesystem limits.  No Python source, shell command, dynamic
    import, socket, or nested programmatic call is evaluated here.
    """

    def __init__(self, dispatcher: Callable[[str, Dict[str, Any]], str]):
        self._dispatcher = dispatcher

    @staticmethod
    def _failure_message(content: str) -> str | None:
        normalized = str(content or "").strip()
        if normalized.lower().startswith(("error:", "error executing tool", "tool error")):
            return normalized
        return None

    def execute(self, calls: Any) -> PTCExecutionResult:
        if not isinstance(calls, list):
            return PTCExecutionResult(False, error="calls must be an array")
        if not calls:
            return PTCExecutionResult(False, error="calls must contain at least one item")
        if len(calls) > PTC_MAX_CALLS:
            return PTCExecutionResult(False, error=f"at most {PTC_MAX_CALLS} calls are allowed")

        validated: List[tuple[str, Dict[str, Any]]] = []
        for index, call in enumerate(calls):
            if not isinstance(call, dict):
                return PTCExecutionResult(False, error=f"call {index} must be an object")
            if set(call) - {"name", "args"}:
                return PTCExecutionResult(False, error=f"call {index} contains unsupported fields")
            name = call.get("name")
            args = call.get("args", {})
            if not isinstance(name, str) or not name.strip():
                return PTCExecutionResult(False, error=f"call {index} has an invalid tool name")
            name = name.strip()
            if name not in PTC_SAFE_TOOLS:
                return PTCExecutionResult(False, error=f"tool '{name}' is not allowed in a programmatic batch")
            if not isinstance(args, dict):
                return PTCExecutionResult(False, error=f"args for call {index} must be an object")
            if name == "programmatic_tool_call":
                # Defensive even if the allowlist changes in the future.
                return PTCExecutionResult(False, error="nested programmatic_tool_call is not allowed")
            try:
                encoded_args = json.dumps(args, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            except (TypeError, ValueError) as exc:
                return PTCExecutionResult(False, error=f"args for call {index} are not JSON serializable: {exc}")
            if len(encoded_args) > PTC_MAX_ARGS_BYTES:
                return PTCExecutionResult(False, error=f"args for call {index} exceed {PTC_MAX_ARGS_BYTES} bytes")
            validated.append((name, args))

        results: List[PTCCallResult] = []
        total_chars = 0
        for name, args in validated:
            try:
                content = str(self._dispatcher(name, args))
            except Exception as exc:  # Dispatcher errors become an observation, never a fake success.
                results.append(PTCCallResult(name=name, ok=False, error=str(exc)))
                continue
            failure = self._failure_message(content)
            if len(content) > PTC_MAX_RESULT_CHARS:
                content = content[:PTC_MAX_RESULT_CHARS] + "\n... [result truncated by programmatic tool budget]"
            total_chars += len(content)
            if total_chars > PTC_MAX_TOTAL_RESULT_CHARS:
                remaining = max(0, PTC_MAX_TOTAL_RESULT_CHARS - (total_chars - len(content)))
                content = content[:remaining] + "\n... [batch result truncated by programmatic tool budget]"
                total_chars = PTC_MAX_TOTAL_RESULT_CHARS
            results.append(PTCCallResult(name=name, ok=failure is None, content=content, error=failure))

        return PTCExecutionResult(all(result.ok for result in results), calls=results)


__all__ = [
    "PTC_MAX_CALLS",
    "PTC_MAX_ARGS_BYTES",
    "PTC_MAX_RESULT_CHARS",
    "PTC_MAX_TOTAL_RESULT_CHARS",
    "PTC_SAFE_TOOLS",
    "PTCCallResult",
    "PTCExecutionResult",
    "ProgrammaticToolKernel",
]
