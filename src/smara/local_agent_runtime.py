"""Shared model adapter for Smara's local autonomous agent.

Both the command-line client and the Tauri companion use this module to turn
an OpenAI-compatible local model into the small JSON protocol consumed by
``LocalAutonomousAgent``.  Keeping the adapter here prevents the Desktop and
CLI from drifting into two different one-shot agent implementations.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


# This is a character ceiling because the local adapter supports providers
# with different tokenizers.  It leaves headroom for the system prompt and a
# 16k-token answer while preventing a workbook, browser page, or repeated
# tool result from silently overflowing a provider context window.
MAX_LOCAL_HISTORY_CHARS = 160_000
MAX_LOCAL_TOOL_OBSERVATION_CHARS = 24_000

# The shared runtime is imported as ``smara.local_agent_runtime`` in the
# source/CLI and as a top-level module by the PyInstaller Desktop executor.
# Keep both import modes working so the packaged binary can start without a
# Python package context.
try:
    from .local_agent import LocalAutonomousAgent, local_skill_catalog
    from .local_conversation_memory import SQLiteConversationMemory
    from .local_learning import LocalSkillLearningEngine, handle_learn_command, is_learn_command
except ImportError:  # pragma: no cover - exercised by the packaged binary
    from local_agent import LocalAutonomousAgent, local_skill_catalog
    from local_conversation_memory import SQLiteConversationMemory
    from local_learning import LocalSkillLearningEngine, handle_learn_command, is_learn_command


@dataclass(frozen=True)
class LocalModelConfig:
    """Connection details for an OpenAI-compatible private model."""

    base_url: str
    model: str
    api_key: str = ""
    auth_header: str = "authorization"
    label: str = "private model"
    timeout_seconds: float = 300.0
    max_tokens: int = 16_384


def _endpoint(base_url: str) -> str:
    value = str(base_url or "").strip().rstrip("/")
    if not value:
        raise RuntimeError("A private model endpoint is required.")
    return value if value.endswith("/chat/completions") else f"{value}/chat/completions"


def _strip_thinking(text: str) -> str:
    value = str(text or "")
    value = re.sub(r"<think>.*?</think>", "", value, flags=re.DOTALL | re.IGNORECASE)
    value = re.sub(r"^<think>.*", "", value, flags=re.DOTALL | re.IGNORECASE)
    value = re.sub(r"<thinking>.*?</thinking>", "", value, flags=re.DOTALL | re.IGNORECASE)
    value = re.sub(r"^<thinking>.*", "", value, flags=re.DOTALL | re.IGNORECASE)
    value = re.sub(r"\[THOUGHT\].*?\[/THOUGHT\]", "", value, flags=re.DOTALL | re.IGNORECASE)
    value = re.sub(r"```thought.*?```", "", value, flags=re.DOTALL | re.IGNORECASE)
    return value.strip()


def _parse_plan(text: str) -> dict[str, Any] | None:
    """Parse a strict or flexible local action/answer object from model content."""
    body = _strip_thinking(text or "").strip()
    if not body:
        return None
    if body.startswith("```"):
        body = re.sub(r"^```(?:json|python)?\s*", "", body, flags=re.IGNORECASE)
        body = re.sub(r"\s*```$", "", body).strip()

    json_candidate = body
    if not (body.startswith("{") and body.endswith("}")):
        match = re.search(r"\{[\s\S]*\}", body)
        if match:
            json_candidate = match.group(0)

    try:
        value = json.loads(json_candidate)
    except (TypeError, ValueError):
        value = None

    if isinstance(value, dict):
        kind = value.get("kind")
        if kind == "answer" and isinstance(value.get("answer"), str):
            return {"kind": "answer", "answer": _strip_thinking(value["answer"])}

        if kind == "local_action":
            cap = value.get("capability") or value.get("action") or "local_terminal"
            payload = value.get("payload") if isinstance(value.get("payload"), dict) else {}
            title = str(value.get("title") or f"Execute {cap}")[:160]
            obj = str(value.get("objective") or f"Execute {cap}")[:8_000]
            return {
                "kind": "local_action",
                "title": title,
                "objective": obj,
                "capability": str(cap),
                "payload": payload,
            }

        action_name = value.get("action") or value.get("capability") or value.get("tool")
        if isinstance(action_name, str) and action_name.strip():
            payload = value.get("payload")
            if not isinstance(payload, dict):
                if "code" in value and isinstance(value["code"], str):
                    payload = {"code": value["code"]}
                elif "command" in value and isinstance(value["command"], str):
                    payload = {"command": value["command"]}
                elif "arguments" in value and isinstance(value["arguments"], dict):
                    payload = value["arguments"]
                else:
                    payload = {k: v for k, v in value.items() if k not in {"action", "capability", "tool", "title", "objective"}}

            title = str(value.get("title") or f"Execute {action_name}")[:160]
            obj = str(value.get("objective") or f"Execute {action_name}")[:8_000]
            return {
                "kind": "local_action",
                "title": title,
                "objective": obj,
                "capability": action_name.strip(),
                "payload": payload,
            }

    if (body.startswith("import ") or body.startswith("from ") or body.startswith("def ") or "urllib.request" in body or "requests." in body) and "\n" in body:
        return {
            "kind": "local_action",
            "title": "Run Python script",
            "objective": "Execute code to fetch data or compute result",
            "capability": "local_python",
            "payload": {"code": body},
        }

    return None


def _compact_history(history: list[dict[str, Any]], *, max_chars: int = MAX_LOCAL_HISTORY_CHARS) -> list[dict[str, Any]]:
    """Keep the objective and recent evidence within a provider-safe budget.

    This is intentionally deterministic: it never asks a second model to
    summarize sensitive local output.  Old tool observations retain an
    explicit truncation marker, so the planner can request a bounded reread
    instead of treating omitted text as evidence.
    """
    items = [dict(item) for item in history if isinstance(item, dict) and isinstance(item.get("content"), str)]
    if sum(len(str(item.get("content") or "")) for item in items) <= max_chars:
        return items

    # Preserve the original user objective and the latest evidence/actions.
    head = items[:1] if items and items[0].get("role") == "user" else []
    tail = items[-8:]
    protected_ids = {id(item) for item in head + tail}
    compacted: list[dict[str, Any]] = []
    for item in items:
        content = str(item.get("content") or "")
        if id(item) in protected_ids:
            compacted.append(item)
            continue
        reduced = dict(item)
        if item.get("role") == "tool":
            reduced["content"] = content[:600] + "\n[Earlier tool output omitted; request a bounded reread if needed.]"
        else:
            reduced["content"] = content[:400] + "\n[Earlier turn compacted.]"
        compacted.append(reduced)

    total = sum(len(str(item.get("content") or "")) for item in compacted)
    if total <= max_chars:
        return compacted
    # As a final deterministic guard, keep the first objective plus newest
    # turns only.  This must not silently exceed the caller-selected model.
    result = [dict(item) for item in head]
    if result and len(str(result[0].get("content") or "")) > max_chars:
        result[0]["content"] = str(result[0].get("content") or "")[:max_chars]
    remaining = max_chars - sum(len(str(item.get("content") or "")) for item in result)
    selected_tail: list[dict[str, Any]] = []
    for item in reversed(tail):
        if id(item) in {id(entry) for entry in head} or remaining <= 0:
            continue
        content = str(item.get("content") or "")[:min(MAX_LOCAL_TOOL_OBSERVATION_CHARS, remaining)]
        if not content:
            continue
        copy = dict(item)
        copy["content"] = content
        selected_tail.append(copy)
        remaining -= len(content)
        if remaining <= 0:
            break
    # We choose the newest turns first to fit the budget, then restore their
    # chronological order before sending the transcript to the model.
    result.extend(reversed(selected_tail))
    return result


def _messages_from_history(history: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Map the shared loop history to a provider-neutral chat transcript."""
    messages: list[dict[str, str]] = []
    for item in _compact_history(history):
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        if role == "tool":
            # The shared loop intentionally does not retain provider-specific
            # tool-call IDs. Present results as a bounded user-visible turn so
            # Ollama, Sarvam, GLM, and other compatible gateways all accept it.
            name = str(item.get("name") or "local tool")
            messages.append({"role": "user", "content": f"[Result from {name}]\n{content[:MAX_LOCAL_TOOL_OBSERVATION_CHARS]}"})
        elif role in {"user", "assistant", "system"}:
            messages.append({"role": role, "content": content[:MAX_LOCAL_TOOL_OBSERVATION_CHARS]})
    return messages


def _tool_schema() -> dict[str, Any]:
    capabilities = [item["capability"] for item in local_skill_catalog(include_extended=True)]
    return {
        "type": "function",
        "function": {
            "name": "request_local_action",
            "description": (
                "Run exactly one local capability, then wait for its result. "
                "Use this for files, terminal, browser inspection, research, "
                "Git, code graphs, calculations, documents, and tests."
                " For long-running local work, use local_terminal with session_action=start "
                "and later session_action=poll or cancel plus the returned session_id; "
                "persistent sessions are bounded, workspace-scoped, and cannot receive credentials."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "objective", "capability", "payload"],
                "properties": {
                    "title": {"type": "string", "maxLength": 160},
                    "objective": {"type": "string", "maxLength": 8_000},
                    "capability": {"type": "string", "enum": capabilities},
                    "payload": {"type": "object", "additionalProperties": True},
                },
            },
        },
    }


class OpenAICompatiblePlanner:
    """Synchronous model callable used by ``LocalAutonomousAgent``."""

    def __init__(self, config: LocalModelConfig):
        self.config = config
        self.endpoint = _endpoint(config.base_url)
        self.client = httpx.Client(timeout=httpx.Timeout(config.timeout_seconds, connect=15.0))
        catalog = local_skill_catalog(include_extended=True)
        capability_lines = "\n".join(f"- {item['capability']}: {item['description']}" for item in catalog)
        self.system_prompt = (
            "You are Smara's private local autonomous planner. Work in small, "
            "verifiable steps. Return an ordinary clean answer only when the "
            "objective is complete. Otherwise call request_local_action exactly "
            "once. Never claim a local action happened before its result is "
            "provided. Keep paths inside approved workspace roots, avoid secrets, "
            "and prefer read/inspect/test before mutating files. If evidence is "
            "missing, say what is missing rather than guessing. Do not repeat an "
            "identical tool call after an error; repair the plan or stop clearly. "
            "Use local_media for approved images, audio, archives, and rich documents.\n\n"
            "Cross-session local memory is supplied as a bounded system note when "
            "relevant; treat it as a hint and verify it. The user can issue `/learn "
            "<name>` after a successful workflow to save a tested declarative "
            "SKILL.md playbook under the workspace .smara/skills directory.\n\n"
            "Installed capabilities:\n" + capability_lines
        )

    def close(self) -> None:
        self.client.close()

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.config.api_key and self.config.auth_header == "api-subscription-key":
            headers["api-subscription-key"] = self.config.api_key
        elif self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def __call__(self, history: list[dict[str, Any]]) -> dict[str, Any]:
        messages = [{"role": "system", "content": self.system_prompt}]
        messages.extend(_messages_from_history(history))
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "tools": [_tool_schema()],
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "stream": False,
            "max_tokens": max(512, min(int(self.config.max_tokens), 16_384)),
            "temperature": 0.1,
        }
        response = self.client.post(self.endpoint, headers=self._headers(), json=payload)
        if response.status_code in {400, 404, 422}:
            # Some local gateways do not implement function calling. Retry
            # with the same strict JSON contract so they still receive the
            # shared multi-step loop rather than silently becoming one-shot.
            fallback_prompt = (
                self.system_prompt
                + "\n\nFunction calling is unavailable. Return exactly one JSON object: "
                '{"kind":"local_action","title":string,"objective":string,"capability":string,"payload":object}'
                " or {\"kind\":\"answer\",\"answer\":string}."
            )
            fallback_messages = [{"role": "system", "content": fallback_prompt}]
            fallback_messages.extend(_messages_from_history(history))
            fallback_payload = {
                "model": self.config.model,
                "messages": fallback_messages,
                "stream": False,
                "max_tokens": max(512, min(int(self.config.max_tokens), 16_384)),
                "temperature": 0.1,
            }
            response = self.client.post(self.endpoint, headers=self._headers(), json=fallback_payload)
        if response.status_code in {401, 403}:
            raise RuntimeError(f"{self.config.label} rejected the local API key.")
        response.raise_for_status()
        try:
            body = response.json()
            message = body["choices"][0]["message"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise RuntimeError(f"{self.config.label} returned an invalid chat response.") from exc

        calls = message.get("tool_calls") if isinstance(message, dict) else None
        if isinstance(calls, list) and calls:
            function = calls[0].get("function") if isinstance(calls[0], dict) else None
            if not isinstance(function, dict):
                raise RuntimeError(f"{self.config.label} returned an invalid local tool call.")
            raw_args = function.get("arguments", "{}")
            try:
                arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except (TypeError, ValueError) as exc:
                raise RuntimeError(f"{self.config.label} returned malformed local tool arguments.") from exc
            if not isinstance(arguments, dict):
                raise RuntimeError(f"{self.config.label} returned a non-object local tool payload.")
            arguments["kind"] = "local_action"
            plan = _parse_plan(json.dumps(arguments, ensure_ascii=False))
            if plan is None:
                raise RuntimeError("The private model returned an incomplete local action plan.")
            return plan

        content = message.get("content") if isinstance(message, dict) else None
        plan = _parse_plan(content) if isinstance(content, str) else None
        if plan is not None:
            return plan
        if isinstance(content, str) and content.strip():
            return {"kind": "answer", "answer": _strip_thinking(content)}
        raise RuntimeError(f"{self.config.label} returned no answer or local action.")


def run_shared_local_turn(
    *,
    prompt: str,
    state_path: Any,
    config: LocalModelConfig,
    context: list[dict[str, Any]] | None = None,
    max_steps: int = 20,
    action_executor: Any | None = None,
    conversation_id: str | None = None,
    workspace_id: str = "default",
) -> dict[str, Any]:
    """Run the shared agent loop with local cross-session memory.

    The memory lookup happens before planning and the exchange is persisted
    after the answer.  ``/learn`` is handled locally, so a model outage cannot
    prevent a previously completed workflow from becoming a tested playbook.
    """
    conversation = str(conversation_id or "local-default")[:240]
    memory = SQLiteConversationMemory.for_state(state_path)
    if is_learn_command(prompt):
        learned = handle_learn_command(
            prompt,
            workspace_root=Path(workspace_id) if Path(workspace_id).is_dir() else Path.cwd(),
            state_path=state_path,
            conversation_id=conversation,
        )
        memory.append_exchange(
            conversation_id=conversation,
            workspace_id=workspace_id,
            user_message=prompt,
            assistant_message=str(learned.get("answer") or ""),
        )
        return learned

    memory_hits = memory.search(prompt, workspace_id=workspace_id, limit=6)
    memory_context: list[dict[str, str]] = []
    if memory_hits:
        snippets = [
            f"[{hit.get('role', 'turn')}] {str(hit.get('content') or '')[:2_000]}"
            for hit in memory_hits
        ]
        memory_context.append({
            "role": "system",
            "content": "Cross-session local memory (bounded, may be incomplete; verify before acting):\n" + "\n".join(f"- {item}" for item in snippets),
        })
    merged_context = memory_context + list(context or [])
    planner = OpenAICompatiblePlanner(config)
    try:
        agent = LocalAutonomousAgent(state_path, max_steps=max(1, min(int(max_steps), 20)), action_executor=action_executor)
        result = agent.run_turn(prompt, model_callable=planner, context=merged_context)
        answer = str(result.get("answer") or "").strip()
        memory.append_exchange(
            conversation_id=conversation,
            workspace_id=workspace_id,
            user_message=prompt,
            assistant_message=answer,
        )
        try:
            learning_workspace = Path(workspace_id) if Path(workspace_id).is_dir() else Path.cwd()
            LocalSkillLearningEngine(learning_workspace, state_path=state_path).record_task(
                prompt=prompt, result=result, conversation_id=conversation,
            )
        except (OSError, TypeError, ValueError):
            # Learning is best-effort telemetry; it must never make a valid
            # local answer fail because the optional journal is unavailable.
            pass
        result["local_memory_hits"] = len(memory_hits)
        result["local_memory_indexed"] = True
        return result
    finally:
        planner.close()
