from pathlib import Path

from benchmarks.gaia_fair_runner import GaiaFairBenchmark
from smara import cli
from smara.harness import ToolCall, ToolResult


def test_interactive_ask_json_and_gaia_share_incremental_events(tmp_path: Path,monkeypatch,capsys):
    traces=[]
    def fake_run(self,task,max_iterations=25):
        session=self.session_engine;session.begin_incremental(task)
        tool_call=ToolCall("read-once","file_read",{"file_path":"README.md"},str(self.workspace_root))
        session.execute_incremental(tool_call,lambda raw:ToolResult(raw["call_id"],"ok","fixture"))
        session.checkpoint([{"role":"user","content":task}],{"phase":"finish"})
        result=session.finish_incremental("completed","ok")
        traces.append([event["type"] for event in session.inspect()["events"]])
        return {"answer":"ok","raw_answer":"FINAL ANSWER: ok","completed":True,"status":"completed","trace":[],"session":result}
    monkeypatch.setattr("smara.autonomous_agent.SmaraAutonomousAgent.run",fake_run)
    for arguments in (["--workspace",str(tmp_path),"direct objective"],["--workspace",str(tmp_path),"ask","ask objective"],["--workspace",str(tmp_path),"run","json objective","--json"]):
        assert cli.main(arguments)==0;capsys.readouterr()
    monkeypatch.setenv("SMARA_BENCHMARK_MODEL_ENDPOINT","https://model.test/v1");monkeypatch.setenv("SMARA_BENCHMARK_MODEL","test")
    tasks=[{"task_id":"one","Level":"1","Question":"benchmark objective","Final answer":"ok","file_name":""}]
    report=GaiaFairBenchmark(workspace_root=tmp_path,dataset_loader=lambda _token:tasks).evaluate_level("1")
    assert report["correct"]==1 and len(traces)==4
    semantic=lambda events:[event for event in events if event not in {"transport_command"}]
    assert all(semantic(trace)==semantic(traces[0]) for trace in traces[1:])
    assert "tool_admitted" in traces[0] and "checkpoint" in traces[0] and "finished" in traces[0]
