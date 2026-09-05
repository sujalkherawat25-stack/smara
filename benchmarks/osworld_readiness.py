"""Truthful OSWorld readiness checks for the local Smara runtime.

OSWorld is an external, interactive computer-use evaluation. A local report is
only marked ready when its isolated environment and the required perception and
input adapters are genuinely present; this module never fabricates a score.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any

from .evaluation_core import write_report


class OSWorldReadinessRunner:
    """Validate the prerequisites needed before invoking an external OSWorld run."""

    runner_name = "osworld_readiness_preflight"

    def __init__(self, workspace_root: Path | None = None, osworld_root: Path | None = None):
        self.workspace = (workspace_root or Path.cwd()).resolve()
        configured = os.getenv("SMARA_OSWORLD_ROOT", "").strip()
        self.osworld_root = (osworld_root or (Path(configured) if configured else None))

    def preflight(self) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []

        def check(name: str, ok: bool, detail: str) -> None:
            checks.append({"name": name, "ok": ok, "detail": detail})

        root = self.osworld_root
        check("external_environment_root", root is not None and root.exists(), "Set SMARA_OSWORLD_ROOT to an installed isolated OSWorld checkout.")
        if root and root.exists():
            check("environment_runner", (root / "run.py").exists(), "Expected the evaluation runner at run.py.")
            check("task_assets", any((root / name).exists() for name in ("evaluation_examples", "evaluation_tasks", "tasks")), "Expected downloaded task assets.")
            check("desktop_environment", (root / "desktop_env").exists(), "Expected the desktop environment package.")
        else:
            check("environment_runner", False, "Cannot inspect until the isolated environment root is configured.")
            check("task_assets", False, "Cannot inspect until task assets are installed.")
            check("desktop_environment", False, "Cannot inspect until the desktop environment is installed.")

        check("local_browser_inspection", True, "Smara has bounded local page inspection.")
        check("local_media_inspection", True, "Smara has local document, image, audio, video, and archive inspection routes.")
        # Current public Desktop executor intentionally has no unrestricted
        # pointer/keyboard surface. Do not call it ready until an explicit,
        # sandboxed computer-control adapter is built and registered.
        check("computer_control_adapter", False, "A versioned screenshot, pointer, keyboard, and window-state adapter is not registered yet.")
        check("vision_action_loop", False, "A tested screenshot-to-action loop is required before interactive evaluation can run.")
        check("evaluation_model_config", bool(os.getenv("SMARA_BENCHMARK_MODEL_ENDPOINT") and os.getenv("SMARA_BENCHMARK_MODEL")), "Set the benchmark model endpoint and model name in the environment.")
        check("datasets_package", importlib.util.find_spec("datasets") is not None, "Install the evaluation extra for GAIA dataset access.")

        ready = all(item["ok"] for item in checks)
        report = {
            "runner": self.runner_name,
            "benchmark": "OSWorld",
            "status": "ready_to_invoke_external_environment" if ready else "not_ready",
            "score": None,
            "checks": checks,
            "next_action": (
                "Run the external OSWorld evaluator with its version-aligned environments after every check passes."
                if ready
                else "Resolve every failed check; no OSWorld score is emitted by a readiness check."
            ),
        }
        path = write_report(self.workspace / "reports" / "osworld_readiness.json", report)
        report["report_path"] = str(path)
        return report
