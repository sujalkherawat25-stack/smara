"""First-party search-domain hints for common standards and software projects.

These hints constrain discovery only. They contain no expected answers, do
not create evidence, and never bypass the normal public-URL fetch/provenance
checks.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse


_DOMAIN_RE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$",
    re.IGNORECASE,
)

# A group lists interchangeable first-party hosts for one named authority.
# Separate groups mean the question asks about distinct publishers and each
# should be searched independently (for example, an upstream/vendor conflict).
_AUTHORITY_RULES: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (re.compile(r"\b(?:PEP(?:\s*[-#]?\s*\d+)?|Python Enhancement Proposal)\b", re.I), ("peps.python.org",)),
    (re.compile(r"\bRFC(?:\s*[-#]?\s*\d+)?\b|\bRequest for Comments\b", re.I), ("rfc-editor.org", "ietf.org")),
    (re.compile(r"\bOpenSSL\b", re.I), ("openssl.org",)),
    (re.compile(r"\b(?:Red\s*Hat|RHEL)\b", re.I), ("access.redhat.com",)),
    (re.compile(r"\bOpenAI\b", re.I), ("openai.com",)),
    (re.compile(r"\bGit(?:\s+\d|[- ]SCM)\b", re.I), ("git-scm.com",)),
    (re.compile(r"\bNode\.?js\b", re.I), ("nodejs.org",)),
    (re.compile(r"\bCargo\b", re.I), ("doc.rust-lang.org",)),
    (re.compile(r"\bNumPy\b", re.I), ("numpy.org", "github.com")),
    (re.compile(r"\bDjango\b", re.I), ("djangoproject.com", "github.com")),
    (re.compile(r"\bPostgreSQL\b", re.I), ("postgresql.org",)),
    (re.compile(r"\b(?:CPython|Python|tomllib)\b", re.I), ("python.org", "docs.python.org")),
)


def normalize_search_domains(value: object) -> list[str]:
    """Validate bounded search filters; these are hostnames, never URLs."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise ValueError("include_domains must be a list of hostnames")
    domains: list[str] = []
    for raw in value:
        domain = str(raw or "").strip().lower().rstrip(".")
        if not domain or not _DOMAIN_RE.fullmatch(domain):
            raise ValueError("include_domains entries must be valid public hostnames")
        if domain not in domains:
            domains.append(domain)
    if len(domains) > 5:
        raise ValueError("include_domains accepts at most five hostnames")
    return domains


def authority_domain_groups(question: str) -> list[tuple[str, ...]]:
    """Return first-party host groups relevant to named authorities in a query."""
    text = str(question or "")
    groups: list[tuple[str, ...]] = []
    has_pep = bool(_AUTHORITY_RULES[0][0].search(text))
    has_rfc = bool(_AUTHORITY_RULES[1][0].search(text))
    for index, (pattern, domains) in enumerate(_AUTHORITY_RULES):
        if index == 11 and (has_pep or has_rfc):
            continue
        if pattern.search(text) and domains not in groups:
            groups.append(domains)
    return groups


def missing_authority_domain_groups(question: str, source_urls: list[str]) -> list[tuple[str, ...]]:
    """Return authority groups not represented among already fetched sources."""
    hosts = {
        (urlparse(str(url)).hostname or "").casefold().rstrip(".")
        for url in source_urls
        if str(url).startswith(("http://", "https://"))
    }
    missing = []
    for group in authority_domain_groups(question):
        if not any(host == domain or host.endswith("." + domain) for host in hosts for domain in group):
            missing.append(group)
    return missing
