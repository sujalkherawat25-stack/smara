import sys
import time
from pathlib import Path

from smara.harness import Budget,SessionEngine,ToolCall


def test_process_output_advances_across_session_restart(tmp_path: Path):
    engine=SessionEngine(tmp_path,"restart-soak",budget=Budget(30,30,1,100,1),constrained=False);engine.begin_incremental("observe long process")
    script="import time\nfor i in range(6):\n print(f'tick-{i}',flush=True);time.sleep(.05)"
    start=ToolCall("start","process_start",{"argv":[sys.executable,"-c",script],"timeout_seconds":5},str(tmp_path))
    started=engine.execute_incremental(start,lambda _raw:engine.broker.dispatch(start));process_id=started.meta["process_id"];engine.checkpoint([],{"phase":"poll","process_id":process_id});engine.close()
    reopened=SessionEngine(tmp_path,"restart-soak",budget=Budget(30,30,1,100,1),constrained=False);output="";exit_code=None
    for index in range(20):
        poll=ToolCall(f"poll-{index}","process_poll",{"process_id":process_id},str(tmp_path));result=reopened.execute_incremental(poll,lambda _raw,poll=poll:reopened.broker.dispatch(poll));output+=result.text;exit_code=result.exit_code
        if exit_code is not None:break
        time.sleep(.03)
    assert exit_code==0 and "tick-0" in output and "tick-5" in output
    assert reopened.get("usage")["tool_calls"]>1 and reopened.finish_incremental("completed","observed")["status"]=="completed"
