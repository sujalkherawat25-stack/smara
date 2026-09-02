"""Validation and expansion for hosted-requested local workflows.

The hosted agent remains the planner.  A workflow is only a bounded, explicit
task graph handed to the paired desktop for execution; it is not a second
local model loop.  Keeping the graph contract here makes every stage reviewable
before the user approves the child task and prevents an untrusted model output
from smuggling in arbitrary executor capabilities.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .local_agent import LOCAL_SKILLS
from .workspace_contract import validate_workspace_job


MAX_WORKFLOW_STAGES = 16
MAX_STAGE_BYTES = 64 * 1024
MAX_WORKFLOW_BYTES = 256 * 1024
_STAGE_NAME = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
_STAGE_ORDER = {name: index for index, name in enumerate(("inspect", "plan", "edit", "run", "verify", "report"))}
_ALLOWED_KEYS = {"stage", "capability", "payload"}
_SECRET_FIELDS = {"api_key", "access_token", "refresh_token", "password", "secret", "secret_value", "authorization", "private_key"}


def _contains_secret_field(value: Any) -> bool:
    if isinstance(value, dict):
        if any(str(key).strip().lower() in _SECRET_FIELDS for key in value):
            return True
        return any(_contains_secret_field(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_secret_field(item) for item in value)
    return False


def validate_workflow(stages: Any) -> list[dict[str, Any]]:
    """Validate and normalize a sequential inspect-to-report graph with repair loop support.

    Dependencies are generated from the order supplied by the hosted planner,
    so callers cannot create cycles. Capability-specific payload checks still happen in
    the desktop executor under its normal allowlist and approval gates.
    """
    if not isinstance(stages, list) or not 2 <= len(stages) <= MAX_WORKFLOW_STAGES:
        raise ValueError(f"A local workflow needs 2-{MAX_WORKFLOW_STAGES} stages.")
    normalized: list[dict[str, Any]] = []
    total_bytes = 0
    for index, item in enumerate(stages):
        if not isinstance(item, dict) or set(item) != _ALLOWED_KEYS:
            raise ValueError("Each workflow stage must contain only stage, capability, and payload.")
        stage = item.get("stage")
        capability = item.get("capability")
        payload = item.get("payload")
        if not isinstance(stage, str) or not _STAGE_NAME.fullmatch(stage) or stage not in _STAGE_ORDER:
            raise ValueError("Workflow stage names must be inspect, plan, edit, run, verify, or report.")
        if index == 0 and stage not in {"inspect", "plan"}:
            raise ValueError("Workflows must start with an inspect or plan stage.")
        if not isinstance(capability, str) or capability not in LOCAL_SKILLS:
            raise ValueError("Workflow stage capability is not an installed local skill.")
        if not isinstance(payload, dict):
            raise ValueError("Workflow stage payload must be an object.")
        if "workflow" in payload:
            raise ValueError("Nested local workflows are not allowed.")
        if _contains_secret_field(payload):
            raise ValueError("Local workflow payloads cannot carry credentials; use a local credential alias.")
        if "workspace_job" in payload:
            try:
                job = validate_workspace_job(payload["workspace_job"])
            except RuntimeError as exc:
                raise ValueError(str(exc)) from exc
            if capability not in job.allowed_capabilities:
                raise ValueError("workspace_job does not allow the workflow stage capability.")
        try:
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("Workflow stage payload must be JSON serializable.") from exc
        if len(encoded) > MAX_STAGE_BYTES:
            raise ValueError("Workflow stage payload exceeds the 64 KB limit.")
        total_bytes += len(encoded)
        if total_bytes > MAX_WORKFLOW_BYTES:
            raise ValueError("Workflow payload exceeds the 256 KB limit.")
        normalized.append({
            "stage": stage,
            "capability": capability,
            "payload": payload,
            "depends_on": [index - 1] if index else [],
        })
    return normalized


def workflow_summary(stages: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a secret-free summary suitable for task events and UI previews."""
    return {
        "stage_count": len(stages),
        "stages": [
            {"stage": item["stage"], "capability": item["capability"]}
            for item in stages
        ],
    }
