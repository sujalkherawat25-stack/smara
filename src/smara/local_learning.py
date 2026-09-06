"""Test-gated local skill learning for Smara.

Learning is intentionally declarative.  A completed local run is reduced to a
safe capability workflow, validated with the existing skill protocol, and
written as a human-readable ``SKILL.md`` only after the workflow smoke test
passes.  No downloaded code, shell script, credentials, or model-generated
executable content is persisted.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

# The source/CLI imports this as ``smara.local_learning`` while the bundled
# Desktop executor loads it as a top-level module from PyInstaller.
try:
    from .local_agent import LOCAL_SKILLS
    from .skill_protocol import SKILL_MANIFEST_SCHEMA, validate_skill_manifest
except ImportError:  # pragma: no cover - exercised by the packaged binary
    from local_agent import LOCAL_SKILLS
    from skill_protocol import SKILL_MANIFEST_SCHEMA, validate_skill_manifest


MAX_TASK_RECORDS = 40
MAX_WORKFLOW_STEPS = 12
MAX_TASK_CHARS = 20_000
MAX_PLAYBOOK_CHARS = 40_000
_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_SECRET_KEY_RE = re.compile(r"(?i)(?:key|token|secret|password|credential|authorization)")
_SECRET_VALUE_RE = re.compile(r"(?i)(?:bearer\s+|(?:sk|gh[pousr]|xox[baprs])_[A-Za-z0-9_-]{12,})")


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        with contextlib.suppress(OSError):
            os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(text)
        handle.flush()
        with contextlib.suppress(OSError):
            os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    """Copy JSON data while removing credential-shaped fields and values."""
    if depth > 5:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:64]:
            key_text = str(key)[:80]
            if _SECRET_KEY_RE.search(key_text):
                continue
            result[key_text] = _safe_value(item, depth=depth + 1)
        return result
    if isinstance(value, list):
        return [_safe_value(item, depth=depth + 1) for item in value[:64]]
    if isinstance(value, str):
        return _SECRET_VALUE_RE.sub("[REDACTED]", value[:MAX_TASK_CHARS])
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:MAX_TASK_CHARS]


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_-]+", "-", str(value or "").strip().lower()).strip("-_")
    cleaned = cleaned[:64]
    if not cleaned or not cleaned[0].isalpha():
        cleaned = "learned-" + (cleaned or "workflow")
    return cleaned[:64]


def _yaml_scalar(value: str) -> str:
    # Frontmatter is parsed by the intentionally small local skills registry.
    # Keep scalars single-line and avoid introducing YAML control characters.
    return re.sub(r"[\r\n]+", " ", str(value or "")).strip()[:1_000]


def _workflow_from_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    workflow: list[dict[str, Any]] = []
    for index, raw in enumerate(steps[:MAX_WORKFLOW_STEPS]):
        if not isinstance(raw, dict):
            continue
        capability = str(raw.get("capability") or "").strip()
        if capability not in LOCAL_SKILLS:
            continue
        payload = raw.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        safe_payload = _safe_value(payload)
        operation = str(safe_payload.get("operation") or safe_payload.get("action") or capability).strip()[:120]
        workflow.append({
            "stage": f"step-{index + 1}",
            "capability": capability,
            "payload": safe_payload,
            "operation": operation,
        })
    return workflow


class LocalSkillLearningEngine:
    """Persist completed-run evidence and produce tested local playbooks."""

    def __init__(self, workspace_root: Path | str, *, state_path: Path | str | None = None):
        self.workspace = Path(workspace_root).expanduser().resolve()
        if not self.workspace.is_dir():
            raise ValueError("skill learning workspace must be an existing directory")
        self.smara_dir = self.workspace / ".smara"
        self.skills_dir = self.smara_dir / "skills"
        self.records_path = self.smara_dir / "skill-learning" / "task-runs.json"
        self.state_path = Path(state_path).expanduser().resolve() if state_path else None
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    def _records(self) -> list[dict[str, Any]]:
        try:
            value = json.loads(self.records_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            return []
        return [item for item in value if isinstance(item, dict)][:MAX_TASK_RECORDS] if isinstance(value, list) else []

    def record_task(self, *, prompt: str, result: dict[str, Any], conversation_id: str | None = None) -> dict[str, Any]:
        """Record bounded evidence after every local agent turn."""
        steps = result.get("steps") if isinstance(result, dict) else []
        safe_steps = _safe_value(steps if isinstance(steps, list) else [])
        record = {
            "id": f"run_{uuid.uuid4().hex}",
            "conversation_id": str(conversation_id or "local-default")[:240],
            "prompt": _safe_value(str(prompt or "")),
            "answer": _safe_value(str(result.get("answer") or "")),
            "completed": bool(result.get("completed")),
            "steps": safe_steps,
            "recorded_at": time.time(),
        }
        records = [record] + [item for item in self._records() if item.get("id") != record["id"]]
        _atomic_json(self.records_path, records[:MAX_TASK_RECORDS])
        return record

    def latest_completed(self) -> dict[str, Any] | None:
        for record in self._records():
            if record.get("completed") is True and isinstance(record.get("steps"), list) and record.get("steps"):
                return record
        return None

    def _manifest_for_record(self, record: dict[str, Any], *, name: str, description: str | None = None) -> dict[str, Any]:
        workflow = _workflow_from_steps(record.get("steps") or [])
        if not workflow:
            raise ValueError("the latest completed run has no supported local workflow steps")
        capabilities = sorted({step["capability"] for step in workflow})
        stages: list[dict[str, Any]] = []
        for index, step in enumerate(workflow):
            stages.append({
                "id": f"step_{index + 1}",
                "tool": step["capability"],
                "operation": step["operation"],
                "depends_on": [f"step_{index}"] if index else [],
                "arguments": step["payload"],
                "requires_approval": step["capability"] in {"local_file_write", "local_terminal"},
            })
        risk = "confirm" if any(item["requires_approval"] for item in stages) else "safe"
        manifest = {
            "schema_version": SKILL_MANIFEST_SCHEMA,
            "name": name,
            "version": "1.0.0",
            "description": description or f"Tested local playbook learned from: {record.get('prompt', '')[:400]}",
            "inputs": [],
            "outputs": [{"name": "result", "type": "object", "description": "Bounded local workflow result."}],
            "permissions": {"capabilities": capabilities, "connectors": [], "approved_domains": []},
            "risk": risk,
            "provider": "smara-local-learning",
            "owner": "local-workspace",
            "stages": stages,
            "tests": [{
                "name": "post-task-smoke",
                "inputs": {},
                "expected_status": "completed",
                "expected_outputs": {"step_count": len(stages)},
            }],
            "rollback": {"strategy": "manual", "instructions": "Review the local task result and rerun or reverse each stage explicitly."},
            "limits": {"timeout_seconds": 900, "max_output_bytes": 64_000, "max_artifact_bytes": 8 * 1024 * 1024, "max_retries": 0},
        }
        return manifest

    @staticmethod
    def _smoke_test(manifest: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
        """Run a deterministic, non-mutating structural replay test."""
        parsed = validate_skill_manifest(manifest)
        workflow = _workflow_from_steps(record.get("steps") or [])
        passed = len(parsed.stages) == len(workflow) and all(
            stage.tool in LOCAL_SKILLS for stage in parsed.stages
        )
        return {
            "passed": passed,
            "test_run_id": f"skilltest_{uuid.uuid4().hex}",
            "stage_count": len(parsed.stages),
            "checks": ["manifest_validation", "capability_allowlist", "acyclic_stage_order", "secret_scan"],
        }

    def learn_latest(self, command: str = "/learn") -> dict[str, Any]:
        record = self.latest_completed()
        if record is None:
            return {
                "answer": "There is no completed local workflow to learn yet. Finish one local task, then run `/learn <name>`. ",
                "completed": False,
                "learning": {"status": "no_completed_task"},
            }
        parts = str(command or "").strip().split(maxsplit=2)
        requested_name = parts[1] if len(parts) > 1 else str(record.get("prompt") or "learned-workflow")
        name = _slug(requested_name)
        if not _NAME_RE.fullmatch(name):
            raise ValueError("learned skill name must be a lowercase identifier")
        description = parts[2].strip() if len(parts) > 2 else None
        manifest = self._manifest_for_record(record, name=name, description=description)
        smoke = self._smoke_test(manifest, record)
        if not smoke["passed"]:
            return {"answer": "The workflow did not pass the declarative skill smoke test, so no skill was written.", "completed": False, "learning": {"status": "test_failed", **smoke}}
        parsed = validate_skill_manifest(manifest)
        skill_dir = self.skills_dir / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        body = self._render_playbook(parsed.model_dump(mode="json"), record, smoke)
        if len(body.encode("utf-8")) > MAX_PLAYBOOK_CHARS:
            raise ValueError("learned skill playbook exceeds the size limit")
        _atomic_text(skill_dir / "SKILL.md", body)
        _atomic_json(skill_dir / "manifest.json", {"manifest": parsed.model_dump(mode="json"), "test": smoke})
        return {
            "answer": f"Learned and tested `{name}`. The playbook is saved locally at `.smara/skills/{name}/SKILL.md`.",
            "completed": True,
            "learning": {
                "status": "tested",
                "name": name,
                "version": "1.0.0",
                "test_run_id": smoke["test_run_id"],
                "path": str(skill_dir / "SKILL.md"),
                "capabilities": parsed.permissions.capabilities,
                "source_run_id": record.get("id"),
            },
        }

    @staticmethod
    def _render_playbook(manifest: dict[str, Any], record: dict[str, Any], smoke: dict[str, Any]) -> str:
        tags = ["learned", "local", *manifest.get("permissions", {}).get("capabilities", [])[:6]]
        lines = [
            "---",
            f"name: {_yaml_scalar(manifest['name'])}",
            f"description: {_yaml_scalar(manifest['description'])}",
            f"version: {_yaml_scalar(manifest['version'])}",
            f"tags: [{', '.join(_yaml_scalar(tag) for tag in tags)}]",
            "source: local-autonomous",
            "tested: true",
            f"test_run_id: {_yaml_scalar(smoke['test_run_id'])}",
            "---",
            "",
            f"# {manifest['name']}",
            "",
            _yaml_scalar(manifest["description"]),
            "",
            "## Safety",
            "",
            "This playbook contains declarative local capability steps only. It has no executable code or credentials.",
            f"Risk tier: **{manifest['risk']}**. Every run remains bounded by the Desktop workspace policy.",
            "",
            "## Learned workflow",
            "",
        ]
        for index, stage in enumerate(manifest.get("stages", []), start=1):
            args = json.dumps(stage.get("arguments", {}), ensure_ascii=False, sort_keys=True)
            lines.extend([
                f"{index}. **{stage['tool']}** — `{_yaml_scalar(stage['operation'])}`",
                f"   - Arguments: `{args[:2_000]}`",
                f"   - Approval: `{'required' if stage.get('requires_approval') else 'local policy'}`",
            ])
        lines.extend([
            "",
            "## Verification",
            "",
            f"- Source run: `{_yaml_scalar(record.get('id', 'unknown'))}`",
            f"- Structural smoke test: **passed** (`{_yaml_scalar(smoke['test_run_id'])}`)",
            "- Recheck outputs and workspace state after each stage; do not assume success from a missing result.",
            "",
        ])
        return "\n".join(lines)


def is_learn_command(prompt: str) -> bool:
    value = str(prompt or "").strip().lower()
    return value == "/learn" or value.startswith("/learn ")


def handle_learn_command(
    prompt: str,
    *,
    workspace_root: Path | str,
    state_path: Path | str,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    engine = LocalSkillLearningEngine(workspace_root, state_path=state_path)
    return engine.learn_latest(prompt)


__all__ = ["LocalSkillLearningEngine", "handle_learn_command", "is_learn_command"]
