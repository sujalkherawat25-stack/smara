import json
from pathlib import Path
from types import SimpleNamespace

from scripts.run_live_web_acceptance_v2 import (
    _claim_passes,
    build_prompt,
    recompute,
    validate_factual,
    validate_pack_contract,
)


ROOT = Path(__file__).resolve().parents[1]


def test_sealed_v2_pack_has_complete_contract():
    pack = json.loads((ROOT / "tests/evals/live_web_acceptance_v2/manifest.json").read_text(encoding="utf-8"))
    refs = json.loads((ROOT / "tests/evals/live_web_acceptance_v2/references.json").read_text(encoding="utf-8"))["references"]
    assert len(pack["tasks"]) == 20
    assert validate_pack_contract(pack, refs) == []


def test_prompt_never_contains_sealed_reference_answer():
    task = {"id": "case", "category": "current_factual", "as_of": "2026-09-12", "question": "Find it"}
    prompt = build_prompt(task)
    assert "Find it" in prompt
    assert "expected_answer" not in prompt
    assert "required_claims" not in prompt


def test_independent_factual_validator_requires_answer_evidence_and_url():
    record = SimpleNamespace(canonical_url="https://peps.python.org/pep-0703/", text="PEP 703 – Making the Global Interpreter Lock Optional in CPython")
    agent = SimpleNamespace(_research=SimpleNamespace(index=SimpleNamespace(records={"e": record})))
    ref = {"required_claims": [{"any_of": ["PEP 703"]}, {"any_of": ["Global Interpreter Lock Optional"]}], "allowed_domains": ["peps.python.org"]}
    passed, _, detail = validate_factual("PEP 703 makes the Global Interpreter Lock Optional. https://peps.python.org/pep-0703/", agent, ref)
    assert passed
    assert detail["claims"] == [True, True]
    assert not _claim_passes("PEP 999", record.text, {"any_of": ["PEP 999"]})


def test_numeric_recomputation_supports_combined_columns():
    rows = [{"JAN": "1", "FEB": "2", "MAR": "3"}, {"JAN": "4", "FEB": "5", "MAR": "6"}]
    assert recompute(rows, {"operation": "sum", "column": "JAN", "additional_columns": ["FEB", "MAR"]}) == 21
