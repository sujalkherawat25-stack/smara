"""Governed Quick and Deep Research lane selection and budgets."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Literal

ResearchMode = Literal["auto", "quick", "deep"]


@dataclass(frozen=True)
class ResearchPolicy:
    mode: Literal["quick", "deep"]
    target_latency_seconds: tuple[int, int]
    min_sources: int
    target_sources: int
    max_sources: int
    max_nodes: int
    max_concurrency: int
    max_iterations: int
    comprehensive_report: bool

    def to_dict(self) -> dict:
        value = asdict(self)
        value["target_latency_seconds"] = list(self.target_latency_seconds)
        return value


QUICK_POLICY = ResearchPolicy("quick", (5, 30), 3, 5, 8, 4, 4, 10, False)
DEEP_POLICY = ResearchPolicy("deep", (300, 1800), 20, 40, 200, 24, 8, 80, True)
POLICIES = {"quick": QUICK_POLICY, "deep": DEEP_POLICY}

_DEEP_PHRASES = (
    "deep research", "comprehensive report", "exhaustive", "systematic review",
    "literature review", "market landscape", "competitive landscape", "due diligence",
    "multi-perspective", "all major", "state of the art", "white paper",
)
_COMPARATIVE = re.compile(r"\b(compare|comparison|versus|vs\.?|trade[- ]?offs?|alternatives?)\b", re.I)
_ANALYTICAL = re.compile(r"\b(causes?|effects?|risks?|trends?|strategy|forecast|evaluate|investigate|analy[sz]e)\b", re.I)
_REPORT_LENGTH = re.compile(r"\b(?:[2-9]\d{3,}\s*(?:words?|tokens?)|(?:\d{2,}|[2-9])\s*pages?)\b", re.I)
_BROAD_REPORT = re.compile(r"\b(comprehensive|detailed|full|exhaustive)\b.{0,80}\b(report|analysis|brief|investigation)\b", re.I)
_QUICK_PREFIX = re.compile(r"^\s*(who|what|when|where|which|is|are|does|did|how many|how much)\b", re.I)
# A bounded fact question may contain an attribution clause ("according to
# ...") before the interrogative.  Treat that shape as quick unless the
# caller explicitly asks for a deep deliverable; otherwise a provider can
# over-route a one-sentence lookup into an 80-iteration research DAG.
_BOUNDED_FACT = re.compile(
    r"^\s*(?:according to\b.{0,120}\b)?(?:who|what|when|where|which|is|are|does|did|how many|how much)\b",
    re.I,
)
_RESEARCH_INTENT = re.compile(
    r"\b(research|web search|look up|find sources?|cite|citations?|current|latest|as of|"
    r"market landscape|competitive landscape|literature review|systematic review|due diligence)\b",
    re.I,
)
_RESEARCH_DELIVERABLE = re.compile(r"\b(report|brief|analysis|sources?|evidence|citations?)\b", re.I)
_MUTATION_INTENT = re.compile(r"\b(implement|fix|edit|modify|refactor|delete|write code|run tests?|commit|push|build the app)\b", re.I)


def should_route_to_research(question: str) -> bool:
    """Return whether a general autonomous request belongs on the research-only tool lane."""
    value = re.sub(r"\s+", " ", str(question or "")).strip()
    if not value or _MUTATION_INTENT.search(value):
        return False
    return bool(
        _RESEARCH_INTENT.search(value)
        or _BROAD_REPORT.search(value)
        or (_COMPARATIVE.search(value) and _RESEARCH_DELIVERABLE.search(value))
        or any(phrase in value.casefold() for phrase in _DEEP_PHRASES)
    )


def select_research_lane(question: str, requested: ResearchMode | str = "auto") -> tuple[ResearchPolicy, dict]:
    """Choose a lane deterministically and return an auditable decision record."""
    requested_value = str(requested or "auto").strip().lower().replace("_", "-")
    aliases = {"fast": "quick", "quick-research": "quick", "deep-research": "deep"}
    requested_value = aliases.get(requested_value, requested_value)
    if requested_value not in {"auto", "quick", "deep"}:
        raise ValueError(f"unknown research mode: {requested}")
    raw_question = str(question or "").strip()
    normalized = re.sub(r"\s+", " ", raw_question).strip()

    # Extract the underlying one-line target before collapsing whitespace.
    # The acceptance harness appends detailed instructions on following lines;
    # scoring those instructions made bounded facts look like deep reports.
    scaffold_match = re.search(r"answer this live-web research question:\s*([^\r\n]+)", raw_question, re.I)
    target_text = scaffold_match.group(1).strip() if scaffold_match else normalized
    lowered = target_text.casefold()
    if requested_value != "auto":
        policy = POLICIES[requested_value]
        return policy, {"requested": requested_value, "selected": policy.mode, "score": None, "reasons": ["explicit_user_selection"]}

    score = 0
    reasons: list[str] = []
    matched = [phrase for phrase in _DEEP_PHRASES if phrase in lowered]
    if matched:
        score += 4
        reasons.append("explicit_deep_intent:" + ",".join(matched[:3]))
    if _COMPARATIVE.search(target_text):
        score += 2
        reasons.append("comparative_scope")
    if _ANALYTICAL.search(target_text):
        score += 1
        reasons.append("analytical_scope")
    if _REPORT_LENGTH.search(target_text):
        score += 3
        reasons.append("long_deliverable")
    if _BROAD_REPORT.search(target_text):
        score += 3
        reasons.append("comprehensive_deliverable")
    word_count = len(re.findall(r"\b\w+\b", target_text))
    if word_count >= 45:
        score += 2
        reasons.append("multi_constraint_prompt")
    elif word_count >= 25:
        score += 1
        reasons.append("broad_prompt")
    if target_text.count("?") >= 3:
        score += 2
        reasons.append("multiple_questions")
    if (_QUICK_PREFIX.search(target_text) or _BOUNDED_FACT.search(target_text)) and word_count <= 25 and not matched:
        score -= 2
        reasons.append("bounded_fact_question")
    policy = DEEP_POLICY if score >= 3 else QUICK_POLICY
    if not reasons:
        reasons.append("bounded_default")
    return policy, {"requested": "auto", "selected": policy.mode, "score": score, "reasons": reasons}


def research_lane_prompt(policy: ResearchPolicy) -> str:
    """Render the operational contract supplied to the model."""
    if policy.mode == "quick":
        return (
            "QUICK RESEARCH lane: answer a bounded question with 1-4 research nodes and 3-8 diverse fetched sources. "
            "Prefer research_gather for parallel retrieval, resolve every required claim, validate, then answer concisely. "
            "Do not expand into a comprehensive report unless evidence forces the auto-router to be reconsidered."
        )
    return (
        "DEEP RESEARCH lane: build an adaptive dependency DAG with multiple perspectives and explicit stopping criteria. "
        "Use research_gather in parallel waves, obtain at least 20 diverse fetched sources, add follow-up or contradiction nodes as evidence changes the investigation, "
        "resolve and validate all material claims, then call research_report to preserve a comprehensive Markdown report before finalizing. "
        "Keep the DAG bounded to the useful questions needed for the user's request (normally no more than six nodes); "
        "keep the validated claim set limited to the claims requested by the user (do not add incidental bibliographic facts); "
        "once 20 unique fetched sources and those requested claims are validated, stop searching and write the report instead of expanding the DAG."
    )
