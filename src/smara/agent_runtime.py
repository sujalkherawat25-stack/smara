"""Small, provider-neutral conversational runtime for Smara.

This intentionally ports the *boundary* of Memento's chat loop first: retrieve
relevant memory, call a configured model, and return a bounded answer.  It has
no MemoryOS imports and does not pretend that a short chat turn is a durable
task.  Tool use and the full Memento ReAct behaviour are extracted in later,
separately tested slices.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

import httpx


class ConversationMemory(Protocol):
    async def context_for_conversation(
        self, query: str, *, account_id: str, workspace_id: str
    ) -> str: ...


class ChatProvider(Protocol):
    async def complete(self, *, system: str, message: str) -> str: ...


@dataclass(frozen=True)
class ChatTurn:
    conversation_id: str
    message: str
    memory_used: bool
    model: str | None


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


class SmaraAgentRuntime:
    """The first independently deployable Smara agent-runtime boundary."""

    def __init__(self, provider: ChatProvider, memory: ConversationMemory | None = None):
        self._provider = provider
        self._memory = memory

    async def chat(
        self, *, account_id: str, workspace_id: str, message: str, conversation_id: str | None = None
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
        answer = await self._provider.complete(system=system, message=message)
        return ChatTurn(
            conversation_id=conversation_id or f"chat_{uuid.uuid4().hex}",
            message=answer,
            memory_used=bool(context),
            model=getattr(self._provider, "_model", None),
        )
