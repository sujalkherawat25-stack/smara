"""Bounded model/tool loop for one hosted Smara task step."""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .tool_registry import ToolContext, ToolError, ToolRegistry

MAX_AGENT_ITERATIONS = 3
MAX_AGENT_TOOL_CALLS = 3
MAX_AGENT_OUTPUT_CHARS = 8_000
MAX_AGENT_PROMPT_CHARS = 16_000
MAX_AGENT_SECONDS = 90.0
MAX_TOOL_SECONDS = 20.0


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

    def __init__(
        self,
        provider: AgentStepProvider,
        registry: ToolRegistry,
        *,
        max_iterations: int = MAX_AGENT_ITERATIONS,
        max_tool_calls: int = MAX_AGENT_TOOL_CALLS,
        max_seconds: float = MAX_AGENT_SECONDS,
        tool_timeout_seconds: float = MAX_TOOL_SECONDS,
    ):
        self._provider = provider
        self._registry = registry
        self._max_iterations = max(1, min(MAX_AGENT_ITERATIONS, max_iterations))
        self._max_tool_calls = max(0, min(MAX_AGENT_TOOL_CALLS, max_tool_calls))
        self._max_seconds = max(0.05, min(MAX_AGENT_SECONDS, max_seconds))
        self._tool_timeout_seconds = max(0.01, min(MAX_TOOL_SECONDS, tool_timeout_seconds))

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AgentStepError("Smara agent exceeded its bounded execution time.")
        return remaining

    async def _complete(self, *, system: str, message: str, deadline: float) -> str:
        try:
            return await asyncio.wait_for(
                self._provider.complete(system=system, message=message),
                timeout=self._remaining(deadline),
            )
        except TimeoutError as exc:
            raise AgentStepError("Smara agent exceeded its bounded execution time.") from exc

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
        deadline = time.monotonic() + self._max_seconds
        specs = json.dumps(self._registry.describe(), ensure_ascii=False)[:8_000]
        objective = str(task.get("objective") or "").strip()[:6_000]
        if not objective:
            raise AgentStepError("Agent task has no objective.")
        system = (
            "You are Smara's bounded task-step planner. Use only the registered tools. "
            "Never claim an external side effect. desktop.request_action and desktop.request_workflow "
            "do not execute an action: they create a separate durable task that waits for the owner\'s approval. "
            "When the objective asks to create, edit, save, inspect, run, or download something on the paired "
            "desktop, use one of those desktop request tools rather than saying the capability is unavailable. "
            "For a report, spreadsheet, presentation, or PDF, request local_file_write with the matching "
            "create_docx/create_xlsx/create_pptx/create_pdf operation. If the owner says to save it in the local "
            "workspace but gives no filename, use a relative destination such as 'report.pdf'; the "
            "desktop resolves that safely inside its first approved folder. "
            "For multi-step local work, request a workflow with explicit inspect, edit, and verify stages. "
            "Return exactly one JSON object: "
            '{"action":"tool","name":"...","arguments":{...}} or '
            '{"action":"final","answer":"..."}. Do not include markdown fences. '
            f"Registered tools: {specs}"
        )
        observations: list[str] = []
        tools_used = 0
        tool_calls_attempted = 0
        requested_tools: set[str] = set()
        for iteration in range(self._max_iterations):
            message = (
                f"Task objective:\n{objective}\n\n"
                f"Relevant shared memory (possibly empty):\n{memory_context[:6_000]}\n\n"
                f"Tool observations:\n{'\n'.join(observations)[-6_000:] or '(none)'}\n\n"
                f"This is bounded reasoning turn {iteration + 1} of {self._max_iterations}."
            )[:MAX_AGENT_PROMPT_CHARS]
            raw = await self._complete(system=system, message=message, deadline=deadline)
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
                        deadline=deadline,
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
            fingerprint = json.dumps([name, arguments], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if fingerprint in requested_tools:
                observation = "Repeated identical tool request rejected; use the existing observation or return a final answer."
                ok = False
            elif tool_calls_attempted >= self._max_tool_calls:
                observation = "Tool-call budget exhausted; return a final answer using the available observations."
                ok = False
            else:
                requested_tools.add(fingerprint)
                tool_calls_attempted += 1
                try:
                    result = await asyncio.wait_for(
                        self._registry.invoke(name, arguments, tool_context),
                        timeout=min(self._tool_timeout_seconds, self._remaining(deadline)),
                    )
                    observation = result.content
                    tools_used += 1
                    ok = True
                except TimeoutError:
                    observation = "Tool timed out inside Smara's bounded execution window."
                    ok = False
                except ToolError as exc:
                    observation = f"Tool unavailable: {exc}"
                    ok = False
                except Exception as exc:
                    # Registered adapters are external boundaries.  A bug or
                    # transient provider failure must not abort the entire
                    # agent turn or leak an upstream detail into chat.
                    observation = f"Tool failed safely: {type(exc).__name__}."
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
                deadline=deadline,
            )
        else:
            answer = (await self._complete(system=system, message=final_message, deadline=deadline)).strip()
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
        deadline: float,
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
        # Some OpenAI-compatible models keep the planner's JSON discipline
        # when asked for the final prose answer.  Hold that envelope until it
        # is complete so the UI never receives raw {"action":"final"...}
        # tokens, then stream only its answer field.
        buffering_envelope = False
        stream = getattr(self._provider, "stream_complete", None)
        if callable(stream):
            try:
                async with asyncio.timeout(self._remaining(deadline)):
                    async for chunk in stream(system=system, message=message):
                        if not isinstance(chunk, str) or not chunk:
                            continue
                        remaining = MAX_AGENT_OUTPUT_CHARS - sum(len(part) for part in parts)
                        if remaining <= 0:
                            break
                        bounded = chunk[:remaining]
                        parts.append(bounded)
                        if len(parts) == 1 and bounded.lstrip().startswith("{"):
                            buffering_envelope = True
                        if not buffering_envelope:
                            token_hook(bounded)
            except Exception:
                # A stream can fail before any content (safe to retry through
                # the non-stream endpoint) or after partial content (do not
                # issue a second answer and duplicate text in the UI).
                if parts:
                    return "".join(parts).strip()
        if not parts:
            answer = (await self._complete(system=system, message=message, deadline=deadline)).strip()
            if answer:
                parts.append(answer[:MAX_AGENT_OUTPUT_CHARS])
                token_hook(parts[0])
        answer = "".join(parts).strip()
        if not answer:
            raise AgentStepError("Smara agent provider returned an empty final answer.")
        decision = _decode_decision(answer)
        if decision and decision.get("action") == "final" and isinstance(decision.get("answer"), str):
            answer = decision["answer"].strip()
            if buffering_envelope:
                token_hook(answer)
        return answer
