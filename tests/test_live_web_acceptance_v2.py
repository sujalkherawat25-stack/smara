import json
import hashlib
from pathlib import Path
from types import SimpleNamespace

from scripts.run_live_web_acceptance_v2 import (
    _claim_passes,
    audit_safety,
    build_prompt,
    recompute,
    validate_attempt,
    validate_factual,
    validate_pack_contract,
)
from smara.autonomous_agent import get_tool_schemas


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


def test_abstention_language_still_requires_fetched_evidence():
    assert not _claim_passes("The answer is insufficient", "No matching passage here", {"any_of": ["insufficient"]})
    assert _claim_passes("The answer is insufficient", "Evidence status: insufficient", {"any_of": ["insufficient"]})


def test_factual_claim_matching_normalizes_date_ordinals():
    claim = {"any_of": ["11 September 2023"]}
    assert _claim_passes("The source says 11th September 2023", "as of today, 11th September 2023", claim)
    assert _claim_passes("Release date: Oct. 7, 2024", "Release date: Oct. 7, 2024", {"any_of": ["October 7, 2024"]})


def test_factual_claim_matching_accepts_declared_license_and_dependency_equivalents():
    assert _claim_passes(
        "Cargo.lock locks dependencies to specific versions.",
        "The result is stored in Cargo.lock and locks dependencies to specific versions.",
        {"any_of": ["exact dependency versions"]},
    )
    assert _claim_passes(
        "NumPy uses the BSD 3-Clause license.",
        "NumPy is released under the modified BSD license.",
        {"any_of": ["BSD 3-Clause"]},
    )


def test_failed_tool_call_does_not_satisfy_canonical_chain():
    task = {"category": "current_factual"}
    calls = [
        {"name": name, "state": "completed", "result": {"status": "ok"}}
        for name in ("research_plan", "research_fetch", "research_resolve", "research_validate")
    ]
    calls.append({"name": "research_search", "state": "completed", "result": {"status": "error"}})
    passed, reason, detail = validate_attempt(task, {}, SimpleNamespace(), {"completed": True}, calls)
    assert not passed
    assert reason == "canonical_tool_chain_incomplete"
    assert "research_search" not in detail["successful_tool_names"]

def test_strict_research_web_profile_exposes_only_canonical_tools():
    names = {item["function"]["name"] for item in get_tool_schemas("research_web")}
    assert names == {"research_plan", "research_search", "research_fetch", "research_inspect", "research_analyze", "research_resolve", "research_validate"}
    assert get_tool_schemas("research-web") == get_tool_schemas("research_web")
    assert get_tool_schemas("live-web") == get_tool_schemas("research_web")


def test_safety_audit_measures_workspace_and_private_source_violations(tmp_path):
    outside = tmp_path.parent / "outside"
    record = SimpleNamespace(canonical_url="http://127.0.0.1/private", text="private")
    agent = SimpleNamespace(_research=SimpleNamespace(index=SimpleNamespace(records={"e": record})))
    result = audit_safety(agent, [{"call_id": "escape", "workspace_id": str(outside)}], tmp_path)
    assert result["violations"] == 2
    assert not result["passed"]


def test_v3_acceptance_and_disjoint_smoke_contracts_are_valid():
    for folder, expected in (("live_web_acceptance_v3", 20), ("live_web_smoke_v3", 2)):
        pack = json.loads((ROOT / f"tests/evals/{folder}/manifest.json").read_text(encoding="utf-8"))
        refs = json.loads((ROOT / f"tests/evals/{folder}/references.json").read_text(encoding="utf-8"))["references"]
        assert len(pack["tasks"]) == expected
        assert validate_pack_contract(pack, refs) == []
    full_ids = {item["id"] for item in json.loads((ROOT / "tests/evals/live_web_acceptance_v3/manifest.json").read_text())["tasks"]}
    smoke_ids = {item["id"] for item in json.loads((ROOT / "tests/evals/live_web_smoke_v3/manifest.json").read_text())["tasks"]}
    assert full_ids.isdisjoint(smoke_ids)


def test_v3_sealed_hashes_match_capability_declaration_after_v4_promotion():
    capability = json.loads((ROOT / "release/capabilities.json").read_text(encoding="utf-8"))["capabilities"]["live_web_research"]
    declared = capability["pending_acceptance_pack_v3"]
    def digest(relative: str) -> str:
        return hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
    assert capability["status"] == "verified_live_web"
    assert capability["acceptance_pack_v4"]["status"] == "passed_promoted"
    assert declared["manifest_sha256"] == digest("tests/evals/live_web_acceptance_v3/manifest.json")
    assert declared["references_sha256"] == digest("tests/evals/live_web_acceptance_v3/references.json")
    assert declared["smoke_manifest_sha256"] == digest("tests/evals/live_web_smoke_v3/manifest.json")
    assert declared["smoke_references_sha256"] == digest("tests/evals/live_web_smoke_v3/references.json")
