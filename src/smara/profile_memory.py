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
            # "I am ..." is deliberately not a name form.  It is too easy
            # to turn ordinary prose ("I am Indian, remember?") into a
            # false identity record.  Names must be stated explicitly.
            if len(name) >= 3 and len(name.split()) <= 5 and name.lower() not in {"fine", "good", "okay", "ok", "here"}:
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
    account_name = str(facts.get("account_display_name") or "").strip()
    if account_name:
        lines.append(f"The signed-in account display name is {account_name}.")
    name = str(facts.get("preferred_name") or "").strip()
    if name:
        lines.append(f"The user has explicitly said their preferred name is {name}.")
    preference = str(facts.get("stated_preference") or "").strip()
    if preference:
        lines.append(f"The user has explicitly stated this preference: {preference}.")
    return "\n".join(lines)


def profile_summary(facts: dict[str, str] | None) -> str:
    """Return a conservative, deterministic answer to identity questions.

    Semantic recall is useful for project context but is not a trustworthy
    source for personal identity: it can contain old wording, quoted text, or
    assistant mistakes.  This summary intentionally uses only the signed-in
    account display name and explicitly saved facts.
    """
    facts = facts or {}
    account_name = str(facts.get("account_display_name") or "").strip()
    preferred_name = str(facts.get("preferred_name") or "").strip()
    preference = str(facts.get("stated_preference") or "").strip()
    parts: list[str] = []
    if preferred_name:
        parts.append(f"You asked me to call you {preferred_name}.")
    elif account_name:
        parts.append(f"You are signed in as {account_name}.")
    else:
        parts.append("I do not have a confirmed personal profile saved yet.")
    if preference:
        parts.append(f"You have told me that you prefer {preference}.")
    else:
        parts.append("I do not have any other confirmed personal preferences saved.")
    parts.append("I keep project and conversation context separately, and I do not treat unverified chat text as your identity.")
    return " ".join(parts)
