"""Small, explicit account facts used to make shared memory recall reliable.

Syntarus remains Smara's shared semantic-memory provider.  This module stores
only facts a person states directly (name and durable preferences) in Smara's
account-scoped control-plane store.  It avoids asking an eventually-consistent
semantic index to be the sole source of truth for a just-stated identity fact.
"""
from __future__ import annotations

import re


_NAME_PATTERNS = (
    re.compile(r"\bmy\s+name\s+is\s+([A-Za-z][A-Za-z .'-]{0,70})", re.IGNORECASE),
    re.compile(r"\b(?:i\s+am|i['’]m)\s+([A-Za-z][A-Za-z .'-]{0,70})", re.IGNORECASE),
    re.compile(r"\bcall\s+me\s+([A-Za-z][A-Za-z .'-]{0,70})", re.IGNORECASE),
)
_PREFERENCE_PATTERN = re.compile(
    r"\bI\s+(?:prefer|like|want)\s+(.{3,180}?)(?:[.!?]|$)", re.IGNORECASE
)


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" .,!?:;-'\"")[:180]


def explicit_profile_facts(message: str) -> dict[str, str]:
    """Extract a deliberately narrow set of user-stated durable facts.

    Do not infer traits, persist assistant claims, or treat ordinary chat as a
    profile update.  A user can correct these facts simply by stating a newer
    one; the store replaces the same key.
    """
    text = str(message or "").strip()
    facts: dict[str, str] = {}
    for pattern in _NAME_PATTERNS:
        match = pattern.search(text)
        if match:
            # Do not let a permissive human-name character set swallow the
            # next sentence ("My name is Sujal. I prefer …").
            name = _clean(re.split(r"[.!?]", match.group(1), maxsplit=1)[0])
            # Avoid capturing a full sentence from a casual "I'm fine".
            if name and len(name.split()) <= 5 and name.lower() not in {"fine", "good", "okay", "ok", "here"}:
                facts["preferred_name"] = name
                break
    preference = _PREFERENCE_PATTERN.search(text)
    if preference:
        value = _clean(preference.group(1))
        if value:
            facts["stated_preference"] = value
    return facts


def profile_context(facts: dict[str, str] | None) -> str:
    """Render facts as data, never as instructions for the model."""
    facts = facts or {}
    lines: list[str] = []
    name = str(facts.get("preferred_name") or "").strip()
    if name:
        lines.append(f"The user has explicitly said their preferred name is {name}.")
    preference = str(facts.get("stated_preference") or "").strip()
    if preference:
        lines.append(f"The user has explicitly stated this preference: {preference}.")
    return "\n".join(lines)
