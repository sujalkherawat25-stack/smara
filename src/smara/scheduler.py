"""Durable interval scheduler for proactive Smara tasks.

The scheduler only creates ordinary task-graph runs. It never executes tools or
external actions itself; the normal worker and approval contracts remain in
charge after a schedule fires.
"""
from __future__ import annotations
import asyncio
import logging

from .config import settings
from .store import open_task_store

log = logging.getLogger("smara.scheduler")

async def main() -> None:
    store = open_task_store(database_url=settings.database_url, database_path=settings.database_path)
    while True:
        try:
            fired = store.fire_due_schedules(limit=20)
            if fired:
                log.info("created %s scheduled task run(s)", len(fired))
        except Exception:
            log.exception("schedule tick failed")
        await asyncio.sleep(30)

if __name__ == "__main__":
    asyncio.run(main())
