from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from smara.sandbox import SandboxLimits, docker_command
from smara.store import TaskStore
from smara.vault import SecretVault
from smara.worker import run_once


def test_terminal_task_failure_is_recorded_in_dead_letter_queue(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    task = store.create("acct_1", "work", "Unstable", "Retry safely", False)
    for _ in range(3):
        step = store.claim_one("worker")
        assert step is not None
        store.fail_step(step["step_id"], "acct_1", "upstream failure", retry_delay_seconds=0)
    letters = store.dead_letters("acct_1")
    assert len(letters) == 1
    assert letters[0]["task_id"] == task["id"]
    assert letters[0]["attempts"] == 3
    assert store.dead_letters("acct_2") == []


def test_key_ring_reads_old_ciphertext_and_writes_with_new_key():
    old_key, new_key = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    old_ciphertext = SecretVault(old_key).encrypt("secret")
    rotating = SecretVault(f"{new_key},{old_key}")
    assert rotating.decrypt(old_ciphertext) == "secret"
    assert SecretVault(new_key).decrypt(rotating.encrypt("fresh")) == "fresh"


def test_sandbox_recipe_is_networkless_and_has_no_host_mounts():
    command = docker_command("python -c 'print(1)'", SandboxLimits(timeout_seconds=30))
    assert ["--network", "none"] == command[command.index("--network"):command.index("--network") + 2]
    assert "--read-only" in command and "--cap-drop" in command
    assert "-v" not in command and "--volume" not in command
    with pytest.raises(ValueError):
        docker_command("", SandboxLimits())


def test_sandbox_step_runs_only_after_durable_approval(tmp_path: Path, monkeypatch):
    store = TaskStore(str(tmp_path / "smara.db"))
    task = store.create("acct_1", "work", "Check code", "Run a test", True, [{
        "name": "sandbox.test", "executor_kind": "sandbox", "executor_payload": {"command": "pytest -q"},
    }])
    called: list[str] = []
    monkeypatch.setattr("smara.worker.run_sandbox", lambda command: called.append(command) or "passed")
    assert __import__("asyncio").run(run_once(store, None, sandbox_enabled=True))
    assert called == [] and store.get(task["id"], "acct_1")["status"] == "waiting_approval"
    store.decide(task["id"], "acct_1", True, "run it")
    assert __import__("asyncio").run(run_once(store, None, sandbox_enabled=True))
    assert called == ["pytest -q"] and store.get(task["id"], "acct_1")["status"] == "completed"


def test_sandbox_is_not_claimed_when_deployment_capability_is_disabled(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    task = store.create("acct_1", "work", "Check code", "Run a test", False, [{
        "name": "sandbox.test", "executor_kind": "sandbox", "executor_payload": {"command": "pytest -q"},
    }])
    assert __import__("asyncio").run(run_once(store, None, sandbox_enabled=False)) is False
    assert store.get(task["id"], "acct_1")["status"] == "queued"
