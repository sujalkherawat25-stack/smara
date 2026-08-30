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
from . import push

log = logging.getLogger("smara.scheduler")

async def main() -> None:
    store = open_task_store(database_url=settings.database_url, database_path=settings.database_path, redis_url=settings.redis_url if settings.work_signals_enabled else "")
    while True:
        try:
            fired = store.fire_due_schedules(limit=20)
            if fired:
                log.info("created %s scheduled task run(s)", len(fired))
                for item in fired:
                    try:
                        schedule = store.schedule(item["schedule_id"], item["account_id"])
                        await asyncio.to_thread(push.send, store, schedule["account_id"], "Smara scheduled task", f"{schedule['title']} is ready for review or execution.", "/")
                    except Exception:
                        # Delivery is optional and must never prevent the durable
                        # task run from existing or being processed.
                        log.exception("scheduled-task notification failed")
        except Exception:
            log.exception("schedule tick failed")
        await asyncio.sleep(30)

if __name__ == "__main__":
    asyncio.run(main())
