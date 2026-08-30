from __future__ import annotations

import asyncio
import time

from smara.store import TaskStore
from smara.store_async import AsyncStoreFacade


def test_conversation_context_reads_summary_and_turns_together(tmp_path):
    async def exercise():
        store = TaskStore(str(tmp_path / "smara.db"))
        facade = AsyncStoreFacade(store, max_workers=2)
        try:
            await facade.call(
                "append_conversation_exchange",
                "chat_1",
                "acct_1",
                "default",
                "hello",
                "hi there",
                "test-model",
            )
            history, summary = await facade.conversation_context(
                "chat_1", "acct_1", "default"
            )
            assert summary == ""
            assert [turn["role"] for turn in history] == ["user", "assistant"]
            assert history[-1]["content"] == "hi there"
        finally:
            await facade.close()

    asyncio.run(exercise())


def test_blocking_store_call_does_not_freeze_event_loop():
    async def exercise():
        class BlockingStore:
            def wait(self):
                time.sleep(0.12)
                return "done"

        facade = AsyncStoreFacade(BlockingStore(), max_workers=1)
        ticks = 0

        async def ticker():
            nonlocal ticks
            for _ in range(3):
                await asyncio.sleep(0.02)
                ticks += 1

        try:
            result, _ = await asyncio.gather(facade.call("wait"), ticker())
            assert result == "done"
            assert ticks == 3
        finally:
            await facade.close()

    asyncio.run(exercise())
