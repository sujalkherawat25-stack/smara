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
    r"^(?:(?:what(?:'s| is)\s+(?:the\s+)?)?(?:current\s+)?time|"
    r"what\s+time\s+(?:is\s+it|it\s+is)|tell\s+me\s+(?:the\s+)?(?:current\s+)?time)[!.?\s]*$",
    re.IGNORECASE,
)
_CALC_RE = re.compile(r"^(?:please\s+)?(?:calculate|compute)\s+(.+?)[!.?\s]*$", re.IGNORECASE)
_CALC_EXPRESSION_RE = re.compile(r"^[0-9+\-*/%().\s]+$")
_DURABLE_RE = re.compile(
    r"\b(?:write|append|patch|edit|rename|move|delete|create)\b[^\n]{0,80}\b(?:to|in|into|on|under)\b[^\n]{0,80}\b(?:documents|desktop|folder|disk|file|directory|workspace)\b|"
    r"\b(?:save\s+(?:it\s+)?to\s+(?:my\s+)?(?:documents|desktop|folder|disk|file|workspace))|"
    r"\b(?:create\s+(?:a\s+)?(?:file|folder|directory|script)\s+(?:in|on|at|under))|"
    r"\b(?:delete\s+(?:the\s+)?(?:file|folder|directory))|"
    r"\b(?:rename\s+(?:the\s+)?file|move\s+(?:the\s+)?file)|"
    r"\b(?:remind\s+me|schedule\s+(?:a\s+)?(?:task|job|report|script|reminder|workflow|check|run)|schedule\s+(?:every|daily|weekly|monthly|hourly|at))\b|"
    r"\b(?:run\s+(?:a\s+)?(?:terminal|shell|bash|powershell)\s+command)|"
    r"\b(?:run\s+(?:a\s+)?terminal\b)|"
    r"\b(?:run\s+command|run\s+script\s+on\s+desktop)|"
    r"\b(?:open\s+(?:website|browser|url)\s+on\s+desktop)|"
    r"\b(?:cancel|stop|pause|resume)\s+(?:the\s+)?(?:task|job|schedule|workflow)\b|"
    r"\b(?:send\s+email|send\s+message\s+via)\b",
    re.IGNORECASE,
)
_MEMORY_RE = re.compile(
    r"\b(?:remember|remeber|recall|memory|history|earlier|before|previous|last\s+time|"
    r"you\s+said|we\s+discussed|my\s+(?:plan|preference|project|context|name|work)|my)\b",
    re.IGNORECASE,
)
_IDENTITY_MEMORY_RE = re.compile(
    r"\b(?:do\s+you\s+(?:know|remember|remeber)\s+me|who\s+am\s+i|"
    r"what(?:'s|\s+is)\s+my\s+name|tell\s+me\s+about\s+me|"
    r"what\s+do\s+you\s+(?:know|remember|remeber)\s+about\s+me)\b",
    re.IGNORECASE,
)


def is_identity_memory_request(message: str) -> bool:
    """Whether a prompt asks for the user's own identity/profile facts."""
    return bool(_IDENTITY_MEMORY_RE.search(str(message or "").strip()))
_TOOL_RE = re.compile(
    r"\b(?:search|research|look\s+up|find|fetch|weather|latest|today|current|"
    r"news|source|cite|citation|gmail|calendar|drive|github)\b",
    re.IGNORECASE,
)
# Personal connectors are intentionally kept out of the hosted direct-chat
# lane when the deployment is in its local-only posture.  A request such as
# "List my GitHub repositories" needs the paired desktop (and its vault
# token); sending it through the hosted tool registry produces an empty
# registry and an unhelpful model apology instead of a durable approval task.
_LOCAL_INTEGRATION_RE = re.compile(
    r"\b(?:list|show|check|find|search|read|inspect|access|open|use)\b[^\n]{0,160}\b"
    r"(?:github|git\s+hub)\b(?:[^\n]{0,120}\b(?:repo(?:sitory|s)?|pull\s+requests?|issues?)\b)?|"
    r"\b(?:github|git\s+hub)\b[^\n]{0,120}\b(?:repo(?:sitory|s)?|pull\s+requests?|issues?)\b",
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
    "execute_python",
    "graph.inspect_symbol",
    "graph.blast_radius",
    "graph.find_references",
    "research.deep",
    "research.web_search",
    "research.fetch_url",
    "integration.gmail.search",
    "integration.calendar.list",
    "integration.drive.search",
    "integration.github.list",
)


def _direct_request_text(message: str) -> str:
    """Remove harmless conversational lead-ins before exact safe-tool routing.

    A request like "okay great, what time it is" must be handled exactly as
    "what time it is".  This does not remove substantive clauses, and durable
    work is still detected from the original message before direct tools run.
    """
    text = str(message or "").strip()
    lead_in = re.compile(r"^(?:(?:okay|ok|great|thanks?|thank\s+you|well|so|please)[\s,!.]*)+", re.IGNORECASE)
    return lead_in.sub("", text).strip()


def route_request(
    message: str,
    *,
    has_attachments: bool = False,
    explicit_memory: bool = False,
    local_only: bool = False,
) -> RouteDecision:
    text = message.strip()
    direct_text = _direct_request_text(text)
    # Identity questions are memory questions even though natural phrasing
    # often contains neither "memory" nor "remember".  Treating these as
    # small talk was the reason a restarted desktop could answer "Not yet"
    # despite the account having durable context.
    memory_requested = bool(explicit_memory or _MEMORY_RE.search(text) or is_identity_memory_request(text))
    if local_only and _LOCAL_INTEGRATION_RE.search(text):
        return RouteDecision(
            "E", "personal connector requires the paired desktop", 0.99, 3, True, (), True
        )
    if _DURABLE_RE.search(text):
        return RouteDecision(
            "E", "durable or approval-gated work", 0.98, 3, True, (), True
        )
    if _TIME_RE.fullmatch(direct_text):
        return RouteDecision(
            "A", "exact current-time request", 0.99, 1, False, ("current_time",), False,
            ("current_time", {}),
        )
    calc = _CALC_RE.fullmatch(direct_text)
    if calc and _CALC_EXPRESSION_RE.fullmatch(calc.group(1).strip()):
        return RouteDecision(
            "A", "exact bounded arithmetic request", 0.99, 1, False, ("calculate",), False,
            ("calculate", {"expression": calc.group(1).strip()}),
        )
    if _CHITCHAT_RE.fullmatch(text) or (has_attachments and not memory_requested and not _TOOL_RE.search(text)):
        return RouteDecision("B", "self-contained conversational turn", 0.98, 1, memory_requested, _READ_TOOLS, False)
    if memory_requested:
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
                memory_requested, _READ_TOOLS, False,
                ("research.deep", {"query": text, "max_sources": 5}),
            )
        return RouteDecision("D", "read-only tool request", 0.91, complexity, memory_requested, _READ_TOOLS, False)
    return RouteDecision("B", "self-contained answer", 0.84, 1, memory_requested, _READ_TOOLS, False)

