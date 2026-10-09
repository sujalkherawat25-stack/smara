"""Scheduler ledger/budget mechanics, not a mocked native task outcome."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import time
import pytest
from smara.native_schedule import ScheduleStore, RequestBudget, supervisor_lock, tick


def register(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    identity = store.add("synthetic", {"workspace": str(tmp_path), "prompt": "synthetic read-only test", "profile_id": "synthetic", "interval_seconds": 60, "max_requests": 2})
    return store, identity


def test_new_job_is_paused_and_enable_is_explicit(tmp_path):
    store, identity = register(tmp_path)
    assert store.claim() is None
    store.enable(identity, True)
    assert store.claim()["id"] == identity


def test_atomic_claim_prevents_double_dispatch(tmp_path):
    store, identity = register(tmp_path)
    store.enable(identity, True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: store.claim(), range(2)))
    assert sum(claim is not None for claim in claims) == 1


def test_failed_or_unknown_run_pauses_without_retry(tmp_path):
    store, identity = register(tmp_path)
    store.enable(identity, True)
    result = tick(store, runner=lambda _job, _root: ("unknown", {"synthetic_failure": True}))
    assert result["status"] == "unknown"
    assert store.list()[0]["enabled"] == 0
    assert store.claim(time.time() + 1000) is None


def test_crash_recovery_does_not_repeat_a_possibly_accepted_run(tmp_path):
    store, identity = register(tmp_path)
    store.enable(identity, True)
    store.claim()
    with supervisor_lock(store.path):
        store.recover_unknown()
    assert store.list()[0]["last_status"] == "unknown"
    assert store.list()[0]["enabled"] == 0
    assert store.claim() is None


def test_pause_during_run_survives_completion(tmp_path):
    store, identity = register(tmp_path)
    store.enable(identity, True)
    job = store.claim()
    store.enable(identity, False)
    store.finish(job, "completed", {})
    assert store.list()[0]["enabled"] == 0
    with pytest.raises(ValueError, match="Stale"):
        store.finish(job, "completed", {})


def test_second_supervisor_cannot_recover_an_active_supervisor(tmp_path):
    store, _identity = register(tmp_path)
    with supervisor_lock(store.path):
        with pytest.raises(RuntimeError, match="Another"):
            with supervisor_lock(store.path):
                pytest.fail("Second supervisor acquired the lock")


def test_inference_budget_cannot_fabricate_or_repeat_a_response():
    observed = []
    sentinel = object()
    budget = RequestBudget(SimpleNamespace(stream=lambda *args, **kwargs: observed.append(kwargs) or sentinel), 1)
    assert budget.stream("POST", "synthetic", json={"messages": [], "max_tokens": 8192}) is sentinel
    assert observed[0]["json"]["max_tokens"] == 2048
    with pytest.raises(RuntimeError, match="ceiling"):
        budget.stream("POST", "synthetic", json={"messages": []})
    assert len(observed) == 1
