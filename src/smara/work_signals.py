"""Advisory task-work notifications.

Postgres remains the source of truth. Redis is only a wake-up hint, so a lost
message cannot lose work; callers always perform a bounded database claim or
repair read after waking.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

CHANNEL = "smara:work"
LOG = logging.getLogger("smara.work_signals")


class WorkSignalBus:
    """Best-effort synchronous publisher used by the store mutation paths."""

    def __init__(self, redis_url: str = ""):
        self.redis_url = redis_url or os.getenv("SMARA_REDIS_URL", "")
        self._publisher: Any | None = None

    def publish(self, kind: str, *, task_id: str | None = None) -> None:
        if not self.redis_url:
            return
        try:
            if self._publisher is None:
                import redis

                self._publisher = redis.Redis.from_url(
                    self.redis_url, decode_responses=True, socket_timeout=1
                )
            self._publisher.publish(
                CHANNEL,
                json.dumps({"kind": kind, "task_id": task_id}, separators=(",", ":")),
            )
        except Exception as exc:  # advisory only; never fail a durable mutation
            LOG.debug("work signal unavailable: %s", type(exc).__name__)


async def wait_for_signal(redis_url: str, timeout: float = 5.0) -> bool:
    """Wait for a Redis hint, or sleep for the repair-poll interval."""
    timeout = max(0.05, min(25.0, float(timeout)))
    if not redis_url:
        await asyncio.sleep(timeout)
        return False
    client = None
    pubsub = None
    try:
        from redis import asyncio as aioredis

        client = aioredis.from_url(redis_url, decode_responses=True)
        pubsub = client.pubsub()
        await pubsub.subscribe(CHANNEL)
        message = await pubsub.get_message(
            ignore_subscribe_messages=True, timeout=timeout
        )
        return bool(message)
    except Exception as exc:  # repair polling remains authoritative
        LOG.debug("work signal wait unavailable: %s", type(exc).__name__)
        await asyncio.sleep(timeout)
        return False
    finally:
        try:
            if pubsub is not None:
                await pubsub.close()
            if client is not None:
                await client.aclose()
        except Exception:
            pass
