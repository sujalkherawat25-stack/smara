"""Small, provider-neutral conversational runtime for Smara.

This intentionally ports the *boundary* of Memento's chat loop first: retrieve
relevant memory, call a configured model, and return a bounded answer.  It has
no MemoryOS imports and does not pretend that a short chat turn is a durable
task.  Tool use and the full Memento ReAct behaviour are extracted in later,
separately tested slices.
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from collections.abc import AsyncIterator
from typing import Any, Callable, Protocol

import httpx

from .agent_step import BoundedAgentStepRuntime
from .agent_routing import route_request
from .tool_registry import ToolContext, default_tool_registry


class ConversationMemory(Protocol):
    async def context_for_conversation(
        self, query: str, *, account_id: str, workspace_id: str
    ) -> str: ...


class ChatProvider(Protocol):
    async def complete(self, *, system: str, message: Any) -> str: ...
    def stream_complete(self, *, system: str, message: Any) -> AsyncIterator[str]: ...


@dataclass(frozen=True)
class ChatTurn:
    conversation_id: str
    message: str
    memory_used: bool
    model: str | None
    tools_used: int = 0


_CHITCHAT_RE = re.compile(
    r"^(?:hi|hello|hey|hiya|yo|thanks?|thank you|good morning|good afternoon|"
    r"good evening|how are you(?: doing)?|what can you do|who are you)[!.?\s]*$",
    re.IGNORECASE,
)
_TOOL_HINT_RE = re.compile(
    r"\b(?:calculate|compute|search|research|look\s+up|find|fetch|weather|"
    r"latest|today|current|news|time|remember|recall|source|cite|citation)\b",
    re.IGNORECASE,
)

MEMORY_LOOKUP_TIMEOUT_SECONDS = 1.5
CONTEXT_MAX_CHARS = 12_000


def _bounded_context(
    memory: str = "",
    summary: str = "",
    recent: str = "",
    *,
    max_chars: int = CONTEXT_MAX_CHARS,
) -> str:
    """Pack context in priority order without losing recent user turns."""
    sections: list[tuple[str, str, int]] = [
        ("Relevant shared Syntarus memory", memory, 4_000),
        ("Bounded summary of earlier conversation", summary, 2_500),
        ("Recent conversation context (oldest to newest)", recent, 5_000),
    ]
    chunks: list[str] = []
    used = 0
    for label, value, preferred in sections:
        text = str(value or "").strip()
        if not text or used >= max_chars:
            continue
        remaining = min(preferred, max_chars - used)
        if remaining <= 0:
            continue
        chunks.append(f"{label}:\n{text[-remaining:]}")
        used += min(len(text), remaining) + 2
    return "\n\n".join(chunks)


def _triage(message: str) -> tuple[str, int, bool]:
    """Cheap local triage so greetings do not pay for a tool-planning loop.

    Memento uses a small model for this decision. Smara keeps the first pass
    deterministic and zero-cost: only clearly factual/actionable prompts enter
    the bounded tool loop. The model still decides which registered tool to
    call once the request is classified as tool-worthy.
    """
    decision = route_request(message)
    if decision.lane == "A":
        return "deterministic", decision.complexity, True
    if decision.lane == "E":
        return "durable", decision.complexity, False
    if decision.lane == "D":
        return "tool_request", decision.complexity, True
    if decision.lane == "C":
        return "memory", decision.complexity, False
    if _CHITCHAT_RE.fullmatch(message.strip()):
        return "chitchat", decision.complexity, False
    return "conversation", decision.complexity, False


class OpenAICompatibleProvider:
    """Works with providers that implement the standard chat-completions API."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 45.0,
        auth_header: str = "Authorization",
        http_client: httpx.AsyncClient | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._auth_header = auth_header.strip().lower()
        self._http_client = http_client
        self.capability = "chat"

    @asynccontextmanager
    async def _client(self):
        if self._http_client is not None:
            yield self._http_client
            return
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            yield client

    def _headers(self) -> dict[str, str]:
        if self._auth_header in {"api-subscription-key", "api_subscription_key"}:
            return {"api-subscription-key": self._api_key}
        return {"Authorization": f"Bearer {self._api_key}"}

    @staticmethod
    def _retry_delay(response: httpx.Response | Any, attempt: int) -> float:
        headers = getattr(response, "headers", {}) or {}
        try:
            retry_after = float(headers.get("Retry-After", ""))
        except (TypeError, ValueError):
            retry_after = 0.0
        return min(2.0, max(retry_after, 0.25 * (attempt + 1)))

    async def _post_completion(self, client: httpx.AsyncClient, payload: dict[str, Any]) -> httpx.Response:
        """Retry one safe, non-stream provider call on a transient outage."""
        for attempt in range(2):
            try:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                )
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt == 1:
                    raise
                await asyncio.sleep(0.25)
                continue
            status = int(getattr(response, "status_code", 200))
            if attempt == 0 and (status == 429 or status >= 500):
                await asyncio.sleep(self._retry_delay(response, attempt))
                continue
            response.raise_for_status()
            return response
        raise RuntimeError("Configured provider did not return a completion response.")

    @staticmethod
    def _content_from_choice(choice: Any) -> str | None:
        """Read normal text while deliberately ignoring reasoning-only fields."""
        if not isinstance(choice, dict):
            return None
        for container_name in ("message", "delta"):
            container = choice.get(container_name)
            if not isinstance(container, dict):
                continue
            content = container.get("content")
            if isinstance(content, str):
                return content
            # A few OpenAI-compatible gateways return text blocks instead of a
            # plain string. Join only explicit text blocks; never surface
            # reasoning_content to the user.
            if isinstance(content, list):
                parts = [item.get("text", "") for item in content if isinstance(item, dict) and isinstance(item.get("text"), str)]
                joined = "".join(parts)
                if joined:
                    return joined
        return None

    @staticmethod
    def _user_content(message: Any) -> Any:
        return message if isinstance(message, (str, list)) else str(message)

    async def complete(self, *, system: str, message: Any) -> str:
        if not self._base_url or not self._api_key or not self._model:
            raise RuntimeError("No Smara chat provider is configured.")
        async with self._client() as client:
            response = await self._post_completion(
                client,
                {
                    "model": self._model,
                    "temperature": 0.2,
                    # Reasoning-capable providers (notably Sarvam) may spend
                    # more than 1,200 tokens internally before emitting the
                    # visible answer. Keep the final answer bounded by our
                    # agent runtime, but give the provider enough headroom.
                    "max_tokens": 4096,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": self._user_content(message)},
                    ],
                },
            )
        data = response.json()
        try:
            content = self._content_from_choice(data["choices"][0])
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("Configured provider returned an invalid chat response.") from exc
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Configured provider returned an empty chat response.")
        return content.strip()

    async def stream_complete(self, *, system: str, message: Any) -> AsyncIterator[str]:
        """Yield normalized OpenAI-compatible content deltas."""
        if not self._base_url or not self._api_key or not self._model:
            raise RuntimeError("No Smara chat provider is configured.")
        async with self._client() as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/chat/completions",
                headers=self._headers(),
                json={
                    "model": self._model,
                    "temperature": 0.2,
                    "max_tokens": 4096,
                    "stream": True,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": self._user_content(message)},
                    ],
                },
            ) as response:
                response.raise_for_status()
                yielded = False
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line.removeprefix("data:").strip()
                    if payload == "[DONE]":
                        break
                    try:
                        data = json.loads(payload)
                        content = self._content_from_choice(data["choices"][0])
                    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                        continue
                    if isinstance(content, str) and content:
                        yielded = True
                        yield content
                if not yielded:
                    raise RuntimeError("Configured provider returned an empty chat stream.")


class SmaraAgentRuntime:
    """The first independently deployable Smara agent-runtime boundary."""

    def __init__(self, provider: ChatProvider, memory: ConversationMemory | None = None):
        self._provider = provider
        self._memory = memory

    @staticmethod
    def _recent_conversation(history: list[dict[str, Any]] | None) -> str:
        if not history:
            return ""
        lines: list[str] = []
        for turn in history[-16:]:
            role = "User" if turn.get("role") == "user" else "Smara"
            content = str(turn.get("content") or "").strip()
            if content:
                lines.append(f"{role}: {content[:4_000]}")
        return "\n".join(lines)[-12_000:]

    async def _memory_context(
        self,
        message: str,
        *,
        account_id: str,
        workspace_id: str,
        enabled: bool,
        event_hook: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> str:
        if not enabled or self._memory is None:
            return ""
        try:
            value = await asyncio.wait_for(
                self._memory.context_for_conversation(
                    message, account_id=account_id, workspace_id=workspace_id
                ),
                timeout=MEMORY_LOOKUP_TIMEOUT_SECONDS,
            )
            return str(value or "")[:8_000]
        except TimeoutError:
            if event_hook:
                event_hook("agent.memory_unavailable", {"reason": "timeout"})
            return ""
        except Exception:
            if event_hook:
                event_hook("agent.memory_unavailable", {"reason": "unavailable"})
            return ""

    async def _direct_answer(
        self,
        *,
        system: str,
        message: Any,
        token_hook: Callable[[str], None] | None = None,
    ) -> str:
        """Answer a conversational turn without the tool-planning round trip."""
        if token_hook:
            parts: list[str] = []
            stream = getattr(self._provider, "stream_complete", None)
            if callable(stream):
                try:
                    async for chunk in stream(system=system, message=message):
                        if isinstance(chunk, str) and chunk:
                            parts.append(chunk)
                            token_hook(chunk)
                except Exception:
                    # A streaming connection can fail after partial output.
                    # Do not issue a second answer into the same UI stream.
                    # Before the first token, however, the normal completion
                    # endpoint is a safe fallback and avoids turning a brief
                    # provider-stream outage into a failed chat turn.
                    if parts:
                        return "".join(parts).strip()
            if parts:
                return "".join(parts).strip()
        answer = (await self._provider.complete(system=system, message=message)).strip()
        if answer and token_hook:
            token_hook(answer)
        return answer

    async def chat(
        self, *, account_id: str, workspace_id: str, message: str, conversation_id: str | None = None,
        conversation_history: list[dict[str, Any]] | None = None,
        conversation_summary: str = "",
        attachment_context: str = "",
    ) -> ChatTurn:
        decision = route_request(message)
        memory_context = await self._memory_context(
            message,
            account_id=account_id,
            workspace_id=workspace_id,
            enabled=decision.memory_needed,
        )
        recent = self._recent_conversation(conversation_history)
        local_context = _bounded_context(
            memory_context, conversation_summary, recent
        )
        system = (
            "You are Smara, a helpful personal/work agent. Be concise and honest. "
            "A direct chat response cannot claim that it performed an external action. "
            "For work that needs tools, time, approval, or an artifact, explain that it "
            "should be created as a durable Smara task."
        )
        if memory_context:
            system += "\n\nRelevant shared Syntarus memory (may be incomplete):\n" + memory_context
        if conversation_summary.strip() or recent:
            system += "\n\nConversation context (bounded):\n" + local_context
        if attachment_context.strip():
            system += "\n\nUser attachments (bounded previews):\n" + attachment_context[:120_000]
        answer = await self._provider.complete(system=system, message=message)
        return ChatTurn(
            conversation_id=conversation_id or f"chat_{uuid.uuid4().hex}",
            message=answer,
            memory_used=bool(memory_context),
            model=getattr(self._provider, "_model", None),
        )

    async def chat_with_tools(
        self,
        *,
        account_id: str,
        workspace_id: str,
        message: str,
        attachment_images: list[dict[str, str]] | None = None,
        conversation_id: str | None = None,
        conversation_history: list[dict[str, Any]] | None = None,
        conversation_summary: str = "",
        attachment_context: str = "",
        http_client: httpx.AsyncClient | None = None,
        integration_runner: Callable[[str, str, dict[str, Any]], Any] | None = None,
        include_user_integrations: bool = True,
        event_hook: Callable[[str, dict[str, Any]], None] | None = None,
        token_hook: Callable[[str], None] | None = None,
    ) -> ChatTurn:
        """Run the same bounded read-only tool loop used by hosted tasks.

        Direct chat can search, fetch, and calculate. Personal-account
        integrations are included only when the deployment explicitly opts
        in; side effects remain unavailable here and must be represented by an
        approved durable task.
        """
        decision = route_request(
            message,
            has_attachments=bool(attachment_context.strip() or attachment_images),
        )
        intent, complexity, use_tools = _triage(message)
        if event_hook:
            event_hook("agent.phase", {"phase": "triage", "intent": intent, "complexity": complexity})

        registry = default_tool_registry(
            http_client,
            integration_runner=integration_runner,
            include_user_integrations=include_user_integrations,
        ).restrict(set(decision.tools_allowed))

        if decision.deterministic_tool is not None:
            # Preserve the visible phase contract for existing clients while
            # dispatching the safe registered tool without an LLM round trip.
            if event_hook:
                event_hook("agent.phase", {"phase": "retrieve"})
                event_hook("agent.phase", {"phase": "reason_act"})
            name, arguments = decision.deterministic_tool
            try:
                result = await registry.invoke(
                    name,
                    arguments,
                    ToolContext(account_id, workspace_id, http_client, integration_runner=integration_runner),
                )
            except ToolError as exc:
                raise RuntimeError(str(exc)) from exc
            answer = result.content
            if name == "calculate":
                answer = f"The result is {answer}."
            if event_hook and token_hook:
                event_hook("agent.phase", {"phase": "answer"})
            if token_hook:
                token_hook(answer)
            return ChatTurn(
                conversation_id=conversation_id or f"chat_{uuid.uuid4().hex}",
                message=answer,
                memory_used=False,
                model=getattr(self._provider, "_model", None),
                tools_used=1,
            )

        recent = self._recent_conversation(conversation_history)
        memory_context = await self._memory_context(
            message,
            account_id=account_id,
            workspace_id=workspace_id,
            enabled=decision.memory_needed,
            event_hook=event_hook,
        )
        context = _bounded_context(memory_context, conversation_summary, recent)
        if attachment_context.strip() and use_tools:
            # Tool planning receives explicit attachment text once. Direct
            # chat puts it in the user content below, avoiding duplication.
            context = ("User attachments (bounded previews):\n" + attachment_context[:120_000] + "\n\n" + context).strip()
        if event_hook:
            event_hook("agent.phase", {"phase": "retrieve"})
        conversation = conversation_id or f"chat_{uuid.uuid4().hex}"
        if not use_tools:
            system = (
                "You are Smara, a helpful personal/work agent. Be concise, warm, and honest. "
                "Do not claim that you performed an external action. If the user asks for "
                "real work, explain that it can be started as a durable Smara task."
            )
            if memory_context:
                system += "\n\nRelevant shared Syntarus memory (may be incomplete):\n" + memory_context
            if conversation_summary.strip() or recent:
                system += "\n\nConversation context (bounded):\n" + _bounded_context("", conversation_summary, recent)
            if event_hook:
                event_hook("agent.phase", {"phase": "answer"})
            user_content: Any = message
            # Put extracted text in the user turn as well as the system
            # context.  Some provider gateways are conservative about system
            # instructions; explicit user-supplied file text must never be
            # silently ignored (especially for a simple "summarise this"
            # request).
            if attachment_context.strip():
                user_content = (
                    f"{message}\n\nAttached file contents and metadata:\n"
                    f"{attachment_context[:120_000]}"
                )
            if attachment_images:
                text_content = user_content if isinstance(user_content, str) else message
                user_content = [{"type": "text", "text": text_content}]
                user_content.extend({"type": "image_url", "image_url": {"url": image["data_url"]}} for image in attachment_images)
            answer = await self._direct_answer(system=system, message=user_content, token_hook=token_hook)
            if not answer:
                raise RuntimeError("Smara provider returned an empty response.")
            return ChatTurn(
                conversation_id=conversation,
                message=answer,
                memory_used=bool(memory_context),
                model=getattr(self._provider, "_model", None),
            )
        if event_hook:
            event_hook("agent.phase", {"phase": "reason_act"})
        result = await BoundedAgentStepRuntime(
            self._provider,
            registry,
        ).run(
            task={
                "id": conversation,
                "task_run_id": f"run_{conversation}",
                "account_id": account_id,
                "workspace_id": workspace_id,
                "objective": message,
            },
            memory_context=context,
            tool_context=ToolContext(account_id, workspace_id, http_client, integration_runner=integration_runner),
            event_hook=event_hook,
            token_hook=token_hook,
        )
        return ChatTurn(
            conversation_id=conversation,
            message=result.text,
            memory_used=bool(memory_context),
            model=getattr(self._provider, "_model", None),
            tools_used=result.tools_used,
        )
