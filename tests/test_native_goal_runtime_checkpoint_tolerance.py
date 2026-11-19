"""Direct answers (no tools) must not fail a background job on harmless checkpoint format slips."""
import json

import pytest

from smara.native_goal_runtime import checkpoint_from


def base(**extra):
    value = {"decision": "ready_for_review", "summary": "Answered directly", "next_step": "", "plan": [], "completed_steps": ["Answered"],
             "evidence": ["Answered directly from the task; no tools were needed"], "outputs": [], "claims": [], "coverage": []}
    value.update(extra)
    return json.dumps(value)


def test_null_next_step_reads_as_empty(tmp_path):
    assert checkpoint_from(base(next_step=None), tmp_path)["next_step"] == ""


def test_unsourced_claims_are_dropped_and_unlinked_not_trusted(tmp_path):
    claims = [{"claim": "17 x 23 = 391", "url": "", "quote": ""}, {"claim": "Sourced", "url": "https://example.com", "quote": "q"}]
    coverage = [{"requirement_id": "r", "claim_indexes": [0, 1], "outputs": [], "test_commands": []}]
    value = checkpoint_from(base(claims=claims, coverage=coverage), tmp_path)
    assert [c["claim"] for c in value["claims"]] == ["Sourced"]
    assert value["coverage"][0]["claim_indexes"] == [0]


def test_research_still_needs_a_url_and_quote(tmp_path):
    with pytest.raises(ValueError, match="citation"):
        checkpoint_from(base(claims=[{"claim": "x", "url": "", "quote": ""}]), tmp_path, "research")


def test_review_still_needs_evidence(tmp_path):
    with pytest.raises(ValueError, match="evidence"):
        checkpoint_from(base(evidence=[]), tmp_path)
