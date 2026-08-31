from pathlib import Path
import json
from datetime import datetime, timezone
import httpx

import pytest

from smara.models import TaskCreate
from smara.store import TaskStore
from smara.desktop_executor import DesktopRunner, _changed_file_hashes, _read_file, _workspace_inspect, _write_file, execute_step, journal_path, normalize_pairing_code
from smara.local_agent import LocalTaskJournal
from smara.workspace_contract import build_workspace_job


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


def test_workspace_snapshot_returns_bounded_metadata_and_proof(tmp_path: Path):
    (tmp_path / "notes.md").write_text("hello", encoding="utf-8")
    result = json.loads(_workspace_inspect({"operation": "workspace_snapshot", "path": str(tmp_path), "_state": {}}, [tmp_path]))
    assert result["operation"] == "workspace_snapshot"
    assert result["file_count"] == 1
    assert result["snapshot_sha256"] and len(result["snapshot_sha256"]) == 64
    assert result["entries"][0]["path"] == "notes.md"
    assert '"content"' not in json.dumps(result)


def test_prepare_workspace_copy_is_idempotent_and_excludes_git_metadata(tmp_path: Path):
    source = tmp_path / "repo"
    source.mkdir()
    (source / "app.py").write_text("print('ok')", encoding="utf-8")
    (source / ".git").mkdir()
    job = build_workspace_job(
        workspace_root="repo", objective="Prepare an isolated copy", capabilities=["local_file_write"],
        idempotency_key="workspace:copy:0001",
    )
    payload = {"operation": "prepare_workspace", "path": str(source), "workspace_job": job}
    state = {"allowed_roots": [str(tmp_path)], "capabilities": ["local_file_write"]}
    first = json.loads(_write_file(payload, [tmp_path], state))
    second = json.loads(_write_file(payload, [tmp_path], state))
    assert first["workspace_path"] == second["workspace_path"]
    destination = tmp_path / first["workspace_path"]
    assert (destination / "app.py").exists()
    assert not (destination / ".git").exists()


def test_changed_file_hashes_are_bounded_and_do_not_return_contents(tmp_path: Path):
    path = tmp_path / "changed.txt"
    path.write_text("changed", encoding="utf-8")
    result = _changed_file_hashes(tmp_path, {"changed.txt": " M"}, [tmp_path])
    assert result["changed.txt"]["available"] is True
    assert result["changed.txt"]["bytes"] == 7
    assert result["changed.txt"]["sha256"]
    assert "content" not in result["changed.txt"]


def test_executor_step_status_is_account_scoped_and_terminal(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    desktop = store.pair_executor(store.create_executor_pairing("acct_1", "Desktop", ["local_file_read"])["code"])
    task = store.create("acct_1", "work", "Read", "Read", True, [{"name": "read", "executor_kind": "desktop", "required_capability": "local_file_read"}])
    store.decide(task["id"], "acct_1", True, "approved")
    step = store.claim_for_executor(desktop["executor_id"], desktop["token"])
    assert step
    status = store.executor_step_status(desktop["executor_id"], desktop["token"], step["step_id"])
    assert status["status"] == "running" and status["terminal"] is False
    with pytest.raises(KeyError):
        store.executor_step_status(desktop["executor_id"], desktop["token"], "missing-step")
    store.complete_executor_step(desktop["executor_id"], desktop["token"], step["step_id"], "done")
    final = store.executor_step_status(desktop["executor_id"], desktop["token"], step["step_id"])
    assert final["status"] == "completed" and final["terminal"] is True


def test_desktop_reconciles_uncertain_journal_from_hosted_status(tmp_path: Path):
    state = {"smara_url": "https://smara.test", "token": "token", "executor_id": "desktop-1"}
    journal = LocalTaskJournal(journal_path(tmp_path / "desktop.json"))
    journal.record("step-1", "uncertain", task_id="task-1", capability="local_file_write", idempotency_key="k1")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/executors/steps/step-1"
        return httpx.Response(200, json={"step_id": "step-1", "status": "completed", "terminal": True})

    runner = DesktopRunner(tmp_path / "desktop.json")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        runner._reconcile_journal(client, state, journal)
    assert journal.get("step-1")["status"] == "completed"


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


def test_desktop_safe_read_policy_auto_approves_only_declared_read_work(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    pairing = store.create_executor_pairing("acct_1", "Safe desktop", ["local_integration", "local_file_write"])
    desktop = store.pair_executor(pairing["code"])
    read_task = store.create("acct_1", "work", "List repositories", "List my GitHub repositories", True, [{
        "name": "desktop.local_integration", "executor_kind": "desktop", "required_capability": "local_integration",
    }])
    write_task = store.create("acct_1", "work", "Write file", "Write a local file", True, [{
        "name": "desktop.local_file_write", "executor_kind": "desktop", "required_capability": "local_file_write",
    }])
    store.request_approval(read_task["id"], "acct_1")
    store.request_approval(write_task["id"], "acct_1")

    claimed = store.claim_for_executor(desktop["executor_id"], desktop["token"], auto_approve_safe=True)
    assert claimed and claimed["required_capability"] == "local_integration"
    assert store.get(read_task["id"], "acct_1")["requires_approval"] == 0
    assert store.get(write_task["id"], "acct_1")["requires_approval"] == 1
    assert store.get(write_task["id"], "acct_1")["status"] == "waiting_approval"
    events = store.events(read_task["id"], "acct_1")
    assert any(event["type"] == "approval.approved" and json.loads(event["payload"])["source"] == "desktop_policy" for event in events)


def test_desktop_safe_read_policy_does_not_bypass_multi_step_write_work(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    desktop = store.pair_executor(store.create_executor_pairing("acct_1", "Safe desktop", ["local_file_read"]).get("code"))
    task = store.create("acct_1", "work", "Mixed local work", "Read then write", True, [
        {"name": "read", "executor_kind": "desktop", "required_capability": "local_file_read"},
        {"name": "write", "executor_kind": "desktop", "required_capability": "local_file_write", "depends_on": []},
    ])
    store.request_approval(task["id"], "acct_1")
    assert store.claim_for_executor(desktop["executor_id"], desktop["token"], auto_approve_safe=True) is None
    assert store.get(task["id"], "acct_1")["requires_approval"] == 1


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


def test_workspace_repair_budget_stops_automatic_retries(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    desktop = store.pair_executor(store.create_executor_pairing("acct_1", "Desktop", ["local_file_read"])["code"])
    job = build_workspace_job(
        workspace_root="repo", objective="Bounded repair", capabilities=["local_file_read"],
        idempotency_key="workspace:repair:0001",
    )
    task = store.create("acct_1", "work", "Read", "Read", True, [{
        "name": "read", "executor_kind": "desktop", "required_capability": "local_file_read",
        "executor_payload": {"path": "missing.txt", "workspace_job": {**job, "max_repair_attempts": 0}},
    }])
    store.decide(task["id"], "acct_1", True, "approved")
    step = store.claim_for_executor(desktop["executor_id"], desktop["token"])
    assert step
    assert store.fail_executor_step(desktop["executor_id"], desktop["token"], step["step_id"], "missing file") == "failed"
    assert store.get(task["id"], "acct_1")["status"] == "failed"
    events = store.events(task["id"], "acct_1")
    failed = [item for item in events if item["type"] == "step.failed"][-1]
    assert json.loads(failed["payload"])["repair_budget_exhausted"] is True


def test_workspace_repair_budget_allows_one_repair_then_stops(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    desktop = store.pair_executor(store.create_executor_pairing("acct_1", "Desktop", ["local_file_read"])["code"])
    job = build_workspace_job(
        workspace_root="repo", objective="One repair", capabilities=["local_file_read"],
        idempotency_key="workspace:repair:0002",
    )
    payload = {"path": "missing.txt", "workspace_job": {**job, "max_repair_attempts": 1}}
    task = store.create("acct_1", "work", "Read", "Read", True, [{
        "name": "read", "executor_kind": "desktop", "required_capability": "local_file_read", "executor_payload": payload,
    }])
    store.decide(task["id"], "acct_1", True, "approved")
    first = store.claim_for_executor(desktop["executor_id"], desktop["token"])
    assert first and store.fail_executor_step(desktop["executor_id"], desktop["token"], first["step_id"], "first failure") == "retrying"
    with store._connect() as connection:
        connection.execute("UPDATE task_steps SET retry_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", first["step_id"]))
    second = store.claim_for_executor(desktop["executor_id"], desktop["token"])
    assert second
    assert store.fail_executor_step(desktop["executor_id"], desktop["token"], second["step_id"], "second failure") == "failed"


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


def test_desktop_claim_replaces_orphaned_lease_row(tmp_path: Path):
    """A stale lease row must not make a ready step fail with a UNIQUE error."""
    store = TaskStore(str(tmp_path / "smara.db"))
    desktop = store.pair_executor(store.create_executor_pairing("acct_1", "Desktop", ["local_file_read"])["code"])
    task = store.create("acct_1", "work", "Read", "Read", True, [{
        "name": "read", "executor_kind": "desktop", "required_capability": "local_file_read",
    }])
    store.decide(task["id"], "acct_1", True, "approved")
    step = store.steps(task["id"], "acct_1")[0]
    with store._connect() as connection:
        connection.execute(
            "INSERT INTO executor_leases(id,step_id,executor_id,expires_at,created_at) VALUES(?,?,?,?,?)",
            ("lease_orphaned", step["id"], desktop["executor_id"], "2000-01-01T00:00:00+00:00", "2000-01-01T00:00:00+00:00"),
        )

    claimed = store.claim_for_executor(desktop["executor_id"], desktop["token"])
    assert claimed and claimed["step_id"] == step["id"]
    with store._connect() as connection:
        lease = connection.execute(
            "SELECT id,executor_id,completed_at FROM executor_leases WHERE step_id=?",
            (step["id"],),
        ).fetchone()
    assert lease and lease["id"] != "lease_orphaned"
    assert lease["executor_id"] == desktop["executor_id"] and lease["completed_at"] is None


def test_hosted_worker_does_not_recover_running_desktop_lease(tmp_path: Path):
    """Hosted polling must leave local work for the paired desktop owner."""
    store = TaskStore(str(tmp_path / "smara.db"))
    desktop = store.pair_executor(store.create_executor_pairing("acct_1", "Desktop", ["local_terminal"])["code"])
    task = store.create("acct_1", "work", "Terminal", "Run once", True, [{
        "name": "terminal", "executor_kind": "desktop", "required_capability": "local_terminal",
    }])
    store.decide(task["id"], "acct_1", True, "approved")
    claimed = store.claim_for_executor(desktop["executor_id"], desktop["token"], lease_seconds=1)
    assert claimed
    with store._connect() as connection:
        connection.execute("UPDATE task_steps SET lease_expires_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", claimed["step_id"]))

    assert store.claim_one("hosted-worker") is None
    assert store.steps(task["id"], "acct_1")[0]["status"] == "running"


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
