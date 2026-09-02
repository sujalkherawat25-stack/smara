"""The sole boundary between Smara and Syntarus Memory."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol


class MemoryPort(Protocol):
    async def search(self, query: str, *, user_id: str, top_k: int = 10, agent_id: str | None = None, filters: dict | None = None) -> dict: ...
    async def add(self, *, user_id: str, messages: list[dict[str, str]], agent_id: str | None = None, run_id: str | None = None, metadata: dict | None = None, idempotency_key: str | None = None) -> dict: ...


@dataclass(frozen=True)
class MemoryScope:
    """Canonical cross-client Smara-to-Syntarus identity and provenance."""
    account_id: str
    workspace_id: str
    task_id: str
    run_id: str

    @property
    def user_id(self) -> str:
        return self.account_id

    def metadata(self, *, memory_kind: str, status: str, source_type: str) -> dict:
        return {
            "source": "smara_task",
            "workspace_id": self.workspace_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "memory_kind": memory_kind,
            "source_type": source_type,
            "status": status,
        }


class SyntarusMemory:
    def __init__(self, client: MemoryPort): self._client = client

    async def _search(self, query: str, *, user_id: str, workspace_id: str) -> dict:
        """Use optional provider filters without breaking older SDK releases.

        Account identity is always sent.  Metadata filtering is advisory until
        the hosted Syntarus API enforces it, so an SDK that predates the
        ``filters`` argument can safely fall back to the account-scoped search.
        We only retry the specific Python signature error; provider/network
        failures are allowed to propagate to the worker retry contract.
        """
        filters = {"workspace_id": workspace_id, "status": "verified"}
        try:
            return await self._client.search(query, user_id=user_id, top_k=8, filters=filters)
        except TypeError as exc:
            if "unexpected keyword argument 'filters'" not in str(exc):
                raise
            return await self._client.search(query, user_id=user_id, top_k=8)

    async def context_for_task(self, task: dict) -> str:
        """Retrieve shared account context; scope provenance is never omitted.

        The current public SDK exposes account isolation. Workspace/task filters
        are retained in every write's metadata and are ready to pass through
        when the Syntarus scoped-search capability is released; Smara does not
        pretend an unimplemented provider filter is enforced.
        """
        scope = MemoryScope(task["account_id"], task["workspace_id"], task["id"], task["task_run_id"])
        result = await self._search(task["objective"], user_id=scope.user_id, workspace_id=scope.workspace_id)
        return str(result.get("context", result.get("context_string", "")))[:12_000]

    async def context_for_conversation(self, query: str, *, account_id: str, workspace_id: str) -> str:
        """Conversation retrieval through the same SDK-only boundary.

        Workspace metadata is transmitted for the future server-enforced scoped
        retrieval release. Until that platform feature exists it is provenance,
        not an authorization guarantee; account isolation remains the API's
        enforced boundary.
        """
        result = await self._search(query, user_id=account_id, workspace_id=workspace_id)
        return str(result.get("context", result.get("context_string", "")))[:12_000]

    async def remember_completion(self, task: dict, result: str) -> dict:
        # Only a verified final outcome is written; task logs remain in Smara.
        scope = MemoryScope(task["account_id"], task["workspace_id"], task["id"], task.get("task_run_id", task["id"]))
        return await self._client.add(
            user_id=scope.user_id, run_id=scope.run_id,
            messages=[{"role": "user", "content": task["objective"]}, {"role": "assistant", "content": result}],
            metadata=scope.metadata(memory_kind="verified_outcome", status="verified", source_type="task_result"),
            idempotency_key=f"smara-completion-{task['id']}",
        )

    async def remember_verified_research(self, task: dict, report: str, evidence_count: int) -> dict:
        scope = MemoryScope(task["account_id"], task["workspace_id"], task["id"], task["task_run_id"])
        metadata = scope.metadata(memory_kind="verified_research", status="verified", source_type="research_report")
        metadata["evidence_count"] = evidence_count
        return await self._client.add(
            user_id=scope.user_id, run_id=scope.run_id,
            messages=[{"role": "user", "content": task["objective"]}, {"role": "assistant", "content": report[:12_000]}],
            metadata=metadata,
            idempotency_key=f"smara-research-{task['id']}",
        )

    async def remember_conversation_turn(
        self,
        *,
        account_id: str,
        workspace_id: str,
        conversation_id: str,
        user_message: str,
        assistant_message: str,
        turn_id: str | None = None,
    ) -> dict:
        """Persist a completed hosted chat turn in the shared memory plane.

        Chat history remains in Smara's bounded conversation store for prompt
        context.  This separate write gives the long-lived Syntarus memory
        extractor the same user/workspace provenance as durable task results,
        so later conversations can recall stable facts instead of starting
        from an empty memory namespace.  The deterministic idempotency key
        makes reconnects and client retries safe.
        """
        stable_turn_id = turn_id or self._stable_turn_key(user_message, assistant_message)
        scope = MemoryScope(
            account_id,
            workspace_id,
            conversation_id,
            f"chat_{stable_turn_id}",
        )
        metadata = scope.metadata(
            memory_kind="conversation_turn",
            status="verified",
            source_type="chat",
        )
        metadata["source"] = "smara_conversation"
        return await self._client.add(
            user_id=scope.user_id,
            run_id=scope.run_id,
            messages=[
                {"role": "user", "content": user_message[:12_000]},
                {"role": "assistant", "content": assistant_message[:12_000]},
            ],
            metadata=metadata,
            idempotency_key=f"smara-chat-{conversation_id}-{stable_turn_id}",
        )

    @staticmethod
    def _stable_turn_key(user_message: str, assistant_message: str) -> str:
        # Avoid importing a second hashing dependency at call sites and keep
        # the key compact enough for providers that cap idempotency lengths.
        import hashlib

        digest = hashlib.sha256(
            f"{user_message}\x00{assistant_message}".encode("utf-8", "ignore")
        ).hexdigest()
        return digest[:24]

    async def graph_context_for_task(self, task: dict) -> str:
        """Retrieve topological graph context and entity relations from Syntarus."""
        scope = MemoryScope(task["account_id"], task["workspace_id"], task["id"], task.get("task_run_id", task["id"]))
        query = f"graph_entities: {task['objective']}"
        result = await self._search(query, user_id=scope.user_id, workspace_id=scope.workspace_id)
        return str(result.get("context", result.get("context_string", "")))[:16_000]

    async def remember_graph_entities(self, task: dict, entities: list[dict[str, Any]]) -> dict:
        """Persist structured entity triples discovered during execution into Syntarus graph memory."""
        scope = MemoryScope(task["account_id"], task["workspace_id"], task["id"], task.get("task_run_id", task["id"]))
        metadata = scope.metadata(memory_kind="graph_entities", status="verified", source_type="code_graph")
        metadata["entity_count"] = len(entities)
        content = json.dumps(entities, ensure_ascii=False)[:12_000]
        return await self._client.add(
            user_id=scope.user_id,
            run_id=scope.run_id,
            messages=[{"role": "user", "content": task["objective"]}, {"role": "assistant", "content": content}],
            metadata=metadata,
            idempotency_key=f"smara-graph-{task['id']}",
        )

    async def aclose(self) -> None:
        close = getattr(self._client, "aclose", None)
        if close is not None:
            await close()

