import json
from pathlib import Path

import pytest

from smara.local_agent import (
    LocalTaskJournal,
    decorate_local_result,
    journal_path,
    local_skill_catalog,
    validate_local_step,
    workspace_lock,
)
from smara.store import TaskStore


def test_local_skill_catalog_is_explicit_and_bounded():
    catalogue = local_skill_catalog()
    assert {item["capability"] for item in catalogue} == {
        "local_file_read", "local_file_write", "local_terminal", "local_browser", "local_integration",
    }
    assert all(item["input_schema"]["additionalProperties"] is False for item in catalogue)
    assert all(item["idempotency_required"] is True for item in catalogue)


def test_local_step_protocol_adds_idempotency_metadata():
    spec, key = validate_local_step({
        "required_capability": "local_file_write",
        "idempotency_key": "task:1:step:1",
        "executor_payload": {"path": "note.txt", "content": "hello"},
    })
    assert spec.capability == "local_file_write"
    result = json.loads(decorate_local_result('{"action":"local_file_write"}', spec, key))
    assert result["skill"] == "local_file_write"
    assert result["idempotency_key"] == "task:1:step:1"
    assert result["failure_state"] == "completed"


def test_workspace_lock_fails_closed_for_concurrent_mutation(tmp_path: Path):
    state_path = tmp_path / "desktop.json"
    with workspace_lock(tmp_path, state_path=state_path):
        with pytest.raises(RuntimeError, match="Another local task"):
            with workspace_lock(tmp_path, state_path=state_path):
                pass


def test_journal_reconcile_and_bounded_summary(tmp_path: Path):
    journal = LocalTaskJournal(journal_path(tmp_path / "desktop.json"), max_entries=10)
    journal.record("step-1", "claimed", task_id="task-1", capability="local_file_read", idempotency_key="k1")
    journal.record("step-1", "uncertain", task_id="task-1", capability="local_file_read", idempotency_key="k1")
    assert journal.uncertain("step-1", "k1")
    resolved = journal.reconcile("step-1", "completed")
    assert resolved and resolved["status"] == "completed"
    assert not journal.uncertain("step-1", "k1")
    assert journal.summary()["uncertain"] == []


def test_failed_task_retry_returns_local_work_to_approval(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    task = store.create("acct_1", "work", "Local retry", "retry this local operation", True, [{
        "name": "write", "executor_kind": "desktop", "required_capability": "local_file_write",
    }])
    pending = store.claim_one("hosted-worker")
    assert pending and pending["status"] == "waiting_approval"
    store.decide(task["id"], "acct_1", True, "approved")
    step = store.claim_one("desktop-worker", executor_kinds=("hosted", "sandbox"))
    # The hosted worker must not claim the desktop step; fail it directly to
    # model a terminal, non-retryable local error.
    assert step is None
    local = store.claim_for_executor
    desktop = store.pair_executor(store.create_executor_pairing("acct_1", "Desktop", ["local_file_write"])["code"])
    claimed = local(desktop["executor_id"], desktop["token"])
    assert claimed
    assert store.fail_step(claimed["step_id"], "acct_1", "failed", retryable=False) == "failed"
    retried = store.retry_task(task["id"], "acct_1")
    assert retried["status"] == "waiting_approval"
    assert retried["requires_approval"] in (True, 1)
    assert any(event["type"] == "task.retry_requested" for event in store.events(task["id"], "acct_1"))
