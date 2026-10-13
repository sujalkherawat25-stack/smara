"""Ledger invariants; these are not real-model outcome evaluations."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import pytest

from smara.native_goals import GoalStore, strip_windows_verbatim_prefix
from smara.native_goal_runtime import checkpoint_from


def test_final_budget_request_only_serializes_owning_goal_thread(tmp_path):
    from smara.native_goal_runtime import checkpoint_needed
    store = GoalStore(tmp_path / "goals.sqlite")
    identity = create(store, tmp_path, max_requests=3)
    goal = store.claim()
    store.reserve(goal, 10)
    assert not checkpoint_needed(store, identity, "root", "root")
    store.reserve(goal, 10)
    assert not checkpoint_needed(store, identity, "root", "worker")
    assert not checkpoint_needed(store, identity, "root", None)
    assert checkpoint_needed(store, identity, "root", "root")
    assert store.get(identity)["requests"] == 2


def test_rate_limit_reason_is_typed_and_does_not_leak_native_error_text():
    from smara.native_goal_runtime import turn_failure, _failure_detail, _failure_record
    error = turn_failure({"error": {"message": "synthetic-private-diagnostic", "codexErrorInfo": {"responseTooManyFailedAttempts": {"httpStatusCode": 429}}}})
    detail = _failure_detail(_failure_record(error, "wait_for_model_turn"))
    assert "HTTP 429" in detail and "synthetic-private" not in detail


def test_checkpoint_ignores_bounded_coverage_label_but_still_rejects_invalid_links(tmp_path):
    value = checkpoint(claims=[], coverage=[{"requirement_id": "tests", "description": "Synthetic label", "claim_indexes": [], "outputs": [], "test_commands": ["python -m pytest"]}])
    parsed = checkpoint_from(json.dumps(value), tmp_path, "coding")
    assert "description" not in parsed["coverage"][0]
    value["coverage"][0]["claim_indexes"] = [999]
    with pytest.raises(ValueError, match="coverage links"): checkpoint_from(json.dumps(value), tmp_path, "coding")
    value["coverage"][0].update(claim_indexes=[], allow=True)
    with pytest.raises(ValueError, match="coverage links"): checkpoint_from(json.dumps(value), tmp_path, "coding")


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
    assert following["spec"]["cycle_seconds"] == 900


def test_supervisor_runs_two_independent_projects_concurrently_and_stops_cleanly(tmp_path, monkeypatch):
    from contextlib import nullcontext
    import threading
    from smara import native_goal_runtime, native_schedule, native_telegram

    store = GoalStore(tmp_path / "goals.sqlite")
    create(store, tmp_path, objective="First independent task")
    other = tmp_path / "other-project"
    other.mkdir()
    create(store, other, objective="Second independent task")
    both_started = threading.Event()
    release = threading.Event()
    state_lock = threading.Lock()
    started = []

    monkeypatch.setattr(native_schedule, "supervisor_lock", lambda _path: nullcontext())
    monkeypatch.setattr(native_telegram, "run_connector", lambda _store, stop: stop.wait())

    def cycle(_store, goal):
        with state_lock:
            started.append(goal["id"])
            if len(started) == 2:
                both_started.set()
        release.wait(5)
        _store.finish(goal, None, "paused", "Synthetic supervisor shutdown")

    monkeypatch.setattr(native_goal_runtime, "execute_cycle", cycle)
    worker = threading.Thread(target=native_goal_runtime.run, args=(store,))
    worker.start()
    try:
        assert both_started.wait(5), "independent project cycles did not overlap"
    finally:
        with store.connect() as db:
            store.set_meta(db, "service_stop", True)
        release.set()
        worker.join(10)
    assert not worker.is_alive(), "supervisor did not stop and release its workers"
    assert len(started) == 2
    assert all(store.get(identity)["lease"] is None for identity in started)


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
    assert checkpoint_from(json.dumps(coding), tmp_path, "mixed")["claims"][0]["url"] == ""
    with pytest.raises(ValueError, match="citation claims"):
        checkpoint_from(json.dumps(coding), tmp_path, "research")


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


def test_windows_verbatim_project_path_shares_one_interactive_key(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows canonical path spelling is required")
    verbatim = Path("\\\\?\\" + str(tmp_path.resolve()))
    store = GoalStore(tmp_path / "goals.sqlite")
    identity = create(store, verbatim)
    assert len(store.list(tmp_path)) == 1
    assert len(store.list(verbatim)) == 1
    store.interactive(verbatim, "rust-desktop", True)
    assert store.claim() is None
    with pytest.raises(ValueError, match="Another"):
        store.interactive(tmp_path, "python-desktop", True)
    store.interactive(verbatim, "rust-desktop", False)
    assert store.claim()


def test_windows_verbatim_path_conversion_preserves_unc_roots():
    assert strip_windows_verbatim_prefix("\\\\?\\C:\\repo") == "C:\\repo"
    assert strip_windows_verbatim_prefix("\\\\?\\UNC\\server\\share\\repo") == "\\\\server\\share\\repo"


def test_workspace_key_is_backfilled_when_opening_an_existing_goal_ledger(tmp_path):
    import sqlite3
    database = tmp_path / "legacy.sqlite"
    spec = {"workspace": str(tmp_path), "objective": "Legacy task", "success_criteria": "Evidence"}
    with sqlite3.connect(database) as db:
        db.execute("""CREATE TABLE goals (
            id TEXT PRIMARY KEY, spec TEXT NOT NULL, state TEXT NOT NULL,
            session_id TEXT, turn_id TEXT, lease TEXT, due REAL NOT NULL,
            created REAL NOT NULL, updated REAL NOT NULL, cycles INTEGER NOT NULL DEFAULT 0,
            requests INTEGER NOT NULL DEFAULT 0, token_estimate INTEGER NOT NULL DEFAULT 0,
            stalled INTEGER NOT NULL DEFAULT 0, checkpoint TEXT NOT NULL DEFAULT '{}', detail TEXT NOT NULL DEFAULT '')""")
        db.execute("INSERT INTO goals(id,spec,state,due,created,updated) VALUES(?,?,'paused',0,0,0)",
            ("legacygoal", json.dumps(spec)))
    store = GoalStore(database)
    assert [goal["id"] for goal in store.list(tmp_path)] == ["legacygoal"]


def test_live_goal_cannot_be_deleted_under_its_worker(tmp_path):
    store = GoalStore(tmp_path / "goals.sqlite")
    identity = create(store, tmp_path)
    claimed = store.claim()
    with pytest.raises(ValueError, match="owns a worker lease"):
        store.action({"action": "delete", "id": identity})
    assert store.get(identity)["lease"] == claimed["lease"]


def test_goal_claims_run_in_parallel_only_for_distinct_projects(tmp_path):
    store = GoalStore(tmp_path / "goals.sqlite")
    first = create(store, tmp_path, objective="First project task")
    second = create(store, tmp_path, objective="Second task in same project")
    other_workspace = tmp_path / "other-project"
    other_workspace.mkdir()
    other = create(store, other_workspace, objective="Independent project task")
    claimed_first = store.claim()
    claimed_other = store.claim()
    assert claimed_first["id"] == first
    assert claimed_other["id"] == other
    assert store.claim() is None
    store.finish(claimed_first, checkpoint(), "queued", "Continue")
    assert store.claim()["id"] == second


def test_goal_user_input_is_validated_delivered_once_and_scrubbed(tmp_path):
    store = GoalStore(tmp_path / "goals.sqlite")
    identity = create(store, tmp_path)
    goal = store.claim()
    request = store.pending(goal, {"id": "native-question", "method": "item/tool/requestUserInput",
        "params": {"questions": [{"id": "api-key", "question": "Synthetic secret"}]}})
    with pytest.raises(ValueError, match="Answer every requested question"):
        store.respond(identity, request, answers={})
    store.respond(identity, request, answers={"api-key": {"answers": ["synthetic-private-value"]}})
    delivered = store.consume_response(goal, request)
    assert delivered == {"id": "native-question", "result": {"answers": {"api-key": {"answers": ["synthetic-private-value"]}}}}
    assert store.consume_response(goal, request) is None
    with store.connect() as db:
        saved = db.execute("SELECT response,state FROM goal_requests WHERE id=?", (request,)).fetchone()
    assert saved["response"] is None and saved["state"] == "delivered"


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


def test_cycle_failure_records_safe_stage_without_persisting_missing_path(tmp_path):
    from smara.native_goal_runtime import execute_cycle
    from smara.native_provider import ChatEndpoint
    store = GoalStore(tmp_path / "goals.sqlite")
    identity = create(store, tmp_path)

    class MissingSessionFile:
        def __init__(self, *_args):
            error = FileNotFoundError(2, "missing", r"C:\Users\private\secrets\provider-key.json")
            error.smara_failure_context = "required_file_unavailable"
            raise error

    result = execute_cycle(store, store.claim(), endpoint=ChatEndpoint("http://127.0.0.1:9/v1", "synthetic", ""),
        session_factory=MissingSessionFile)
    event = result["events"][-1]
    assert result["id"] == identity and result["state"] == "blocked" and result["lease"] is None
    assert "starting the native session" in result["detail"]
    assert "provider-key.json" not in json.dumps(event)
    assert event["body"]["failure"] == {
        "stage": "start_native_session", "error_type": "FileNotFoundError", "context": "required_file_unavailable"
    }
    with store.connect() as db:
        stored = db.execute("SELECT body FROM goal_events WHERE goal_id=? ORDER BY seq DESC LIMIT 1", (identity,)).fetchone()[0]
    assert "provider-key.json" not in stored and "C:\\Users\\private" not in stored


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


def test_interrupted_resume_retains_prior_test_evidence_without_certifying_completion(tmp_path):
    from types import SimpleNamespace
    from smara.native_goal_runtime import partial_checkpoint
    old_test = {"id": "test", "type": "commandExecution", "command": "python -m pytest",
                "status": "completed", "exitCode": 0}
    old_edit = {"id": "edit", "type": "fileChange", "status": "completed", "changes": []}
    session = SimpleNamespace(observed_prior_transcript={"turns": [{"id": "old", "items": [old_edit, old_test]}]},
        observed_items={"duplicate": old_edit, "new": {"id": "new", "type": "agentMessage", "text": "Still working"}})
    goal = {"spec": {"workspace": str(tmp_path), "task_kind": "coding"}, "checkpoint": {}}
    result = partial_checkpoint(session, goal, 1)
    assert result["partial"] and result["decision"] == "continue"
    assert result["outcome"]["tests"][0]["passed"]
    assert not result["outcome"]["passed"]


def test_new_edit_invalidates_prior_tests_in_partial_resume(tmp_path):
    from types import SimpleNamespace
    from smara.native_goal_runtime import partial_checkpoint
    session = SimpleNamespace(observed_prior_transcript={"turns": [{"id": "old", "items": [
        {"id": "test", "type": "commandExecution", "command": "python -m pytest", "status": "completed", "exitCode": 0}]}]},
        observed_items={"new": {"id": "new-edit", "type": "fileChange", "status": "completed", "changes": []}})
    result = partial_checkpoint(session, {"spec": {"workspace": str(tmp_path), "task_kind": "coding"}, "checkpoint": {}}, 1)
    assert not result["outcome"]["tests"] and not result["outcome"]["passed"]
