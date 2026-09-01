"""Defensive helpers for provider streams.

OpenAI-compatible providers normally return deltas, but a few gateways and
local servers return cumulative snapshots or repeat a small prefix when a
connection is resumed.  The UI consumes append-only token events, so normalize
those variants at the runtime boundary before forwarding text to clients.
"""
from __future__ import annotations


def append_stream_delta(current: str, chunk: str) -> tuple[str, str]:
    """Append only the new portion of *chunk* and return ``(text, delta)``.

    Exact repeats, cumulative snapshots, and small boundary overlaps are
    treated as already-delivered text.  Genuine deltas are preserved, including
    intentional repeated words when there is no clear token boundary overlap.
    """
    if not chunk:
        return current, ""
    if not current:
        return chunk, chunk
    if chunk == current or current.startswith(chunk):
        return current, ""
    if chunk.startswith(current):
        delta = chunk[len(current):]
        return chunk, delta

    # A resumed stream may repeat the tail of the previous chunk (for example
    # ``"How"`` followed by ``"How can I"``).  Trim the longest overlap only
    # when the join has a whitespace/punctuation boundary; this avoids turning
    # a genuine phrase such as ``"hello" + "lo"`` into a false dedupe.
    max_overlap = min(len(current), len(chunk))
    for overlap in range(max_overlap, 0, -1):
        if not current.endswith(chunk[:overlap]):
            continue
        suffix = chunk[overlap:]
        left_boundary = current[-overlap - 1] if len(current) > overlap else " "
        right_boundary = suffix[:1]
        overlap_boundary = (
            overlap >= 2
            and (
                chunk[overlap - 1].isspace()
                or (right_boundary and right_boundary.isspace())
                or left_boundary.isspace()
                or chunk[overlap - 1] in ".,!?;:)]}"
            )
        )
        # A one-character overlap is safe when the repeated character is a
        # standalone token (``"I"``/``"a"``) followed by whitespace.
        single_token_overlap = overlap == 1 and bool(right_boundary) and right_boundary.isspace()
        if overlap_boundary or single_token_overlap:
            return current + suffix, suffix
        break
    return current + chunk, chunk
