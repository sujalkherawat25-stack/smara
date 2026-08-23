"""Small, provider-neutral conversational runtime for Smara.

This intentionally ports the *boundary* of Memento's chat loop first: retrieve
relevant memory, call a configured model, and return a bounded answer.  It has
no MemoryOS imports and does not pretend that a short chat turn is a durable
task.  Tool use and the full Memento ReAct behaviour are extracted in later,
separately tested slices.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from collections.abc import AsyncIterator
from typing import Any, Callable, Protocol

import httpx

from .agent_step import BoundedAgentStepRuntime
from .tool_registry import ToolContext, default_tool_registry


class ConversationMemory(Protocol):
    async def context_for_conversation(
        self, query: str, *, account_id: str, workspace_id: str
    ) -> str: ...


class ChatProvider(Protocol):
    async def complete(self, *, system: str, message: str) -> str: ...
    def stream_complete(self, *, system: str, message: str) -> AsyncIterator[str]: ...


@dataclass(frozen=True)
class ChatTurn:
    conversation_id: str
    message: str
    memory_used: bool
    model: str | None
    tools_used: int = 0


class OpenAICompatibleProvider:
    """Works with providers that implement the standard chat-completions API."""

    def __init__(self, *, base_url: str, api_key: str, model: str, timeout_seconds: float = 45.0):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds

    async def complete(self, *, system: str, message: str) -> str:
        if not self._base_url or not self._api_key or not self._model:
            raise RuntimeError("No Smara chat provider is configured.")
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            response = await client.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self._model,
                    "temperature": 0.2,
                    "max_tokens": 1200,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": message},
                    ],
                },
            )
        response.raise_for_status()
        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("Configured provider returned an invalid chat response.") from exc
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Configured provider returned an empty chat response.")
        return content.strip()

    async def stream_complete(self, *, system: str, message: str) -> AsyncIterator[str]:
        """Yield normalized OpenAI-compatible content deltas."""
        if not self._base_url or not self._api_key or not self._model:
            raise RuntimeError("No Smara chat provider is configured.")
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self._model,
                    "temperature": 0.2,
                    "max_tokens": 1200,
                    "stream": True,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": message},
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
                        content = data["choices"][0]["delta"].get("content")
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

    async def chat(
        self, *, account_id: str, workspace_id: str, message: str, conversation_id: str | None = None,
        conversation_history: list[dict[str, Any]] | None = None,
        conversation_summary: str = "",
    ) -> ChatTurn:
        context = ""
        if self._memory is not None:
            try:
                context = await self._memory.context_for_conversation(
                    message, account_id=account_id, workspace_id=workspace_id
                )
            except Exception:
                # Memory is useful context, never a reason to make ordinary chat unavailable.
                context = ""
        system = (
            "You are Smara, a helpful personal/work agent. Be concise and honest. "
            "A direct chat response cannot claim that it performed an external action. "
            "For work that needs tools, time, approval, or an artifact, explain that it "
            "should be created as a durable Smara task."
        )
        if context:
            system += "\n\nRelevant shared Syntarus memory (may be incomplete):\n" + context[:12_000]
        recent = self._recent_conversation(conversation_history)
        if conversation_summary.strip():
            system += "\n\nBounded summary of earlier conversation:\n" + conversation_summary.strip()[-8_000:]
        if recent:
            system += "\n\nRecent conversation context (oldest to newest):\n" + recent
        answer = await self._provider.complete(system=system, message=message)
        return ChatTurn(
            conversation_id=conversation_id or f"chat_{uuid.uuid4().hex}",
            message=answer,
            memory_used=bool(context),
            model=getattr(self._provider, "_model", None),
        )

    async def chat_with_tools(
        self,
        *,
        account_id: str,
        workspace_id: str,
        message: str,
        conversation_id: str | None = None,
        conversation_history: list[dict[str, Any]] | None = None,
        conversation_summary: str = "",
        http_client: httpx.AsyncClient | None = None,
        event_hook: Callable[[str, dict[str, Any]], None] | None = None,
        token_hook: Callable[[str], None] | None = None,
    ) -> ChatTurn:
        """Run the same bounded read-only tool loop used by hosted tasks.

        Direct chat can search, fetch, calculate, and inspect configured
        read-only integrations. Side effects remain unavailable here and must
        be represented by an approved durable task.
        """
        context = ""
        if self._memory is not None:
            try:
                context = await self._memory.context_for_conversation(
                    message, account_id=account_id, workspace_id=workspace_id
                )
            except Exception:
                context = ""
        if conversation_summary.strip():
            context = (context + "\n\nBounded summary of earlier conversation:\n" + conversation_summary.strip()[-8_000:]).strip()
        recent = self._recent_conversation(conversation_history)
        if recent:
            context = (context + "\n\nRecent conversation context (oldest to newest):\n" + recent).strip()
        conversation = conversation_id or f"chat_{uuid.uuid4().hex}"
        result = await BoundedAgentStepRuntime(
            self._provider,
            default_tool_registry(http_client),
        ).run(
            task={
                "id": conversation,
                "task_run_id": f"run_{conversation}",
                "account_id": account_id,
                "workspace_id": workspace_id,
                "objective": message,
            },
            memory_context=context,
            tool_context=ToolContext(account_id, workspace_id, http_client),
            event_hook=event_hook,
            token_hook=token_hook,
        )
        return ChatTurn(
            conversation_id=conversation,
            message=result.text,
            memory_used=bool(context),
            model=getattr(self._provider, "_model", None),
            tools_used=result.tools_used,
        )
