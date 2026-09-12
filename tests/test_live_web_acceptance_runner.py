import json
from pathlib import Path

from scripts.run_live_web_acceptance import validate_acceptance_contract


def test_component_pack_cannot_claim_end_to_end_live_acceptance():
    manifest={"tasks":[{"id":"fact","category":"current_factual"},{"id":"data","category":"quantitative_analysis"}]}
    refs={"fact":{"key_terms":["answer"]},"data":{"expected_mean":42}}
    errors=validate_acceptance_contract(manifest,refs)
    assert "manifest must declare canonical_agent=true" in errors
    assert "fact: expected_answer is required" in errors
    assert "fact: required_claims are required" in errors
    assert "fact: as_of timestamp is required" in errors
    assert "data: live_data_url is required for live-data promotion" in errors


def test_complete_live_acceptance_contract_is_admitted():
    manifest={"canonical_agent":True,"tasks":[
        {"id":"fact","category":"current_factual","as_of":"2026-09-12T00:00:00Z"},
        {"id":"data","category":"quantitative_analysis","live_data_url":"https://data.example/series.csv"},
    ]}
    refs={"fact":{"expected_answer":"Answer 42","required_claims":["Answer 42"]},"data":{"expected_mean":42}}
    assert validate_acceptance_contract(manifest,refs)==[]


def test_v1_component_pack_is_rejected_for_capability_promotion():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "tests/evals/live_web_acceptance/manifest.json").read_text(encoding="utf-8"))
    references = json.loads((root / "tests/evals/live_web_acceptance/references.json").read_text(encoding="utf-8"))["references"]

    errors = validate_acceptance_contract(manifest, references)

    assert "manifest must declare canonical_agent=true" in errors
    assert any("expected_answer" in error for error in errors)
    assert any("live_data_url" in error for error in errors)
