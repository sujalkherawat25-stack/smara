"""Deterministic hybrid reranking for provider search results."""
from __future__ import annotations

from collections import Counter
import math
import re
from urllib.parse import urlparse

from .research import canonical_source_url


def _terms(value: str) -> list[str]:
    return re.findall(r"[a-z0-9][a-z0-9-]{1,}", str(value or "").casefold())


def _trigrams(value: str) -> Counter[str]:
    compact = " ".join(_terms(value))
    return Counter(compact[i:i + 3] for i in range(max(0, len(compact) - 2)))


def _cosine(left: Counter[str], right: Counter[str]) -> float:
    denominator = math.sqrt(sum(v * v for v in left.values()) * sum(v * v for v in right.values()))
    return sum(value * right.get(key, 0) for key, value in left.items()) / denominator if denominator else 0.0


def hybrid_rank(query: str, hits: list, limit: int) -> list:
    """Fuse provider semantic order, lexical relevance, authority, and host diversity."""
    query_terms = Counter(_terms(query))
    query_grams = _trigrams(query)
    unique = {}
    for rank, hit in enumerate(hits, start=1):
        canonical = canonical_source_url(hit.url)
        if canonical and canonical not in unique:
            unique[canonical] = (rank, hit)
    scored = []
    for canonical, (rank, hit) in unique.items():
        document = f"{hit.title} {hit.snippet}"
        document_terms = Counter(_terms(document))
        lexical = sum(min(count, document_terms.get(term, 0)) for term, count in query_terms.items()) / max(1, sum(query_terms.values()))
        character = _cosine(query_grams, _trigrams(document))
        provider = 1.0 / (20.0 + rank)
        quality = 1.0 if getattr(hit, "quality", "") == "primary" else 0.5 if getattr(hit, "quality", "") == "secondary" else 0.0
        host = (urlparse(canonical).hostname or "").lower()
        authority = 1.0 if host.endswith((".gov", ".edu")) or host in {"who.int", "un.org", "worldbank.org"} else 0.0
        score = 0.38 * lexical + 0.22 * character + 0.20 * quality + 0.12 * authority + 0.08 * provider
        scored.append((score, canonical, hit))
    scored.sort(key=lambda item: (-item[0], item[1]))
    selected = []
    hosts: Counter[str] = Counter()
    while scored and len(selected) < max(1, int(limit)):
        best_index = max(
            range(len(scored)),
            key=lambda index: scored[index][0] - 0.12 * hosts[(urlparse(scored[index][1]).hostname or "").lower()],
        )
        _, canonical, hit = scored.pop(best_index)
        selected.append(hit)
        hosts[(urlparse(canonical).hostname or "").lower()] += 1
    return selected
