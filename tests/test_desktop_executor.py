from pathlib import Path

from smara.store import TaskStore


def test_paired_desktop_claims_only_declared_capability(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    pairing = store.create_executor_pairing("acct_1", "Sujal desktop", ["local_file_read"])
    desktop = store.pair_executor(pairing["code"])
    task = store.create("acct_1", "work", "Read local note", "Read a local file", False, [{
        "name": "desktop.read_file", "depends_on": [], "executor_kind": "desktop", "required_capability": "local_file_read", "executor_payload": {"path": "approved.txt"},
    }])

    assert store.claim_one("hosted-worker") is None
    step = store.claim_for_executor(desktop["executor_id"], desktop["token"])
    assert step and step["step_id"]
    assert step["required_capability"] == "local_file_read"
    assert step["executor_payload"] == {"path": "approved.txt"}
    store.complete_executor_step(desktop["executor_id"], desktop["token"], step["step_id"], "Read-only action completed.")
    assert store.get(task["id"], "acct_1")["status"] == "completed"


def test_pairing_is_single_use_and_capability_is_enforced(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    pairing = store.create_executor_pairing("acct_1", "Limited desktop", ["local_file_read"])
    desktop = store.pair_executor(pairing["code"])
    try:
        store.pair_executor(pairing["code"])
        assert False, "pairing code must be single-use"
    except KeyError:
        pass
    store.create("acct_1", "work", "Write file", "Write local file", False, [{
        "name": "desktop.write_file", "depends_on": [], "executor_kind": "desktop", "required_capability": "local_file_write",
    }])
    assert store.claim_for_executor(desktop["executor_id"], desktop["token"]) is None


def test_desktop_failure_returns_the_step_to_its_retry_contract(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    desktop = store.pair_executor(store.create_executor_pairing("acct_1", "Desktop", ["local_file_read"])["code"])
    task = store.create("acct_1", "work", "Read", "Read", False, [{"name": "read", "executor_kind": "desktop", "required_capability": "local_file_read"}])
    step = store.claim_for_executor(desktop["executor_id"], desktop["token"])
    assert step
    assert store.fail_executor_step(desktop["executor_id"], desktop["token"], step["step_id"], "outside approved root") == "retrying"
    assert store.get(task["id"], "acct_1")["status"] == "queued"
