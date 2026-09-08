import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
import pytest
from smara.harness import SessionEngine
from smara.local_transport import EventGap,LocalTransport,LocalTransportServer

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

def test_authenticated_loopback_transport_replays_versioned_events(tmp_path):
    workspace=tmp_path/"space Ω";workspace.mkdir();token="z"*32;transport=LocalTransport(token,[workspace])
    def runner(engine,request):engine.checkpoint([{"role":"user","content":request}],{"phase":"finish"});return engine.finish_incremental("completed","ok")
    server=LocalTransportServer(transport,runner);base=server.start()
    try:
        body=json.dumps({"workspace":str(workspace),"request":"objective","command_id":"one"}).encode();request=urllib.request.Request(base+"/v1/sessions/http/run",data=body,headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"})
        response=json.loads(urllib.request.urlopen(request,timeout=5).read());assert response["version"]==1 and response["result"]["status"]=="completed"
        query=urllib.parse.urlencode({"workspace":str(workspace),"after":0});inspect=urllib.request.Request(base+f"/v1/sessions/http?{query}",headers={"Authorization":f"Bearer {token}"})
        replay=json.loads(urllib.request.urlopen(inspect,timeout=5).read());assert replay["result"]["envelope_version"]==1 and replay["result"]["events"]
        denied=urllib.request.Request(base+f"/v1/sessions/http?{query}",headers={"Authorization":"Bearer wrong"})
        with pytest.raises(urllib.error.HTTPError) as error:urllib.request.urlopen(denied,timeout=5)
        assert error.value.code==403
    finally:server.close()

def test_desktop_app_adapter_returns_canonical_events(tmp_path,monkeypatch):
    import smara.app_adapter as adapter
    def fake_run(self,task,max_iterations):
        self.session_engine.begin_incremental(task);result=self.session_engine.finish_incremental("completed","ok");return {"session":result}
    monkeypatch.setattr(adapter.SmaraAutonomousAgent,"run",fake_run)
    payload=adapter.run_canonical_task("desktop objective",tmp_path,budget_profile="short")
    assert payload["result"]["status"]=="completed"
    assert [event["type"] for event in payload["events"]]==["started","finished"]
    rust=Path("apps/desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")
    assert "from smara.app_adapter import run_canonical_task" in rust and "LocalAutonomousEngine" not in rust[rust.index("async fn run_goal_task"):rust.index("async fn get_goal_sessions")]
