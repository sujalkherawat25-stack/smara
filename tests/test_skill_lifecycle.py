from pathlib import Path

import pytest

from smara.skills_system import SkillLifecycleManager


def test_skill_promotion_requires_gate(tmp_path: Path):
    manager = SkillLifecycleManager(tmp_path)
    candidate = manager.submit_candidate("demo-skill", source_run_id="run-1", gate={"passed": False})
    assert candidate["state"] == "quarantined"
    with pytest.raises(ValueError):
        manager.promote("demo-skill")
    good = {"passed": True, "overall_rate": 1.0, "category_rates": {"local": 1.0}, "false_completions": 0, "safety_violations": 0, "reproducible": True}
    manager.submit_candidate("demo-skill", gate=good)
    assert manager.promote("demo-skill")["state"] == "promoted"
    assert manager.list(include_quarantined=False)[0]["name"] == "demo-skill"
