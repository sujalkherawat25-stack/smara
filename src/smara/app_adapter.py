"""Thin application adapter over the canonical durable agent engine."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .autonomous_agent import SmaraAutonomousAgent,normalize_tool_profile
from .harness import BUDGET_PROFILES,SessionEngine
from .runtime_session import SQLiteRuntimeSessionStore,session_store_for_workspace


def application_envelope(session:SessionEngine,result:dict[str,Any]|None=None,*,runtime_store:SQLiteRuntimeSessionStore|None=None)->dict[str,Any]:
    record=session.inspect();state=record["state"];canonical=result or state.get("result") or {}
    continuation_id=state.get("continuation_artifact_id");remaining={}
    if continuation_id:
        try:
            import json
            remaining=json.loads(session.resolve_artifact(continuation_id)).get("remaining_budget",{})
        except Exception:remaining={}
    artifacts=sorted(str(path) for path in session.artifacts.glob("*"))
    envelope={"version":1,"session_id":session.session_id,"status":canonical.get("status","interrupted"),"answer":canonical.get("answer",""),"research_mode":state.get("research_mode"),"research_lane_decision":state.get("research_lane_decision"),"research_report_path":state.get("research_report_path"),"progress":record["events"],"remaining_budget":remaining,"artifact_locations":artifacts,"unresolved_work":list(canonical.get("unresolved_items",[])),"resume":{"command":f"smara resume {session.session_id} --json","session_id":session.session_id}}
    # ``progress`` remains the rich SessionEngine journal for compatibility;
    # ``runtime`` is the small cross-entry-point cursor clients use to
    # reconnect and replay events after a process/UI restart.
    if runtime_store is not None:
        with_runtime = runtime_store.snapshot(session.session_id)
        envelope["runtime"] = with_runtime
        envelope["event_cursor"] = with_runtime["cursor"]
        envelope["reconnect"] = {"session_id": session.session_id, "after": with_runtime["cursor"]}
    return envelope


def run_canonical_task(objective:str,workspace:str|Path=".",session_id:str|None=None,budget_profile:str="auto",tool_profile:str="full",research_mode:str="auto") -> dict[str,Any]:
    prompt=str(objective).strip();root=Path(workspace).resolve()
    if not root.is_dir():raise ValueError("workspace must exist")
    if not prompt:return {"status":"needs_input","answer":"","unresolved_items":["objective is empty"]}
    tool_profile=normalize_tool_profile(tool_profile)
    if tool_profile not in {"full","research","research_web","coding"}:raise ValueError("unknown tool profile")
    if research_mode not in {"auto","quick","deep"}:raise ValueError("unknown research mode")
    if tool_profile=="full":
        from .research_modes import should_route_to_research
        if research_mode!="auto" or should_route_to_research(prompt):tool_profile="research_web"
    if budget_profile=="auto":
        if tool_profile=="research_web":
            from .research_modes import select_research_lane
            budget_profile="research_deep" if select_research_lane(prompt,research_mode)[0].mode=="deep" else "research_quick"
        else:budget_profile="long"
    if budget_profile not in BUDGET_PROFILES:raise ValueError("unknown budget profile")
    session=SessionEngine(root,session_id,budget=BUDGET_PROFILES[budget_profile],constrained=False)
    runtime_store=session_store_for_workspace(root)
    runtime_store.create_or_get(session.session_id,request=prompt,workspace_id=str(root),mode="cli",tool_profile=tool_profile,research_mode=research_mode)
    if runtime_store.should_cancel(session.session_id):
        session.cancel()
    else:
        runtime_store.checkpoint(session.session_id,status="running",tool_profile=tool_profile,research_mode=research_mode,event="turn.started",event_payload={"request":prompt[:500]})
    try:
        from .cli import _load_local_profiles,_resolve_profile_key
        profiles,active_id,credentials=_load_local_profiles()
        profile=next((item for item in profiles if item.get("id")==active_id),profiles[0])
        config=session.get("model_config") or {"profile_id":profile.get("id"),"model":profile.get("model"),"base_url":profile.get("base_url","https://api.sarvam.ai/v2"),"auth_header":profile.get("auth_header","authorization")}
        session.set("model_config",config)
        profile=next((item for item in profiles if item.get("id")==config["profile_id"]),config)
        tool_profile=normalize_tool_profile(session.get("tool_profile") or tool_profile)
        session.set("tool_profile",tool_profile)
        research_mode=session.get("research_mode") or research_mode
        result=SmaraAutonomousAgent(workspace_root=root,profile=tool_profile,session_engine=session,api_key=_resolve_profile_key(profile,credentials),model=config["model"],base_url=config["base_url"],auth_header=config["auth_header"],research_mode=research_mode).run(prompt,max_iterations=None)
        canonical=result.get("session") or session.inspect().get("state",{}).get("result")
        runtime_store.checkpoint(session.session_id,status="cancelled" if runtime_store.should_cancel(session.session_id) else str(canonical.get("status") or "needs_input"),result=canonical,unresolved=canonical.get("unresolved_items") or [],event=f"turn.{str(canonical.get('status') or 'needs_input')}")
        envelope=application_envelope(session,canonical,runtime_store=runtime_store)
        return {"result":canonical,"events":envelope["progress"],**envelope}
    except Exception as exc:
        with_runtime = runtime_store.should_cancel(session.session_id)
        try:
            runtime_store.checkpoint(session.session_id,status="cancelled" if with_runtime else "failed",unresolved=[str(exc)[:500]],event="turn.cancelled" if with_runtime else "turn.failed",event_payload={"error":str(exc)[:500]})
        except Exception:
            pass
        raise
    finally:session.close()
