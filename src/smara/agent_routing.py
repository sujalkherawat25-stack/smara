"""Deterministic, capability-preserving routing for Smara chat turns.

Routing is intentionally conservative and has no authority of its own.  It
only decides which already-registered read-only path is appropriate; local,
external-write, scheduled, and approval-required requests remain durable task
work.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RouteDecision:
    lane: str
    reason: str
    confidence: float
    complexity: int
    memory_needed: bool
    tools_allowed: tuple[str, ...]
    durable_required: bool
    deterministic_tool: tuple[str, dict] | None = None


_CHITCHAT_RE = re.compile(
    r"^(?:hi|hello|hey|hiya|yo|thanks?|thank you|good morning|good afternoon|"
    r"good evening|how are you(?: doing)?|what can you do|who are you)[!.?\s]*$",
    re.IGNORECASE,
)
_TIME_RE = re.compile(
    r"^(?:(?:what(?:'s| is) the\s+)?(?:current\s+)?time|what\s+time\s+is\s+it)[!.?\s]*$",
    re.IGNORECASE,
)
_CALC_RE = re.compile(r"^(?:please\s+)?(?:calculate|compute)\s+(.+?)[!.?\s]*$", re.IGNORECASE)
_CALC_EXPRESSION_RE = re.compile(r"^[0-9+\-*/%().\s]+$")
_DURABLE_RE = re.compile(
    r"\b(?:write|append|patch|edit|rename|move|delete|create|send|email|message|"
    r"remind|schedule|terminal|command|run\s+(?:a\s+)?script|browser|open\s+website|"
    r"desktop|local\s+(?:file|folder|app)|upload|download|cancel|stop|pause|resume)\b",
    re.IGNORECASE,
)
_MEMORY_RE = re.compile(
    r"\b(?:remember|recall|memory|history|earlier|before|previous|last\s+time|"
    r"you\s+said|we\s+discussed|my\s+(?:plan|preference|project|context|name|work)|my)\b",
    re.IGNORECASE,
)
_TOOL_RE = re.compile(
    r"\b(?:search|research|look\s+up|find|fetch|weather|latest|today|current|"
    r"news|source|cite|citation|gmail|calendar|drive|github)\b",
    re.IGNORECASE,
)
_DEEP_RESEARCH_RE = re.compile(
    r"\b(?:detailed\s+(?:analysis|breakdown|report)|comprehensive\s+(?:analysis|research|review|guide)|"
    r"complete\s+(?:guide|analysis|report|breakdown)|deep\s+(?:dive|research|search)|"
    r"how\s+(?:to|do\s+(?:we|you))\s+(?:build|make|design)|"
    # Accept natural hyphenated targets too ("1,200-word analysis" and
    # "at least 1200-words"), otherwise a demanding research request falls
    # into the generic planner and can never use the evidence-first writer.
    r"with\s+citations?|(?:at\s+least\s+|minimum(?:\s+of)?\s+)?[\d,]+(?:\s*[-–]\s*|\s+)words?|compare|comparison|"
    r"current\s+(?:state|landscape|architecture|developments?)|latest\s+(?:news|developments?|release))\b",
    re.IGNORECASE,
)

_READ_TOOLS = (
    "current_time",
    "calculate",
    "research.deep",
    "research.web_search",
    "research.fetch_url",
    "integration.gmail.search",
    "integration.calendar.list",
    "integration.drive.search",
    "integration.github.list",
)


def route_request(
    message: str,
    *,
    has_attachments: bool = False,
    explicit_memory: bool = False,
) -> RouteDecision:
    text = message.strip()
    if _DURABLE_RE.search(text):
        return RouteDecision(
            "E", "durable or approval-gated work", 0.98, 3, True, (), True
        )
    if _TIME_RE.fullmatch(text):
        return RouteDecision(
            "A", "exact current-time request", 0.99, 1, False, ("current_time",), False,
            ("current_time", {}),
        )
    calc = _CALC_RE.fullmatch(text)
    if calc and _CALC_EXPRESSION_RE.fullmatch(calc.group(1).strip()):
        return RouteDecision(
            "A", "exact bounded arithmetic request", 0.99, 1, False, ("calculate",), False,
            ("calculate", {"expression": calc.group(1).strip()}),
        )
    if _CHITCHAT_RE.fullmatch(text) or (has_attachments and not _MEMORY_RE.search(text) and not _TOOL_RE.search(text)):
        return RouteDecision("B", "self-contained conversational turn", 0.98, 1, bool(explicit_memory), _READ_TOOLS, False)
    if explicit_memory or _MEMORY_RE.search(text):
        if not _TOOL_RE.search(text):
            return RouteDecision("C", "personal or prior-context question", 0.94, 2, True, _READ_TOOLS, False)
    if _TOOL_RE.search(text) or _DEEP_RESEARCH_RE.search(text):
        complexity = 3 if len(text) > 1_000 or text.lower().count(" and ") >= 2 else 2
        if _DEEP_RESEARCH_RE.search(text):
            # Deep research is deterministic orchestration, not a model
            # suggestion. This prevents the planner from stopping after one
            # shallow search result and producing an evidence-starved answer.
            return RouteDecision(
                "D", "multi-source research request", max(0.91, 0.94), max(3, complexity),
                bool(explicit_memory or _MEMORY_RE.search(text)), _READ_TOOLS, False,
                ("research.deep", {"query": text, "max_sources": 5}),
            )
        return RouteDecision("D", "read-only tool request", 0.91, complexity, bool(explicit_memory or _MEMORY_RE.search(text)), _READ_TOOLS, False)
    return RouteDecision("B", "self-contained answer", 0.84, 1, bool(explicit_memory), _READ_TOOLS, False)
