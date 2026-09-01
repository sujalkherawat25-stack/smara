"""Versioned, declarative skill contracts for Smara.

Skills are reusable *descriptions* of bounded tool graphs.  They are not
plugins and never contain Python, shell, credentials, or executable code.
The local executor can use a published manifest as an additional allowlist,
while the registry keeps the review/test/publish lifecycle auditable.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .local_agent import LOCAL_SKILLS


SKILL_MANIFEST_SCHEMA = "smara.skill.v1"
SKILL_STATES = ("draft", "tested", "published", "deprecated")
_NAME = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_VERSION = re.compile(r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.:-]{1,79}$")
_STAGE_ID = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
_DANGEROUS_KEYS = {
    "command", "shell", "script", "code", "eval", "exec", "executable",
    "powershell", "credential", "credentials", "api_key", "access_token",
    "refresh_token", "password", "secret", "secret_value", "private_key",
    "authorization", "client_secret",
}
_SECRET_VALUE = re.compile(r"(?:bearer\s+|sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_-]{12,})", re.I)
_INPUT_REFERENCE = re.compile(r"^\$input\.([a-z][a-z0-9_-]{1,63})$")
_MUTATING_CAPABILITIES = {"local_file_write", "local_terminal"}
SKILL_STORE_VERSION = 1
SKILL_REGISTRY_MAX_ENTRIES = 128


def _reject_executable_or_secret(value: Any) -> None:
    """Reject dangerous keys and credential-shaped values recursively."""
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).strip().lower() in _DANGEROUS_KEYS:
                raise ValueError("skill manifests cannot contain executable code or credentials")
            _reject_executable_or_secret(item)
    elif isinstance(value, list):
        for item in value:
            _reject_executable_or_secret(item)
    elif isinstance(value, str) and _SECRET_VALUE.search(value):
        raise ValueError("skill manifests cannot contain credential values")


def _unique_names(values: list[str], label: str) -> list[str]:
    normalized = [value.strip() for value in values]
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{label} names must be unique")
    return normalized


class SkillInput(BaseModel):
    """One typed input exposed to the user or a routine trigger."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=64)
    type: Literal["string", "integer", "number", "boolean", "object", "array"]
    description: str = Field(default="", max_length=500)
    required: bool = True

    @field_validator("name")
    @classmethod
    def name_is_safe(cls, value: str) -> str:
        value = value.strip()
        if not _NAME.fullmatch(value):
            raise ValueError("skill input names must be lowercase identifiers")
        return value


class SkillOutput(BaseModel):
    """One bounded, named value a skill promises to return."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=64)
    type: Literal["string", "integer", "number", "boolean", "object", "array"]
    description: str = Field(default="", max_length=500)
    required: bool = True

    @field_validator("name")
    @classmethod
    def name_is_safe(cls, value: str) -> str:
        value = value.strip()
        if not _NAME.fullmatch(value):
            raise ValueError("skill output names must be lowercase identifiers")
        return value


class SkillLimits(BaseModel):
    """Hard ceilings copied into the local approval preview."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    timeout_seconds: int = Field(default=60, ge=1, le=3_600)
    max_output_bytes: int = Field(default=32_000, ge=1, le=1_048_576)
    max_artifact_bytes: int = Field(default=8 * 1024 * 1024, ge=0, le=50 * 1024 * 1024)
    max_retries: int = Field(default=0, ge=0, le=3)


class SkillPermissions(BaseModel):
    """Capabilities and connectors a skill is allowed to request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    capabilities: list[str] = Field(default_factory=list, max_length=12)
    connectors: list[str] = Field(default_factory=list, max_length=12)
    approved_domains: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("capabilities")
    @classmethod
    def known_capabilities(cls, values: list[str]) -> list[str]:
        values = _unique_names(values, "capability")
        if any(value not in LOCAL_SKILLS for value in values):
            raise ValueError("skill permissions contain an unknown local capability")
        return values

    @field_validator("connectors")
    @classmethod
    def connector_names_are_safe(cls, values: list[str]) -> list[str]:
        values = _unique_names(values, "connector")
        if any(not _IDENTIFIER.fullmatch(value) for value in values):
            raise ValueError("connector names must be lowercase identifiers")
        return values

    @field_validator("approved_domains")
    @classmethod
    def domains_are_bounded(cls, values: list[str]) -> list[str]:
        values = _unique_names(values, "domain")
        if any(not value or len(value) > 253 or "/" in value or " " in value for value in values):
            raise ValueError("approved_domains must contain hostnames only")
        return [value.lower() for value in values]


class SkillStage(BaseModel):
    """A typed tool invocation in a declarative DAG."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=2, max_length=32)
    tool: str = Field(min_length=2, max_length=80)
    operation: str = Field(min_length=1, max_length=120)
    depends_on: list[str] = Field(default_factory=list, max_length=12)
    arguments: dict[str, Any] = Field(default_factory=dict, max_length=32)
    requires_approval: bool = False

    @field_validator("id")
    @classmethod
    def id_is_safe(cls, value: str) -> str:
        value = value.strip()
        if not _STAGE_ID.fullmatch(value):
            raise ValueError("skill stage ids must be lowercase identifiers")
        return value

    @field_validator("tool")
    @classmethod
    def tool_is_safe(cls, value: str) -> str:
        value = value.strip()
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("skill stage tool names must be lowercase identifiers")
        return value

    @field_validator("operation")
    @classmethod
    def operation_is_safe(cls, value: str) -> str:
        return value.strip()

    @field_validator("depends_on")
    @classmethod
    def dependencies_are_safe(cls, values: list[str]) -> list[str]:
        values = _unique_names(values, "stage dependency")
        if any(not _STAGE_ID.fullmatch(value) for value in values):
            raise ValueError("skill stage dependencies must name stages")
        return values

    @field_validator("arguments")
    @classmethod
    def arguments_are_declarative(cls, value: dict[str, Any]) -> dict[str, Any]:
        _reject_executable_or_secret(value)
        try:
            json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError("skill stage arguments must be JSON serializable") from exc
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > 32 * 1024:
            raise ValueError("skill stage arguments exceed the 32 KB limit")
        return value


class SkillTestCase(BaseModel):
    """A disposable, secret-free acceptance case recorded before publishing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=120)
    inputs: dict[str, Any] = Field(default_factory=dict, max_length=32)
    expected_status: Literal["completed", "failed", "blocked"] = "completed"
    expected_outputs: dict[str, Any] = Field(default_factory=dict, max_length=32)

    @model_validator(mode="after")
    def bounded_values(self) -> "SkillTestCase":
        _reject_executable_or_secret(self.inputs)
        _reject_executable_or_secret(self.expected_outputs)
        try:
            encoded = json.dumps(self.model_dump(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("skill test case must be JSON serializable") from exc
        if len(encoded) > 64 * 1024:
            raise ValueError("skill test case exceeds the 64 KB limit")
        return self


class SkillRollback(BaseModel):
    """Human-readable rollback contract shown with a published skill."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy: Literal["none", "restore_snapshot", "reverse_steps", "manual"] = "none"
    instructions: str = Field(default="", max_length=2_000)

    @model_validator(mode="after")
    def instructions_match_strategy(self) -> "SkillRollback":
        if self.strategy != "none" and not self.instructions.strip():
            raise ValueError("rollback instructions are required for a rollback strategy")
        return self


class SkillManifest(BaseModel):
    """The stable, versioned package format for a reusable Smara skill."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    schema_version: Literal["smara.skill.v1"] = SKILL_MANIFEST_SCHEMA
    name: str = Field(min_length=2, max_length=64)
    version: str = Field(min_length=5, max_length=32)
    description: str = Field(min_length=1, max_length=2_000)
    inputs: list[SkillInput] = Field(default_factory=list, max_length=32)
    outputs: list[SkillOutput] = Field(default_factory=list, max_length=32)
    permissions: SkillPermissions = Field(default_factory=SkillPermissions)
    risk: Literal["safe", "confirm"] = "confirm"
    provider: str = Field(default="smara", min_length=2, max_length=80)
    owner: str = Field(min_length=1, max_length=200)
    stages: list[SkillStage] = Field(min_length=1, max_length=12)
    tests: list[SkillTestCase] = Field(min_length=1, max_length=20)
    rollback: SkillRollback = Field(default_factory=SkillRollback)
    limits: SkillLimits = Field(default_factory=SkillLimits)

    @field_validator("name")
    @classmethod
    def name_is_safe(cls, value: str) -> str:
        value = value.strip()
        if not _NAME.fullmatch(value):
            raise ValueError("skill names must be lowercase identifiers")
        return value

    @field_validator("version")
    @classmethod
    def version_is_semver(cls, value: str) -> str:
        value = value.strip()
        if not _VERSION.fullmatch(value):
            raise ValueError("skill version must use semantic versioning")
        return value

    @field_validator("provider", "owner")
    @classmethod
    def metadata_is_bounded(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def graph_and_permissions_are_consistent(self) -> "SkillManifest":
        input_names = _unique_names([item.name for item in self.inputs], "skill input")
        output_names = _unique_names([item.name for item in self.outputs], "skill output")
        if not output_names:
            raise ValueError("a skill must declare at least one output")
        if any(not name for name in input_names + output_names):
            raise ValueError("skill input/output names cannot be empty")
        stage_ids = [stage.id for stage in self.stages]
        if len(set(stage_ids)) != len(stage_ids):
            raise ValueError("skill stage ids must be unique")
        stage_set = set(stage_ids)
        allowed = set(self.permissions.capabilities)
        for stage in self.stages:
            if any(parent not in stage_set for parent in stage.depends_on):
                raise ValueError("skill stage depends_on references an unknown stage")
            if stage.id in stage.depends_on:
                raise ValueError("a skill stage cannot depend on itself")
            if stage.tool in LOCAL_SKILLS and stage.tool not in allowed:
                raise ValueError("every local tool must be declared in permissions.capabilities")
            if stage.requires_approval and self.risk != "confirm":
                raise ValueError("an approval-gated stage requires risk=confirm")
        # Kahn's algorithm catches cycles without allowing a graph to be run.
        pending = {stage.id: set(stage.depends_on) for stage in self.stages}
        resolved: set[str] = set()
        while pending:
            ready = [stage_id for stage_id, deps in pending.items() if not deps - resolved]
            if not ready:
                raise ValueError("skill stages must form an acyclic graph")
            resolved.update(ready)
            for stage_id in ready:
                pending.pop(stage_id)
        return self

    def fingerprint(self) -> str:
        """Return a stable digest used to invalidate stale approvals."""
        encoded = json.dumps(
            self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def _input_references(value: Any, found: set[str]) -> None:
    """Collect explicit ``$input.name`` references from a workflow payload."""
    if isinstance(value, dict):
        for item in value.values():
            _input_references(item, found)
    elif isinstance(value, list):
        for item in value:
            _input_references(item, found)
    elif isinstance(value, str):
        match = _INPUT_REFERENCE.fullmatch(value.strip())
        if match:
            found.add(match.group(1))


def draft_skill_from_workflow(
    *,
    name: str,
    version: str,
    description: str,
    owner: str,
    workflow: list[dict[str, Any]],
    tests: list[dict[str, Any]],
    provider: str = "smara",
    rollback: dict[str, Any] | None = None,
) -> SkillManifest:
    """Convert an approved bounded workflow into a reviewable draft manifest.

    The workflow validator remains the source of truth for stage order,
    capability allowlists, payload size, and credential rejection.  This
    helper only translates that already-safe event shape into a reusable
    declarative package; it never infers executable code or secrets.
    """
    from .workflow import validate_workflow

    normalized = validate_workflow(workflow)
    references: set[str] = set()
    capabilities: set[str] = set()
    stages: list[dict[str, Any]] = []
    mutating = False
    for index, item in enumerate(normalized):
        payload = dict(item["payload"])
        _input_references(payload, references)
        capability = item["capability"]
        capabilities.add(capability)
        operation = payload.get("operation") or payload.get("action") or item["stage"]
        if capability in _MUTATING_CAPABILITIES or str(operation).lower() in {"write", "edit", "delete", "run", "download"}:
            mutating = True
        stages.append({
            "id": f"{item['stage']}_{index}",
            "tool": capability,
            "operation": str(operation)[:120],
            "depends_on": [stages[-1]["id"]] if stages else [],
            "arguments": payload,
            "requires_approval": capability in _MUTATING_CAPABILITIES,
        })
    input_models = [
        {"name": input_name, "type": "string", "description": "Inferred from the approved workflow."}
        for input_name in sorted(references)
    ]
    return SkillManifest.model_validate({
        "schema_version": SKILL_MANIFEST_SCHEMA,
        "name": name,
        "version": version,
        "description": description,
        "inputs": input_models,
        "outputs": [{"name": "result", "type": "object", "description": "Bounded workflow result."}],
        "permissions": {"capabilities": sorted(capabilities), "connectors": [], "approved_domains": []},
        "risk": "confirm" if mutating else "safe",
        "provider": provider,
        "owner": owner,
        "stages": stages,
        "tests": tests,
        "rollback": rollback or {"strategy": "none"},
    })


def validate_skill_manifest(value: Any) -> SkillManifest:
    """Parse a manifest and expose one safe error type to callers."""
    if not isinstance(value, dict):
        raise RuntimeError("skill manifest must be an object")
    try:
        _reject_executable_or_secret(value)
        return SkillManifest.model_validate(value)
    except ValueError as exc:
        raise RuntimeError(f"Invalid skill manifest: {str(exc)[:500]}") from exc


@dataclass(frozen=True)
class SkillRecord:
    manifest: SkillManifest
    state: Literal["draft", "tested", "published", "deprecated"]
    fingerprint: str
    tested: bool = False
    test_run_id: str | None = None
    approved_by: str | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "schema_version": self.manifest.schema_version,
            "name": self.manifest.name,
            "version": self.manifest.version,
            "description": self.manifest.description,
            "state": self.state,
            "risk": self.manifest.risk,
            "provider": self.manifest.provider,
            "owner": self.manifest.owner,
            "capabilities": self.manifest.permissions.capabilities,
            "connectors": self.manifest.permissions.connectors,
            "stages": [stage.id for stage in self.manifest.stages],
            "fingerprint": self.fingerprint,
            "tested": self.tested,
        }


class SkillRegistry:
    """Thread-safe in-memory lifecycle registry.

    Persistence belongs in the durable task/control-plane store.  This first
    slice deliberately keeps the policy pure and makes that future adapter
    straightforward; no manifest is executable unless it is published and
    its fingerprint still matches the tested version.
    """

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], SkillRecord] = {}
        self._lock = RLock()

    @staticmethod
    def _clone_record(record: SkillRecord) -> SkillRecord:
        """Return a detached copy so callers cannot mutate registry state.

        Pydantic's ``frozen`` model blocks attribute assignment but nested
        lists/dicts remain mutable.  A detached model keeps the approved
        fingerprint meaningful even when a caller holds a returned record.
        """
        manifest = SkillManifest.model_validate(record.manifest.model_dump(mode="json"))
        return SkillRecord(
            manifest, record.state, record.fingerprint, record.tested,
            record.test_run_id, record.approved_by,
        )

    def register(self, manifest: SkillManifest | dict[str, Any]) -> SkillRecord:
        parsed = manifest if isinstance(manifest, SkillManifest) else validate_skill_manifest(manifest)
        key = (parsed.name, parsed.version)
        with self._lock:
            if key in self._records:
                raise RuntimeError(f"skill {parsed.name}@{parsed.version} is already registered")
            record = SkillRecord(
                SkillManifest.model_validate(parsed.model_dump(mode="json")),
                "draft",
                parsed.fingerprint(),
            )
            self._records[key] = record
            return self._clone_record(record)

    def get(self, name: str, version: str) -> SkillRecord:
        with self._lock:
            try:
                return self._clone_record(self._records[(name, version)])
            except KeyError as exc:
                raise RuntimeError(f"skill {name}@{version} is not registered") from exc

    def record_test(self, name: str, version: str, *, passed: bool, run_id: str) -> SkillRecord:
        if not run_id.strip() or len(run_id) > 200:
            raise ValueError("test run id is required")
        with self._lock:
            current = self.get(name, version)
            if current.state in {"published", "deprecated"}:
                raise RuntimeError("published or deprecated skills cannot be retested in place; register a new version")
            state: Literal["draft", "tested", "published", "deprecated"] = "tested" if passed else "draft"
            updated = SkillRecord(current.manifest, state, current.fingerprint, passed, run_id.strip())
            self._records[(name, version)] = updated
            return self._clone_record(updated)

    def publish(self, name: str, version: str, *, approved_by: str) -> SkillRecord:
        if not approved_by.strip() or len(approved_by) > 200:
            raise ValueError("approved_by is required")
        with self._lock:
            current = self.get(name, version)
            if current.state != "tested" or not current.tested:
                raise RuntimeError("skill must pass a disposable test before publishing")
            updated = SkillRecord(current.manifest, "published", current.fingerprint, True, current.test_run_id, approved_by.strip())
            self._records[(name, version)] = updated
            return self._clone_record(updated)

    def deprecate(self, name: str, version: str) -> SkillRecord:
        with self._lock:
            current = self.get(name, version)
            if current.state != "published":
                raise RuntimeError("only published skills can be deprecated")
            updated = SkillRecord(current.manifest, "deprecated", current.fingerprint, current.tested, current.test_run_id, current.approved_by)
            self._records[(name, version)] = updated
            return self._clone_record(updated)

    def assert_runnable(self, name: str, version: str, manifest: SkillManifest | dict[str, Any] | None = None) -> SkillRecord:
        """Fail closed if approval is missing or the manifest changed."""
        with self._lock:
            record = self.get(name, version)
            if record.state != "published":
                raise RuntimeError("skill is not published")
            if record.manifest.fingerprint() != record.fingerprint:
                raise RuntimeError("skill manifest changed after approval; publish a new version")
            if manifest is not None:
                candidate = manifest if isinstance(manifest, SkillManifest) else validate_skill_manifest(manifest)
                if candidate.fingerprint() != record.fingerprint:
                    raise RuntimeError("skill manifest changed after approval; publish a new version")
            return record

    def summaries(self) -> list[dict[str, Any]]:
        with self._lock:
            return [record.summary() for record in self._records.values()]


class PersistentSkillRegistry(SkillRegistry):
    """Atomic, restart-safe registry for private Desktop skill packages.

    The file contains manifests and lifecycle metadata only; credential values
    are rejected by validation and are never written.  A malformed or tampered
    file fails closed instead of being silently replaced with an empty store.
    """

    def __init__(self, path: Path, *, max_entries: int = SKILL_REGISTRY_MAX_ENTRIES) -> None:
        super().__init__()
        self.path = path
        self.max_entries = max(10, min(max_entries, SKILL_REGISTRY_MAX_ENTRIES))
        self._load()

    def _load(self) -> None:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            raise RuntimeError("The local skill store is unreadable; no skills were loaded.") from exc
        if not isinstance(value, dict) or value.get("version") != SKILL_STORE_VERSION:
            raise RuntimeError("The local skill store has an unsupported schema version.")
        entries = value.get("skills")
        if not isinstance(entries, list) or len(entries) > self.max_entries:
            raise RuntimeError("The local skill store contains too many or invalid entries.")
        loaded: dict[tuple[str, str], SkillRecord] = {}
        allowed_keys = {"manifest", "state", "fingerprint", "tested", "test_run_id", "approved_by"}
        for item in entries:
            if not isinstance(item, dict) or set(item) != allowed_keys:
                raise RuntimeError("The local skill store contains an invalid record.")
            manifest = validate_skill_manifest(item.get("manifest"))
            state = item.get("state")
            if state not in SKILL_STATES or not isinstance(item.get("tested"), bool):
                raise RuntimeError("The local skill store contains an invalid lifecycle state.")
            fingerprint = item.get("fingerprint")
            if fingerprint != manifest.fingerprint():
                raise RuntimeError("The local skill store contains a stale manifest fingerprint.")
            if state in {"tested", "published", "deprecated"} and item["tested"] is not True:
                raise RuntimeError("A tested skill record must retain its test marker.")
            if state == "published" and not isinstance(item.get("approved_by"), str):
                raise RuntimeError("A published skill record must retain its approver.")
            key = (manifest.name, manifest.version)
            if key in loaded:
                raise RuntimeError("The local skill store contains a duplicate skill version.")
            loaded[key] = SkillRecord(
                manifest, state, fingerprint, item["tested"], item.get("test_run_id"), item.get("approved_by")
            )
        with self._lock:
            self._records = loaded

    def _persist_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        records = list(self._records.values())[: self.max_entries]
        payload = {
            "version": SKILL_STORE_VERSION,
            "skills": [
                {
                    "manifest": record.manifest.model_dump(mode="json"),
                    "state": record.state,
                    "fingerprint": record.fingerprint,
                    "tested": record.tested,
                    "test_run_id": record.test_run_id,
                    "approved_by": record.approved_by,
                }
                for record in records
            ],
        }
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            with contextlib.suppress(OSError):
                os.fsync(handle.fileno())
        try:
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def _persist_after(self, operation):
        with self._lock:
            previous = dict(self._records)
            try:
                result = operation()
                self._persist_locked()
                return result
            except Exception:
                self._records = previous
                raise

    def register(self, manifest: SkillManifest | dict[str, Any]) -> SkillRecord:
        return self._persist_after(lambda: super(PersistentSkillRegistry, self).register(manifest))

    def record_test(self, name: str, version: str, *, passed: bool, run_id: str) -> SkillRecord:
        return self._persist_after(lambda: super(PersistentSkillRegistry, self).record_test(name, version, passed=passed, run_id=run_id))

    def publish(self, name: str, version: str, *, approved_by: str) -> SkillRecord:
        return self._persist_after(lambda: super(PersistentSkillRegistry, self).publish(name, version, approved_by=approved_by))

    def deprecate(self, name: str, version: str) -> SkillRecord:
        return self._persist_after(lambda: super(PersistentSkillRegistry, self).deprecate(name, version))
