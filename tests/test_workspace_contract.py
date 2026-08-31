import json
from pathlib import Path

import pytest

from smara.desktop_executor import execute_step
from smara.workspace_contract import (
    WorkspaceArtifact,
    WorkspaceJobSpec,
    WorkspaceStageResult,
    build_workspace_job,
    validate_workspace_job,
    workspace_job_summary,
)


def _job(**overrides):
    value = build_workspace_job(
        workspace_root="repo",
        objective="Inspect and verify the repository",
        capabilities=["local_file_read"],
        idempotency_key="workspace:test:0001",
        acceptance_checks=["The repository is inspected and proof is recorded."],
    )
    value.update(overrides)
    return value


def test_workspace_job_is_versioned_and_summary_is_bounded():
    job = validate_workspace_job(_job())
    summary = workspace_job_summary(job)
    assert job.schema_version == "smara.workspace.v1"
    assert summary["budgets"]["max_repair_attempts"] == 0
    assert "secret" not in json.dumps(summary).lower()


@pytest.mark.parametrize("override", [
    {"workspace_root": "C:/Users/owner/Documents"},
    {"workspace_root": "repo/../../outside"},
    {"allowed_capabilities": ["local_file_read", "local_file_read"]},
    {"allowed_capabilities": ["unknown"]},
    {"approval_policy": "read_only", "allowed_capabilities": ["local_file_write"]},
    {"api_key": "sk-this-must-not-travel"},
])
def test_workspace_job_rejects_escape_duplicates_policy_and_secrets(override):
    with pytest.raises(RuntimeError):
        validate_workspace_job({**_job(), **override})


def test_workspace_artifact_and_stage_result_are_bounded():
    artifact = WorkspaceArtifact(
        name="report.txt",
        path="reports/report.txt",
        sha256="a" * 64,
        bytes=12,
        preview="ready",
    )
    stage = WorkspaceStageResult(
        stage="verify",
        status="completed",
        summary="Tests passed",
        files_inspected=["src/main.py"],
        artifacts=[artifact],
        tests=[{"name": "pytest", "status": "passed"}],
    )
    assert stage.schema_version == "smara.workspace.stage.v1"
    assert stage.artifacts[0].path == "reports/report.txt"


def test_desktop_executor_enforces_job_capability_before_action(tmp_path: Path):
    state = {"allowed_roots": [str(tmp_path)], "capabilities": ["local_file_read"]}
    step = {
        "required_capability": "local_file_read",
        "idempotency_key": "task:step:0001",
        "executor_payload": {
            "operation": "list_tree",
            "workspace_job": _job(allowed_capabilities=["local_terminal"]),
        },
    }
    with pytest.raises(RuntimeError, match="not allowed"):
        execute_step(step, state)


def test_desktop_executor_attaches_job_metadata_to_safe_result(tmp_path: Path):
    (tmp_path / "hello.txt").write_text("hello", encoding="utf-8")
    state = {"allowed_roots": [str(tmp_path)], "capabilities": ["local_file_read"]}
    step = {
        "required_capability": "local_file_read",
        "idempotency_key": "task:step:0002",
        "executor_payload": {"operation": "list_tree", "workspace_job": _job()},
    }
    result = json.loads(execute_step(step, state))
    assert result["workspace_job"]["schema_version"] == "smara.workspace.v1"
    assert result["workspace_job"]["workspace_root"] == "repo"
    assert result["skill"] == "local_file_read"

