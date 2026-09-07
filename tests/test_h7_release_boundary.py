import json
import pytest
from smara.harness import SessionEngine
from smara.local_transport import EventGap,LocalTransport

def test_event_cursor_reconnect_and_scope(tmp_path):
    workspace=tmp_path/"space";workspace.mkdir();engine=SessionEngine(workspace,"events");engine.begin_incremental("x");engine.checkpoint([{"role":"user","content":"x"}],{"phase":"model"})
    token="t"*32;transport=LocalTransport(token,[workspace]);first=transport.inspect(token,workspace,"events",0);assert first["events"] and first["cursor"]==first["events"][-1]["sequence"]
    assert transport.inspect(token,workspace,"events",first["cursor"])["events"]==[]
    with pytest.raises(PermissionError):transport.inspect("bad",workspace,"events")
    outside=tmp_path/"outside";outside.mkdir()
    with pytest.raises(PermissionError):transport.inspect(token,outside,"events")

def test_capability_manifest_never_promotes_unmeasured_external_gates():
    manifest=json.load(open("release/capabilities.json",encoding="utf-8"))
    assert manifest["capabilities"]["isolated_desktop"]["status"]=="unavailable"
    assert manifest["capabilities"]["official_gaia"]["status"]=="unmeasured"

def test_run_resume_cancel_are_idempotent_and_use_canonical_session(tmp_path):
    workspace=tmp_path/"workspace";workspace.mkdir();token="x"*32;transport=LocalTransport(token,[workspace]);calls=[]
    def runner(engine,request):
        calls.append(request);return engine.finish_incremental("completed","done")
    first=transport.run(token,workspace,"run","objective","command-1",runner)
    duplicate=transport.run(token,workspace,"run","objective","command-1",runner)
    resumed=transport.resume(token,workspace,"run","command-2",runner)
    assert first==duplicate and resumed["status"]=="completed" and calls==["objective","objective"]
    cancelled=transport.cancel(token,workspace,"run","command-3");assert cancelled["status"]=="completed"
    assert transport.cancel(token,workspace,"run","command-3")==cancelled
    with pytest.raises(EventGap):transport.inspect(token,workspace,"run",10_000)
