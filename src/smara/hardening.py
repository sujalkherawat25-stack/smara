"""Rate limit boundaries: local development fallback plus shared Redis."""
from __future__ import annotations
import time
from collections import defaultdict, deque


class FixedWindowLimiter:
    def __init__(self, limit: int): self.limit = max(1, limit); self._hits = defaultdict(deque)
    def allow(self, key: str) -> bool:
        now = time.monotonic(); values = self._hits[key]
        while values and values[0] <= now - 60: values.popleft()
        if len(values) >= self.limit: return False
        values.append(now); return True


class RedisFixedWindowLimiter:
    """Redis-backed fixed-window limiter shared by every API replica."""
    def __init__(self, url: str, limit: int, *, allow_local_fallback: bool):
        self.url, self.limit = url, max(1, limit)
        self.allow_local_fallback, self._fallback, self._client = allow_local_fallback, FixedWindowLimiter(limit), None

    async def allow(self, key: str) -> bool:
        if not self.url:
            if self.allow_local_fallback:
                return self._fallback.allow(key)
            raise RuntimeError("SMARA_REDIS_URL is required for production distributed rate limiting.")
        try:
            if self._client is None:
                from redis.asyncio import from_url
                self._client = from_url(self.url, decode_responses=True)
            bucket = f"smara:rate:{int(time.time() // 60)}:{key}"
            async with self._client.pipeline(transaction=True) as pipe:
                pipe.incr(bucket); pipe.expire(bucket, 61)
                count, _ = await pipe.execute()
            return int(count) <= self.limit
        except Exception:
            if self.allow_local_fallback:
                return self._fallback.allow(key)
            raise
