import os
import time
import threading
import pytest
from smara.harness import Budget,BudgetExceeded,SessionEngine
from smara.skill_candidates import SkillCandidate,SkillCandidateStore

def _slow_worker_for_cancel(worker,goal,context,output):time.sleep(10)

def test_child_budget_cannot_escape_root_or_depth(tmp_path):
    session=SessionEngine(tmp_path,"root",budget=Budget(60,10,4,1000,1));session.begin_incremental("root")
    reservation=session.reserve_child_budget(Budget(20,4,2,400,.4),depth=1)
    with pytest.raises(BudgetExceeded):session.reserve_child_budget(Budget(20,7,3,700,.7),depth=1)
    with pytest.raises(BudgetExceeded):session.reserve_child_budget(Budget(20,1,1,100,.1),depth=2)
    session.reconcile_child_budget(reservation,{"tool_calls":3,"model_calls":1,"billed_tokens":300,"dollars":.2})
    assert session.inspect()["state"]["usage"]["tool_calls"]==3

def test_skills_stay_quarantined_until_held_out_transfer(tmp_path):
    store=SkillCandidateStore(tmp_path);candidate=SkillCandidate("repair",1,"session-1",("read_file",),"inspect then test")
    store.save(candidate);assert candidate.status=="quarantined"
    with pytest.raises(ValueError):store.promote(candidate,[{"passed":False}])
    store.promote(candidate,[{"task":"fresh","passed":True}]);assert candidate.status=="promoted"
    store.revoke(candidate,"regression");assert candidate.status=="revoked"

def test_worker_source_has_no_process_global_chdir():
    import inspect,smara.subagent_orchestrator as module
    source=inspect.getsource(module.SubagentWorker.run)
    assert "os.chdir" not in source
    assert module.DELEGATION_ENABLED is False
    entry=inspect.getsource(module._worker_process_entry)
    assert "os.environ.clear()" in entry and "SMARA_MODEL" not in entry

def test_orchestrator_reserves_root_budget_before_spawn(monkeypatch,tmp_path):
    import smara.subagent_orchestrator as module
    root=SessionEngine(tmp_path,"parent",budget=Budget(60,1,1,10,.01));root.begin_incremental("parent")
    monkeypatch.setattr(module,"DELEGATION_ENABLED",True)
    result=module.SubagentOrchestrator(workspace_root=tmp_path,root_session=root).delegate("child",max_iterations=2)
    assert result.status=="FAILED" and "child_" in result.error
    assert not root.get("child_reservations",{})

def test_root_cancellation_terminates_spawned_worker(monkeypatch,tmp_path):
    import smara.subagent_orchestrator as module
    root=SessionEngine(tmp_path,"cancel-parent",budget=Budget(30,20,10,200_000,2));root.begin_incremental("parent")
    monkeypatch.setattr(module,"DELEGATION_ENABLED",True);monkeypatch.setattr(module,"_worker_process_entry",_slow_worker_for_cancel)
    def cancel():
        other=SessionEngine(tmp_path,"cancel-parent");other.cancel();other.close()
    timer=threading.Timer(.3,cancel);timer.start()
    started=time.monotonic();result=module.SubagentOrchestrator(workspace_root=tmp_path,root_session=root).delegate("slow",max_iterations=1,timeout=10);timer.join()
    assert result.error=="cancelled" and time.monotonic()-started<5
    assert next(iter(root.get("child_reservations").values()))["status"]=="reconciled"

def test_swarm_cannot_bypass_disabled_root_orchestrator(tmp_path):
    import inspect
    from smara.swarm import SwarmOrchestrator,ImplementerAgent
    result=SwarmOrchestrator(tmp_path).run_swarm("do not launch")
    assert result.status=="FAILED" and result.architect_plan is None
    assert "SubagentWorker(" not in inspect.getsource(ImplementerAgent.execute_plan)
