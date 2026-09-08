"""Durable progress and anti-thrashing classification."""
from __future__ import annotations

import hashlib, json
from dataclasses import asdict, dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ProgressRecord:
    fingerprint: str
    call_id: str
    tool: str
    subject_revision: str
    result_hash: str
    passed: bool
    material_progress: bool
    reason: str


def fingerprint(tool: str, arguments: Mapping[str,Any], revision: str) -> str:
    value=json.dumps([tool,arguments,revision],sort_keys=True,separators=(",",":"),default=str).encode()
    return hashlib.sha256(value).hexdigest()


def result_hash(text: str) -> str: return hashlib.sha256(text.encode("utf-8",errors="replace")).hexdigest()


def classify(window: list[Mapping[str,Any]], current: ProgressRecord, *, identical_failure_limit: int=3, alternating_limit: int=6, churn_limit: int=4) -> tuple[str|None,str]:
    records=[*window,current.__dict__]
    same=[item for item in records if item.get("fingerprint")==current.fingerprint]
    if not current.passed and len(same)>=identical_failure_limit: return "stop","repeated_identical_failure"
    tail=records[-alternating_limit:]
    if len(tail)>=alternating_limit and len({item.get("fingerprint") for item in tail})==2 and not any(item.get("material_progress") for item in tail): return "recover","alternating_call_loop"
    revision_tail=records[-4:]
    mutation_tools={"write_file","file_write","patch_file","patch"}
    if len(revision_tail)==4 and all(item.get("tool") in mutation_tools for item in revision_tail):
        revisions=[item.get("subject_revision") for item in revision_tail]
        if revisions[0]==revisions[2] and revisions[1]==revisions[3] and revisions[0]!=revisions[1]: return "recover","edit_revert_cycle"
    churn=[item for item in records[-churn_limit:] if item.get("tool")=="todo" and not item.get("material_progress")]
    if len(churn)>=churn_limit:return "recover","todo_churn"
    return None,""
