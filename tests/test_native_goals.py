"""Ledger invariants; these are not real-model outcome evaluations."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import pytest

from smara.native_goals import GoalStore
from smara.native_goal_runtime import checkpoint_from


def create(store, root, **kwargs):
    return store.action({"action": "create", "objective": "Inspect the synthetic project", "success_criteria": "Return evidence",
        "workspace": str(root), "profile_id": "synthetic", "interval_seconds": 0, **kwargs})


def checkpoint(**kwargs):
    return {"decision": "continue", "summary": "Made progress", "next_step": "Continue",
        "plan": ["Inspect", "Report"], "completed_steps": ["Inspect"], "evidence": ["Observed a file"], "outputs": [], **kwargs}


def test_claim_is_atomic_and_checkpoint_resumes_same_session(tmp_path):
    store = GoalStore(tmp_path / "goals.sqlite")
    identity = create(store, tmp_path)
    with ThreadPoolExecutor(max_workers=6) as pool:
        claims = list(pool.map(lambda _: store.claim(), range(6)))
    assert sum(item is not None for item in claims) == 1
    goal = next(item for item in claims if item)
    store.attach(goal, "native-session-1", "turn-1")
    store.finish(goal, checkpoint(), "queued", "Continue")
    following = GoalStore(store.path).claim()
    assert following["session_id"] == "native-session-1"
    assert following["checkpoint"]["completed_steps"] == ["Inspect"]
    assert following["cycles"] == 2


@pytest.mark.parametrize("action,state", [("pause", "paused"), ("stop", "stopped")])
def test_user_control_during_cycle_survives_completion(tmp_path, action, state):
    store = GoalStore(tmp_path / "goals.sqlite")
    identity = create(store, tmp_path)
    goal = store.claim()
    store.action({"action": action, "id": identity})
    if action == "pause":
        with pytest.raises(ValueError, match="stopping"):
            store.action({"action": "resume", "id": identity})
    store.finish(goal, checkpoint(), "queued", "Model wants more work")
    assert store.get(identity)["state"] == state
    assert store.claim() is None


def test_recovery_expires_approvals_and_preserves_session_and_budget(tmp_path):
    store = GoalStore(tmp_path / "goals.sqlite")
    identity = create(store, tmp_path)
    goal = store.claim()
    store.attach(goal, "saved-session")
    store.reserve(goal, 1200)
    approval = store.pending(goal, {"id": 15, "method": "item/commandExecution/requestApproval", "params": {"command": "synthetic"}})
    store.recover()
    recovered = store.get(identity)
    assert recovered["state"] == "paused"
    assert recovered["session_id"] == "saved-session"
    assert recovered["requests"] == 1 and recovered["token_estimate"] == 1200
    assert store.claim() is None
    with pytest.raises(ValueError, match="expired"):
        store.respond(identity, approval, True)
    store.action({"action": "resume", "id": identity})
    assert store.claim()["session_id"] == "saved-session"


def test_approval_is_scoped_and_can_only_be_delivered_once(tmp_path):
    store = GoalStore(tmp_path / "goals.sqlite")
    identity = create(store, tmp_path)
    goal = store.claim()
    request = store.pending(goal, {"id": "native-rpc-7", "method": "item/permissions/requestApproval", "params": {"permissions": {"network": {"enabled": True}}}})
    store.respond(identity, request, False)
    assert store.consume_response(goal, request) == {"id": "native-rpc-7", "result": {"permissions": {}, "scope": "turn"}}
    assert store.consume_response(goal, request) is None
    with pytest.raises(ValueError):
        store.respond(identity, request, True)


def test_budget_is_cumulative_across_cycles_and_restart(tmp_path):
    store = GoalStore(tmp_path / "goals.sqlite")
    identity = create(store, tmp_path, max_requests=2)
    goal = store.claim()
    store.reserve(goal, 100)
    store.finish(goal, checkpoint(), "queued", "Continue")
    goal = GoalStore(store.path).claim()
    store.reserve(goal, 200)
    with pytest.raises(RuntimeError, match="budget"):
        store.reserve(goal, 300)
    store.finish(goal, checkpoint(), "queued", "Continue")
    assert store.claim() is None
    assert store.get(identity)["state"] == "paused"
    assert store.get(identity)["token_estimate"] == 300
    store.action({"action": "extend", "id": identity, "max_requests": 3})
    store.action({"action": "resume", "id": identity})
    assert store.claim()


def test_repeated_no_progress_blocks_without_endless_retries(tmp_path):
    store = GoalStore(tmp_path / "goals.sqlite")
    identity = create(store, tmp_path)
    for _ in range(3):
        goal = store.claim()
        assert goal
        store.finish(goal, checkpoint(), "queued", "Continue")
    assert store.get(identity)["state"] == "blocked"
    assert store.claim() is None


def test_model_success_requires_human_acceptance_and_feedback_invalidates_review(tmp_path):
    store = GoalStore(tmp_path / "goals.sqlite")
    identity = create(store, tmp_path)
    goal = store.claim()
    store.finish(goal, checkpoint(decision="ready_for_review"), "ready_for_review", "Review result")
    assert store.get(identity)["state"] != "completed"
    store.action({"action": "feedback", "id": identity, "text": "Also inspect tests"})
    assert store.get(identity)["state"] == "queued"
    with pytest.raises(ValueError):
        store.action({"action": "accept", "id": identity})
    goal = store.claim()
    store.finish(goal, checkpoint(decision="ready_for_review"), "ready_for_review", "Review result", [item["id"] for item in store.feedback(identity)])
    store.action({"action": "accept", "id": identity})
    assert store.get(identity)["state"] == "completed"


def test_feedback_is_retained_until_acknowledged_and_deduplicated(tmp_path):
    store = GoalStore(tmp_path / "goals.sqlite")
    identity = create(store, tmp_path)
    request = {"action": "feedback", "id": identity, "text": "Inspect tests", "feedback_id": "receipt-1"}
    store.action(request); store.action(request)
    assert len(store.feedback(identity)) == 1
    goal = store.claim()
    store.finish(goal, None, "blocked", "Interrupted")
    assert len(store.feedback(identity)) == 1
    store.action({"action": "resume", "id": identity})
    goal = store.claim()
    store.finish(goal, checkpoint(), "queued", "Continue", ["receipt-1"])
    assert store.feedback(identity) == []


def test_checkpoint_requires_real_workspace_outputs_and_evidence(tmp_path):
    with pytest.raises(ValueError):
        checkpoint_from(json.dumps(checkpoint(decision="ready_for_review", evidence=[])), tmp_path)
    with pytest.raises((ValueError, OSError)):
        checkpoint_from(json.dumps(checkpoint(outputs=["../outside.txt"])), tmp_path)
    assert checkpoint_from(json.dumps(checkpoint()), tmp_path)["decision"] == "continue"


def test_coding_checkpoint_claims_do_not_require_a_research_url(tmp_path):
    coding = checkpoint(claims=[{"claim": "The function now adds values", "url": "", "quote": "return a + b"}])
    assert checkpoint_from(json.dumps(coding), tmp_path, "coding")["claims"][0]["url"] == ""
    with pytest.raises(ValueError, match="citation claims"):
        checkpoint_from(json.dumps(coding), tmp_path, "mixed")


def test_stale_cycle_cannot_override_new_lease(tmp_path):
    store = GoalStore(tmp_path / "goals.sqlite")
    identity = create(store, tmp_path)
    abandoned = store.claim()
    store.recover()
    store.action({"action": "resume", "id": identity})
    following = store.claim()
    with pytest.raises(RuntimeError, match="Stale"):
        store.finish(abandoned, checkpoint(), "queued", "Stale")
    assert store.get(identity)["lease"] == following["lease"]


def test_interactive_project_work_and_goals_do_not_overlap(tmp_path):
    store = GoalStore(tmp_path / "goals.sqlite")
    identity = create(store, tmp_path)
    store.interactive(tmp_path, "foreground-owner", True)
    assert store.claim() is None
    with pytest.raises(ValueError, match="Another"):
        store.interactive(tmp_path, "other-owner", True)
    store.interactive(tmp_path, "foreground-owner", False)
    assert store.claim()
    with pytest.raises(ValueError, match="A goal"):
        store.interactive(tmp_path, "foreground-owner", True)


def test_feedback_arriving_during_cycle_prevents_premature_review(tmp_path):
    store = GoalStore(tmp_path / "goals.sqlite")
    identity = create(store, tmp_path)
    goal = store.claim()
    store.action({"action": "feedback", "id": identity, "text": "Change the requested output"})
    store.finish(goal, checkpoint(decision="ready_for_review"), "ready_for_review", "Model finished old request")
    assert store.get(identity)["state"] == "queued"
    assert store.feedback(identity)


def test_parallel_pending_approvals_and_pause_do_not_deliver_stale_decisions(tmp_path):
    store = GoalStore(tmp_path / "goals.sqlite")
    identity = create(store, tmp_path)
    goal = store.claim()
    requests = [store.pending(goal, {"id": n, "method": "item/commandExecution/requestApproval", "params": {}}) for n in (1, 2)]
    store.respond(identity, requests[0], False)
    assert store.consume_response(goal, requests[0])
    assert store.get(identity)["state"] == "awaiting_approval"
    store.respond(identity, requests[1], False)
    store.action({"action": "pause", "id": identity})
    assert store.consume_response(goal, requests[1]) is None


def test_creation_receipt_prevents_duplicate_and_conflicting_goals(tmp_path):
    store = GoalStore(tmp_path / "goals.sqlite")
    first = create(store, tmp_path, identity="samecreationreceipt")
    assert create(store, tmp_path, identity="samecreationreceipt") == first
    assert len(store.list()) == 1
    with pytest.raises(ValueError, match="different goal"):
        create(store, tmp_path, identity="samecreationreceipt", objective="Another objective")


def test_missing_provider_blocks_only_its_goal_and_releases_the_lease(tmp_path, monkeypatch):
    from smara.native_goal_runtime import execute_cycle
    from smara import native_profiles
    store = GoalStore(tmp_path / "goals.sqlite")
    identity = create(store, tmp_path)
    def unavailable():
        raise ValueError("Synthetic private provider detail that must not reach the client")
    monkeypatch.setattr(native_profiles, "load_profiles", unavailable)
    result = execute_cycle(store, store.claim())
    assert result["state"] == "blocked" and result["lease"] is None
    assert "private provider detail" not in result["detail"]
    assert result["requests"] == 0
    following = create(store, tmp_path, objective="Another queued goal")
    assert store.claim()["id"] == following != identity


def test_rejected_token_reservation_requires_an_explicit_budget_increase(tmp_path):
    store = GoalStore(tmp_path / "goals.sqlite")
    identity = create(store, tmp_path, max_token_estimate=4096)
    goal = store.claim()
    with pytest.raises(RuntimeError, match="budget"):
        store.reserve(goal, 5000)
    store.finish(goal, None, "blocked", "Provider reservation rejected")
    assert store.get(identity)["state"] == "paused"
    assert store.get(identity)["requests"] == 0
    with pytest.raises(ValueError, match="Extend"):
        store.action({"action": "resume", "id": identity})
    store.action({"action": "extend", "id": identity, "max_token_estimate": 6000})
    store.action({"action": "resume", "id": identity})
    store.reserve(store.claim(), 5000)
    assert store.get(identity)["requests"] == 1


def test_native_foreground_ownership_waits_for_parallel_worker_completion():
    from smara.native_runtime import NativeProjectActivity
    activity = NativeProjectActivity()
    activity.request({"id": 1})
    activity.observe({"id": 1, "result": {"turn": {"id": "parent-turn", "status": "inProgress"}}})
    activity.observe({"method": "item/completed", "params": {"item": {"type": "collabAgentToolCall", "receiverThreadIds": ["worker-thread"], "agentsStates": {}}}})
    activity.observe({"method": "turn/completed", "params": {"threadId": "parent-thread", "turn": {"id": "parent-turn"}}})
    assert activity.busy
    activity.observe({"method": "item/completed", "params": {"item": {"type": "collabAgentToolCall", "receiverThreadIds": ["worker-thread"], "agentsStates": {"worker-thread": {"status": "completed"}}}}})
    assert not activity.busy


def test_late_start_ack_cannot_relock_a_finished_native_turn():
    from smara.native_runtime import NativeProjectActivity
    activity = NativeProjectActivity()
    activity.request({"id": 1})
    activity.observe({"method": "turn/completed", "params": {"threadId": "parent", "turn": {"id": "finished"}}})
    activity.observe({"id": 1, "result": {"turn": {"id": "finished", "status": "inProgress"}}})
    activity.observe({"method": "turn/started", "params": {"turn": {"id": "finished"}}})
    assert not activity.busy


def test_project_lease_is_released_only_after_native_cleanup(tmp_path):
    from smara.native_goal_runtime import execute_cycle
    from smara.native_provider import ChatEndpoint
    store = GoalStore(tmp_path / "goals.sqlite")
    identity = create(store, tmp_path)
    observed = []
    class SessionFixture:
        def __init__(self, *_args):
            pass
        def send(self, _message):
            pass
        def rpc(self, method, _params=None):
            return {"initialize": {}, "thread/start": {"thread": {"id": "synthetic-thread"}},
                "turn/start": {"turn": {"id": "synthetic-turn"}},
                "thread/read": {"thread": {"turns": [{"id": "synthetic-turn", "items": [{"type": "agentMessage", "text": json.dumps(checkpoint(decision="ready_for_review"))}]}]}}}[method]
        def wait_turn(self, _identity):
            return {"status": "completed"}
        def close(self):
            observed.append(bool(store.control(identity)["lease"]))
    result = execute_cycle(store, store.claim(), endpoint=ChatEndpoint("http://127.0.0.1:9/v1", "synthetic", ""), session_factory=SessionFixture)
    assert observed == [True]
    assert result["state"] == "ready_for_review" and result["lease"] is None


def test_model_cannot_bypass_missing_native_test_evidence(tmp_path):
    from smara.native_goal_runtime import execute_cycle
    from smara.native_provider import ChatEndpoint
    store = GoalStore(tmp_path / "goals.sqlite")
    create(store, tmp_path, task_kind="coding")
    class SessionFixture:
        def __init__(self, *_args): pass
        def send(self, _message): pass
        def rpc(self, method, _params=None):
            claimed = checkpoint(decision="ready_for_review", outcome={"passed": True})
            return {"initialize": {}, "thread/start": {"thread": {"id": "synthetic-thread"}},
                "turn/start": {"turn": {"id": "synthetic-turn"}},
                "thread/read": {"thread": {"turns": [{"id": "synthetic-turn", "items": [{"type": "agentMessage", "text": json.dumps(claimed)}]}]}}}[method]
        def wait_turn(self, _identity): return {"status": "completed"}
        def close(self): pass
    result = execute_cycle(store, store.claim(), endpoint=ChatEndpoint("http://127.0.0.1:9/v1", "synthetic", ""), session_factory=SessionFixture)
    assert result["state"] == "queued"
    assert not result["checkpoint"]["outcome"]["passed"]
    assert "Tests" in result["checkpoint"]["next_step"]
