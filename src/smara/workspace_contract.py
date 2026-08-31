"""Versioned contract for bounded local workspace jobs.

The hosted agent may propose a job, but the desktop is the authority that
validates and executes it.  Keeping this contract small and explicit gives the
web/desktop consoles a stable shape for progress, diffs, tests, and artifacts
without ever putting credential values in a task payload.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


WORKSPACE_JOB_SCHEMA = "smara.workspace.v1"
LOCAL_CAPABILITIES = (
    "local_file_read",
    "local_file_write",
    "local_terminal",
    "local_browser",
    "local_integration",
)
_CAPABILITY_SET = set(LOCAL_CAPABILITIES)
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,239}$")
_CREDENTIAL_ALIAS = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_RELATIVE = re.compile(r"^(?![\\/])(?![A-Za-z]:)(?!.*(?:^|[\\/])\.\.(?:[\\/]|$)).+$")
_SECRET_KEYS = {
    "api_key", "apikey", "access_token", "refresh_token", "password", "secret",
    "secret_value", "authorization", "private_key", "client_secret", "credential",
}
_SECRET_VALUE = re.compile(r"(?:sk-[A-Za-z0-9_-]{12,}|xai-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_-]{12,})")
_READ_ONLY_CAPABILITIES = {"local_file_read", "local_browser", "local_integration"}


def _reject_secret_fields(value: Any) -> None:
    """Reject credential-shaped keys recursively, including nested payloads."""
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).strip().lower() in _SECRET_KEYS:
                raise ValueError("workspace jobs cannot contain credential values; use credential_aliases")
            _reject_secret_fields(item)
    elif isinstance(value, list):
        for item in value:
            _reject_secret_fields(item)
    elif isinstance(value, str) and _SECRET_VALUE.search(value):
        raise ValueError("workspace jobs cannot contain credential values")


def validate_relative_path(value: str, *, field: str = "path") -> str:
    """Accept a workspace-relative path and reject absolute/traversal forms."""
    normalized = value.strip().replace("\\", "/")
    if not normalized or not _SAFE_RELATIVE.fullmatch(normalized) or normalized in {".", ".."}:
        raise ValueError(f"{field} must be a relative path inside the approved workspace")
    return normalized


class WorkspaceArtifact(BaseModel):
    """Bounded proof that a local stage produced an artifact."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=240)
    kind: Literal["file", "diff", "test", "log", "report", "other"] = "file"
    path: str | None = Field(default=None, max_length=500)
    uri: str | None = Field(default=None, max_length=1_000)
    sha256: str | None = None
    bytes: int = Field(default=0, ge=0, le=8 * 1024 * 1024)
    media_type: str | None = Field(default=None, max_length=120)
    preview: str | None = Field(default=None, max_length=4_000)

    @field_validator("path")
    @classmethod
    def path_is_relative(cls, value: str | None) -> str | None:
        return validate_relative_path(value, field="artifact.path") if value is not None else None

    @field_validator("sha256")
    @classmethod
    def hash_is_sha256(cls, value: str | None) -> str | None:
        if value is not None and not _SHA256.fullmatch(value.lower()):
            raise ValueError("artifact.sha256 must be a lowercase SHA-256 digest")
        return value.lower() if value is not None else None

    @model_validator(mode="after")
    def has_reference(self) -> "WorkspaceArtifact":
        if not self.path and not self.uri:
            raise ValueError("artifact must include a relative path or bounded uri")
        return self


class WorkspaceChangedFile(BaseModel):
    """Hash-level change proof; file contents are never part of the contract."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=500)
    before_sha256: str | None = None
    after_sha256: str | None = None
    change: Literal["added", "modified", "deleted", "renamed", "unchanged"] = "modified"
    diff_summary: str | None = Field(default=None, max_length=2_000)

    @field_validator("path")
    @classmethod
    def path_is_relative(cls, value: str) -> str:
        return validate_relative_path(value, field="changed_file.path")

    @field_validator("before_sha256", "after_sha256")
    @classmethod
    def hashes_are_sha256(cls, value: str | None) -> str | None:
        if value is not None and not _SHA256.fullmatch(value.lower()):
            raise ValueError("changed file hashes must be lowercase SHA-256 digests")
        return value.lower() if value is not None else None


class WorkspaceCommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=1, max_length=1_000)
    exit_code: int = Field(ge=-255, le=255)
    duration_ms: int = Field(default=0, ge=0, le=3_600_000)
    output_preview: str = Field(default="", max_length=4_000)


class WorkspaceTestResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    status: Literal["passed", "failed", "skipped", "blocked"]
    output_preview: str = Field(default="", max_length=4_000)


class WorkspaceStageResult(BaseModel):
    """Stable, bounded output shown by both run consoles."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["smara.workspace.stage.v1"] = "smara.workspace.stage.v1"
    stage: Literal["inspect", "plan", "edit", "run", "verify", "report"]
    status: Literal["completed", "failed", "cancelled", "blocked"]
    summary: str = Field(min_length=1, max_length=4_000)
    files_inspected: list[str] = Field(default_factory=list, max_length=100)
    files_changed: list[WorkspaceChangedFile] = Field(default_factory=list, max_length=100)
    commands_run: list[WorkspaceCommandResult] = Field(default_factory=list, max_length=20)
    tests: list[WorkspaceTestResult] = Field(default_factory=list, max_length=20)
    artifacts: list[WorkspaceArtifact] = Field(default_factory=list, max_length=20)
    warnings: list[str] = Field(default_factory=list, max_length=20)
    next_action: str | None = Field(default=None, max_length=1_000)

    @field_validator("files_inspected")
    @classmethod
    def inspected_paths_are_relative(cls, values: list[str]) -> list[str]:
        return [validate_relative_path(value, field="files_inspected path") for value in values]


class WorkspaceJobSpec(BaseModel):
    """Versioned inspect-to-report job envelope sent to the local executor."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    schema_version: Literal["smara.workspace.v1"] = WORKSPACE_JOB_SCHEMA
    workspace_root: str = Field(min_length=1, max_length=500)
    objective: str = Field(min_length=1, max_length=20_000)
    acceptance_checks: list[str] = Field(min_length=1, max_length=12)
    allowed_capabilities: list[str] = Field(min_length=1, max_length=5)
    idempotency_key: str = Field(min_length=8, max_length=240)
    time_budget_seconds: int = Field(default=900, ge=1, le=3_600)
    cost_budget_inr: float = Field(default=0, ge=0, le=100_000)
    max_repair_attempts: int = Field(default=0, ge=0, le=3)
    approval_policy: Literal["always", "on_mutation", "read_only"] = "on_mutation"
    credential_aliases: list[str] = Field(default_factory=list, max_length=12)
    repository: bool = False
    base_revision: str | None = Field(default=None, max_length=160)

    @field_validator("workspace_root")
    @classmethod
    def root_is_relative(cls, value: str) -> str:
        return validate_relative_path(value, field="workspace_root")

    @field_validator("acceptance_checks")
    @classmethod
    def checks_are_bounded(cls, values: list[str]) -> list[str]:
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError("acceptance_checks must contain non-empty descriptions")
        return [value.strip()[:2_000] for value in values]

    @field_validator("allowed_capabilities")
    @classmethod
    def capabilities_are_known(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values) or any(value not in _CAPABILITY_SET for value in values):
            raise ValueError("allowed_capabilities contains an unknown or duplicate capability")
        return values

    @field_validator("idempotency_key")
    @classmethod
    def key_is_safe(cls, value: str) -> str:
        if not _IDEMPOTENCY_KEY.fullmatch(value.strip()):
            raise ValueError("idempotency_key must be 8-240 safe characters")
        return value.strip()

    @field_validator("credential_aliases")
    @classmethod
    def aliases_are_names_only(cls, values: list[str]) -> list[str]:
        normalized = [value.strip().upper() for value in values]
        if len(set(normalized)) != len(normalized) or any(not _CREDENTIAL_ALIAS.fullmatch(value) for value in normalized):
            raise ValueError("credential_aliases must contain unique uppercase environment names")
        return normalized

    @model_validator(mode="after")
    def policy_matches_capabilities(self) -> "WorkspaceJobSpec":
        if self.approval_policy == "read_only" and any(item not in _READ_ONLY_CAPABILITIES for item in self.allowed_capabilities):
            raise ValueError("read_only approval_policy cannot include a mutating capability")
        if self.repository and self.workspace_root in {".", ""}:
            raise ValueError("repository jobs need an explicit workspace_root")
        return self


def validate_workspace_job(value: Any) -> WorkspaceJobSpec:
    """Parse a job envelope and expose a stable RuntimeError to the executor."""
    if not isinstance(value, dict):
        raise RuntimeError("workspace_job must be an object")
    try:
        _reject_secret_fields(value)
        return WorkspaceJobSpec.model_validate(value)
    except ValueError as exc:
        raise RuntimeError(f"Invalid workspace job: {str(exc)[:500]}") from exc


def workspace_job_summary(job: WorkspaceJobSpec) -> dict[str, Any]:
    """Return the safe, UI-friendly subset used in task results and events."""
    return {
        "schema_version": job.schema_version,
        "workspace_root": job.workspace_root,
        "objective": job.objective[:500],
        "acceptance_checks": job.acceptance_checks,
        "allowed_capabilities": job.allowed_capabilities,
        "idempotency_key": job.idempotency_key,
        "budgets": {
            "time_budget_seconds": job.time_budget_seconds,
            "cost_budget_inr": job.cost_budget_inr,
            "max_repair_attempts": job.max_repair_attempts,
        },
        "approval_policy": job.approval_policy,
        "credential_aliases": job.credential_aliases,
        "repository": job.repository,
        "base_revision": job.base_revision,
    }


def build_workspace_job(*, workspace_root: str, objective: str, capabilities: list[str],
                        idempotency_key: str, acceptance_checks: list[str] | None = None,
                        approval_policy: str = "on_mutation") -> dict[str, Any]:
    """Build a deterministic default envelope for model-created desktop work."""
    return WorkspaceJobSpec(
        workspace_root=workspace_root,
        objective=objective,
        acceptance_checks=acceptance_checks or ["The requested local stage completes and reports bounded proof."],
        allowed_capabilities=capabilities,
        idempotency_key=idempotency_key,
        approval_policy=approval_policy,
    ).model_dump(mode="json")
