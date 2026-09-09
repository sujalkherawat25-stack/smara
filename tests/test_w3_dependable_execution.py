import json
import subprocess
import sys
import time
from pathlib import Path

from smara.autonomous_agent import SmaraAutonomousAgent,get_tool_schemas
from smara.harness import Budget,SessionEngine,ToolCall
from smara.output_validation import validate_csv,validate_json,validate_report


def agent(root:Path,name="w3"):
    session=SessionEngine(root,name,budget=Budget(120,50,50,500_000,2),constrained=False)
    return SmaraAutonomousAgent(api_key="fixture",profile="coding",workspace_root=root,session_engine=session),session


def invoke(value,name,args):return json.loads(value.execute_tool(name,args))


def test_w3_typed_process_tools_and_repair_validator(tmp_path):
    value,_=agent(tmp_path);names={x["function"]["name"] for x in get_tool_schemas("coding")}
    assert {"process_start","process_poll","process_stdin","process_cancel"}<=names
    bad=tmp_path/"answer.json";bad.write_text('{"answer": 41}',encoding="utf-8")
    assert not validate_json(bad,required_fields=["answer"],expected={"answer":42}).passed
    bad.write_text('{"answer": 42}',encoding="utf-8")
    assert validate_json(bad,required_fields=["answer"],expected={"answer":42}).passed


def test_w3_failed_process_is_not_success(tmp_path):
    value,_=agent(tmp_path);out=invoke(value,"process_start",{"argv":[sys.executable,"-c","raise SystemExit(7)"],"cwd":"."});pid=out["meta"]["process_id"]
    for _ in range(50):
        state=invoke(value,"process_poll",{"process_id":pid,"cursor":0,"max_chars":100})
        if state["meta"]["done"]:break
        time.sleep(.02)
    assert state["status"]=="error" and state["exit_code"]==7


def test_w3_user_edit_conflict_is_preserved(tmp_path):
    value,_=agent(tmp_path);target=tmp_path/"owned.txt";target.write_text("user edit",encoding="utf-8")
    result=value._execution_broker.dispatch(ToolCall("conflict","write_file",{"path":"owned.txt","content":"agent edit","expected_sha256":"0"*64},str(tmp_path)))
    assert result.status=="denied" and target.read_text()=="user edit"


def test_w3_unicode_space_cwd_and_csv_content(tmp_path):
    work=tmp_path/"ü space";work.mkdir();value,_=agent(tmp_path)
    out=invoke(value,"process_start",{"argv":[sys.executable,"-c","from pathlib import Path;Path('x.csv').write_text('name,value\\nalpha,42\\n',encoding='utf-8')"],"cwd":"ü space"});pid=out["meta"]["process_id"]
    for _ in range(50):
        state=invoke(value,"process_poll",{"process_id":pid})
        if state["meta"]["done"]:break
        time.sleep(.02)
    assert validate_csv(work/"x.csv",required_columns=["name","value"],expected_rows=[{"name":"alpha","value":"42"}]).passed


def test_w3_interactive_stdin_and_cursor_chunks(tmp_path):
    value,_=agent(tmp_path);code="x=input();print('reply:'+x,flush=True)";start=invoke(value,"process_start",{"argv":[sys.executable,"-u","-c",code],"cwd":"."});pid=start["meta"]["process_id"]
    invoke(value,"process_stdin",{"process_id":pid,"text":"hello\n"})
    for _ in range(50):
        state=invoke(value,"process_poll",{"process_id":pid,"cursor":0,"max_chars":6})
        if state["meta"]["done"]:break
        time.sleep(.02)
    tail=invoke(value,"process_poll",{"process_id":pid,"cursor":state["meta"]["cursor"],"max_chars":100})
    assert state["output"]+tail["output"]=="reply:hello\r\n" or state["output"]+tail["output"]=="reply:hello\n"


def test_w3_session_cancel_stops_canary(tmp_path):
    value,session=agent(tmp_path);canary=tmp_path/"late.txt";code="import time;time.sleep(2);open('late.txt','w').write('bad')"
    invoke(value,"process_start",{"argv":[sys.executable,"-c",code],"cwd":"."});session.cancel();time.sleep(2.2)
    assert not canary.exists()


def test_w3_real_runtime_restart_marks_process_uncertain(tmp_path):
    script="""import json,sys\nfrom smara.harness import SessionEngine\nw=sys.argv[1];e=SessionEngine(w,'restart',constrained=False);r=e.run('x',[{'name':'process_start','arguments':{'argv':[sys.executable,'-c','import time;time.sleep(30)'],'cwd':'.','timeout_seconds':40}}]);print(json.dumps(r))\n"""
    env=dict(__import__('os').environ);env["PYTHONPATH"]=str(Path(__file__).parents[1]/"src")
    subprocess.run([sys.executable,"-c",script,str(tmp_path)],env=env,check=True,capture_output=True,text=True)
    engine=SessionEngine(tmp_path,"restart",constrained=False);call=engine.inspect()["calls"][0];pid=call["result"]["meta"]["process_id"]
    state=engine.broker.processes.poll(pid)
    assert state["status"]=="interrupted_uncertain" and state["reconnectable"] is False


def test_w3_three_compactions_preserve_constraints_evidence_and_report(tmp_path):
    engine=SessionEngine(tmp_path,"compact",budget=Budget(100,20,20,10000,1));artifact,_=engine.artifact_store.put(b"proof",".txt")
    state={"constraints":["keep Unicode ✓"],"acceptance_criteria":["report says verified"],"unresolved_questions":["none"],"next_action":"validate"}
    parent=None
    for index in range(3):
        engine.checkpoint([{"role":"tool","content":"x"*3000}],{**state,"evidence_artifact_ids":[artifact]});current=engine.get("continuation_artifact_id");assert current!=parent;parent=current
        engine.close();engine=SessionEngine(tmp_path,"compact")
        from smara.continuation import ContinuationState
        loaded=ContinuationState.from_dict(json.loads(engine.resolve_artifact(engine.get("continuation_artifact_id"))))
        assert loaded.constraints==("keep Unicode ✓",) and engine.resolve_artifact(artifact)==b"proof"
    report=tmp_path/"report.md";report.write_text("Result independently verified with durable evidence.",encoding="utf-8")
    assert validate_report(report,required_phrases=["independently verified"],minimum_words=5).passed
