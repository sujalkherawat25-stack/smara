import json
from pathlib import Path

import pytest

from scripts import run_live_web_acceptance_v2 as evaluation


@pytest.mark.parametrize("override", [
    {"max_rupees": 450},
    {"max_rupees": 200, "allow_budget_extension": True},
    {"max_rupees": 450, "allow_budget_extension": True, "max_tokens_per_attempt": 200_000},
    {"max_rupees": 450, "allow_budget_extension": True, "max_iterations": 13},
    {"max_rupees": 450, "allow_budget_extension": True, "max_seconds": 3000},
])
def test_resume_rejects_unapproved_or_nonmonetary_ceiling_changes(tmp_path, override):
    root = Path(__file__).resolve().parents[1]
    pack = root / "tests/evals/live_web_acceptance_v5/manifest.json"
    refs = root / "tests/evals/live_web_acceptance_v5/references.json"
    evidence = tmp_path / "resume.json"
    evidence.write_text(json.dumps({
        "manifest_sha256": evaluation.sha256(pack),
        "references_sha256": evaluation.sha256(refs),
        "model": "glm5.3",
        "ceilings": {"max_rupees": 250, "max_seconds": 2700,
                     "max_tokens_per_attempt": 150_000, "max_iterations": 12},
    }), encoding="utf-8")
    arguments = {"key": "offline-fixture", "pack_path": pack, "ref_path": refs,
                 "evidence_path": evidence, "resume": True, "max_rupees": 250,
                 "max_seconds": 2700, "max_tokens_per_attempt": 150_000,
                 "max_iterations": 12}
    arguments.update(override)
    with pytest.raises(ValueError, match="resume ceilings differ"):
        evaluation.run_gate(**arguments)
    assert json.loads(evidence.read_text(encoding="utf-8"))["ceilings"]["max_rupees"] == 250
