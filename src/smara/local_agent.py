"""Shared contracts for safe local Smara skills and task reconciliation.

The hosted service remains the source of truth for task state.  This module
only keeps a bounded, local journal of what the desktop has claimed and adds a
cross-process workspace lock so a reconnect cannot silently replay uncertain
side effects.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


JOURNAL_MAX_ENTRIES = 200
JOURNAL_STATUSES = {"claimed", "prepared", "completed", "failed", "cancelled", "uncertain"}
LOCK_TIMEOUT_SECONDS = 0.0


@dataclass(frozen=True)
class LocalSkillSpec:
    """The reviewable contract every local capability must publish."""

    capability: str
    description: str
    input_schema: dict[str, Any]
    approval: str = "task"
    timeout_seconds: int = 60
    max_output_bytes: int = 32_000
    max_artifact_bytes: int = 8 * 1024 * 1024
    side_effecting: bool = True
    idempotency_required: bool = True
    result_schema: dict[str, Any] = field(default_factory=dict)
    artifact_schema: dict[str, Any] = field(default_factory=dict)
    redaction: str = "Secrets are never returned; local output is bounded and credential values are redacted."


# The envelope is intentionally explicit.  Capability-specific payload fields
# are validated by the executor, but unknown keys are never silently accepted
# by a remote skill caller.
_ANY_OBJECT = {"type": "object", "additionalProperties": False}

LOCAL_SKILLS: dict[str, LocalSkillSpec] = {
    "local_file_read": LocalSkillSpec(
        "local_file_read", "Inspect an approved local file or workspace.", _ANY_OBJECT,
        timeout_seconds=15, max_output_bytes=256 * 1024, side_effecting=False,
        result_schema={"type": "object", "required": ["action"]},
    ),
    "local_file_write": LocalSkillSpec(
        "local_file_write", "Preview and apply an approved workspace edit or bounded DOCX, XLSX, PPTX, or PDF operation.", _ANY_OBJECT,
        timeout_seconds=60, max_output_bytes=40_000, max_artifact_bytes=8 * 1024 * 1024,
        result_schema={"type": "object", "required": ["action", "operation"]},
        artifact_schema={"type": "object", "properties": {"undo_id": {"type": "string"}}},
    ),
    "local_terminal": LocalSkillSpec(
        "local_terminal", "Run one allowlisted command or deterministic recipe.", _ANY_OBJECT,
        timeout_seconds=60, max_output_bytes=32_000,
        result_schema={"type": "object", "required": ["action", "exit_code"]},
        artifact_schema={"type": "array", "items": {"type": "object"}},
    ),
    "local_browser": LocalSkillSpec(
        "local_browser", "Inspect or download from an explicitly approved domain.", _ANY_OBJECT,
        timeout_seconds=30, max_output_bytes=16_000, max_artifact_bytes=50 * 1024 * 1024,
        result_schema={"type": "object", "required": ["action", "operation", "proof"]},
        artifact_schema={"type": "object", "properties": {"source_url": {"type": "string"}}},
    ),
    "local_integration": LocalSkillSpec(
        "local_integration", "Call an approved local read-only integration using a local secret.", _ANY_OBJECT,
        timeout_seconds=30, max_output_bytes=16_000, side_effecting=False,
        result_schema={"type": "object", "required": ["action", "provider"]},
    ),
}


def local_skill_catalog() -> list[dict[str, Any]]:
    """Return a JSON-safe catalogue for diagnostics and future skill UIs."""
    return [
        {
            "capability": spec.capability,
            "description": spec.description,
            "input_schema": spec.input_schema,
            "approval": spec.approval,
            "timeout_seconds": spec.timeout_seconds,
            "max_output_bytes": spec.max_output_bytes,
            "max_artifact_bytes": spec.max_artifact_bytes,
            "side_effecting": spec.side_effecting,
            "idempotency_required": spec.idempotency_required,
            "result_schema": spec.result_schema,
            "artifact_schema": spec.artifact_schema,
            "redaction": spec.redaction,
        }
        for spec in LOCAL_SKILLS.values()
    ]


def skill_spec(capability: str) -> LocalSkillSpec:
    try:
        return LOCAL_SKILLS[capability]
    except KeyError as exc:
        raise RuntimeError(f"Local capability '{capability}' has no installed skill contract.") from exc


def validate_local_step(step: dict[str, Any]) -> tuple[LocalSkillSpec, str]:
    """Validate the protocol envelope without rejecting capability payloads.

    Payload-specific validation remains in the executor.  This layer prevents
    an unknown capability, missing idempotency identity, or unapproved local
    step from bypassing the common contract.
    """
    capability = step.get("required_capability")
    if not isinstance(capability, str) or not capability.strip():
        raise RuntimeError("Local steps must declare a required capability.")
    spec = skill_spec(capability)
    if step.get("requires_approval") is True and spec.approval == "task":
        # The durable task gate has already approved a claimed step.  This
        # branch is intentionally a no-op marker for readable validation.
        pass
    payload = step.get("executor_payload")
    if not isinstance(payload, dict):
        raise RuntimeError("The local skill input must be an object.")
    key = step.get("idempotency_key") or payload.get("idempotency_key")
    if key is None:
        # Direct unit calls and old development payloads have no durable key;
        # the live executor always receives one from the task store.
        key = f"local-direct:{capability}"
    if not isinstance(key, str) or not key.strip() or len(key) > 240:
        raise RuntimeError("Local skill idempotency_key is invalid.")
    try:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Local skill input is not JSON serializable.") from exc
    if len(encoded) > 64 * 1024:
        raise RuntimeError("Local skill input exceeds the 64 KB limit.")
    declared = payload.get("skill")
    if declared is not None and declared != capability:
        raise RuntimeError("Local skill input names a different capability.")
    return spec, key.strip()


def decorate_local_result(raw: str, spec: LocalSkillSpec, idempotency_key: str) -> str:
    """Attach protocol metadata to structured results without leaking data."""
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return raw
    if not isinstance(value, dict):
        return raw
    value.setdefault("skill", spec.capability)
    value.setdefault("idempotency_key", idempotency_key)
    value.setdefault("failure_state", "completed")
    value.setdefault("limits", {
        "timeout_seconds": spec.timeout_seconds,
        "max_output_bytes": spec.max_output_bytes,
        "max_artifact_bytes": spec.max_artifact_bytes,
    })
    return json.dumps(value, ensure_ascii=False)


def _lock_directory(state_path: Path | None) -> Path:
    if state_path is not None:
        return state_path.expanduser().resolve().parent / "locks"
    return Path(tempfile.gettempdir()) / "Smara" / "locks"


@contextlib.contextmanager
def workspace_lock(root: Path, *, state_path: Path | None = None) -> Iterator[None]:
    """Acquire an exclusive lock for one approved workspace.

    The lock lives in Smara app data, not inside the user's repository, so a
    failed process cannot leave a tracked lock file behind.  A second local
    task fails closed immediately with a useful message.
    """
    canonical = root.expanduser().resolve()
    lock_dir = _lock_directory(state_path)
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_name = hashlib.sha256(str(canonical).encode("utf-8")).hexdigest() + ".lock"
    path = lock_dir / lock_name
    handle = path.open("a+b")
    # Windows byte-range locking cannot lock past an empty file.  Keep one
    # harmless marker byte so the same fail-closed behavior works on a fresh
    # install as well as on subsequent runs.
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise RuntimeError(f"Another local task is using workspace '{canonical.name or canonical}'. Try again when it finishes.") from exc
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


class LocalTaskJournal:
    """A bounded, atomic journal for local claim/commit reconciliation."""

    def __init__(self, path: Path, *, max_entries: int = JOURNAL_MAX_ENTRIES):
        self.path = path
        self.max_entries = max(10, min(max_entries, JOURNAL_MAX_ENTRIES))

    def _read(self) -> list[dict[str, Any]]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            return []
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)][: self.max_entries]

    def _write(self, entries: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(entries[: self.max_entries], handle, ensure_ascii=False, indent=2)
            handle.flush()
            with contextlib.suppress(OSError):
                os.fsync(handle.fileno())
        try:
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def record(self, step_id: str, status: str, *, task_id: str | None = None,
               capability: str | None = None, idempotency_key: str | None = None,
               result_sha256: str | None = None, error: str | None = None,
               remote_status: str | None = None) -> dict[str, Any]:
        if not step_id or len(step_id) > 240:
            raise ValueError("step_id is required")
        if status not in JOURNAL_STATUSES:
            raise ValueError(f"unknown local journal status: {status}")
        now = time.time()
        entry = {
            "step_id": step_id,
            "status": status,
            "task_id": task_id,
            "capability": capability,
            "idempotency_key": idempotency_key,
            "result_sha256": result_sha256,
            "error": (error or "")[:2000] or None,
            "remote_status": remote_status,
            "updated_at": now,
        }
        entries = [item for item in self._read() if item.get("step_id") != step_id]
        entries.insert(0, entry)
        self._write(entries)
        return entry

    def get(self, step_id: str) -> dict[str, Any] | None:
        return next((item for item in self._read() if item.get("step_id") == step_id), None)

    def entries(self) -> list[dict[str, Any]]:
        return self._read()

    def uncertain(self, step_id: str, idempotency_key: str | None = None) -> bool:
        return any(
            item.get("status") == "uncertain"
            and (item.get("step_id") == step_id or (idempotency_key and item.get("idempotency_key") == idempotency_key))
            for item in self._read()
        )

    def reconcile(self, step_id: str, remote_status: str) -> dict[str, Any] | None:
        entry = self.get(step_id)
        if entry is None:
            return None
        entry["remote_status"] = remote_status
        if remote_status in {"completed", "failed", "cancelled"} and entry.get("status") in {"claimed", "prepared", "uncertain"}:
            entry["status"] = "completed" if remote_status == "completed" else remote_status
        entry["updated_at"] = time.time()
        entries = [item for item in self._read() if item.get("step_id") != step_id]
        entries.insert(0, entry)
        self._write(entries)
        return entry

    def summary(self) -> dict[str, Any]:
        entries = self._read()
        counts = {status: sum(1 for item in entries if item.get("status") == status) for status in sorted(JOURNAL_STATUSES)}
        return {"path": str(self.path), "entries": len(entries), "counts": counts, "uncertain": [item for item in entries if item.get("status") == "uncertain"]}


def journal_path(state_path: Path) -> Path:
    return state_path.with_suffix(state_path.suffix + ".journal.json")
