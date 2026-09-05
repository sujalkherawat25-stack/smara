"""Reproducible GAIA evaluation through Smara's shared local runtime.

This runner has no answer registry, no synthetic task substitutions, and no
permissive substring scorer.  It reports unsupported inputs and execution
errors separately from scored wrong answers.
"""
from __future__ import annotations

import os
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .evaluation_core import extract_final_answer, safe_trace, strict_answer_match, write_report


def _load_validation_split(token: str) -> Any:
    try:
        from datasets import load_dataset
    except ImportError as exc:  # pragma: no cover - depends on optional package
        raise RuntimeError("GAIA evaluation requires the optional 'datasets' package. Install smara[test-eval].") from exc
    try:
        return load_dataset("gaia-benchmark/GAIA", "2023_all", token=token or None)["validation"]
    except Exception as exc:  # pragma: no cover - depends on network and dataset access
        raise RuntimeError(
            "The official GAIA dataset could not be loaded. Check Hugging Face access, network policy, and HF_TOKEN; "
            "no benchmark result was recorded."
        ) from exc


def _download_attachment(file_name: str, token: str, cache_root: Path) -> Path | None:
    """Download one benchmark attachment into an isolated task cache."""
    if not file_name:
        return None
    name = Path(file_name).name
    if name != file_name or not name:
        raise RuntimeError("Dataset returned an unsafe attachment name.")
    target = cache_root / name
    if target.exists():
        return target
    cache_root.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "SmaraEvaluation/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"https://huggingface.co/datasets/gaia-benchmark/GAIA/resolve/main/2023/validation/{name}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read(64 * 1024 * 1024 + 1)
    if len(data) > 64 * 1024 * 1024:
        raise RuntimeError("Benchmark attachment exceeds the 64 MB evaluation limit.")
    temporary = target.with_suffix(target.suffix + ".partial")
    temporary.write_bytes(data)
    temporary.replace(target)
    return target


class GaiaFairBenchmark:
    """Runs official GAIA dataset questions using the Desktop/CLI agent loop."""

    runner_name = "gaia_shared_local_runtime_strict"

    def __init__(
        self,
        token: str = "",
        workspace_root: Path | None = None,
        *,
        state_path: Path | None = None,
        dataset_loader: Callable[[str], Any] | None = None,
        turn_runner: Callable[..., dict[str, Any]] | None = None,
    ):
        self.token = token or os.getenv("HF_TOKEN", "")
        self.workspace = (workspace_root or Path.cwd()).resolve()
        self.report_dir = self.workspace / "reports"
        self.cache_dir = self.workspace / "data" / "gaia_files"
        self.dataset_loader = dataset_loader or _load_validation_split
        self.turn_runner = turn_runner
        if state_path is None:
            from smara.desktop_executor import default_state_path
            state_path = default_state_path()
        self.state_path = Path(state_path)

    @staticmethod
    def _model_config() -> Any:
        from smara.local_agent_runtime import LocalModelConfig
        endpoint = os.getenv("SMARA_BENCHMARK_MODEL_ENDPOINT", "").strip()
        model = os.getenv("SMARA_BENCHMARK_MODEL", "").strip()
        api_key = os.getenv("SMARA_BENCHMARK_API_KEY", "")
        auth_header = os.getenv("SMARA_BENCHMARK_AUTH_HEADER", "authorization").strip().lower()
        if auth_header not in {"authorization", "api-subscription-key"}:
            raise RuntimeError("SMARA_BENCHMARK_AUTH_HEADER must be authorization or api-subscription-key.")
        if not endpoint or not model:
            raise RuntimeError(
                "Set SMARA_BENCHMARK_MODEL_ENDPOINT and SMARA_BENCHMARK_MODEL before a GAIA run. "
                "The fair runner never reads or prints Desktop credentials."
            )
        return LocalModelConfig(
            base_url=endpoint,
            model=model,
            api_key=api_key,
            auth_header=auth_header,
            label="benchmark model",
            timeout_seconds=float(os.getenv("SMARA_BENCHMARK_TIMEOUT_SECONDS", "300")),
            max_tokens=16_384,
        )

    @staticmethod
    def _prompt(question: str, attachment: Path | None) -> str:
        suffix = "\n\nReturn `FINAL ANSWER: <answer>` with only the answer required by the task."
        if attachment is None:
            return question + suffix
        return (
            f"{question}\n\nA benchmark attachment is available only at this local path: {attachment}. "
            "Use an appropriate local read or media capability to inspect it. Do not assume its contents."
            + suffix
        )

    def _run_task(self, task: dict[str, Any], *, level: str, config: Any) -> dict[str, Any]:
        task_id = str(task.get("task_id") or "")
        question = str(task.get("Question") or "")
        expected = str(task.get("Final answer") or "")
        file_name = str(task.get("file_name") or "")
        started = time.monotonic()
        attachment: Path | None = None
        try:
            attachment = _download_attachment(file_name, self.token, self.cache_dir / task_id) if file_name else None
            if self.turn_runner is None:
                from smara.local_agent_runtime import run_shared_local_turn
                result = run_shared_local_turn(
                    prompt=self._prompt(question, attachment),
                    state_path=self.state_path,
                    config=config,
                    max_steps=20,
                )
            else:
                result = self.turn_runner(
                    prompt=self._prompt(question, attachment), state_path=self.state_path, config=config, max_steps=20
                )
            answer = str(result.get("answer") or "")
            extracted = extract_final_answer(answer)
            completed = bool(result.get("completed"))
            outcome = "scored" if completed else "execution_error"
            correct = strict_answer_match(extracted, expected) if outcome == "scored" else False
            failure_reason = result.get("failure_reason") if not completed else None
        except Exception as exc:
            answer = ""
            extracted = ""
            result = {"steps": []}
            outcome = "execution_error"
            correct = False
            failure_reason = f"{type(exc).__name__}: {exc}"
        return {
            "task_id": task_id,
            "level": str(level),
            "question": question,
            "expected_answer": expected,
            "answer": answer,
            "extracted_answer": extracted,
            "correct": correct,
            "outcome": outcome,
            "failure_reason": failure_reason,
            "attachment": {"name": file_name, "path": str(attachment) if attachment else None},
            "trace": safe_trace(list(result.get("steps") or [])),
            "duration_seconds": round(time.monotonic() - started, 3),
        }

    def evaluate_level(self, level: str = "1", start_idx: int = 0, max_tasks: int | None = None) -> dict[str, Any]:
        validation = self.dataset_loader(self.token)
        tasks = [dict(item) for item in validation if str(item.get("Level")) == str(level)]
        selected = tasks[start_idx : start_idx + max_tasks if max_tasks is not None else None]
        # Fail before executing a task when the model configuration is absent;
        # an all-error report is not a meaningful benchmark attempt.
        config = self._model_config()
        results = [self._run_task(task, level=str(level), config=config) for task in selected]
        scored = [item for item in results if item["outcome"] == "scored"]
        correct = sum(1 for item in scored if item["correct"])
        report = {
            "runner": self.runner_name,
            "dataset": "gaia-benchmark/GAIA",
            "split": "validation",
            "level": str(level),
            "scoring": "strict normalized exact equality; no substring or answer-registry fallback",
            "total_selected": len(results),
            "total_scored": len(scored),
            "execution_errors": sum(1 for item in results if item["outcome"] == "execution_error"),
            "correct": correct,
            "incorrect": len(scored) - correct,
            "accuracy_percent": round((correct / len(scored)) * 100, 2) if scored else 0.0,
            "results": results,
        }
        report_path = write_report(self.report_dir / f"gaia_fair_level{level}_results.json", report)
        report["report_path"] = str(report_path)
        return report


# Compatibility import for existing automation.  The report now identifies the
# stricter runner and must not be compared with legacy permissive numbers.
GaiaOfficialBenchmark = GaiaFairBenchmark
