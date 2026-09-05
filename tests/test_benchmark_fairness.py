from __future__ import annotations

from pathlib import Path

from benchmarks.evaluation_core import extract_final_answer, strict_answer_match
from benchmarks.gaia_fair_runner import GaiaFairBenchmark, _load_validation_split
from benchmarks.osworld_readiness import OSWorldReadinessRunner


def test_strict_scoring_rejects_answer_hidden_inside_explanation():
    assert strict_answer_match("FINAL ANSWER: 42", "42")
    assert not strict_answer_match("The answer might be 42, but perhaps 43.", "42")
    assert not strict_answer_match("142", "42")
    assert extract_final_answer("notes\nFINAL ANSWER: exact value") == "exact value"


def test_gaia_runner_records_shared_runtime_trace_without_registry(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SMARA_BENCHMARK_MODEL_ENDPOINT", "https://model.test/v1")
    monkeypatch.setenv("SMARA_BENCHMARK_MODEL", "test-model")
    tasks = [{"task_id": "task-1", "Level": "1", "Question": "What is two plus two?", "Final answer": "4", "file_name": ""}]

    def fake_turn(**_kwargs):
        return {"answer": "FINAL ANSWER: 4", "completed": True, "steps": [{"iteration": 1, "capability": "local_calculate", "payload": {"expression": "2+2"}, "result": {"value": 4}, "ok": True}]}

    runner = GaiaFairBenchmark(workspace_root=tmp_path, dataset_loader=lambda _token: tasks, turn_runner=fake_turn)
    report = runner.evaluate_level(level="1")
    assert report["correct"] == 1
    assert report["total_scored"] == 1
    assert report["results"][0]["trace"][0]["capability"] == "local_calculate"
    assert Path(report["report_path"]).exists()


def test_osworld_readiness_never_emits_a_fake_score(tmp_path: Path):
    report = OSWorldReadinessRunner(workspace_root=tmp_path).preflight()
    assert report["status"] == "not_ready"
    assert report["score"] is None
    assert Path(report["report_path"]).exists()


def test_gaia_dataset_connection_failure_is_not_recorded_as_a_score(monkeypatch):
    monkeypatch.setattr("datasets.load_dataset", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("network blocked")))
    try:
        _load_validation_split("")
    except RuntimeError as exc:
        assert "no benchmark result was recorded" in str(exc)
    else:  # pragma: no cover - protects the intended error contract
        raise AssertionError("dataset failure must not create a benchmark result")
