"""Compatibility entry point for a real GAIA evaluation.

The old GAIA-Desktop harness generated synthetic tasks and a pre-written PDF.
Desktop/CLI callers now share the strict :mod:`gaia_fair_runner` implementation
and the official validation data. This module only preserves the old import and
summary field names used by existing automation.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from .gaia_fair_runner import GaiaFairBenchmark


class GaiaDesktopBenchmark:
    """Run strict GAIA tasks through the same local runtime as Desktop."""

    def __init__(self, workspace_root: Path | None = None, dataset_path: Path | None = None):
        self.workspace = (workspace_root or Path.cwd()).resolve()
        self.dataset_path = dataset_path

    def run_all(self, *, level: str | None = None, max_tasks: int | None = None) -> dict[str, Any]:
        started = time.monotonic()
        selected_level = str(level or os.getenv("SMARA_GAIA_DESKTOP_LEVEL", "1"))
        report = GaiaFairBenchmark(
            workspace_root=self.workspace,
            dataset_path=self.dataset_path,
        ).evaluate_level(level=selected_level, max_tasks=max_tasks)
        scored = [item for item in report["results"] if item["outcome"] == "scored"]
        passed = sum(1 for item in scored if item["correct"])
        total = len(report["results"])
        return {
            "runner": report["runner"],
            "dataset": report["dataset"],
            "level": selected_level,
            "total_tasks": total,
            "total_duration_seconds": round(time.monotonic() - started, 3),
            "total_scored": len(scored),
            "passed": passed,
            "failed": total - passed,
            "execution_errors": report["execution_errors"],
            "pass_rate_percent": round((passed / len(scored)) * 100, 2) if scored else 0.0,
            "score": None if report["execution_errors"] else passed,
            "results": report["results"],
            "report_path": report["report_path"],
        }


if __name__ == "__main__":
    result = GaiaDesktopBenchmark().run_all()
    print(result["report_path"])
