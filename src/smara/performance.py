"""Small, privacy-safe timing primitives for Smara request traces.

The trace deliberately stores only durations and counters.  It must never be
used as a request-content log or as a place to put provider responses.
"""
from __future__ import annotations

import re
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator


_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$")


def request_id(value: str | None = None) -> str:
    """Return a bounded caller id or a new opaque id."""
    candidate = (value or "").strip()
    if candidate and _REQUEST_ID_RE.fullmatch(candidate):
        return candidate
    return f"req_{uuid.uuid4().hex}"


@dataclass
class TimingTrace:
    """Request-scoped monotonic marks with a stable, safe serialization."""

    trace_id: str = field(default_factory=request_id)
    started_at: float = field(default_factory=time.perf_counter)
    _marks: dict[str, float] = field(default_factory=dict)
    _counters: dict[str, int] = field(default_factory=dict)

    def mark(self, name: str) -> None:
        if name:
            self._marks[name[:64]] = time.perf_counter() - self.started_at

    def increment(self, name: str, amount: int = 1) -> None:
        if not name:
            return
        key = name[:64]
        self._counters[key] = max(0, self._counters.get(key, 0) + int(amount))

    @contextmanager
    def span(self, name: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            if name:
                # A span is represented as elapsed duration, while marks are
                # cumulative offsets from request start.
                self._marks[f"{name[:56]}_ms"] = round((time.perf_counter() - started) * 1000, 1)

    def as_dict(self) -> dict[str, object]:
        timings: dict[str, int | float] = {name: round(seconds * 1000, 1) for name, seconds in self._marks.items() if not name.endswith("_ms")}
        timings.update({name: value for name, value in self._marks.items() if name.endswith("_ms")})
        timings["total_ms"] = round((time.perf_counter() - self.started_at) * 1000, 1)
        return {"trace_id": self.trace_id, "timings": timings, "counters": dict(self._counters)}

