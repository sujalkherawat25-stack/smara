import json
import time
from pathlib import Path

import pytest

from smara.context_packing import ModelContextProfile, pack_messages
from smara.harness import Budget, BudgetExceeded, RunRequest, SessionEngine, ToolCall, ToolResult
from smara.progress import ProgressRecord, classify


def call(root: Path, call_id: str, name: str, **arguments) -> ToolCall:
    return ToolCall(call_id,name,arguments,str(root.resolve()))


def test_r25_three_compactions_preserve_repair_evidence(tmp_path: Path):
    target=tmp_path/"value.py"; session=SessionEngine(tmp_path,"r25",budget=Budget(60,8,4,20_000,1));session.begin_incremental("repair value")
    def write_one(raw):target.write_text("value=1");return ToolResult(raw["call_id"],"ok",changed_paths=("value.py",))
    def patch_two(raw):target.write_text("value=2");return ToolResult(raw["call_id"],"ok",changed_paths=("value.py",))
    session.execute_incremental(call(tmp_path,"write","file_write",path="value.py"),write_one)
    session.execute_incremental(call(tmp_path,"fail","terminal",command="pytest"),lambda raw:ToolResult(raw["call_id"],"error","1 failed",exit_code=1,meta={"evidence_scope":"focused"}))
    session.checkpoint([{"role":"user","content":"must equal two","_smara_mandatory":True}],{"phase":"repair"});first=session.get("continuation_artifact_id")
    session.execute_incremental(call(tmp_path,"repair","patch",path="value.py"),patch_two)
    session.checkpoint([{"role":"user","content":"must equal two","_smara_mandatory":True}],{"phase":"verify"});second=session.get("continuation_artifact_id")
    session.execute_incremental(call(tmp_path,"pass","terminal",command="pytest"),lambda raw:ToolResult(raw["call_id"],"ok","1 passed",exit_code=0,meta={"evidence_scope":"focused"}))
    session.checkpoint([{"role":"user","content":"must equal two","_smara_mandatory":True}],{"phase":"finish"});third=session.get("continuation_artifact_id")
    continuation=json.loads(session.resolve_artifact(third));assert continuation["parent_checkpoint_id"]==second and second!=first
    assert continuation["failed_evidence_ids"] and continuation["passing_evidence_ids"]
    assert session.finish_incremental("completed","done")["status"]=="completed"


def test_r26_unicode_large_schema_request_remains_protocol_valid():
    transcript=[{"role":"system","content":"constraint λ","_smara_mandatory":True}]
    for i in range(8):transcript.extend([{"role":"assistant","tool_calls":[{"id":f"c{i}"}],"content":""},{"role":"tool","tool_call_id":f"c{i}","content":"🧪"*400}])
    profile=ModelContextProfile("utf8-upper",5000,500,100,100);packed=pack_messages(transcript,profile,tools=[{"schema":"λ"*500}])
    assert packed.input_tokens+profile.output_reserve+profile.safety_margin<=profile.input_capacity
    present={tool["id"] for item in packed.messages for tool in item.get("tool_calls",[])}
    assert all(item.get("tool_call_id") in present for item in packed.messages if item.get("role")=="tool")


def test_r27_restart_after_checkpoint_does_not_replay_mutation(tmp_path: Path):
    target=tmp_path/"once.txt";session=SessionEngine(tmp_path,"r27",budget=Budget(tool_calls=2));session.begin_incremental("once")
    executions=[]
    def execute(raw):executions.append(raw["call_id"]);target.write_text("once");return ToolResult(raw["call_id"],"ok",changed_paths=("once.txt",))
    mutation=call(tmp_path,"stable-id","file_write",path="once.txt");session.execute_incremental(mutation,execute);session.checkpoint([],{"phase":"next"});session.close()
    reopened=SessionEngine(tmp_path,"r27",budget=Budget(tool_calls=2));assert reopened.execute_incremental(mutation,execute).ok
    assert executions==["stable-id"] and json.loads(reopened.resolve_artifact(reopened.get("continuation_artifact_id")))["next_action"]=="next"


def test_r28_corrupt_and_stale_evidence_cannot_complete(tmp_path: Path):
    target=tmp_path/"x.txt";target.write_text("a");session=SessionEngine(tmp_path,"r28",budget=Budget(tool_calls=4));session.begin_incremental("change")
    def edit(raw):target.write_text("b");return ToolResult(raw["call_id"],"ok",changed_paths=("x.txt",))
    session.execute_incremental(call(tmp_path,"edit","file_write",path="x.txt"),edit)
    session.execute_incremental(call(tmp_path,"verify","terminal",command="pytest"),lambda raw:ToolResult(raw["call_id"],"ok","pass",meta={"evidence_scope":"focused"}))
    session.checkpoint([],{"phase":"done"});artifact=session.get("continuation_artifact_id");next(session.artifacts.glob(f"{artifact}.*")).write_bytes(b"corrupt")
    with pytest.raises(ValueError):session.resolve_artifact(artifact)
    target.write_text("c")
    assert session.finish_incremental("completed","done")["status"]=="needs_input"


def test_r29_repeated_and_alternating_reads_are_bounded(tmp_path: Path):
    (tmp_path/"x").write_text("x");session=SessionEngine(tmp_path,"r29",budget=Budget(tool_calls=2));session.begin_incremental("read")
    executions=[]
    def execute(raw):executions.append(1);return ToolResult(raw["call_id"],"ok","x")
    results=[session.execute_incremental(call(tmp_path,str(i),"file_read",file_path="x"),execute) for i in range(4)]
    assert executions==[1] and results[-1].error_kind=="stall_detected"
    def record(fp):return ProgressRecord(fp,fp,"file_read","r","h",True,False,"observation")
    alternating=[record(value) for value in ("a","b","a","b","a","b")]
    assert classify([item.__dict__ for item in alternating[:-1]],alternating[-1])[1]=="alternating_call_loop"


def test_r30_todo_and_edit_revert_churn_do_not_certify_progress():
    todos=[ProgressRecord(str(i),str(i),"todo","r","h",True,False,"observation") for i in range(4)]
    assert classify([item.__dict__ for item in todos[:-1]],todos[-1])[1]=="todo_churn"
    edits=[ProgressRecord(str(i),str(i),"patch",revision,"h",True,True,"revision_changed") for i,revision in enumerate(("a","b","a","b"))]
    assert classify([item.__dict__ for item in edits[:-1]],edits[-1])[1]=="edit_revert_cycle"


def test_r31_changed_read_and_advancing_poll_are_not_stalled(tmp_path: Path):
    target=tmp_path/"x";target.write_text("a");session=SessionEngine(tmp_path,"r31",budget=Budget(tool_calls=3));session.begin_incremental("observe")
    seen=[]
    def execute(raw):seen.append(target.read_text());return ToolResult(raw["call_id"],"ok",seen[-1])
    session.execute_incremental(call(tmp_path,"a","file_read",file_path="x"),execute);target.write_text("b");session.execute_incremental(call(tmp_path,"b","file_read",file_path="x"),execute)
    assert seen==["a","b"]
    polls=[ProgressRecord(str(i),str(i),"process_poll",f"r{i}",str(i),True,True,"advancing_output") for i in range(8)]
    assert all(classify([item.__dict__ for item in polls[:i]],polls[i])[0] is None for i in range(len(polls)))


def test_r32_pause_budget_and_cancel_survive_restart(tmp_path: Path):
    session=SessionEngine(tmp_path,"r32",budget=Budget(1,2,1,100,1));session.begin_incremental("bounded");session.reserve_model_call(50);session.pause();elapsed=session._elapsed_wall();time.sleep(.02)
    assert session._elapsed_wall()==pytest.approx(elapsed,abs=.005)
    session.resume_clock();session.checkpoint([],{"phase":"model"});session.close();reopened=SessionEngine(tmp_path,"r32",budget=Budget(1,2,1,100,1));assert reopened.get("usage")["billed_tokens"]==50
    reopened.cancel();executed=[]
    with pytest.raises(BudgetExceeded,match="cancelled"):reopened.execute_incremental(call(tmp_path,"after","file_read",file_path="x"),lambda raw:executed.append(1))
    with pytest.raises(BudgetExceeded,match="cancelled"):reopened.reserve_model_call(1)
    assert not executed


def test_non_coding_output_contract_is_independently_enforced(tmp_path: Path):
    request=RunRequest("contract",str(tmp_path.resolve()),"return JSON",output_contract={"format":"json","required_fields":["answer"]})
    session=SessionEngine(tmp_path,"contract");session.begin_request(request)
    rejected=session.finish_incremental("completed","not json");assert rejected["status"]=="needs_input" and "valid JSON" in rejected["unresolved_items"][0]
