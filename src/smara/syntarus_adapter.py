"""The sole boundary between Smara and Syntarus Memory."""
from __future__ import annotations

from typing import Protocol


class MemoryPort(Protocol):
    async def search(self, query: str, *, user_id: str, top_k: int = 10, agent_id: str | None = None) -> dict: ...
    async def add(self, *, user_id: str, messages: list[dict[str, str]], agent_id: str | None = None, run_id: str | None = None, metadata: dict | None = None, idempotency_key: str | None = None) -> dict: ...


class SyntarusMemory:
    def __init__(self, client: MemoryPort): self._client = client

    async def context_for_task(self, account_id: str, objective: str) -> str:
        result = await self._client.search(objective, user_id=account_id, top_k=8)
        return str(result.get("context", result.get("context_string", "")))

    async def remember_completion(self, task: dict, result: str) -> dict:
        # Only a verified final outcome is written; task logs remain in Smara.
        return await self._client.add(
            user_id=task["account_id"], run_id=task["id"],
            messages=[{"role": "user", "content": task["objective"]}, {"role": "assistant", "content": result}],
            metadata={"source": "smara_task", "workspace_id": task["workspace_id"], "task_id": task["id"]},
            idempotency_key=f"smara-completion-{task['id']}",
        )
