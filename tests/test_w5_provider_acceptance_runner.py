"""Unit tests for Phase A auditable acceptance runner."""
import json
import time
from pathlib import Path
import pytest

from scripts.run_w5_provider_acceptance import (
    build_run_identity,
    compute_category_totals,
    run_acceptance,
    sanitize_evidence_record,
    OUTPUT_RUPEES_PER_M,
    save_atomic,
)


def test_atomic_evidence_save_retries_transient_windows_lock(tmp_path, monkeypatch):
    target = tmp_path / "evidence.json"
    original_replace = Path.replace
    attempts = []

    def locked_twice(source, destination):
        attempts.append(source)
        if len(attempts) < 3:
            raise PermissionError(13, "sharing violation")
        return original_replace(source, destination)

    monkeypatch.setattr(Path, "replace", locked_twice)
    monkeypatch.setattr("scripts.run_w5_provider_acceptance.time.sleep", lambda _seconds: None)

    save_atomic(target, {"status": "complete"})

    assert len(attempts) == 3
    assert json.loads(target.read_text(encoding="utf-8")) == {"status": "complete"}


@pytest.fixture
def mock_pack_and_refs(tmp_path):
    pack_path = tmp_path / "manifest.json"
    ref_path = tmp_path / "references.json"

    pack_data = {
        "version": 1,
        "suite": "test-suite",
        "repetitions": 2,
        "tasks": [
            {"id": "T-R01", "category": "research", "input": {"evidence": "fact", "claim": "fact"}, "validator": "claim_state"},
            {"id": "T-L01", "category": "local", "input": {"kind": "json", "value": 42}, "validator": "json_exact"},
        ]
    }
    ref_data = {
        "version": 1,
        "answers": {
            "T-R01": "supported",
            "T-L01": True
        }
    }
    pack_path.write_text(json.dumps(pack_data), encoding="utf-8")
    ref_path.write_text(json.dumps(ref_data), encoding="utf-8")
    return pack_path, ref_path, pack_data


def test_resume_retains_failed_attempt_and_does_not_reexecute(tmp_path, mock_pack_and_refs, monkeypatch):
    pack_path, ref_path, pack_data = mock_pack_and_refs
    evidence_path = tmp_path / "evidence.json"

    initial_report = build_run_identity(tmp_path, pack_path, ref_path)
    failed_attempt = {
        "case": "T-R01",
        "category": "research",
        "repeat": 1,
        "attempt_id": "T-R01-r1",
        "status": "failed",
        "completed": False,
        "validated": False,
        "safety_violation": False,
        "passed": False,
        "usage": {"billed_tokens": 100},
    }
    initial_report["runs"] = [failed_attempt]
    evidence_path.write_text(json.dumps(initial_report), encoding="utf-8")

    executed_keys = []
    def mock_execute(item, repeat, key, base_url="", model=""):
        executed_keys.append((item["id"], repeat))
        return {
            "case": item["id"],
            "category": item["category"],
            "repeat": repeat,
            "attempt_id": f"{item['id']}-r{repeat}",
            "status": "completed",
            "completed": True,
            "validated": True,
            "safety_violation": False,
            "passed": True,
            "usage": {"billed_tokens": 200},
        }
    monkeypatch.setattr("scripts.run_w5_provider_acceptance.execute", mock_execute)

    report, exit_code = run_acceptance(
        key="test-key",
        pack_path=pack_path,
        ref_path=ref_path,
        evidence_path=evidence_path,
        resume=True,
        repo_root=tmp_path,
    )

    assert ("T-R01", 1) not in executed_keys
    assert set(executed_keys) == {("T-L01", 1), ("T-R01", 2), ("T-L01", 2)}
    assert len(report["runs"]) == 4
    first_run = report["runs"][0]
    assert first_run["case"] == "T-R01" and first_run["repeat"] == 1 and first_run["passed"] is False


def test_interrupted_or_missing_attempt_resumes_exactly_once(tmp_path, mock_pack_and_refs, monkeypatch):
    pack_path, ref_path, pack_data = mock_pack_and_refs
    evidence_path = tmp_path / "evidence.json"

    initial_report = build_run_identity(tmp_path, pack_path, ref_path)
    initial_report["runs"] = [
        {"case": "T-R01", "category": "research", "repeat": 1, "passed": True, "usage": {"billed_tokens": 100}},
        {"case": "T-L01", "category": "local", "repeat": 1, "passed": True, "usage": {"billed_tokens": 100}},
    ]
    evidence_path.write_text(json.dumps(initial_report), encoding="utf-8")

    executed_keys = []
    def mock_execute(item, repeat, key, base_url="", model=""):
        executed_keys.append((item["id"], repeat))
        return {
            "case": item["id"],
            "category": item["category"],
            "repeat": repeat,
            "attempt_id": f"{item['id']}-r{repeat}",
            "passed": True,
            "completed": True,
            "validated": True,
            "usage": {"billed_tokens": 100},
        }
    monkeypatch.setattr("scripts.run_w5_provider_acceptance.execute", mock_execute)

    report, exit_code = run_acceptance(
        key="test-key",
        pack_path=pack_path,
        ref_path=ref_path,
        evidence_path=evidence_path,
        resume=True,
        repo_root=tmp_path,
    )

    assert executed_keys == [("T-R01", 2), ("T-L01", 2)]
    assert len(report["runs"]) == 4


def test_duplicate_attempt_id_is_rejected(mock_pack_and_refs):
    _, _, pack_data = mock_pack_and_refs
    runs = [
        {"case": "T-R01", "category": "research", "repeat": 1, "passed": True},
        {"case": "T-R01", "category": "research", "repeat": 1, "passed": False},
    ]
    with pytest.raises(ValueError, match="Duplicate attempt found"):
        compute_category_totals(pack_data, runs, "running", 10.0)


def test_all_72_expected_pairs_are_required_for_complete_matrix():
    pack = {
        "repetitions": 3,
        "tasks": [{"id": f"T-{i:02d}", "category": "research"} for i in range(24)]
    }
    runs = [
        {"case": f"T-{i:02d}", "category": "research", "repeat": r, "passed": True, "completed": True, "validated": True}
        for r in range(1, 4)
        for i in range(24)
    ][:-1]
    assert len(runs) == 71

    summary = compute_category_totals(pack, runs, "complete", 50.0)
    assert summary["gate_passed"] is False
    assert summary["attempted"] == 71
    assert summary["total_expected"] == 72


def test_category_arithmetic_and_thresholds_are_exact():
    pack = {
        "repetitions": 3,
        "tasks": [
            {"id": "R01", "category": "research"},
            {"id": "R02", "category": "research"},
            {"id": "L01", "category": "local"},
        ]
    }
    runs = [
        {"case": "R01", "category": "research", "repeat": 1, "passed": True, "completed": True, "validated": True},
        {"case": "R01", "category": "research", "repeat": 2, "passed": True, "completed": True, "validated": True},
        {"case": "R01", "category": "research", "repeat": 3, "passed": True, "completed": True, "validated": True},
        {"case": "R02", "category": "research", "repeat": 1, "passed": True, "completed": True, "validated": True},
        {"case": "R02", "category": "research", "repeat": 2, "passed": True, "completed": True, "validated": True},
        {"case": "R02", "category": "research", "repeat": 3, "passed": False, "completed": False, "validated": False},
        {"case": "L01", "category": "local", "repeat": 1, "passed": True, "completed": True, "validated": True},
        {"case": "L01", "category": "local", "repeat": 2, "passed": True, "completed": True, "validated": True},
        {"case": "L01", "category": "local", "repeat": 3, "passed": False, "completed": False, "validated": False},
    ]
    summary = compute_category_totals(pack, runs, "complete", 20.0)
    assert summary["categories"]["research"]["passed"] == 5
    assert summary["categories"]["research"]["rate"] == pytest.approx(5/6, 0.001)
    assert summary["categories"]["research"]["threshold_passed"] is True

    assert summary["categories"]["local"]["passed"] == 2
    assert summary["categories"]["local"]["rate"] == pytest.approx(2/3, 0.001)
    assert summary["categories"]["local"]["threshold_passed"] is False
    assert summary["gate_passed"] is False


def test_one_safety_violation_forces_gate_failure_even_at_72_of_72():
    pack = {
        "repetitions": 3,
        "tasks": [{"id": f"T-{i:02d}", "category": "research"} for i in range(24)]
    }
    runs = [
        {
            "case": f"T-{i:02d}",
            "category": "research",
            "repeat": r,
            "passed": True,
            "completed": True,
            "validated": True,
            "safety_violation": (i == 0 and r == 1),
        }
        for r in range(1, 4)
        for i in range(24)
    ]
    summary = compute_category_totals(pack, runs, "complete", 50.0)
    assert summary["attempted"] == 72
    assert summary["safety"]["violations_count"] == 1
    assert summary["safety"]["passed"] is False
    assert summary["gate_passed"] is False


def test_cost_and_time_caps_stop_before_admitting_another_call(tmp_path, mock_pack_and_refs, monkeypatch):
    pack_path, ref_path, pack_data = mock_pack_and_refs
    evidence_path = tmp_path / "evidence.json"

    call_count = 0
    def mock_execute(item, repeat, key, base_url="", model=""):
        nonlocal call_count
        call_count += 1
        return {
            "case": item["id"],
            "category": item["category"],
            "repeat": repeat,
            "attempt_id": f"{item['id']}-r{repeat}",
            "passed": True,
            "completed": True,
            "validated": True,
            "usage": {"billed_tokens": 4_000_000},
        }
    monkeypatch.setattr("scripts.run_w5_provider_acceptance.execute", mock_execute)

    report, exit_code = run_acceptance(
        key="test-key",
        pack_path=pack_path,
        ref_path=ref_path,
        evidence_path=evidence_path,
        max_rupees=150.0,
        repo_root=tmp_path,
    )

    assert call_count == 1
    assert report["terminal_state"] == "cost_limit"


def test_evidence_contains_no_supplied_sentinel_secret(tmp_path, mock_pack_and_refs, monkeypatch):
    pack_path, ref_path, pack_data = mock_pack_and_refs
    evidence_path = tmp_path / "evidence.json"
    secret_sentinel = "SECRET_KEY_SENTINEL_XYZ_987654321"

    def mock_execute(item, repeat, key, base_url="", model=""):
        return {
            "case": item["id"],
            "category": item["category"],
            "repeat": repeat,
            "attempt_id": f"{item['id']}-r{repeat}",
            "passed": True,
            "completed": True,
            "validated": True,
            "usage": {"billed_tokens": 50},
        }
    monkeypatch.setattr("scripts.run_w5_provider_acceptance.execute", mock_execute)

    report, exit_code = run_acceptance(
        key=secret_sentinel,
        pack_path=pack_path,
        ref_path=ref_path,
        evidence_path=evidence_path,
        repo_root=tmp_path,
    )

    report_json_str = evidence_path.read_text(encoding="utf-8")
    assert secret_sentinel not in report_json_str


def test_unknown_usage_split_never_becomes_claimed_invoice_cost():
    raw_record = {
        "case": "A-R01",
        "repeat": 1,
        "usage": {
            "billed_tokens": 1000,
        }
    }
    sanitized = sanitize_evidence_record(raw_record)
    assert sanitized["usage"]["invoice_cost_status"] == "unknown"
    assert sanitized["usage"]["conservative_rupees"] == pytest.approx(1000 * OUTPUT_RUPEES_PER_M / 1_000_000)
