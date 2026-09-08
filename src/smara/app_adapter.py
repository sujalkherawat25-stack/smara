"""Thin application adapter over the canonical durable agent engine."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .autonomous_agent import SmaraAutonomousAgent
from .harness import BUDGET_PROFILES,SessionEngine


def run_canonical_task(objective:str,workspace:str|Path=".",session_id:str|None=None,budget_profile:str="long") -> dict[str,Any]:
    prompt=str(objective).strip();root=Path(workspace).resolve()
    if not root.is_dir():raise ValueError("workspace must exist")
    if not prompt:return {"status":"needs_input","answer":"","unresolved_items":["objective is empty"]}
    if budget_profile not in BUDGET_PROFILES:raise ValueError("unknown budget profile")
    session=SessionEngine(root,session_id,budget=BUDGET_PROFILES[budget_profile],constrained=False)
    try:
        result=SmaraAutonomousAgent(workspace_root=root,profile="full",session_engine=session).run(prompt,max_iterations=25)
        canonical=result.get("session") or session.inspect().get("state",{}).get("result")
        return {"result":canonical,"events":session.inspect()["events"]}
    finally:session.close()
