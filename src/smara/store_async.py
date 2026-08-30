"""Async facade for Smara's intentionally synchronous state-store contract.

The store keeps its proven sync implementation for workers and CLI callers,
but API handlers must not block the event loop while a database connection is
leased or a busy query runs. This small facade puts those bounded operations
on a shared thread pool without changing the public TaskStore methods.
"""
from __future__ import annotations

import asyncio
import functools
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable


class AsyncStoreFacade:
    def __init__(self, store: Any, *, max_workers: int = 8):
        self.store = store
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="smara-store",
        )

    async def call(self, method: str | Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        target = getattr(self.store, method) if isinstance(method, str) else method
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            functools.partial(target, *args, **kwargs),
        )

    async def conversation_context(self, *args: Any, **kwargs: Any) -> tuple[list[dict], str]:
        return await self.call("conversation_context", *args, **kwargs)

    async def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)
