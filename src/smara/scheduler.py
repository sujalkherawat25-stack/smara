"""Scheduler process placeholder: durable schedule rows will enqueue tasks here.

It is deliberately separate from the API so adding cron/recurrence never makes
web replicas execute the same scheduled task twice.
"""
from __future__ import annotations
import asyncio

async def main() -> None:
    while True:
        await asyncio.sleep(30)

if __name__ == "__main__":
    asyncio.run(main())
