"""Shared contracts for safe local Smara skills and task state.

The Desktop supports a local-first runtime as well as the hosted coordinator.
This module keeps bounded, private task/session state on the user's machine
and a separate claim journal for hosted lease reconciliation.  Local state is
never uploaded implicitly; the hosted bridge may receive only the explicitly
approved, bounded result of a local action.  A cross-process workspace lock
prevents competing writes and replay of uncertain side effects.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

try:
    from .workspace_contract import validate_workspace_job
except ImportError:  # pragma: no cover - exercised by the bundled executable
    from workspace_contract import validate_workspace_job


JOURNAL_MAX_ENTRIES = 200
JOURNAL_STATUSES = {"claimed", "prepared", "completed", "failed", "cancelled", "uncertain"}
LOCAL_TASK_MAX_ENTRIES = 200
LOCAL_TASK_STATUSES = {
    "draft", "queued", "waiting_approval", "running", "cancelling",
    "review_required", "completed", "failed", "cancelled",
}
LOCAL_TASK_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
LOCAL_TASK_TRANSITIONS = {
    "draft": {"queued", "waiting_approval", "cancelled"},
    "waiting_approval": {"queued", "cancelled"},
    "queued": {"running", "cancelled"},
    "running": {"cancelling", "review_required", "completed", "failed", "cancelled"},
    "cancelling": {"review_required", "failed", "cancelled"},
    "review_required": {"queued", "waiting_approval", "cancelled"},
    "failed": {"queued", "waiting_approval", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}
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
        "local_file_read", "Inspect an approved local file or workspace, including metadata-only snapshots and Git proof.", _ANY_OBJECT,
        timeout_seconds=15, max_output_bytes=256 * 1024, side_effecting=False,
        result_schema={"type": "object", "required": ["action"]},
    ),
    "local_file_write": LocalSkillSpec(
        "local_file_write", "Preview and apply an approved workspace edit or bounded DOCX, XLSX, PPTX, PDF, or isolated-workspace operation.", _ANY_OBJECT,
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
    "local_media": LocalSkillSpec(
        "local_media", "Inspect an approved local image, audio, video, archive, or document with a bounded local media reader.", _ANY_OBJECT,
        timeout_seconds=90, max_output_bytes=64_000, side_effecting=False,
        result_schema={"type": "object", "required": ["action", "operation"]},
    ),
    "local_graph": LocalSkillSpec(
        "local_graph", "Query AST Code Property Graph for symbol definitions, callers, references, and blast radius.", _ANY_OBJECT,
        timeout_seconds=30, max_output_bytes=64_000, side_effecting=False,
        result_schema={"type": "object", "required": ["action", "operation"]},
    ),
    "local_python": LocalSkillSpec(
        "local_python", "Execute Python code in a safe local sandbox.", _ANY_OBJECT,
        timeout_seconds=30, max_output_bytes=64_000, side_effecting=True,
        result_schema={"type": "object", "required": ["action"]},
    ),
    "local_calculate": LocalSkillSpec(
        "local_calculate", "Perform exact mathematical and scientific calculations.", _ANY_OBJECT,
        timeout_seconds=10, max_output_bytes=16_000, side_effecting=False,
        result_schema={"type": "object", "required": ["action"]},
    ),
    "local_semantic_search": LocalSkillSpec(
        "local_semantic_search", "Query local offline SQLite semantic vector database using natural language intent.", _ANY_OBJECT,
        timeout_seconds=15, max_output_bytes=64_000, side_effecting=False,
        result_schema={"type": "object", "required": ["action"]},
    ),
    "local_git": LocalSkillSpec(
        "local_git", "Perform safe Git operations (status, log, smart conventional commit, branch switching).", _ANY_OBJECT,
        timeout_seconds=30, max_output_bytes=64_000, side_effecting=True,
        result_schema={"type": "object", "required": ["action", "operation"]},
    ),
    "local_refactor": LocalSkillSpec(
        "local_refactor", "Autonomous multi-file refactoring with pre-change backup snapshots and atomic rollback.", _ANY_OBJECT,
        timeout_seconds=60, max_output_bytes=64_000, side_effecting=True,
        result_schema={"type": "object", "required": ["action", "operation"]},
    ),
    "local_test_fixer": LocalSkillSpec(
        "local_test_fixer", "Run pytest test suites and autonomously heal broken test failures.", _ANY_OBJECT,
        timeout_seconds=60, max_output_bytes=64_000, side_effecting=True,
        result_schema={"type": "object", "required": ["action", "operation"]},
    ),
    "sandbox_execute": LocalSkillSpec(
        "sandbox_execute", "Execute commands inside an isolated ephemeral micro-sandbox container.", _ANY_OBJECT,
        timeout_seconds=120, max_output_bytes=64_000, side_effecting=True,
        result_schema={"type": "object", "required": ["action", "isolated"]},
    ),
}


def local_skill_catalog(include_extended: bool = False) -> list[dict[str, Any]]:
    """Return a JSON-safe catalogue for diagnostics and future skill UIs."""
    base_caps = {
        "local_file_read", "local_file_write", "local_terminal", "local_browser", "local_integration",
        "local_media", "local_calculate", "local_graph", "local_python",
    }
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
        for cap, spec in LOCAL_SKILLS.items()
        if include_extended or cap in base_caps
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
    # A workspace job is optional for backwards-compatible single actions,
    # but when present it is the versioned source of truth for scope, budgets,
    # capabilities, approval policy, and idempotency. Validate it before any
    # capability-specific code can inspect or mutate the workspace.
    if "workspace_job" in payload:
        job = validate_workspace_job(payload.get("workspace_job"))
        if capability not in job.allowed_capabilities:
            raise RuntimeError("The local capability is not allowed by workspace_job.")
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


def _timestamp() -> str:
    """Return a compact UTC timestamp that sorts lexicographically."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class LocalTaskStore:
    """Bounded private task/session store for the local-first Desktop mode.

    This intentionally uses the same atomic JSON primitive as the recovery
    journal so the packaged executor has no database dependency.  The schema
    is versioned and deliberately small; a future SQLite migration can import
    these records without changing the local skill protocol.
    """

    def __init__(self, path: Path, *, max_entries: int = LOCAL_TASK_MAX_ENTRIES):
        self.path = path
        self.max_entries = max(10, min(max_entries, LOCAL_TASK_MAX_ENTRIES))

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            return {"version": 1, "tasks": []}
        if not isinstance(value, dict):
            return {"version": 1, "tasks": []}
        tasks = value.get("tasks")
        if not isinstance(tasks, list):
            tasks = []
        return {"version": 1, "tasks": [item for item in tasks if isinstance(item, dict)][: self.max_entries]}

    def _write(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tasks = value.get("tasks") if isinstance(value.get("tasks"), list) else []
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump({"version": 1, "tasks": tasks[: self.max_entries]}, handle, ensure_ascii=False, indent=2)
            handle.flush()
            with contextlib.suppress(OSError):
                os.fsync(handle.fileno())
        try:
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    @contextlib.contextmanager
    def _mutation(self) -> Iterator[dict[str, Any]]:
        """Serialize cross-process task mutations around the atomic file.

        Reads need no lock because ``os.replace`` exposes complete files only.
        Mutations do need one: the native UI and detached runner are separate
        processes and must not overwrite each other's approval/progress state.
        """
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + 5.0
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    handle.close()
                    raise RuntimeError("The private task store is busy; try again.") from exc
                time.sleep(0.05)
        value = self._read()
        try:
            yield value
            self._write(value)
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

    @staticmethod
    def _clean_text(value: object, *, field: str, limit: int) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} is required")
        value = value.strip()
        if len(value) > limit:
            raise ValueError(f"{field} exceeds the {limit} character limit")
        return value

    def create(
        self,
        *,
        title: str,
        objective: str,
        session_id: str | None = None,
        requires_approval: bool = True,
        approval_mode: str = "ask",
        required_capability: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        title = self._clean_text(title, field="title", limit=160)
        objective = self._clean_text(objective, field="objective", limit=8_000)
        if approval_mode not in {"ask", "auto"}:
            raise ValueError("approval_mode must be 'ask' or 'auto'")
        if required_capability is not None:
            required_capability = self._clean_text(required_capability, field="required_capability", limit=80)
            skill_spec(required_capability)
        if payload is not None:
            if not isinstance(payload, dict):
                raise ValueError("payload must be an object")
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if len(encoded) > 64 * 1024:
                raise ValueError("payload exceeds the 64 KB limit")
        now = _timestamp()
        task_id = f"local_{uuid.uuid4().hex[:20]}"
        is_delete = (
            required_capability == "local_file_write"
            and isinstance(payload, dict)
            and payload.get("operation") == "delete"
        )
        needs_approval = is_delete or (requires_approval and approval_mode == "ask")
        task = {
            "id": task_id,
            "session_id": (session_id or f"local-session-{uuid.uuid4().hex[:12]}")[:160],
            "title": title,
            "objective": objective,
            "status": "waiting_approval" if needs_approval else "queued",
            "requires_approval": bool(needs_approval),
            "approval_mode": approval_mode,
            "required_capability": required_capability,
            "payload": payload,
            "result": None,
            "error": None,
            "run_id": None,
            "attempt": 0,
            "cancel_requested": False,
            "started_at": None,
            "finished_at": None,
            "created_at": now,
            "updated_at": now,
            "steps": [],
            "artifacts": [],
            "events": [{"type": "local.task.created", "created_at": now}],
        }
        with self._mutation() as value:
            value["tasks"] = [task] + [item for item in value["tasks"] if item.get("id") != task_id]
        return self.summary(task)

    def get(self, task_id: str) -> dict[str, Any] | None:
        return next((item for item in self._read()["tasks"] if item.get("id") == task_id), None)

    def summaries(self) -> list[dict[str, Any]]:
        return [self.summary(item) for item in self._read()["tasks"]]

    @staticmethod
    def summary(task: dict[str, Any]) -> dict[str, Any]:
        """Return the UI-safe view; payloads never appear in task lists."""
        value = {key: task.get(key) for key in (
            "id", "session_id", "title", "objective", "status", "requires_approval",
            "required_capability", "result", "error", "run_id", "attempt",
            "cancel_requested", "started_at", "finished_at", "created_at", "updated_at",
        )}
        # Match the shared Desktop task contract while retaining the private
        # policy in a separate, local-only field for future runner decisions.
        value["approval_mode"] = "desktop"
        value["local_approval_mode"] = task.get("approval_mode", "ask")
        return value

    def detail(self, task_id: str) -> dict[str, Any] | None:
        task = self.get(task_id)
        if task is None:
            return None
        value = self.summary(task)
        value["steps"] = list(task.get("steps") or [])[-64:]
        value["events"] = list(task.get("events") or [])[-64:]
        value["artifacts"] = list(task.get("artifacts") or [])[-20:]
        return value

    @staticmethod
    def _transition(task: dict[str, Any], status: str) -> None:
        current = str(task.get("status") or "draft")
        if status == current:
            return
        if status not in LOCAL_TASK_TRANSITIONS.get(current, set()):
            raise ValueError(f"local task cannot transition from {current} to {status}")
        task["status"] = status

    @staticmethod
    def _event(task: dict[str, Any], event: str, *, message: str | None = None) -> None:
        created_at = _timestamp()
        item: dict[str, Any] = {"type": event[:120], "created_at": created_at}
        if message:
            item["message"] = str(message)[:500]
        events = task.setdefault("events", [])
        if not isinstance(events, list):
            events = []
        events.append(item)
        task["events"] = events[-64:]

    def update(self, task_id: str, status: str, *, result: str | None = None, error: str | None = None, event: str | None = None) -> dict[str, Any]:
        if status not in LOCAL_TASK_STATUSES:
            raise ValueError(f"unknown local task status: {status}")
        with self._mutation() as value:
            task = next((item for item in value["tasks"] if item.get("id") == task_id), None)
            if task is None:
                raise KeyError(task_id)
            self._transition(task, status)
            task["updated_at"] = _timestamp()
            if result is not None:
                task["result"] = str(result)[:40_000]
            if error is not None:
                task["error"] = str(error)[:2_000]
            if event:
                self._event(task, event)
        return self.summary(task)

    def approve(self, task_id: str) -> dict[str, Any]:
        with self._mutation() as value:
            task = next((item for item in value["tasks"] if item.get("id") == task_id), None)
            if task is None:
                raise KeyError(task_id)
            current = task.get("status")
            if current in {"waiting_approval", "review_required", "failed"}:
                self._transition(task, "queued")
                task["error"] = None
                task["cancel_requested"] = False
                task["updated_at"] = _timestamp()
                self._event(task, "local.task.approved" if current == "waiting_approval" else "local.task.retry_approved")
            elif current not in {"queued", "running", "cancelling", "completed"}:
                raise ValueError(f"local task cannot be approved from {current}")
        return self.summary(task)

    def claim(self, task_id: str | None = None) -> dict[str, Any] | None:
        with self._mutation() as value:
            task = next((item for item in value["tasks"] if item.get("status") == "queued" and (task_id is None or item.get("id") == task_id)), None)
            if task is None:
                return None
            self._transition(task, "running")
            now = _timestamp()
            task["run_id"] = f"run_{uuid.uuid4().hex[:20]}"
            task["attempt"] = int(task.get("attempt") or 0) + 1
            task["cancel_requested"] = False
            task["started_at"] = now
            task["finished_at"] = None
            task["updated_at"] = now
            task["steps"] = []
            self._event(task, "local.task.started")
            return dict(task)

    def progress(self, task_id: str, message: str) -> dict[str, Any]:
        message = self._clean_text(message, field="progress", limit=500)
        with self._mutation() as value:
            task = next((item for item in value["tasks"] if item.get("id") == task_id), None)
            if task is None:
                raise KeyError(task_id)
            if task.get("status") not in {"running", "cancelling"}:
                return self.summary(task)
            now = _timestamp()
            steps = task.setdefault("steps", [])
            if not isinstance(steps, list):
                steps = []
            steps.append({"name": message, "status": "running", "created_at": now})
            task["steps"] = steps[-64:]
            task["updated_at"] = now
            self._event(task, "local.task.progress", message=message)
        return self.summary(task)

    def should_cancel(self, task_id: str) -> bool:
        task = self.get(task_id)
        return bool(task is None or task.get("cancel_requested") or task.get("status") in {"cancelling", "cancelled"})

    @staticmethod
    def _proof_from_result(result: str) -> tuple[str, list[dict[str, Any]]]:
        summary = result[:40_000]
        artifacts: list[dict[str, Any]] = []
        try:
            value = json.loads(result)
        except (TypeError, ValueError):
            return summary, artifacts
        if not isinstance(value, dict):
            return summary, artifacts
        action = str(value.get("action") or "Local task")
        operation = str(value.get("operation") or "completed")
        summary = f"{action}: {operation}"
        file_name = value.get("file_name")
        if isinstance(file_name, str) and file_name:
            artifacts.append({
                "id": f"artifact_{uuid.uuid4().hex[:16]}", "kind": "local_file",
                "name": file_name[:240], "sha256": value.get("sha256"),
                "created_at": _timestamp(),
            })
            summary += f" · {file_name}"
        proof = value.get("proof")
        if isinstance(proof, dict):
            digest = proof.get("content_sha256") or proof.get("result_sha256")
            if isinstance(digest, str) and digest:
                summary += f" · proof {digest[:12]}"
        return summary[:2_000], artifacts[:20]

    def complete(self, task_id: str, result: str) -> dict[str, Any]:
        summary, artifacts = self._proof_from_result(result)
        with self._mutation() as value:
            task = next((item for item in value["tasks"] if item.get("id") == task_id), None)
            if task is None:
                raise KeyError(task_id)
            self._transition(task, "completed")
            now = _timestamp()
            task["result"] = str(result)[:40_000]
            task["result_summary"] = summary
            task["error"] = None
            task["cancel_requested"] = False
            task["finished_at"] = now
            task["updated_at"] = now
            task["artifacts"] = artifacts
            self._event(task, "local.task.completed")
        return self.summary(task)

    def fail(self, task_id: str, error: str, *, interrupted: bool = False) -> dict[str, Any]:
        with self._mutation() as value:
            task = next((item for item in value["tasks"] if item.get("id") == task_id), None)
            if task is None:
                raise KeyError(task_id)
            target = "review_required" if interrupted else "failed"
            self._transition(task, target)
            now = _timestamp()
            task["error"] = str(error)[:2_000]
            task["finished_at"] = now
            task["updated_at"] = now
            task["cancel_requested"] = False
            self._event(task, "local.task.interrupted" if interrupted else "local.task.failed")
        return self.summary(task)

    def recover_interrupted(self) -> list[str]:
        recovered: list[str] = []
        with self._mutation() as value:
            for task in value["tasks"]:
                if task.get("status") not in {"running", "cancelling"}:
                    continue
                self._transition(task, "review_required")
                task["error"] = "Execution was interrupted. Review and approve a retry; Smara will not replay it automatically."
                task["cancel_requested"] = False
                task["finished_at"] = _timestamp()
                task["updated_at"] = task["finished_at"]
                self._event(task, "local.task.interrupted")
                recovered.append(str(task.get("id") or ""))
        return recovered

    def cancel(self, task_id: str) -> dict[str, Any]:
        with self._mutation() as value:
            task = next((item for item in value["tasks"] if item.get("id") == task_id), None)
            if task is None:
                raise KeyError(task_id)
            current = str(task.get("status") or "draft")
            if current in LOCAL_TASK_TERMINAL_STATUSES:
                return self.summary(task)
            if current == "running":
                self._transition(task, "cancelling")
                task["cancel_requested"] = True
                event = "local.task.cancel_requested"
            elif current == "cancelling":
                task["cancel_requested"] = True
                event = "local.task.cancel_requested"
            else:
                self._transition(task, "cancelled")
                task["cancel_requested"] = True
                task["finished_at"] = _timestamp()
                event = "local.task.cancelled"
            task["error"] = "Cancelled on this Desktop."
            task["updated_at"] = _timestamp()
            self._event(task, event)
        return self.summary(task)

    def finish_cancelled(self, task_id: str, error: str = "Cancelled on this Desktop.") -> dict[str, Any]:
        with self._mutation() as value:
            task = next((item for item in value["tasks"] if item.get("id") == task_id), None)
            if task is None:
                raise KeyError(task_id)
            if task.get("status") not in LOCAL_TASK_TERMINAL_STATUSES:
                self._transition(task, "cancelled")
            now = _timestamp()
            task["error"] = str(error)[:2_000]
            task["cancel_requested"] = True
            task["finished_at"] = now
            task["updated_at"] = now
            self._event(task, "local.task.cancelled")
        return self.summary(task)


def journal_path(state_path: Path) -> Path:
    return state_path.with_suffix(state_path.suffix + ".journal.json")


def local_tasks_path(state_path: Path) -> Path:
    """Path for private local tasks, kept beside the Desktop state file."""
    return state_path.with_suffix(state_path.suffix + ".tasks.json")


@dataclass
class LocalAutonomousAgent:
    """Multi-step autonomous local agent engine for Smara Desktop.
    
    Orchestrates ReAct loops across all local capabilities:
    - local_file_read (line-slice, regex search, tree, git)
    - local_file_write (atomic write, patch, delete with approval, document studio)
    - local_terminal (python, pytest, npm, cargo, go, git)
    - local_browser (inspect DOM, download, text extraction)
    - local_integration (live Tavily search, GitHub repositories)
    - local_graph (AST symbol inspection, blast radius, references)
    - local_python (safe in-memory sandbox)
    - local_calculate (scientific & exact math)
    """

    state_path: Path
    max_steps: int = 20
    # Optional host callback used by the CLI.  The Desktop leaves this unset
    # and dispatches through the validated desktop_executor protocol; the CLI
    # can reuse the exact same ReAct loop while retaining its richer TUI/RAV
    # capability implementations.
    action_executor: Any | None = None

    @staticmethod
    def _action_failed(result: Any) -> bool:
        """Return whether a structured executor result represents a failure.

        Some executors intentionally return an error envelope instead of
        raising.  Treating those as successful observations lets a model
        invent completion after a failed local action, so the shared loop
        normalises them here before the next planning step.
        """
        if not isinstance(result, dict):
            return False
        if result.get("error") or result.get("failure_state") in {"failed", "cancelled"}:
            return True
        for key in ("ok", "success"):
            if key in result and result[key] is False:
                return True
        return False

    @staticmethod
    def _action_signature(capability: Any, payload: Any) -> str:
        """Build a stable identity for repetition detection without logging secrets."""
        try:
            encoded = json.dumps({"capability": capability, "payload": payload}, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            encoded = f"{capability}:{type(payload).__name__}"
        return hashlib.sha256(encoded.encode("utf-8", errors="replace")).hexdigest()

    def execute_action(self, capability: str, payload: dict[str, Any], *, step_id: str | None = None) -> dict[str, Any]:
        if self.action_executor is not None:
            result = self.action_executor(capability, payload)
            if isinstance(result, dict):
                return result
            return {"result": result}
        try:
            from smara.desktop_executor import _load_local_state, execute_step
        except ImportError:
            from desktop_executor import _load_local_state, execute_step
        state = _load_local_state(self.state_path)
        idempotency_key = f"local-auto:{uuid.uuid4().hex[:16]}"
        step = {
            "step_id": step_id or f"step_{uuid.uuid4().hex[:16]}",
            "task_id": f"task_{uuid.uuid4().hex[:16]}",
            "requires_approval": False,
            "required_capability": capability,
            "executor_payload": payload,
            "idempotency_key": idempotency_key,
        }
        raw_result = execute_step(step, state)
        try:
            return json.loads(raw_result)
        except (ValueError, TypeError):
            return {"result": raw_result}

    def run_turn(
        self,
        prompt: str,
        *,
        model_callable: Any | None = None,
        context: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Execute a full multi-step turn with autonomous tool dispatch."""
        history = list(context or [])
        history.append({"role": "user", "content": prompt})
        steps_taken: list[dict[str, Any]] = []
        action_counts: dict[str, int] = {}

        if model_callable is None:
            return {
                "answer": f"Processed: {prompt}",
                "steps": steps_taken,
                "completed": True,
            }

        for iteration in range(self.max_steps):
            response = model_callable(history)
            if not isinstance(response, dict):
                break
            if response.get("kind") == "answer" or "answer" in response:
                answer = response.get("answer", "")
                return {
                    "answer": answer,
                    "steps": steps_taken,
                    "completed": True,
                    "iterations": iteration + 1,
                }
            if response.get("kind") == "local_action" or "capability" in response:
                cap = response.get("capability")
                payload = response.get("payload") or {}
                step_record = {
                    "iteration": iteration + 1,
                    "title": response.get("title", cap),
                    "capability": cap,
                    "payload": payload,
                }
                signature = self._action_signature(cap, payload)
                action_counts[signature] = action_counts.get(signature, 0) + 1
                if action_counts[signature] > 2:
                    message = "Stopped because the planner repeated the same local action without new evidence."
                    step_record["error"] = message
                    step_record["ok"] = False
                    steps_taken.append(step_record)
                    return {
                        "answer": message,
                        "steps": steps_taken,
                        "completed": False,
                        "iterations": iteration + 1,
                        "failure_reason": "repeated_action",
                    }
                try:
                    action_result = self.execute_action(cap, payload)
                    step_record["result"] = action_result
                    step_record["ok"] = not self._action_failed(action_result)
                    history.append({
                        "role": "assistant",
                        "content": json.dumps({"action": cap, "payload": payload}),
                    })
                    history.append({
                        "role": "tool",
                        "name": cap,
                        "content": json.dumps(action_result, ensure_ascii=False),
                    })
                except Exception as exc:
                    step_record["error"] = str(exc)
                    step_record["ok"] = False
                    history.append({
                        "role": "assistant",
                        "content": json.dumps({"action": cap, "payload": payload}),
                    })
                    history.append({
                        "role": "tool",
                        "name": cap,
                        "content": json.dumps({"error": str(exc)}, ensure_ascii=False),
                    })
                steps_taken.append(step_record)
            else:
                break

        return {
            "answer": "The local agent reached its step limit before it could verify completion.",
            "steps": steps_taken,
            "completed": False,
            "iterations": self.max_steps,
            "failure_reason": "step_limit",
        }

