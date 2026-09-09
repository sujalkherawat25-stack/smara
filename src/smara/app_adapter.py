"""Thin application adapter over the canonical durable agent engine."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .autonomous_agent import SmaraAutonomousAgent
from .harness import BUDGET_PROFILES,SessionEngine


def application_envelope(session:SessionEngine,result:dict[str,Any]|None=None)->dict[str,Any]:
    record=session.inspect();state=record["state"];canonical=result or state.get("result") or {}
    continuation_id=state.get("continuation_artifact_id");remaining={}
    if continuation_id:
        try:
            import json
            remaining=json.loads(session.resolve_artifact(continuation_id)).get("remaining_budget",{})
        except Exception:remaining={}
    artifacts=sorted(str(path) for path in session.artifacts.glob("*"))
    return {"version":1,"session_id":session.session_id,"status":canonical.get("status","interrupted"),"answer":canonical.get("answer",""),"progress":record["events"],"remaining_budget":remaining,"artifact_locations":artifacts,"unresolved_work":list(canonical.get("unresolved_items",[])),"resume":{"command":f"smara resume {session.session_id} --json","session_id":session.session_id}}


def run_canonical_task(objective:str,workspace:str|Path=".",session_id:str|None=None,budget_profile:str="long") -> dict[str,Any]:
    prompt=str(objective).strip();root=Path(workspace).resolve()
    if not root.is_dir():raise ValueError("workspace must exist")
    if not prompt:return {"status":"needs_input","answer":"","unresolved_items":["objective is empty"]}
    if budget_profile not in BUDGET_PROFILES:raise ValueError("unknown budget profile")
    session=SessionEngine(root,session_id,budget=BUDGET_PROFILES[budget_profile],constrained=False)
    try:
        result=SmaraAutonomousAgent(workspace_root=root,profile="full",session_engine=session).run(prompt,max_iterations=25)
        canonical=result.get("session") or session.inspect().get("state",{}).get("result")
        envelope=application_envelope(session,canonical)
        return {"result":canonical,"events":envelope["progress"],**envelope}
    finally:session.close()
