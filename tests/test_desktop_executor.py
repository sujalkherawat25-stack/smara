from pathlib import Path
import json
from datetime import datetime, timezone

import pytest

from smara.models import TaskCreate
from smara.store import TaskStore
from smara.desktop_executor import _changed_file_hashes, _read_file, normalize_pairing_code


def test_pairing_code_normalizes_copied_whitespace():
    assert normalize_pairing_code(" 9aea 8e4f\r\n") == "9AEA8E4F"
    with pytest.raises(RuntimeError, match="8 hexadecimal"):
        normalize_pairing_code("9AEA8E4")


def test_local_file_read_returns_type_and_encoding_without_content(tmp_path: Path):
    path = tmp_path / "notes.md"
    path.write_text("hello Smara\n", encoding="utf-8")
    result = json.loads(_read_file({"path": str(path)}, [tmp_path]))
    assert result["kind"] == "text"
    assert result["encoding"] == "utf-8"
    assert result["media_type"] == "text/markdown"
    assert result["content_shared"] is False
    assert "content" not in result


def test_changed_file_hashes_are_bounded_and_do_not_return_contents(tmp_path: Path):
    path = tmp_path / "changed.txt"
    path.write_text("changed", encoding="utf-8")
    result = _changed_file_hashes(tmp_path, {"changed.txt": " M"}, [tmp_path])
    assert result["changed.txt"]["available"] is True
    assert result["changed.txt"]["bytes"] == 7
    assert result["changed.txt"]["sha256"]
    assert "content" not in result["changed.txt"]


def test_paired_desktop_claims_only_declared_capability(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    pairing = store.create_executor_pairing("acct_1", "Sujal desktop", ["local_file_read"])
    desktop = store.pair_executor(pairing["code"])
    task = store.create("acct_1", "work", "Read local note", "Read a local file", True, [{
        "name": "desktop.read_file", "depends_on": [], "executor_kind": "desktop", "required_capability": "local_file_read", "executor_payload": {"path": "approved.txt"},
    }])

    pending = store.claim_one("hosted-worker")
    assert pending and pending["status"] == "waiting_approval"
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


@pytest.mark.parametrize("capability", ["local_file_write", "local_terminal", "local_browser"])
def test_uncertain_side_effecting_desktop_lease_is_never_replayed(tmp_path: Path, capability: str):
    store = TaskStore(str(tmp_path / "smara.db"))
    first = store.pair_executor(store.create_executor_pairing("acct_1", "Desktop A", [capability])["code"])
    second = store.pair_executor(store.create_executor_pairing("acct_1", "Desktop B", [capability])["code"])
    task = store.create("acct_1", "work", "Risky local action", "Run once", True, [{
        "name": "desktop.action", "executor_kind": "desktop", "required_capability": capability,
    }])
    store.decide(task["id"], "acct_1", True, "approved")
    claimed = store.claim_for_executor(first["executor_id"], first["token"], lease_seconds=1)
    assert claimed
    with store._connect() as connection:
        connection.execute(
            "UPDATE task_steps SET lease_expires_at=? WHERE id=?",
            ("2000-01-01T00:00:00+00:00", claimed["step_id"]),
        )

    # Polling from another executor performs lease recovery, but an uncertain
    # write/terminal/browser action is failed closed instead of run twice.
    assert store.claim_for_executor(second["executor_id"], second["token"]) is None
    failed = store.get(task["id"], "acct_1")
    assert failed["status"] == "failed"
    dead_letters = store.dead_letters("acct_1")
    assert len(dead_letters) == 1
    assert dead_letters[0]["step_id"] == claimed["step_id"]
    assert "automatic replay was blocked" in dead_letters[0]["error"]
    assert any(event["type"] == "executor.lease_expired_uncertain" for event in store.events(task["id"], "acct_1"))


def test_desktop_step_heartbeat_refreshes_only_current_lease(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    desktop = store.pair_executor(store.create_executor_pairing("acct_1", "Desktop", ["local_file_read"])["code"])
    task = store.create("acct_1", "work", "Read", "Read", True, [{
        "name": "read", "executor_kind": "desktop", "required_capability": "local_file_read",
    }])
    store.decide(task["id"], "acct_1", True, "approved")
    step = store.claim_for_executor(desktop["executor_id"], desktop["token"], lease_seconds=1)
    assert step
    before = datetime.fromisoformat(step["lease_expires_at"])
    refreshed = store.heartbeat_executor_step(desktop["executor_id"], desktop["token"], step["step_id"], lease_seconds=180)
    after = datetime.fromisoformat(refreshed["lease_expires_at"])
    assert refreshed["ok"] is True and refreshed["cancel_requested"] is False
    assert after > before and after > datetime.now(timezone.utc)
    with store._connect() as connection:
        lease = connection.execute("SELECT expires_at FROM executor_leases WHERE step_id=?", (step["step_id"],)).fetchone()
    assert lease and lease["expires_at"] == refreshed["lease_expires_at"]


def test_desktop_step_heartbeat_does_not_extend_cancelled_work(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    desktop = store.pair_executor(store.create_executor_pairing("acct_1", "Desktop", ["local_file_read"])["code"])
    task = store.create("acct_1", "work", "Read", "Read", True, [{
        "name": "read", "executor_kind": "desktop", "required_capability": "local_file_read",
    }])
    store.decide(task["id"], "acct_1", True, "approved")
    step = store.claim_for_executor(desktop["executor_id"], desktop["token"])
    assert step
    store.cancel(task["id"], "acct_1")
    result = store.heartbeat_executor_step(desktop["executor_id"], desktop["token"], step["step_id"])
    assert result == {"ok": False, "cancel_requested": True, "step_id": step["step_id"]}


def test_cancelled_desktop_step_is_terminally_cancelled_not_failed(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    desktop = store.pair_executor(store.create_executor_pairing("acct_1", "Desktop", ["local_terminal"])["code"])
    task = store.create("acct_1", "work", "Terminal", "Run a local command", True, [{
        "name": "terminal", "executor_kind": "desktop", "required_capability": "local_terminal",
    }])
    store.decide(task["id"], "acct_1", True, "approved")
    step = store.claim_for_executor(desktop["executor_id"], desktop["token"])
    assert step
    store.cancel(task["id"], "acct_1")
    assert store.fail_executor_step(desktop["executor_id"], desktop["token"], step["step_id"], "cancelled locally") == "cancelled"
    assert store.get(task["id"], "acct_1")["status"] == "cancelled"
    assert not store.dead_letters("acct_1")


def test_executor_progress_is_lease_scoped_and_sanitised(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    desktop = store.pair_executor(store.create_executor_pairing("acct_1", "Desktop", ["local_file_read"])["code"])
    task = store.create("acct_1", "work", "Read", "Read", True, [{
        "name": "read", "executor_kind": "desktop", "required_capability": "local_file_read",
    }])
    store.decide(task["id"], "acct_1", True, "approved")
    step = store.claim_for_executor(desktop["executor_id"], desktop["token"])
    assert step
    store.append_executor_progress(desktop["executor_id"], desktop["token"], step["step_id"], "  Read started\nlocally  ")
    events = store.events(task["id"], "acct_1")
    progress = next(event for event in events if event["type"] == "executor.progress")
    assert "Read started locally" in progress["payload"]


def test_stale_desktop_executor_cannot_refresh_recovered_lease(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    first = store.pair_executor(store.create_executor_pairing("acct_1", "Desktop A", ["local_file_read"])["code"])
    second = store.pair_executor(store.create_executor_pairing("acct_1", "Desktop B", ["local_file_read"])["code"])
    task = store.create("acct_1", "work", "Read", "Read", True, [{
        "name": "read", "executor_kind": "desktop", "required_capability": "local_file_read",
    }])
    store.decide(task["id"], "acct_1", True, "approved")
    claimed = store.claim_for_executor(first["executor_id"], first["token"], lease_seconds=1)
    assert claimed
    with store._connect() as connection:
        connection.execute("UPDATE task_steps SET lease_expires_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", claimed["step_id"]))
    recovered = store.claim_for_executor(second["executor_id"], second["token"])
    assert recovered and recovered["lease_owner"] == second["executor_id"]
    with pytest.raises(KeyError, match="lease"):
        store.heartbeat_executor_step(first["executor_id"], first["token"], claimed["step_id"])


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


def test_desktop_self_revoke_requires_its_own_token(tmp_path: Path):
    store = TaskStore(tmp_path / "smara.db")
    desktop = store.pair_executor(store.create_executor_pairing("acct_1", "Desktop", ["local_file_read"])["code"])
    with pytest.raises(KeyError):
        store.revoke_executor_with_token(desktop["executor_id"], "wrong-token")
    store.revoke_executor_with_token(desktop["executor_id"], desktop["token"])
    with pytest.raises(KeyError):
        store.executor(desktop["executor_id"], desktop["token"])
