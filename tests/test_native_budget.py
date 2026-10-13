"""Local policy/SQLite regressions, not a live-provider quality score."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace
import threading
import pytest
from smara.native_budget import output_payload, reservation_estimate
from smara.native_admission import ProviderAdmission, AdmissionClient, AdmissionError
from smara.native_goal_runtime import GoalBudget, checkpoint_from
from smara.native_schedule import RequestBudget


def test_modes_keep_provider_allowance_without_hidden_short_caps():
    payload = {"messages": [], "max_tokens": 16384}
    sent = []
    client = SimpleNamespace(stream=lambda **kwargs: sent.append(kwargs["json"]) or kwargs["json"])
    store = SimpleNamespace(reserve=lambda *_: None)
    assert output_payload(payload)["max_tokens"] == 16384
    assert GoalBudget(client, store, {"spec": {}}).stream(json=payload)["max_tokens"] == 16384
    assert RequestBudget(client, 1).stream(json=payload)["max_tokens"] == 16384
    assert payload == {"messages": [], "max_tokens": 16384}
    assert output_payload(payload, 8192)["max_tokens"] == 8192
    assert reservation_estimate(payload) >= 16384


@pytest.mark.parametrize("value", [True, 0, -1, "4096", 1000001])
def test_bad_output_allowance_rejected(value):
    with pytest.raises(ValueError): output_payload({"max_tokens": value})


def test_schedule_reserves_output_and_rejects_before_transport():
    sent = []
    client = SimpleNamespace(stream=lambda **kw: sent.append(kw))
    budget = RequestBudget(client, 3, token_ceiling=4096)
    with pytest.raises(RuntimeError, match="ceiling"):
        budget.stream(json={"messages": [], "max_tokens": 16384})
    assert not sent and budget.requests == 0


def test_checkpoint_prose_fences_are_recovered_without_inference(tmp_path):
    value = dict(decision="continue", summary="Observed synthetic state", next_step="Next step",
                 plan=[], completed_steps=[], evidence=[], outputs=[], claims=[])
    text = json.dumps(value)
    for surrounding in (text, "```json\n" + text + "\n```", "Progress:\n" + text + "\nEnd."):
        assert checkpoint_from(surrounding, tmp_path)["summary"] == value["summary"]
    for invalid in (text + text, text[:-1], '{"broken":' + text):
        with pytest.raises(ValueError): checkpoint_from(invalid, tmp_path)


def test_two_brokers_share_atomic_capacity_and_expiry(tmp_path):
    clock = [1000.]
    options = dict(concurrent=1, per_minute=2, wait_seconds=0, lease_seconds=10, clock=lambda: clock[0])
    first = ProviderAdmission(tmp_path / "admission.sqlite", "hashed-provider", **options)
    second = ProviderAdmission(first.path, first.identity, **options)
    lease = first.acquire()
    with pytest.raises(AdmissionError): second.acquire()
    first.release(lease)
    second.release(second.acquire())
    with pytest.raises(AdmissionError): first.acquire()
    clock[0] += 61
    abandoned = first.acquire()
    clock[0] += 11
    second.release(second.acquire())
    first.release(abandoned)


def test_atomic_parallel_admission_has_no_extra_slot(tmp_path):
    admission = ProviderAdmission(tmp_path / "admission.sqlite", "synthetic", concurrent=2, wait_seconds=0)
    def acquire(_):
        try: return admission.acquire()
        except AdmissionError: return None
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(acquire, range(6)))
    assert len([r for r in results if r]) == 2
    for lease in filter(None, results): admission.release(lease)


def test_failed_stream_releases_slot_and_cancelled_adapter_sends_nothing(tmp_path):
    calls = []
    @contextmanager
    def fail(**kwargs):
        calls.append(kwargs)
        raise OSError("Synthetic transport failure")
        yield
    admission = ProviderAdmission(tmp_path / "a.sqlite", "synthetic", concurrent=1, wait_seconds=0)
    client = AdmissionClient(SimpleNamespace(stream=fail, close=lambda: None), admission)
    with pytest.raises(OSError):
        with client.stream(json={}): pass
    admission.release(admission.acquire())
    client.close()
    with pytest.raises(AdmissionError):
        with client.stream(json={}): pass
    assert len(calls) == 1


def test_disconnected_native_peer_never_buys_a_queued_request(tmp_path):
    from smara.native_admission import peer_context
    import socket
    calls = []
    @contextmanager
    def stream(**kwargs):
        calls.append(kwargs); yield object()
    left, right = socket.socketpair()
    client = AdmissionClient(SimpleNamespace(stream=stream, close=lambda: None), ProviderAdmission(tmp_path / "a.sqlite", "synthetic", wait_seconds=0))
    try:
        right.close()
        with peer_context(left), pytest.raises(AdmissionError):
            with client.stream(json={}): pass
        assert not calls
    finally:
        left.close(); client.close()


def test_unexpected_worker_fault_is_isolated_and_supervisor_keeps_running(tmp_path, monkeypatch):
    from smara import native_goal_runtime, native_telegram
    from smara.native_goals import GoalStore
    import time
    store = GoalStore(tmp_path / "goals.sqlite")
    other = tmp_path / "other"; other.mkdir()
    def create(root):
        return store.action(dict(action="create", workspace=str(root), objective="Synthetic goal",
                                 success_criteria="Synthetic result", profile_id="synthetic", interval_seconds=0))
    broken, healthy = create(tmp_path), create(other)
    release = threading.Event()
    monkeypatch.setattr(native_telegram, "run_connector", lambda _store, stop: stop.wait())
    def cycle(ledger, goal):
        if goal["id"] == broken:
            raise OSError("Synthetic private error is never exposed")
        release.wait(5)
        ledger.finish(goal, None, "paused", "Synthetic shutdown")
    monkeypatch.setattr(native_goal_runtime, "execute_cycle", cycle)
    worker = threading.Thread(target=native_goal_runtime.run, args=(store,))
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while store.get(broken)["state"] != "blocked" and time.monotonic() < deadline:
            time.sleep(.02)
        assert store.get(broken)["state"] == "blocked"
        assert store.get(broken)["lease"] is None
        assert "private" not in store.get(broken)["detail"]
        assert worker.is_alive() and store.get(healthy)["lease"] is not None
    finally:
        with store.connect() as db: store.set_meta(db, "service_stop", True)
        release.set(); worker.join(10)
    assert not worker.is_alive()


def test_interruption_keeps_observed_results_without_inventing_completion(tmp_path):
    from smara.native_goal_runtime import partial_checkpoint
    (tmp_path / "calculator.py").write_text("def add(a, b): return a + b\n")
    items = {"edit": {"id": "edit", "type": "fileChange", "status": "completed", "changes": [{"path": str(tmp_path / "calculator.py")}]},
             "test": {"id": "test", "type": "commandExecution", "status": "completed", "command": "python -m pytest", "exitCode": 0}}
    goal = {"spec": {"workspace": str(tmp_path), "task_kind": "coding"}, "checkpoint": {}}
    partial = partial_checkpoint(SimpleNamespace(observed_items=items), goal, 5)
    assert partial["partial"] and partial["decision"] == "continue"
    assert partial["outputs"] == ["calculator.py"]
    assert all(check["passed"] for check in partial["outcome"]["checks"])
    assert not partial["outcome"]["passed"] and "not verified" in partial["summary"]


def test_targeted_claim_cannot_take_another_due_goal(tmp_path):
    from smara.native_goals import GoalStore
    store = GoalStore(tmp_path / "goals.sqlite")
    spec = dict(action="create", workspace=str(tmp_path), objective="Synthetic first goal", success_criteria="Evidence", profile_id="synthetic", interval_seconds=0)
    first = store.action(spec)
    second = store.action({**spec, "objective": "Synthetic second goal"})
    assert store.claim(second)["id"] == second
    assert store.get(first)["state"] == "queued"
    assert store.claim(first) is None  # Same-project ownership remains enforced.
