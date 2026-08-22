"""The sole boundary between Smara and Syntarus Memory."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


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

    async def aclose(self) -> None:
        close = getattr(self._client, "aclose", None)
        if close is not None:
            await close()
