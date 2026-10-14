import json
import pytest
from types import SimpleNamespace
from scripts.run_native_outcome_suite import CASES, LiveBudget, main, saved_continuation


def test_catalog_preview_never_claims_real_evaluation_success(tmp_path):
    root = tmp_path / "catalog"
    assert main(["--out", str(root)]) == 0
    report = json.loads((root / "report.json").read_text())
    assert report["mode"] == "catalog_only" and not report["passed"]
    assert not report["cases"]
    assert {"latest", "historical", "failed-page"}.issubset(CASES)
    brief = CASES["memory-opportunity"]
    assert brief["kind"] == "research" and brief["max_requests"] == 8
    assert "public marketing does not establish demand" in brief["prompt"]
    assert len(brief["requirements"]) == 3
    with pytest.raises(FileExistsError): main(["--out", str(root)])


def test_live_guard_reserves_before_transport_and_preserves_allowance(tmp_path):
    from contextlib import contextmanager
    sent = []
    @contextmanager
    def stream(**kwargs):
        sent.append(kwargs["json"])
        yield object()
    budget = LiveBudget(tmp_path / "budget.json", requests=1, rupees=300)
    client = budget.wrap(SimpleNamespace(stream=stream))
    with client.stream(json={"max_tokens": 16384}): pass
    assert sent[0]["max_tokens"] == 16384
    with pytest.raises(RuntimeError, match="ceiling"):
        with client.stream(json={}): pass
    assert len(sent) == 1 and json.loads(budget.path.read_text())["requests"] == 1


def test_cumulative_continuation_carries_reservations_without_reset(tmp_path):
    first = LiveBudget(tmp_path / "first.json", requests=12, rupees=300)
    first.state.update(requests=10, reserved_inr_estimate=270.70)
    first.save()
    resumed = LiveBudget(tmp_path / "second.json", requests=18, rupees=500, carry=first.path)
    assert resumed.state["requests"] == 10
    assert resumed.state["reserved_inr_estimate"] == 270.70
    assert json.loads(first.path.read_text())["request_ceiling"] == 12
    with pytest.raises(ValueError):
        LiveBudget(tmp_path / "invalid.json", requests=9, rupees=500, carry=first.path)


def test_saved_continuation_rejects_passed_or_unrelated_sessions(tmp_path):
    root = tmp_path / "run"
    (root / "coding" / ".git").mkdir(parents=True)
    budget = root / "reservations.json"
    (root / "report.json").write_text(json.dumps({"mode": "live", "cases": [{"case": "coding", "passed": False}]}))
    assert saved_continuation(root, ["coding"], budget)["coding"]["passed"] is False
    for cases, carry in [(["mixed"], budget), (["coding", "coding"], budget), (["coding"], tmp_path / "other.json")]:
        with pytest.raises(ValueError): saved_continuation(root, cases, carry)
    (root / "report.json").write_text(json.dumps({"mode": "live", "cases": [{"case": "coding", "passed": True}]}))
    with pytest.raises(ValueError): saved_continuation(root, ["coding"], budget)


def test_saved_continuation_follows_cumulative_ledger_without_reset(tmp_path):
    root, continuation = tmp_path / "run", tmp_path / "continuation"
    (root / "coding" / ".git").mkdir(parents=True)
    continuation.mkdir()
    previous = {"mode": "live", "cases": [{"case": "coding", "passed": False}]}
    (root / "report.json").write_text(json.dumps(previous))
    budget = root / "reservations.json"
    budget.write_text(json.dumps({"requests": 10}))
    carried = {"requests": 16, "previous_ledger": str(budget.resolve())}
    ledger = continuation / "reservations.json"
    ledger.write_text(json.dumps(carried))
    (continuation / "report.json").write_text(json.dumps({**previous, "budget": carried}))
    assert saved_continuation(root, ["coding"], ledger)["coding"]["passed"] is False
    carried["previous_ledger"] = str(ledger.resolve())
    ledger.write_text(json.dumps(carried))
    (continuation / "report.json").write_text(json.dumps({**previous, "budget": carried}))
    with pytest.raises(ValueError, match="Cyclic"): saved_continuation(root, ["coding"], ledger)


@pytest.mark.parametrize("estimate", [float("nan"), float("inf"), True, "10"])
def test_malformed_previous_budget_cannot_authorize_requests(tmp_path, estimate):
    previous = tmp_path / "previous.json"
    previous.write_text(json.dumps({"request_ceiling": 12, "ceiling_inr": 300, "requests": 10, "reserved_inr_estimate": estimate}))
    with pytest.raises(ValueError): LiveBudget(tmp_path / "new.json", requests=18, rupees=500, carry=previous)
