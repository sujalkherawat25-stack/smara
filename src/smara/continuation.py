"""Versioned durable handoff state; summaries are views, never evidence."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class ContinuationState:
    version: int = 1
    objective: str = ""
    constraints: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    tasks: tuple[Mapping[str, Any], ...] = ()
    decisions: tuple[str, ...] = ()
    unresolved_questions: tuple[str, ...] = ()
    changed_paths: tuple[str, ...] = ()
    workspace_revision: str = ""
    failed_evidence_ids: tuple[str, ...] = ()
    passing_evidence_ids: tuple[str, ...] = ()
    research_artifact_ids: tuple[str, ...] = ()
    pending_call_ids: tuple[str, ...] = ()
    uncertain_call_ids: tuple[str, ...] = ()
    active_handles: tuple[Mapping[str, Any], ...] = ()
    usage: Mapping[str, Any] = field(default_factory=dict)
    remaining_budget: Mapping[str, Any] = field(default_factory=dict)
    next_action: str = ""
    parent_checkpoint_id: str | None = None

    def to_dict(self) -> dict[str, Any]: return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ContinuationState":
        if int(value.get("version", 0)) != 1: raise ValueError("unsupported continuation version")
        fields = cls.__dataclass_fields__
        return cls(**{key: value[key] for key in fields if key in value})
