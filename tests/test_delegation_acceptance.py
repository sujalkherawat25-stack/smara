import pytest

from smara.delegation_acceptance import build_control_evidence, evaluate_delegation_matrix


def test_control_plane_fixture_passes_without_provider_calls():
    evidence = build_control_evidence()
    assert evidence["provider_calls"] == 0
    assert evidence["matrix"]["gate_passed"] is True
    assert evidence["decision"].startswith("control_plane_passed")


def test_matrix_rejects_safety_or_role_failure():
    result = evaluate_delegation_matrix([
        {"role": "researcher", "passed": True},
        {"role": "researcher", "passed": False, "safety_violation": True},
        {"role": "coder", "passed": True},
        {"role": "coder", "passed": True},
    ])
    assert result["gate_passed"] is False
    assert result["safety_violations"] == 1


def test_skill_matrix_promotion_requires_complete_pass(tmp_path):
    from smara.skill_candidates import SkillCandidate, SkillCandidateStore
    store = SkillCandidateStore(tmp_path)
    candidate = SkillCandidate("matrix", 1, "fixture", ("read",), "inspect then test")
    store.save(candidate)
    with pytest.raises(ValueError):
        store.promote_matrix(candidate, {"gate_passed": False})
    summary = evaluate_delegation_matrix([{"role": "researcher", "passed": True}] * 2)
    store.promote_matrix(candidate, summary)
    assert candidate.status == "promoted"
