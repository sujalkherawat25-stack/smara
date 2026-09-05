"""Reproducible GAIA evaluation through Smara's shared local runtime.

This runner has no answer registry, no synthetic task substitutions, and no
permissive substring scorer.  It reports unsupported inputs and execution
errors separately from scored wrong answers.
"""
from __future__ import annotations

import os
import time
import urllib.request
import csv
import json
from pathlib import Path
from typing import Any, Callable, Iterable

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


def _rows_from_json(path: Path) -> list[dict[str, Any]]:
    """Read a task export without treating reports/results as a dataset."""
    if path.suffix.lower() == ".jsonl":
        rows: list[dict[str, Any]] = []
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise RuntimeError(f"GAIA JSONL line {line_no} is not an object.")
            rows.append(value)
        return rows
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("validation", "data", "tasks", "examples"):
            candidate = value.get(key)
            if isinstance(candidate, list):
                return [item for item in candidate if isinstance(item, dict)]
    raise RuntimeError("GAIA JSON must contain a list of task objects (or validation/data/tasks/examples).")


def _validate_local_rows(rows: Iterable[dict[str, Any]], source: Path) -> list[dict[str, Any]]:
    """Validate the official task shape before any model or attachment work."""
    validated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(rows, 1):
        row = dict(raw)
        task_id = str(row.get("task_id") or "").strip()
        question = str(row.get("Question") or "").strip()
        expected = str(row.get("Final answer") or "").strip()
        if not task_id or not question or not expected:
            raise RuntimeError(
                f"GAIA dataset {source} row {index} is missing task_id, Question, or Final answer. "
                "Legacy result reports are not accepted as datasets."
            )
        if task_id in seen:
            raise RuntimeError(f"GAIA dataset {source} contains duplicate task_id {task_id!r}.")
        seen.add(task_id)
        level = str(row.get("Level") or "").strip()
        if level not in {"1", "2", "3"}:
            raise RuntimeError(f"GAIA dataset {source} row {index} has invalid Level {level!r}.")
        row["task_id"] = task_id
        row["Question"] = question
        row["Final answer"] = expected
        row["Level"] = level
        validated.append(row)
    if not validated:
        raise RuntimeError(f"GAIA dataset {source} contains no task rows.")
    return validated


def _load_local_validation_split(dataset_path: Path) -> list[dict[str, Any]]:
    """Load a previously downloaded GAIA export, with zero network access."""
    source = dataset_path.expanduser().resolve()
    if not source.exists():
        raise RuntimeError(f"Configured local GAIA dataset does not exist: {source}")
    if source.is_dir():
        candidates = list(source.rglob("metadata.parquet"))
        if not candidates:
            candidates = list(source.rglob("*.jsonl")) + list(source.rglob("*.json"))
        if not candidates:
            raise RuntimeError(f"No metadata.parquet, JSON, or JSONL task export found under {source}.")
        # Prefer the validation metadata when a full snapshot contains test too.
        candidates.sort(key=lambda item: ("validation" not in str(item).lower(), len(str(item))))
        source = candidates[0]
    suffix = source.suffix.lower()
    if suffix in {".json", ".jsonl"}:
        rows = _rows_from_json(source)
    elif suffix == ".csv":
        with source.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    elif suffix == ".parquet":
        try:
            import pyarrow.parquet as parquet
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("Reading local GAIA parquet requires pyarrow (install smara[test-eval]).") from exc
        try:
            rows = parquet.read_table(source).to_pylist()
        except Exception as exc:  # pragma: no cover - depends on local optional stack
            raise RuntimeError(f"Could not read local GAIA parquet {source}: {exc}") from exc
    else:
        raise RuntimeError("Local GAIA dataset must be a metadata.parquet, JSON, JSONL, or CSV export.")
    return _validate_local_rows(rows, source)


def _discover_cached_dataset(workspace_root: Path) -> Path | None:
    """Select an existing local snapshot before falling back to network mode."""
    configured = os.getenv("SMARA_GAIA_DATASET", "").strip()
    if configured:
        return Path(configured).expanduser()
    candidates = (
        workspace_root / "data" / "gaia_dataset",
        workspace_root / "data" / "gaia",
        Path.home() / ".cache" / "huggingface" / "hub" / "datasets--gaia-benchmark--GAIA" / "snapshots",
    )
    return next((candidate for candidate in candidates if candidate.exists()), None)


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
        dataset_path: Path | None = None,
    ):
        self.token = token or os.getenv("HF_TOKEN", "")
        self.workspace = (workspace_root or Path.cwd()).resolve()
        self.report_dir = self.workspace / "reports"
        self.cache_dir = self.workspace / "data" / "gaia_files"
        self.dataset_loader = dataset_loader or _load_validation_split
        self.turn_runner = turn_runner
        # A supplied loader is primarily used by callers with a controlled
        # fixture; do not silently replace it with the host's global cache.
        self.dataset_path = (
            Path(dataset_path).expanduser()
            if dataset_path is not None
            else (None if dataset_loader is not None else _discover_cached_dataset(self.workspace))
        )
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
            attachment = self._resolve_attachment(task, task_id, file_name)
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

    def _resolve_attachment(self, task: dict[str, Any], task_id: str, file_name: str) -> Path | None:
        """Prefer local dataset attachment paths; download only for official online mode."""
        if not file_name:
            return None
        file_path = str(task.get("file_path") or "").strip()
        candidates: list[Path] = []
        if file_path:
            path = Path(file_path).expanduser()
            if path.is_absolute():
                candidates.append(path)
            elif self.dataset_path:
                candidates.extend((self.dataset_path.parent / path, self.dataset_path / path))
        candidates.extend((self.cache_dir / file_name, self.cache_dir / task_id / file_name))
        if self.dataset_path and self.dataset_path.is_dir():
            # A Hugging Face snapshot may keep attachments beside metadata or
            # in a nested validation directory.  Resolve by basename only.
            candidates.extend(self.dataset_path.rglob(file_name))
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
        if self.dataset_path:
            raise RuntimeError(f"Attachment {file_name!r} for task {task_id} is missing from the local GAIA cache.")
        return _download_attachment(file_name, self.token, self.cache_dir / task_id)

    def evaluate_level(self, level: str = "1", start_idx: int = 0, max_tasks: int | None = None) -> dict[str, Any]:
        validation = (
            _load_local_validation_split(self.dataset_path)
            if self.dataset_path is not None
            else self.dataset_loader(self.token)
        )
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
            "dataset": str(self.dataset_path) if self.dataset_path is not None else "gaia-benchmark/GAIA",
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
