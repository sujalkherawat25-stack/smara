"""Non-scoring readiness inspection for an existing GAIA dataset cache.

This module never invokes a model, downloads data, or computes an accuracy
score.  It only validates that the task export and its attachments are ready
for a later fair benchmark run.
"""
from __future__ import annotations

import os
from collections import Counter
from pathlib import Path
from typing import Any

from .evaluation_core import write_report
from .gaia_fair_runner import _load_local_validation_split


def discover_cached_dataset(workspace_root: Path) -> Path | None:
    """Find a complete local GAIA snapshot without contacting Hugging Face."""
    configured = os.getenv("SMARA_GAIA_DATASET", "").strip()
    if configured:
        return Path(configured).expanduser()
    candidates = [
        workspace_root / "data" / "gaia_dataset",
        workspace_root / "data" / "gaia",
        Path.home() / ".cache" / "huggingface" / "hub" / "datasets--gaia-benchmark--GAIA" / "snapshots",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


class GaiaReadiness:
    """Inspect local task metadata and attachment coverage only."""

    def __init__(self, workspace_root: Path | None = None, dataset_path: Path | None = None):
        self.workspace = (workspace_root or Path.cwd()).resolve()
        self.dataset_path = dataset_path or discover_cached_dataset(self.workspace)

    def inspect(self) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []

        def check(name: str, ok: bool, detail: str) -> None:
            checks.append({"name": name, "ok": bool(ok), "detail": detail})

        if self.dataset_path is None:
            check("local_dataset_path", False, "Set SMARA_GAIA_DATASET or place a GAIA snapshot under data/gaia_dataset.")
            rows: list[dict[str, Any]] = []
            source = None
        else:
            source = self.dataset_path.expanduser().resolve()
            try:
                rows = _load_local_validation_split(source)
                check("local_dataset_path", True, str(source))
                check("task_schema", True, f"Validated {len(rows)} official task rows.")
            except Exception as exc:
                rows = []
                check("local_dataset_path", False, str(exc))
                check("task_schema", False, "No task rows were accepted; no benchmark can start.")

        by_level = Counter(str(row.get("Level")) for row in rows)
        expected_attachments = [str(row.get("file_name") or "").strip() for row in rows if str(row.get("file_name") or "").strip()]
        cache_root = self.workspace / "data" / "gaia_files"
        missing: list[str] = []
        attachments_by_level: dict[str, dict[str, int]] = {}
        for name in expected_attachments:
            if not (cache_root / name).is_file():
                missing.append(name)
        for level in ("1", "2", "3"):
            level_rows = [row for row in rows if str(row.get("Level")) == level and str(row.get("file_name") or "").strip()]
            level_missing = [str(row.get("file_name")) for row in level_rows if not (cache_root / str(row.get("file_name"))).is_file()]
            attachments_by_level[level] = {"referenced": len(level_rows), "present": len(level_rows) - len(level_missing), "missing": len(level_missing)}
        check(
            "attachment_cache",
            not missing,
            f"{len(expected_attachments) - len(missing)}/{len(expected_attachments)} attachments present in {cache_root}."
            if expected_attachments
            else "No attachments referenced by the validated rows.",
        )
        model_ready = bool(os.getenv("SMARA_BENCHMARK_MODEL_ENDPOINT", "").strip() and os.getenv("SMARA_BENCHMARK_MODEL", "").strip())
        check(
            "benchmark_model_config",
            model_ready,
            "Model endpoint and model name are configured (credentials are not displayed)."
            if model_ready
            else "Set SMARA_BENCHMARK_MODEL_ENDPOINT and SMARA_BENCHMARK_MODEL (credentials are never printed).",
        )
        report: dict[str, Any] = {
            "runner": "gaia_local_readiness",
            "benchmark": "GAIA",
            "score": None,
            "status": "ready_to_invoke_fair_runner" if all(item["ok"] for item in checks) else "not_ready",
            "dataset_path": str(source) if source else None,
            "task_count": len(rows),
            "tasks_by_level": dict(sorted(by_level.items())),
            "attachment_count": len(expected_attachments),
            "attachments_by_level": attachments_by_level,
            "missing_attachments": missing,
            "checks": checks,
            "next_action": "Run the fair GAIA command only after reviewing this manifest; this inspection did not run tasks or contact the network.",
        }
        path = write_report(self.workspace / "reports" / "gaia_readiness.json", report)
        report["report_path"] = str(path)
        return report
