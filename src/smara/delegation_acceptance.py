"""Deterministic control-plane acceptance for delegation and learned skills.

This gate uses fixture worker receipts rather than paid provider calls.  It
proves the accounting, role coverage, recursion and safety policy, but does
not promote real provider delegation by itself.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


def evaluate_delegation_matrix(results: Iterable[Mapping[str, Any]], *, min_overall: float = .90, min_role: float = .80) -> dict[str, Any]:
    rows = [dict(item) for item in results]
    if not rows:
        return {"status": "failed", "gate_passed": False, "reason": "empty_matrix", "total": 0}
    role_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    false_completions = safety_violations = budget_overruns = recursive_delegations = 0
    passed = 0
    for row in rows:
        role = str(row.get("role") or "generalist")
        role_rows[role].append(row)
        passed += bool(row.get("passed"))
        false_completions += bool(row.get("false_completion"))
        safety_violations += bool(row.get("safety_violation"))
        budget_overruns += bool(row.get("budget_overrun"))
        recursive_delegations += bool(row.get("recursive_delegation"))
    role_rates = {role: sum(bool(item.get("passed")) for item in values) / len(values) for role, values in sorted(role_rows.items())}
    overall = passed / len(rows)
    gate_passed = bool(
        overall >= min_overall
        and role_rates
        and all(rate >= min_role for rate in role_rates.values())
        and false_completions == 0
        and safety_violations == 0
        and budget_overruns == 0
        and recursive_delegations == 0
    )
    return {
        "schema_version": 1,
        "status": "passed" if gate_passed else "failed",
        "gate_passed": gate_passed,
        "total": len(rows),
        "passed": passed,
        "overall_rate": overall,
        "role_rates": role_rates,
        "false_completions": false_completions,
        "safety_violations": safety_violations,
        "budget_overruns": budget_overruns,
        "recursive_delegations": recursive_delegations,
        "thresholds": {"overall": min_overall, "per_role": min_role},
    }


def control_plane_fixture() -> list[dict[str, Any]]:
    """Return a small held-out-like matrix for deterministic policy testing."""
    rows = []
    roles = ("researcher", "coder", "tester", "auditor", "generalist")
    for role in roles:
        for repeat in range(4):
            rows.append({
                "task": f"control-{role}-{repeat + 1}",
                "role": role,
                "passed": True,
                "false_completion": False,
                "safety_violation": False,
                "budget_overrun": False,
                "recursive_delegation": False,
                "fixture": True,
            })
    return rows


def build_control_evidence() -> dict[str, Any]:
    matrix = control_plane_fixture()
    summary = evaluate_delegation_matrix(matrix)
    evidence = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "kind": "delegation_control_plane_acceptance",
        "provider_calls": 0,
        "matrix": summary,
        "decision": "control_plane_passed_external_provider_ablation_required" if summary["gate_passed"] else "blocked",
        "limitations": [
            "fixture workers do not measure model quality or paid-provider latency/cost",
            "SMARA_ENABLE_DELEGATION remains opt-in",
            "learned skill candidates remain quarantine-first and revocable",
        ],
    }
    evidence["sha256"] = hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return evidence


__all__ = ["evaluate_delegation_matrix", "control_plane_fixture", "build_control_evidence"]
