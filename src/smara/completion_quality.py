"""Shared checks that keep explicit incomplete work out of success states."""
from __future__ import annotations

import re

_UNRESOLVED_FINAL_PATTERNS = (
    re.compile(r"\bleft\s+(?:unimplemented|unfinished|incomplete)\b", re.IGNORECASE),
    re.compile(r"\b(?:remains?|still)\s+(?:unimplemented|unfinished|incomplete|unresolved)\b", re.IGNORECASE),
    re.compile(r"\b(?:could not|couldn't|unable to|cannot|can't)\s+(?:complete|implement|finish|resolve)\b", re.IGNORECASE),
    re.compile(r"\b(?:i|we)\s+(?:still\s+)?(?:need|require)\s+(?:(?:your|the|an?|approved)\s+)*(?:policy|requirements|clarification|information|input)\b", re.IGNORECASE),
    re.compile(r"(?:^|[.!?\n]\s*)missing\s+(?:the\s+)?(?:approved\s+)?(?:policy|requirements|clarification|information|input)\b", re.IGNORECASE),
)


def final_answer_reports_unresolved_work(answer: str) -> bool:
    """Recognize clear admissions that implementation or required information is missing."""
    text = str(answer or "")
    return any(pattern.search(text) for pattern in _UNRESOLVED_FINAL_PATTERNS)
