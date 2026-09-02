"""Bounded model/tool loop for one hosted Smara task step."""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .streaming import append_stream_delta
from .tool_registry import ToolContext, ToolError, ToolRegistry

MAX_AGENT_ITERATIONS = 15
MAX_AGENT_TOOL_CALLS = 20
MAX_AGENT_OUTPUT_CHARS = 32_000
MAX_AGENT_PROMPT_CHARS = 64_000
MAX_AGENT_SECONDS = 180.0
MAX_TOOL_SECONDS = 30.0


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
        if isinstance(value, dict):
            return value
    except (json.JSONDecodeError, TypeError):
        pass

    # Extract first embedded JSON object if wrapped in explanatory text
    start = candidate.find("{")
    if start != -1:
        depth = 0
        in_quote = False
        escape = False
        for i in range(start, len(candidate)):
            c = candidate[i]
            if in_quote:
                if escape:
                    escape = False
                elif c == "\\":
                    escape = True
                elif c == '"':
                    in_quote = False
            else:
                if c == '"':
                    in_quote = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            val = json.loads(candidate[start : i + 1])
                            if isinstance(val, dict):
                                return val
                        except Exception:
                            break
    return None


class BoundedAgentStepRuntime:
    """Select and execute registered tools in a multi-turn ReAct loop, then return an answer."""

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
        specs = json.dumps(self._registry.describe(), ensure_ascii=False)[:32_000]
        objective = str(task.get("objective") or "").strip()[:16_000]
        if not objective:
            raise AgentStepError("Agent task has no objective.")
        system = (
            "You are Smara, an autonomous and highly capable AI agent. "
            "Think step by step and use the registered tools to accomplish the task. "
            "When tools return observations, evaluate them carefully and determine the next step. "
            "If a tool returns an error, self-correct and try an alternative approach. "
            "When the objective asks to create, edit, inspect, or run something on the paired "
            "desktop, use desktop.request_action or desktop.request_workflow to schedule approved local work. "
            "For a report, spreadsheet, presentation, or PDF, request local_file_write with create_docx/create_xlsx/create_pptx/create_pdf. "
            "Return exactly one JSON object without markdown fences: "
            '{"action":"tool","name":"...","arguments":{...}} or '
            '{"action":"final","answer":"..."}. '
            f"Registered tools:\n{specs}"
        )
        observations: list[str] = []
        tools_used = 0
        tool_calls_attempted = 0
        requested_tools: set[str] = set()
        for iteration in range(self._max_iterations):
            obs_formatted = "\n".join(observations)[-24_000:] if observations else "(none)"
            message = (
                f"Task objective:\n{objective}\n\n"
                f"Relevant shared memory:\n{memory_context[:16_000] or '(none)'}\n\n"
                f"Tool observations:\n{obs_formatted}\n\n"
                f"Reasoning turn {iteration + 1} of {self._max_iterations}."
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
                    observation = f"Tool failed safely: {type(exc).__name__}."
                    ok = False
            if event_hook:
                event_hook("agent.tool_completed", {"tool": name, "ok": ok, "preview": observation[:500]})
            observations.append(f"{name}: {observation[:16_000]}")

        final_obs = "\n".join(observations)[-24_000:] if observations else "(none)"
        final_message = (
            f"Task objective:\n{objective}\n\n"
            f"Verified tool observations:\n{final_obs}\n\n"
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
        obs_text = "\n".join(observations)[-24_000:] if observations else "(none)"
        message = (
            f"User request:\n{objective}\n\n"
            f"Relevant context:\n{context[:16_000] or '(none)'}\n\n"
            f"Verified tool observations:\n{obs_text}\n\n"
            f"Planning draft:\n{draft[:8_000] or '(none)'}\n\n"
            "Return only the final answer."
        )[:MAX_AGENT_PROMPT_CHARS]
        streamed = ""
        buffering_envelope = False
        stream = getattr(self._provider, "stream_complete", None)
        if callable(stream):
            try:
                async with asyncio.timeout(self._remaining(deadline)):
                    async for chunk in stream(system=system, message=message):
                        if not isinstance(chunk, str) or not chunk:
                            continue
                        previous = streamed
                        _, delta = append_stream_delta(previous, chunk)
                        remaining = MAX_AGENT_OUTPUT_CHARS - len(previous)
                        if remaining <= 0:
                            break
                        bounded = delta[:remaining]
                        streamed = previous + bounded
                        if not previous and streamed.lstrip().startswith("{"):
                            buffering_envelope = True
                        if bounded and not buffering_envelope:
                            token_hook(bounded)
            except Exception:
                if streamed:
                    return streamed.strip()
        if not streamed:
            answer = (await self._complete(system=system, message=message, deadline=deadline)).strip()
            if answer:
                streamed = answer[:MAX_AGENT_OUTPUT_CHARS]
                token_hook(streamed)
        answer = streamed.strip()
        if not answer:
            raise AgentStepError("Smara agent provider returned an empty final answer.")
        decision = _decode_decision(answer)
        if decision and decision.get("action") == "final" and isinstance(decision.get("answer"), str):
            answer = decision["answer"].strip()
            if buffering_envelope:
                token_hook(answer)
        return answer

