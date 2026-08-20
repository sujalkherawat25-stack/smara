"""Small safe defaults; production can replace the limiter with Redis."""
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
