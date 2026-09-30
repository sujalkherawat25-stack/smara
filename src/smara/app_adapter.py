"""Thin application adapter over the canonical durable agent engine."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .autonomous_agent import SmaraAutonomousAgent,normalize_tool_profile
from .harness import BUDGET_PROFILES,SessionEngine
from .runtime_session import SQLiteRuntimeSessionStore,session_store_for_workspace


def _research_review(session: SessionEngine, state: dict[str, Any]) -> dict[str, Any] | None:
    """Expose bounded claim-to-passage receipts from the immutable research snapshot."""
    artifact_id = state.get("research_state_artifact_id")
    if not artifact_id:
        return None
    try:
        import json
        snapshot = json.loads(session.resolve_artifact(str(artifact_id)))
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        return {"status": "unavailable", "passed": False, "claims": [], "evidence": [], "failures": []}

    evidence_records = snapshot.get("evidence", {}).get("records", [])
    by_id = {
        str(item.get("id")): item
        for item in evidence_records
        if isinstance(item, dict) and item.get("id")
    }
    from .research_session import analysis_source_records

    analyses = snapshot.get("analyses") or []
    validation = snapshot.get("validation") or {}
    score = validation.get("score") or {}
    checks = score.get("claims") or []
    claims: list[dict[str, Any]] = []
    cited_ids: set[str] = set()
    for check in checks[:200]:
        if not isinstance(check, dict):
            continue
        citations = []
        for citation in (check.get("citations") or [])[:10]:
            if not isinstance(citation, dict):
                continue
            evidence_id = str(citation.get("evidence_id") or "")
            record = by_id.get(evidence_id)
            if record is None:
                continue
            cited_ids.add(evidence_id)
            source_records = analysis_source_records(evidence_id, analyses, by_id)
            cited_ids.update(item["evidence_id"] for item in source_records)
            source_urls = [item["url"] for item in source_records]
            citations.append({
                "evidence_id": evidence_id,
                "supported": bool(citation.get("supported")),
                "state": str(citation.get("state") or "insufficient"),
                "reason": str(citation.get("reason") or ""),
                "url": source_urls[0] if source_urls else str(record.get("canonical_url") or ""),
                "source_urls": source_urls,
                "evidence_url": str(record.get("canonical_url") or ""),
                "kind": "derived_analysis" if source_urls else str(record.get("kind") or ""),
                "retrieved_at": str(record.get("retrieved_at") or ""),
                "passage": str(record.get("text") or "")[:1200],
                "passage_sha256": str(citation.get("passage_sha256") or record.get("text_sha256") or ""),
            })
        claims.append({
            "claim": str(check.get("claim") or "")[:2000],
            "supported": bool(check.get("supported")),
            "citations": citations,
        })

    # Include fetched but uncited sources and failed pages in the inspector,
    # while keeping the application envelope bounded for long Deep runs.
    evidence = []
    for record in evidence_records:
        if not isinstance(record, dict) or len(evidence) >= 80:
            continue
        kind = str(record.get("kind") or "")
        if kind not in {"fetched_passage", "pdf_page", "pdf_table", "image_ocr"}:
            continue
        evidence.append({
            "evidence_id": str(record.get("id") or ""),
            "cited": str(record.get("id") or "") in cited_ids,
            "url": str(record.get("canonical_url") or ""),
            "kind": kind,
            "retrieved_at": str(record.get("retrieved_at") or ""),
            "sha256": str(record.get("content_sha256") or ""),
            "passage": str(record.get("text") or "")[:1200],
        })
    failures = [
        {"url": str(item.get("canonical_url") or "")[:1000], "error": str(item.get("error") or "")[:300]}
        for item in (snapshot.get("evidence", {}).get("failures") or [])[:80]
        if isinstance(item, dict)
    ]
    return {
        "status": "passed" if validation.get("passed") else "failed",
        "passed": bool(validation.get("passed")),
        "claim_count": int(score.get("claim_count") or len(claims)),
        "supported_claims": int(score.get("supported_claims") or 0),
        "evidence_precision": float(score.get("evidence_precision") or 0.0),
        "evidence_coverage": float(score.get("evidence_coverage") or 0.0),
        "claims": claims,
        "evidence": evidence,
        "failures": failures,
        "note": "Deterministic passage support is a review signal; inspect excerpts for nuanced claims.",
    }


def application_envelope(session:SessionEngine,result:dict[str,Any]|None=None,*,runtime_store:SQLiteRuntimeSessionStore|None=None)->dict[str,Any]:
    record=session.inspect();state=record["state"];canonical=result or state.get("result") or {}
    continuation_id=state.get("continuation_artifact_id");remaining={}
    if continuation_id:
        try:
            import json
            remaining=json.loads(session.resolve_artifact(continuation_id)).get("remaining_budget",{})
        except Exception:remaining={}
    artifacts=sorted(str(path) for path in session.artifacts.glob("*"))
    envelope={"version":1,"session_id":session.session_id,"status":canonical.get("status","interrupted"),"answer":canonical.get("answer",""),"research_mode":state.get("research_mode"),"research_lane_decision":state.get("research_lane_decision"),"research_report_path":state.get("research_report_path"),"research_review":_research_review(session,state),"progress":record["events"],"remaining_budget":remaining,"artifact_locations":artifacts,"unresolved_work":list(canonical.get("unresolved_items",[])),"resume":{"command":f"smara resume {session.session_id} --json","session_id":session.session_id}}
    # ``progress`` remains the rich SessionEngine journal for compatibility;
    # ``runtime`` is the small cross-entry-point cursor clients use to
    # reconnect and replay events after a process/UI restart.
    if runtime_store is not None:
        with_runtime = runtime_store.snapshot(session.session_id)
        envelope["runtime"] = with_runtime
        envelope["event_cursor"] = with_runtime["cursor"]
        envelope["reconnect"] = {"session_id": session.session_id, "after": with_runtime["cursor"]}
    return envelope


def run_canonical_task(objective:str,workspace:str|Path=".",session_id:str|None=None,budget_profile:str="auto",tool_profile:str="full",research_mode:str="auto",*,model_settings:dict[str,Any]|None=None,context_history:list[dict[str,Any]]|None=None,on_progress:Any=None,cancel_requested:Any=None) -> dict[str,Any]:
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
        if model_settings is not None:
            profile = {**model_settings, "id": "desktop-selected"}
            profiles, credentials = [profile], {}
        else:
            profiles,active_id,credentials=_load_local_profiles()
            profile=next((item for item in profiles if item.get("id")==active_id),profiles[0])
        config=session.get("model_config") or {"profile_id":profile.get("id"),"model":profile.get("model"),"base_url":profile.get("base_url","https://api.sarvam.ai/v2"),"auth_header":profile.get("auth_header","authorization")}
        session.set("model_config",config)
        profile=next((item for item in profiles if item.get("id")==config["profile_id"]),config)
        tool_profile=normalize_tool_profile(session.get("tool_profile") or tool_profile)
        session.set("tool_profile",tool_profile)
        research_mode=session.get("research_mode") or research_mode
        def progress(kind: str, data: dict[str, Any]) -> None:
            if cancel_requested and cancel_requested():
                session.cancel()
            if on_progress:
                on_progress(kind, data)
        agent = SmaraAutonomousAgent(workspace_root=root,profile=tool_profile,session_engine=session,api_key=str(model_settings.get("api_key") or "") if model_settings is not None else _resolve_profile_key(profile,credentials),model=config["model"],base_url=config["base_url"],auth_header=config["auth_header"],research_mode=research_mode,on_progress=progress)
        try:
            run_options: dict[str, Any] = {"max_iterations": None}
            if context_history is not None:
                run_options["context_history"] = context_history
            result=agent.run(prompt, **run_options)
        finally:
            agent._browser.shutdown()
            agent._cancel_owned_processes()
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
