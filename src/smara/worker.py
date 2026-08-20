from __future__ import annotations

import asyncio
import os

from syntarus import AsyncMemoryClient
from .config import settings
from .store import TaskStore, open_task_store
from .syntarus_adapter import SyntarusMemory
from .research import ResearchExecutor


async def run_once(store: TaskStore, memory: SyntarusMemory | None) -> bool:
    task = store.claim_one()
    if task is None:
        return False
    if task["status"] == "waiting_approval":
        return True
    try:
        context = ""
        if memory is not None:
            context = await memory.context_for_task(task)
        if task["name"].startswith("research."):
            outcome = await ResearchExecutor(store).run_step(task)
            if outcome.report and memory is not None:
                await memory.remember_verified_research(task, outcome.report, outcome.verified_evidence_count)
            store.complete_step(task["step_id"], task["account_id"], outcome.text)
            return True
    # Executor integration is intentionally explicit. This worker has no
    # implicit shell/browser privileges; a registered executor will replace
    # this deterministic safe completion step.
        result = "Task accepted by Smara. Executor integration is required to perform external actions."
        if context:
            result += " Relevant shared memory was retrieved."
        if memory is not None:
            await memory.remember_completion(task, result)
        store.complete_step(task["step_id"], task["account_id"], result)
    except Exception as exc:
        store.fail_step(task["step_id"], task["account_id"], str(exc))
    return True


async def main() -> None:
    store = open_task_store(database_url=settings.database_url, database_path=settings.database_path)
    memory = None
    if settings.syntarus_api_key:
        memory = SyntarusMemory(AsyncMemoryClient(settings.syntarus_api_key, base_url=settings.syntarus_base_url))
    try:
        while True:
            worked = await run_once(store, memory)
            await asyncio.sleep(0.2 if worked else 2)
    finally:
        if memory and hasattr(memory._client, "aclose"):
            await memory._client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
