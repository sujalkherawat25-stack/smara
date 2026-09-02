from __future__ import annotations

import copy

import pytest

from smara.skill_protocol import draft_skill_from_workflow
from smara.store import TaskStore


def _manifest(owner: str = "acct_1") -> dict:
    return {
        "schema_version": "smara.skill.v1",
        "name": "daily-inspect",
        "version": "1.0.0",
        "description": "Inspect an approved workspace.",
        "inputs": [{"name": "root", "type": "string"}],
        "outputs": [{"name": "result", "type": "object"}],
        "permissions": {"capabilities": ["local_file_read"]},
        "risk": "safe",
        "provider": "smara",
        "owner": owner,
        "stages": [
            {"id": "inspect", "tool": "local_file_read", "operation": "tree", "arguments": {"path": "$input.root"}},
        ],
        "tests": [{"name": "empty", "inputs": {"root": "workspace"}}],
        "rollback": {"strategy": "none"},
    }


def test_hosted_skill_lifecycle_is_account_scoped_and_durable(tmp_path):
    path = tmp_path / "smara.db"
    store = TaskStore(str(path))
    created = store.create_skill("acct_1", _manifest())
    assert created["state"] == "draft"
    assert store.list_skills("acct_2") == []
    with pytest.raises(ValueError, match="already registered"):
        store.create_skill("acct_1", _manifest())
    with pytest.raises(ValueError, match="owner"):
        store.create_skill("acct_2", _manifest())

    tested = store.record_skill_test("acct_1", "daily-inspect", "1.0.0", passed=True, run_id="run-1")
    assert tested["state"] == "tested"
    published = store.publish_skill("acct_1", "daily-inspect", "1.0.0")
    assert published["state"] == "published"
    assert published["approved_by"] == "acct_1"
    assert store.assert_skill_runnable("acct_1", "daily-inspect", "1.0.0")["state"] == "published"

    reopened = TaskStore(str(path))
    assert reopened.get_skill("acct_1", "daily-inspect", "1.0.0")["state"] == "published"
    with pytest.raises(ValueError, match="retested"):
        reopened.record_skill_test("acct_1", "daily-inspect", "1.0.0", passed=False, run_id="run-2")

    deprecated = reopened.deprecate_skill("acct_1", "daily-inspect", "1.0.0")
    assert deprecated["state"] == "deprecated"
    with pytest.raises(ValueError, match="not published"):
        reopened.assert_skill_runnable("acct_1", "daily-inspect", "1.0.0")


def test_hosted_skill_rejects_manifest_changes_after_publish(tmp_path):
    store = TaskStore(str(tmp_path / "smara.db"))
    store.create_skill("acct_1", _manifest())
    store.record_skill_test("acct_1", "daily-inspect", "1.0.0", passed=True, run_id="run-1")
    store.publish_skill("acct_1", "daily-inspect", "1.0.0")
    changed = copy.deepcopy(_manifest())
    changed["description"] = "Changed after approval"
    with pytest.raises(ValueError, match="changed after approval"):
        store.assert_skill_runnable("acct_1", "daily-inspect", "1.0.0", changed)


def test_taught_skill_can_be_persisted_as_a_draft(tmp_path):
    manifest = draft_skill_from_workflow(
        name="safe-edit",
        version="1.0.0",
        description="Inspect then edit an approved workspace.",
        owner="acct_1",
        workflow=[
            {"stage": "inspect", "capability": "local_file_read", "payload": {"path": "$input.root"}},
            {"stage": "edit", "capability": "local_file_write", "payload": {"path": "note.txt", "content": "hello"}},
        ],
        tests=[{"name": "disposable", "inputs": {"root": "workspace"}}],
    )
    stored = TaskStore(str(tmp_path / "smara.db")).create_skill("acct_1", manifest.model_dump(mode="json"))
    assert stored["state"] == "draft"
    assert stored["manifest"]["risk"] == "confirm"
