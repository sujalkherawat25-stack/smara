import hashlib
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from scripts import run_live_web_acceptance_v2 as evaluation


ROOT = Path(__file__).resolve().parents[1]


def test_v5_sealed_pack_adds_temporal_conflict_and_failed_page_cases():
    pack = json.loads((ROOT / "tests/evals/live_web_acceptance_v5/manifest.json").read_text(encoding="utf-8"))
    refs = json.loads((ROOT / "tests/evals/live_web_acceptance_v5/references.json").read_text(encoding="utf-8"))["references"]
    assert len(pack["tasks"]) == 24
    assert {item["category"] for item in pack["tasks"][-4:]} == {"today_latest", "historical_as_of", "source_conflict", "failed_page"}
    assert evaluation.validate_pack_contract(pack, refs) == []
    capability = json.loads((ROOT / "release/capabilities.json").read_text(encoding="utf-8"))["capabilities"]["live_web_research"]["acceptance_pack_v5"]
    digest = lambda relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
    assert capability["manifest_sha256"] == digest("tests/evals/live_web_acceptance_v5/manifest.json")
    assert capability["references_sha256"] == digest("tests/evals/live_web_acceptance_v5/references.json")
    assert capability["execution_status"] == "completed_gate_failed"
    assert capability["evidence"]["sha256"] == digest("release/evidence/LIVE_WEB_ACCEPTANCE_V5.json")
    assert capability["evidence"]["score"] == "14/24"


def test_today_prompt_uses_local_date_and_explicit_latest_intent():
    prompt = evaluation.build_prompt({"id": "today", "category": "today_latest", "question": "What is today's latest release?"})
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
    assert today in prompt
    assert "latest stable Python 3 release" in prompt
    assert "not from memory" in prompt
    assert "final answer must include both the ISO current date and exact version" in prompt


def test_historical_prompt_preserves_as_of_cutoff():
    prompt = evaluation.build_prompt({"id": "history", "category": "historical_as_of", "as_of": "2025-03-31", "question": "What was latest by then?"})
    assert "As of 2025-03-31" in prompt
    assert "strict historical cutoff" in prompt
    assert "Do not substitute today's latest" in prompt


def test_citation_support_requires_the_cited_record_to_support_the_claim():
    cited = SimpleNamespace(canonical_url="https://peps.python.org/pep-0703/", text="This page is about a different proposal.")
    uncited = SimpleNamespace(canonical_url="https://docs.python.org/pep-0703/", text="PEP 703 makes the Global Interpreter Lock optional in CPython.")
    agent = SimpleNamespace(_research=SimpleNamespace(index=SimpleNamespace(records={"a": cited, "b": uncited})))
    passed, _, detail = evaluation.validate_factual(
        "PEP 703 makes the Global Interpreter Lock optional. https://peps.python.org/pep-0703/",
        agent,
        {"required_claims": [{"any_of": ["PEP 703"]}, {"any_of": ["Global Interpreter Lock Optional"]}],
         "allowed_domains": ["peps.python.org"]},
    )
    assert not passed
    assert detail["answer_coverage"] == 1.0
    assert detail["citation_support"] == 0.0


def test_today_validator_checks_dynamic_date_live_version_and_official_citation(monkeypatch):
    expected = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
    url = "https://www.python.org/downloads/source"
    record = SimpleNamespace(canonical_url=url, text="Latest Python 3 Release - Python 3.14.7")
    agent = SimpleNamespace(_research=SimpleNamespace(index=SimpleNamespace(records={"official": record})))
    monkeypatch.setattr(evaluation, "_current_python_release", lambda: "3.14.7")
    passed, _, detail = evaluation.validate_today_latest(
        f"Today is {expected}. The latest stable Python 3 release is 3.14.7. {url}", agent)
    assert passed
    assert detail["answer_coverage"] == 1.0
    assert detail["citation_support_fraction"] == 1.0


def test_failed_page_validator_requires_exact_failure_and_recovery_sources():
    required = "https://www.python.org/smara-research-intent-probe-404/"
    research = SimpleNamespace(index=SimpleNamespace(failures=[{"canonical_url": required, "error": "404"}]),
                               fetched_source_urls=lambda: ["a", "b", "c"])
    task = {"required_failed_url": required}
    passed, _, detail = evaluation.validate_failed_page(task, SimpleNamespace(_research=research))
    assert passed
    assert detail["recovery_source_count"] == 3
