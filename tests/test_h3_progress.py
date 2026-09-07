from pathlib import Path

from smara.harness import Budget, SessionEngine, ToolCall, ToolResult
from smara.progress import ProgressRecord, classify


def test_identical_read_reuses_receipt_across_restart(tmp_path: Path):
    (tmp_path/"x.txt").write_text("x")
    session=SessionEngine(tmp_path,"cache",budget=Budget(tool_calls=3)); session.begin_incremental("read")
    executions=[]
    def execute(raw): executions.append(1); return ToolResult(raw["call_id"],"ok","x")
    first=ToolCall("one","file_read",{"file_path":"x.txt"},str(tmp_path.resolve()))
    second=ToolCall("two","file_read",{"file_path":"x.txt"},str(tmp_path.resolve()))
    session.execute_incremental(first,execute)
    reopened=SessionEngine(tmp_path,"cache",budget=Budget(tool_calls=3))
    result=reopened.execute_incremental(second,execute)
    assert result.text=="x" and executions==[1]
    assert any(event["type"]=="tool_result_reused" for event in reopened.inspect()["events"])


def test_cached_read_escalates_strategy_then_stops(tmp_path: Path):
    (tmp_path/"x.txt").write_text("x")
    session=SessionEngine(tmp_path,"bounded-cache",budget=Budget(tool_calls=2)); session.begin_incremental("read")
    executions=[]
    def execute(raw): executions.append(1); return ToolResult(raw["call_id"],"ok","x")
    for call_id in ("one","two"):
        assert session.execute_incremental(ToolCall(call_id,"file_read",{"file_path":"x.txt"},str(tmp_path)),execute).ok
    recovery=session.execute_incremental(ToolCall("three","file_read",{"file_path":"x.txt"},str(tmp_path)),execute)
    stopped=session.execute_incremental(ToolCall("four","file_read",{"file_path":"x.txt"},str(tmp_path)),execute)
    assert "different strategy" in recovery.text
    assert stopped.error_kind=="stall_detected" and executions==[1]
    calls=session.inspect()["calls"]
    assert [call["call_id"] for call in calls]==["one","two","three","four"]


def test_file_change_invalidates_cached_read(tmp_path: Path):
    target=tmp_path/"x.txt"; target.write_text("one")
    session=SessionEngine(tmp_path,"changed",budget=Budget(tool_calls=3)); session.begin_incremental("read")
    seen=[]
    def execute(raw): seen.append(target.read_text()); return ToolResult(raw["call_id"],"ok",seen[-1])
    session.execute_incremental(ToolCall("a","file_read",{"file_path":"x.txt"},str(tmp_path.resolve())),execute)
    target.write_text("two")
    session.execute_incremental(ToolCall("b","file_read",{"file_path":"x.txt"},str(tmp_path.resolve())),execute)
    assert seen==["one","two"]


def test_alternating_loop_and_todo_churn_are_not_progress():
    def rec(fp,tool="file_read"): return ProgressRecord(fp,fp,tool,"r","h",True,False,"observation")
    window=[rec("a").__dict__,rec("b").__dict__,rec("a").__dict__,rec("b").__dict__,rec("a").__dict__]
    assert classify(window,rec("b"))[1]=="alternating_call_loop"
    todos=[rec(str(i),"todo").__dict__ for i in range(3)]
    assert classify(todos,rec("4","todo"))[1]=="todo_churn"


def test_advancing_poll_is_not_reusable_read():
    from smara.harness import REUSABLE_READ_TOOLS
    assert "process_poll" not in REUSABLE_READ_TOOLS
