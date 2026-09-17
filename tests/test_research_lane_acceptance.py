import json
from pathlib import Path

from scripts.run_live_web_acceptance_v2 import validate_pack_contract
from smara.autonomous_agent import SmaraAutonomousAgent
from smara.harness import Budget, SessionEngine
from smara.research_session import CanonicalResearchSession
from smara.research_modes import DEEP_POLICY, QUICK_POLICY, select_research_lane


ROOT = Path(__file__).resolve().parents[1]


def test_research_lane_acceptance_pack_is_sealed_and_balanced():
    pack = json.loads((ROOT / "tests/evals/research_lanes_acceptance_v1/manifest.json").read_text(encoding="utf-8"))
    refs = json.loads((ROOT / "tests/evals/research_lanes_acceptance_v1/references.json").read_text(encoding="utf-8"))["references"]
    assert validate_pack_contract(pack, refs) == []
    assert len(pack["tasks"]) == 8
    assert sum(item["expected_lane"] == "quick" for item in pack["tasks"]) == 4
    assert sum(item["expected_lane"] == "deep" for item in pack["tasks"]) == 4


def test_lane_acceptance_questions_route_to_declared_auto_lanes():
    pack = json.loads((ROOT / "tests/evals/research_lanes_acceptance_v1/manifest.json").read_text(encoding="utf-8"))
    for task in pack["tasks"]:
        if task["requested_mode"] != "auto":
            continue
        policy, decision = select_research_lane(task["question"])
        expected = QUICK_POLICY if task["expected_lane"] == "quick" else DEEP_POLICY
        assert policy == expected, (task["id"], decision)


def test_lane_acceptance_contract_rejects_unknown_mode():
    pack = {"canonical_agent": True, "reference_access": "validator_only", "tasks": [{"id": "x", "category": "quick_research", "requested_mode": "turbo", "expected_lane": "quick", "as_of": "2026-09-14", "question": "What?"}]}
    refs = {"x": {"expected_answer": "answer", "required_claims": [{"any_of": ["answer"]}]}}
    errors = validate_pack_contract(pack, refs)
    assert any("requested_mode" in error for error in errors)


def test_quick_lane_recovers_after_provider_timeout_with_validated_state(tmp_path, monkeypatch):
    engine = SessionEngine(tmp_path, "quick-timeout-recovery", budget=Budget(60, 10, 2, 50_000, 1), constrained=False)
    engine.set("research_policy", QUICK_POLICY.to_dict())
    engine.set("research_mode", "quick")
    session = CanonicalResearchSession(session_engine=engine)
    session.plan("What is the verified value?", [{"id": "claim", "question": "What is the verified value?"}])
    claim = "The verified value is 42 kilograms."
    records = [
        session.index.add(kind="fetched_passage", url=f"https://source-{index}.test/value", content=claim.encode(), text=claim, start=0, end=len(claim))
        for index in range(3)
    ]
    session.resolve("claim", claim, [records[0].id])
    assert session.validate([{"claim": claim, "evidence_ids": [records[0].id]}])["passed"]
    agent = SmaraAutonomousAgent(api_key="fixture", profile="research_web", workspace_root=tmp_path, session_engine=engine, research_mode="quick")
    monkeypatch.setattr(agent, "_call_model_api", lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("provider timeout")))
    result = agent.run("What is the verified value?", max_iterations=1)
    assert result["status"] == "completed"
    assert claim in result["answer"]


def test_deep_lane_recovers_report_after_provider_timeout(tmp_path, monkeypatch):
    engine = SessionEngine(tmp_path, "deep-timeout-recovery", budget=Budget(60, 30, 5, 200_000, 1), constrained=False)
    engine.set("research_policy", DEEP_POLICY.to_dict())
    engine.set("research_mode", "deep")
    session = CanonicalResearchSession(session_engine=engine)
    session.plan("What is the verified value?", [{"id": "claim", "question": "What is the verified value?"}])
    claim = "The verified value is 42 kilograms."
    records = [
        session.index.add(kind="fetched_passage", url=f"https://source-{index}.test/value", content=claim.encode(), text=claim, start=0, end=len(claim))
        for index in range(20)
    ]
    session.resolve("claim", claim, [records[0].id])
    assert session.validate([{"claim": claim, "evidence_ids": [records[0].id]}])["passed"]
    agent = SmaraAutonomousAgent(api_key="fixture", profile="research_web", workspace_root=tmp_path, session_engine=engine, research_mode="deep")
    monkeypatch.setattr(agent, "_call_model_api", lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("provider timeout")))
    result = agent.run("What is the verified value?", max_iterations=1)
    assert result["status"] == "completed"
    assert claim in result["answer"]
    assert engine.get("research_report_artifact_id")
