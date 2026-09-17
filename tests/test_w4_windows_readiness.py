import json
from pathlib import Path

from smara.app_adapter import application_envelope
from smara.doctor import diagnose
from smara.harness import Budget,SessionEngine
from smara.local_transport import LocalTransport


def test_w4_doctor_runs_independent_functional_probes(tmp_path):
    result=diagnose(tmp_path)
    assert result["ok"]
    assert {"configured","available","tested","status","detail"}<=set(result["checks"]["browser_backend"])
    assert result["checks"]["browser_backend"]["tested"]
    assert result["checks"]["process_operations"]["tested"]
    assert set(result["profiles"])=={"research","quick_research","deep_research","local_execution"}
    assert "research_validate" in result["profiles"]["research"]["capabilities"]
    expected={"research_plan","research_search","research_fetch","research_gather","research_inspect","research_analyze","research_resolve","research_validate","research_report"}
    assert set(result["profiles"]["quick_research"]["capabilities"])==expected
    assert set(result["profiles"]["deep_research"]["capabilities"])==expected
    assert result["profiles"]["quick_research"]["budget"]["wall_seconds"]==120
    assert result["profiles"]["deep_research"]["budget"]["wall_seconds"]==1800
    assert "process_start" in result["profiles"]["local_execution"]["capabilities"]
    rendered=json.dumps(result);assert "API_KEY" not in rendered and "Bearer " not in rendered


def test_w4_application_and_transport_share_canonical_fields(tmp_path):
    engine=SessionEngine(tmp_path,"parity",budget=Budget(100,10,10,1000,1));engine.begin_incremental("objective");engine.checkpoint([{"role":"user","content":"objective"}],{"constraints":["keep"],"next_action":"finish"});result=engine.finish_incremental("needs_input","partial",["verify output"])
    app=application_envelope(engine,result);token="x"*32;transport=LocalTransport(token,[tmp_path]);wire=transport.inspect(token,tmp_path,"parity")
    assert app["session_id"]==wire["session_id"]=="parity"
    assert app["status"]==result["status"] and app["unresolved_work"]==["verify output"]
    assert app["progress"]==wire["events"] and app["resume"]["session_id"]=="parity"
    assert app["remaining_budget"] and all(Path(path).is_file() for path in app["artifact_locations"])
