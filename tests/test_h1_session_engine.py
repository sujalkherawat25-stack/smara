from pathlib import Path
from smara.harness import SessionEngine, ToolResult

def test_interrupted_edit_test_resumes_with_same_event_schema(tmp_path: Path):
    engine = SessionEngine(tmp_path, "demo")
    calls = [{"name": "edit"}, {"name": "test"}]
    def interrupted(call):
        if call["name"] == "edit":
            (tmp_path / "x.py").write_text("x = 1\n")
            return ToolResult(call["call_id"], "ok", changed_paths=("x.py",))
        raise KeyboardInterrupt()
    try: engine.run("repair", calls, interrupted)
    except KeyboardInterrupt: pass
    resumed = SessionEngine(tmp_path, "demo").run("repair", calls, lambda call: ToolResult(call["call_id"], "ok", exit_code=0), resume=True)
    assert resumed["status"] == "completed"
    record = SessionEngine(tmp_path, "demo").inspect()
    assert [e["type"] for e in record["events"]][-2:] == ["tool_result", "finished"]
