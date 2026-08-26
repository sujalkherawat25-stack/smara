from pathlib import Path

import pytest

from smara.models import TaskCreate
from smara.store import TaskStore


def test_paired_desktop_claims_only_declared_capability(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    pairing = store.create_executor_pairing("acct_1", "Sujal desktop", ["local_file_read"])
    desktop = store.pair_executor(pairing["code"])
    task = store.create("acct_1", "work", "Read local note", "Read a local file", True, [{
        "name": "desktop.read_file", "depends_on": [], "executor_kind": "desktop", "required_capability": "local_file_read", "executor_payload": {"path": "approved.txt"},
    }])

    assert store.claim_one("hosted-worker") is None
    store.decide(task["id"], "acct_1", True, "approved")
    step = store.claim_for_executor(desktop["executor_id"], desktop["token"])
    assert step and step["step_id"]
    assert step["required_capability"] == "local_file_read"
    assert step["executor_payload"] == {"path": "approved.txt"}
    store.complete_executor_step(desktop["executor_id"], desktop["token"], step["step_id"], "Read-only action completed.")
    assert store.get(task["id"], "acct_1")["status"] == "completed"
    artifacts = store.artifacts(task["id"], "acct_1")
    assert len(artifacts) == 1
    assert artifacts[0]["kind"] == "desktop_step_result"
    assert artifacts[0]["content"] == "Read-only action completed."


def test_pairing_is_single_use_and_capability_is_enforced(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    pairing = store.create_executor_pairing("acct_1", "Limited desktop", ["local_file_read"])
    desktop = store.pair_executor(pairing["code"])
    try:
        store.pair_executor(pairing["code"])
        assert False, "pairing code must be single-use"
    except KeyError:
        pass
    task = store.create("acct_1", "work", "Write file", "Write local file", True, [{
        "name": "desktop.write_file", "depends_on": [], "executor_kind": "desktop", "required_capability": "local_file_write",
    }])
    assert store.claim_for_executor(desktop["executor_id"], desktop["token"]) is None
    store.decide(task["id"], "acct_1", True, "approved")
    assert store.claim_for_executor(desktop["executor_id"], desktop["token"]) is None


def test_desktop_failure_returns_the_step_to_its_retry_contract(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    desktop = store.pair_executor(store.create_executor_pairing("acct_1", "Desktop", ["local_file_read"])["code"])
    task = store.create("acct_1", "work", "Read", "Read", True, [{"name": "read", "executor_kind": "desktop", "required_capability": "local_file_read"}])
    store.decide(task["id"], "acct_1", True, "approved")
    step = store.claim_for_executor(desktop["executor_id"], desktop["token"])
    assert step
    assert store.fail_executor_step(desktop["executor_id"], desktop["token"], step["step_id"], "outside approved root") == "retrying"


def test_desktop_expired_lease_is_recovered_by_next_executor_poll(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    first = store.pair_executor(store.create_executor_pairing("acct_1", "Desktop A", ["local_file_read"])["code"])
    second = store.pair_executor(store.create_executor_pairing("acct_1", "Desktop B", ["local_file_read"])["code"])
    task = store.create("acct_1", "work", "Read", "Read", True, [{"name": "read", "executor_kind": "desktop", "required_capability": "local_file_read"}])
    store.decide(task["id"], "acct_1", True, "approved")
    claimed = store.claim_for_executor(first["executor_id"], first["token"], lease_seconds=1)
    assert claimed
    with store._connect() as connection:
        connection.execute("UPDATE task_steps SET lease_expires_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", claimed["step_id"]))
    recovered = store.claim_for_executor(second["executor_id"], second["token"])
    assert recovered and recovered["step_id"] == claimed["step_id"]
    assert recovered["lease_owner"] == second["executor_id"]


def test_executor_heartbeat_cannot_expand_pairing_capabilities(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    desktop = store.pair_executor(store.create_executor_pairing("acct_1", "Desktop", ["local_file_read"])["code"])
    with pytest.raises(ValueError, match="cannot add capabilities"):
        store.heartbeat_executor(desktop["executor_id"], desktop["token"], ["local_file_read", "local_terminal"])


def test_desktop_executor_cannot_claim_before_task_approval(tmp_path: Path):
    store = TaskStore(tmp_path / "smara.db")
    desktop = store.pair_executor(store.create_executor_pairing("acct_1", "Desktop", ["local_file_read"])["code"])
    task = store.create("acct_1", "work", "Read", "Read", True, [{"name": "read", "executor_kind": "desktop", "required_capability": "local_file_read"}])
    assert store.claim_for_executor(desktop["executor_id"], desktop["token"]) is None
    store.decide(task["id"], "acct_1", True, "approved")
    assert store.claim_for_executor(desktop["executor_id"], desktop["token"]) is not None
    assert store.get(task["id"], "acct_1")["status"] == "running"


def test_desktop_capability_cannot_be_enabled_by_unapproved_direct_task(tmp_path: Path):
    store = TaskStore(tmp_path / "smara.db")
    desktop = store.pair_executor(store.create_executor_pairing("acct_1", "Desktop", ["local_file_read"])["code"])
    # Callers that bypass API validation still cannot run local work without
    # a durable approval record.
    store.create("acct_1", "work", "Read", "Read", False, [{"name": "read", "executor_kind": "desktop", "required_capability": "local_file_read"}])
    assert store.claim_for_executor(desktop["executor_id"], desktop["token"]) is None


def test_api_task_model_requires_approval_for_local_execution():
    with pytest.raises(ValueError, match="must require approval"):
        TaskCreate(
            title="Read local file",
            objective="Read notes",
            requires_approval=False,
            steps=[{"name": "read", "executor_kind": "desktop", "required_capability": "local_file_read"}],
        )


def test_desktop_revoke_is_account_scoped_and_immediate(tmp_path: Path):
    store = TaskStore(tmp_path / "smara.db")
    desktop = store.pair_executor(store.create_executor_pairing("acct_1", "Desktop", ["local_file_read"])["code"])
    try:
        store.revoke_executor(desktop["executor_id"], "acct_2")
        assert False, "another account must not revoke this desktop"
    except KeyError:
        pass
    store.revoke_executor(desktop["executor_id"], "acct_1")
    try:
        store.executor(desktop["executor_id"], desktop["token"])
        assert False, "revoked desktop credentials must stop working immediately"
    except KeyError:
        pass
