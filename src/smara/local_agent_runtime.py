"""Shared model adapter for Smara's local autonomous agent.

Both the command-line client and the Tauri companion use this module to turn
an OpenAI-compatible local model into the small JSON protocol consumed by
``LocalAutonomousAgent``.  Keeping the adapter here prevents the Desktop and
CLI from drifting into two different one-shot agent implementations.
"""
from __future__ import annotations

import json
import re
import contextlib
from datetime import date
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

# A private model is allowed to answer ordinary conversational questions from
# its own knowledge, but a current/live/web request must never silently turn
# into a stale prose answer.  The Desktop has read-only Tavily/Exa connectors
# for this lane; the small intent gate below makes their use deterministic
# even when a provider declines to emit a function call.
_LIVE_WEB_TERMS = (
    "web search", "search the web", "search online", "look online", "look it up",
    "live web", "browse the web", "weather", "rainfall", "forecast", "temperature",
    "today", "tonight", "tomorrow", "currently", "current", "latest", "breaking news",
    "news today", "as of now", "right now", "up-to-date", "up to date",
)
_LIVE_WEB_REFUSALS = (
    "don't have access", "do not have access", "no access to live", "unable to perform",
    "can't perform", "cannot perform", "can't access", "cannot access", "restricted to",
    "check google", "check a weather app", "i recommend checking", "i'd recommend checking",
)

# The shared runtime is imported as ``smara.local_agent_runtime`` in the
# source/CLI and as a top-level module by the PyInstaller Desktop executor.
# Keep both import modes working so the packaged binary can start without a
# Python package context.
try:
    from .local_agent import LocalAutonomousAgent, local_skill_catalog
    from .local_conversation_memory import SQLiteConversationMemory
    from .local_learning import LocalSkillLearningEngine, handle_learn_command, is_learn_command
    from .runtime_session import session_store_for_state
except ImportError:  # pragma: no cover - exercised by the packaged binary
    from local_agent import LocalAutonomousAgent, local_skill_catalog
    from local_conversation_memory import SQLiteConversationMemory
    from local_learning import LocalSkillLearningEngine, handle_learn_command, is_learn_command
    from runtime_session import session_store_for_state


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


def _sanitize_surrogates(obj: Any) -> Any:
    """Recursively clean surrogate characters to prevent UnicodeEncodeError in JSON/HTTP clients."""
    if isinstance(obj, str):
        return obj.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
    if isinstance(obj, dict):
        return {k: _sanitize_surrogates(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_surrogates(v) for v in obj]
    return obj


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


def _live_web_query(prompt: str, context: list[dict[str, Any]] | None = None) -> str | None:
    """Return a bounded live-web query for an explicit/current request.

    Follow-ups such as ``do the web search`` need the preceding user question
    to be useful.  Only prior user turns are considered; assistant prose is
    deliberately ignored so a previous refusal cannot become the next query.
    This is an intent guard, not a general classifier: local code/file work is
    left to the model planner.
    """
    prompt_text = str(prompt or "").strip()
    turns = list(context or [])
    prior_user = ""
    for item in reversed(turns):
        if isinstance(item, dict) and item.get("role") == "user":
            value = str(item.get("content") or "").strip()
            if value:
                prior_user = value
                break
    combined = " ".join(part for part in (prior_user, prompt_text) if part).lower()
    if not any(term in combined for term in _LIVE_WEB_TERMS):
        return None
    # “Current” is also common in local coding requests. Keep those on the
    # deterministic workspace tools rather than spending a web request.
    if re.search(r"\b(?:current|latest)\s+(?:git\s+)?(?:branch|file|workspace|repo(?:sitory)?|working tree|code|changes?)\b", combined):
        return None
    # Explicit opt-out wins, including questions such as “how do I search the
    # web without using the network?”.
    if re.search(r"\b(?:don't|do not|without|avoid|never)\s+(?:do\s+)?(?:a\s+)?(?:live\s+)?web\s+search", combined):
        return None
    # A short follow-up should reuse the last substantive user question.  For
    # a fresh query, preserve the user's exact wording and add today's date so
    # weather/news providers do not answer from an old index snapshot.
    follow_up = bool(re.fullmatch(r"(?:please\s+)?(?:do\s+)?(?:the\s+)?(?:web\s+)?search[.!?]*", prompt_text, re.IGNORECASE))
    query = prior_user if follow_up and prior_user else prompt_text
    if not query:
        return None
    if any(term in combined for term in ("weather", "rainfall", "forecast", "temperature", "today", "tonight", "tomorrow", "latest", "breaking news", "current")):
        query = f"{query} (live information for {date.today().isoformat()})"
    return query[:500]


def _live_web_refusal(answer: str) -> bool:
    lowered = str(answer or "").lower()
    return any(marker in lowered for marker in _LIVE_WEB_REFUSALS)


def _live_web_fallback_answer(raw_result: str, query: str) -> str | None:
    """Build a truthful source-backed answer if the model ignores evidence."""
    try:
        payload = json.loads(raw_result)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("action") != "local_integration":
        return None
    rows = payload.get("results")
    if not isinstance(rows, list) or not rows:
        return None
    lines = [f"### Live web results\n\nI searched for **{query}** using the local {str(payload.get('provider') or 'web')} connector.\n"]
    for index, item in enumerate(rows[:5], 1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "Source").strip()
        url = str(item.get("url") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        lines.append(f"{index}. [{title}]({url})\n   {snippet}\n")
    if len(lines) == 1:
        return None
    lines.append("\nThese are current search results, not a guaranteed official forecast. Verify important decisions against the cited primary source.")
    return "\n".join(lines)


def _compact_history(history: list[dict[str, Any]], *, max_chars: int = MAX_LOCAL_HISTORY_CHARS) -> list[dict[str, Any]]:
    """Pack complete protocol groups within a conservative local budget."""
    items = []
    for item in history:
        if not isinstance(item,dict) or not isinstance(item.get("content"),str):continue
        normalized=dict(item)
        if normalized.get("role")=="tool" and not normalized.get("tool_call_id"):
            normalized["role"]="user";normalized["_smara_local_tool"]=True
        items.append(normalized)
    try:
        from .context_packing import ModelContextProfile, pack_messages
    except ImportError:  # pragma: no cover - exercised by the bundled executable
        from context_packing import ModelContextProfile, pack_messages
    profile = ModelContextProfile("unknown:local", max_chars + 64, 0, safety_margin=32, protocol_overhead=32)
    return list(pack_messages(items, profile).messages)


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
        if role == "tool" or item.get("_smara_local_tool"):
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
            "For any current, weather, news, live-web, or explicit web-search "
            "request, use local_integration with provider tavily or exa before "
            "answering; never use local_browser for a search-engine query and "
            "never claim that live web access is unavailable when connector "
            "evidence is supplied. Cite the returned source URLs in the answer. "
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
        payload = _sanitize_surrogates(payload)
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
            fallback_payload = _sanitize_surrogates(fallback_payload)
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
    # Desktop and CLI share one durable session envelope and event ledger.
    # The specialised conversation/task stores remain intact, while resume
    # callers now get one stable id and status contract.
    runtime_sessions = session_store_for_state(state_path)
    runtime_sessions.create_or_get(
        conversation,
        request=prompt,
        workspace_id=workspace_id,
        account_id="local",
        mode="local",
        model_profile=config.label,
    )
    runtime_sessions.checkpoint(
        conversation,
        status="running",
        event="turn.started",
        event_payload={"request": prompt[:500]},
    )
    if runtime_sessions.should_cancel(conversation):
        runtime_sessions.checkpoint(conversation, status="cancelled", unresolved=["cancelled by user"], event="turn.cancelled", event_payload={"reason": "cancelled before start"})
        return {"answer": "The local session was cancelled.", "steps": [], "completed": False, "cancelled": True, "status": "cancelled", "unresolved_items": ["cancelled by user"], "session_id": conversation, "runtime_session": runtime_sessions.snapshot(conversation)}
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
        learned_status = "completed" if learned.get("completed") else "needs_input"
        runtime_sessions.checkpoint(
            conversation,
            status=learned_status,
            result=learned,
            unresolved=[] if learned.get("completed") else [str((learned.get("learning") or {}).get("status") or "learning did not complete")],
            event="turn.completed" if learned.get("completed") else "turn.needs_input",
            event_payload={"learning": learned.get("learning") or {}},
        )
        learned["status"] = learned_status
        learned["session_id"] = conversation
        learned["runtime_session"] = runtime_sessions.snapshot(conversation)
        learned["event_cursor"] = learned["runtime_session"]["cursor"]
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
    merged_context = _sanitize_surrogates(merged_context)
    # Deterministic live-web preflight.  This runs before the model gets a
    # chance to answer from stale knowledge, and it also handles terse
    # follow-ups (“do the web search”) by using the preceding user turn.
    live_context = list(merged_context)
    if not any(isinstance(item, dict) and item.get("role") == "user" for item in live_context):
        # The native Desktop normally supplies its JSON transcript, but the
        # CLI, a restart, or a direct executor call may not.  Recover only
        # recent turns from the same conversation so a terse follow-up still
        # has an unambiguous subject without leaking another chat's context.
        try:
            recent = memory.recent(workspace_id=workspace_id, limit=16)
            for item in reversed(recent):
                if item.get("conversation_id") == conversation and item.get("role") in {"user", "assistant"}:
                    live_context.append({"role": item.get("role"), "content": str(item.get("content") or "")[:MAX_LOCAL_TOOL_OBSERVATION_CHARS]})
        except (OSError, RuntimeError, TypeError, ValueError):
            pass
    live_query = _live_web_query(prompt, live_context)
    live_web_result: str | None = None
    live_web_error: str | None = None
    if live_query:
        preflight_agent = LocalAutonomousAgent(state_path, max_steps=1, action_executor=action_executor, cancel_check=lambda: runtime_sessions.should_cancel(conversation))
        errors: list[str] = []
        for provider in ("tavily", "exa"):
            try:
                candidate = preflight_agent.execute_action(
                    "local_integration",
                    {"provider": provider, "operation": "search", "query": live_query, "max_results": 5},
                    step_id=f"live-web-preflight-{provider}",
                )
                if not LocalAutonomousAgent._action_failed(candidate):
                    live_web_result = candidate.get("result") if isinstance(candidate, dict) and isinstance(candidate.get("result"), str) else json.dumps(candidate, ensure_ascii=False)
                    break
                errors.append(str(candidate.get("error") or f"{provider} returned no evidence"))
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                errors.append(str(exc)[:500])
        if live_web_result is None:
            live_web_error = "; ".join(errors)[:1_000] or "No local web connector returned evidence."
        else:
            merged_context.append({
                "role": "system",
                "content": (
                    "LIVE_WEB_PREFLIGHT_PRESENT: The following bounded, read-only local connector "
                    "evidence was fetched for this turn. Treat it as the source of truth for "
                    "current claims, cite its URLs, and answer directly. Do not say you lack web "
                    "access and do not repeat the same search action.\n" + live_web_result[:MAX_LOCAL_TOOL_OBSERVATION_CHARS]
                ),
            })
        if live_web_error:
            merged_context.append({
                "role": "system",
                "content": (
                    "LIVE_WEB_PREFLIGHT_FAILED: A live lookup was required, but the local connectors "
                    f"failed ({live_web_error}). Do not invent current facts; explain the connector "
                    "failure and ask the user to update the local web credential if needed."
                ),
            })
    planner = OpenAICompatiblePlanner(config)
    try:
        agent = LocalAutonomousAgent(state_path, max_steps=max(1, min(int(max_steps), 20)), action_executor=action_executor, cancel_check=lambda: runtime_sessions.should_cancel(conversation))
        result = agent.run_turn(_sanitize_surrogates(prompt), model_callable=planner, context=merged_context)
        result = _sanitize_surrogates(result)
        answer = str(result.get("answer") or "").strip()
        if live_web_result is not None:
            # Surface the preflight as a real tool step in Desktop telemetry;
            # it was executed through the same validated capability contract.
            steps = result.setdefault("steps", [])
            if isinstance(steps, list):
                steps.insert(0, {
                    "iteration": 0,
                    "title": "Live web preflight",
                    "capability": "local_integration",
                    "payload": {"provider": "tavily_or_exa", "operation": "search", "query": live_query},
                    "result": live_web_result,
                    "ok": True,
                })
            try:
                evidence = json.loads(live_web_result)
            except (TypeError, ValueError):
                evidence = {}
            result["live_web"] = {
                "query": live_query,
                "provider": evidence.get("provider") if isinstance(evidence, dict) else None,
                "citations": evidence.get("citations", []) if isinstance(evidence, dict) else [],
                "proof": evidence.get("proof") if isinstance(evidence, dict) else None,
            }
            if _live_web_refusal(answer):
                fallback = _live_web_fallback_answer(live_web_result, live_query)
                if fallback:
                    answer = fallback
                    result["answer"] = answer
            elif isinstance(evidence, dict):
                citations = [str(item) for item in evidence.get("citations", []) if isinstance(item, str)]
                # A provider may return a useful summary without links. Keep
                # the answer useful but auditable by appending the exact URLs.
                if citations and not any(url in answer for url in citations):
                    result["answer"] = answer.rstrip() + "\n\nSources:\n" + "\n".join(f"- {url}" for url in citations[:5])
                    answer = result["answer"]
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
        completed = bool(result.get("completed"))
        cancelled = bool(result.get("cancelled")) or runtime_sessions.should_cancel(conversation)
        unresolved = result.get("unresolved_items")
        if not isinstance(unresolved, list):
            unresolved = [] if completed else ["The agent did not mark this turn complete."]
        runtime_sessions.checkpoint(
            conversation,
            status="cancelled" if cancelled else ("completed" if completed else "needs_input"),
            result=result,
            unresolved=[str(item)[:500] for item in (unresolved if not cancelled else ["cancelled by user"])],
            event="turn.cancelled" if cancelled else ("turn.completed" if completed else "turn.needs_input"),
            event_payload={"steps": len(result.get("steps") or []) if isinstance(result.get("steps"), list) else 0},
        )
        result["status"] = "cancelled" if cancelled else ("completed" if completed else "needs_input")
        result["session_id"] = conversation
        result["runtime_session"] = runtime_sessions.snapshot(conversation)
        result["event_cursor"] = result["runtime_session"]["cursor"]
        return result
    except Exception as exc:
        with contextlib.suppress(Exception):
            runtime_sessions.checkpoint(
                conversation,
                status="failed",
                unresolved=[str(exc)[:500]],
                event="turn.failed",
                event_payload={"error": str(exc)[:500]},
            )
        raise
    finally:
        planner.close()
