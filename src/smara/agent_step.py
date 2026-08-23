"""Bounded model/tool loop for one hosted Smara task step."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .tool_registry import ToolContext, ToolError, ToolRegistry

MAX_AGENT_ITERATIONS = 3
MAX_AGENT_OUTPUT_CHARS = 8_000
MAX_AGENT_PROMPT_CHARS = 16_000


class AgentStepProvider(Protocol):
    async def complete(self, *, system: str, message: str) -> str: ...


class AgentStepError(RuntimeError):
    """A safe, user-actionable hosted agent-step failure."""


@dataclass(frozen=True)
class AgentStepResult:
    text: str
    tools_used: int = 0


def _decode_decision(raw: str) -> dict[str, Any] | None:
    candidate = raw.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if candidate.startswith("json"):
            candidate = candidate[4:].lstrip()
    try:
        value = json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


class BoundedAgentStepRuntime:
    """Select and execute only registered tools, then return a bounded answer."""

    def __init__(self, provider: AgentStepProvider, registry: ToolRegistry, *, max_iterations: int = MAX_AGENT_ITERATIONS):
        self._provider = provider
        self._registry = registry
        self._max_iterations = max(1, min(MAX_AGENT_ITERATIONS, max_iterations))

    async def run(
        self,
        *,
        task: dict[str, Any],
        memory_context: str = "",
        tool_context: ToolContext,
        event_hook: Callable[[str, dict[str, Any]], None] | None = None,
        token_hook: Callable[[str], None] | None = None,
    ) -> AgentStepResult:
        if not getattr(self._provider, "_base_url", "") or not getattr(self._provider, "_api_key", "") or not getattr(self._provider, "_model", ""):
            raise AgentStepError("Smara agent model provider is not configured.")
        specs = json.dumps(self._registry.describe(), ensure_ascii=False)[:8_000]
        objective = str(task.get("objective") or "").strip()[:6_000]
        if not objective:
            raise AgentStepError("Agent task has no objective.")
        system = (
            "You are Smara's bounded task-step planner. Use only the registered read-only tools. "
            "Never claim an external side effect. Return exactly one JSON object: "
            '{"action":"tool","name":"...","arguments":{...}} or '
            '{"action":"final","answer":"..."}. Do not include markdown fences. '
            f"Registered tools: {specs}"
        )
        observations: list[str] = []
        tools_used = 0
        for iteration in range(self._max_iterations):
            message = (
                f"Task objective:\n{objective}\n\n"
                f"Relevant shared memory (possibly empty):\n{memory_context[:6_000]}\n\n"
                f"Tool observations:\n{'\n'.join(observations)[-6_000:] or '(none)'}\n\n"
                f"This is bounded reasoning turn {iteration + 1} of {self._max_iterations}."
            )[:MAX_AGENT_PROMPT_CHARS]
            raw = await self._provider.complete(system=system, message=message)
            decision = _decode_decision(raw)
            if decision is None:
                answer = raw.strip()
                if answer:
                    return AgentStepResult(answer[:MAX_AGENT_OUTPUT_CHARS], tools_used)
                raise AgentStepError("Smara agent provider returned an empty decision.")
            action = decision.get("action")
            if action == "final":
                answer = decision.get("answer")
                if not isinstance(answer, str) or not answer.strip():
                    raise AgentStepError("Smara agent provider returned an empty final answer.")
                if token_hook:
                    return AgentStepResult(await self._stream_final(
                        objective=objective,
                        context=memory_context,
                        observations=observations,
                        draft=answer.strip(),
                        token_hook=token_hook,
                    ), tools_used)
                return AgentStepResult(answer.strip()[:MAX_AGENT_OUTPUT_CHARS], tools_used)
            if action != "tool":
                raise AgentStepError("Smara agent provider returned an unsupported action.")
            name = decision.get("name")
            arguments = decision.get("arguments", {})
            if not isinstance(name, str) or not isinstance(arguments, dict):
                raise AgentStepError("Smara agent provider returned invalid tool arguments.")
            if event_hook:
                event_hook("agent.tool_requested", {"tool": name, "iteration": iteration + 1})
            try:
                result = await self._registry.invoke(name, arguments, tool_context)
                observation = result.content
                tools_used += 1
                ok = True
            except ToolError as exc:
                observation = f"Tool unavailable: {exc}"
                ok = False
            if event_hook:
                event_hook("agent.tool_completed", {"tool": name, "ok": ok, "preview": observation[:500]})
            observations.append(f"{name}: {observation[:4_000]}")

        final_message = (
            f"Task objective:\n{objective}\n\n"
            f"Verified tool observations:\n{'\n'.join(observations)[-8_000:]}\n\n"
            "Return only a concise final answer grounded in these observations. Do not claim work you did not perform."
        )[:MAX_AGENT_PROMPT_CHARS]
        if token_hook:
            answer = await self._stream_final(
                objective=objective,
                context=memory_context,
                observations=observations,
                draft="",
                token_hook=token_hook,
            )
        else:
            answer = (await self._provider.complete(system=system, message=final_message)).strip()
        if not answer:
            raise AgentStepError("Smara agent provider returned an empty final answer.")
        decision = _decode_decision(answer)
        if decision and isinstance(decision.get("answer"), str):
            answer = decision["answer"]
        return AgentStepResult(answer[:MAX_AGENT_OUTPUT_CHARS], tools_used)

    async def _stream_final(
        self,
        *,
        objective: str,
        context: str,
        observations: list[str],
        draft: str,
        token_hook: Callable[[str], None],
    ) -> str:
        system = (
            "You are Smara, a concise and honest personal/work agent. Answer the user directly. "
            "Use only the supplied context and verified tool observations. Never claim an external "
            "side effect. Do not expose chain-of-thought and do not return a JSON envelope."
        )
        message = (
            f"User request:\n{objective}\n\n"
            f"Relevant context (possibly empty):\n{context[:8_000]}\n\n"
            f"Verified tool observations:\n{'\n'.join(observations)[-6_000:] or '(none)'}\n\n"
            f"Planning-pass draft (use only if accurate):\n{draft[:4_000] or '(none)'}\n\n"
            "Return only the final answer."
        )[:MAX_AGENT_PROMPT_CHARS]
        parts: list[str] = []
        stream = getattr(self._provider, "stream_complete", None)
        if callable(stream):
            async for chunk in stream(system=system, message=message):
                if not isinstance(chunk, str) or not chunk:
                    continue
                remaining = MAX_AGENT_OUTPUT_CHARS - sum(len(part) for part in parts)
                if remaining <= 0:
                    break
                bounded = chunk[:remaining]
                parts.append(bounded)
                token_hook(bounded)
        else:
            answer = (await self._provider.complete(system=system, message=message)).strip()
            if answer:
                parts.append(answer[:MAX_AGENT_OUTPUT_CHARS])
                token_hook(parts[0])
        answer = "".join(parts).strip()
        if not answer:
            raise AgentStepError("Smara agent provider returned an empty final answer.")
        return answer
