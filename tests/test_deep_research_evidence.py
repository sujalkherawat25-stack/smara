from __future__ import annotations

from pathlib import Path

import pytest

from smara.deep_research import DeepResearchEngine


def test_research_refuses_to_report_without_retrieved_evidence(tmp_path: Path):
    engine = DeepResearchEngine(tmp_path)
    with pytest.raises(RuntimeError, match="will not create a report from invented sources"):
        engine.synthesize_market_analysis("agent systems", [])


def test_research_report_is_a_source_ledger_not_a_canned_market_claim(tmp_path: Path):
    engine = DeepResearchEngine(tmp_path)
    analysis = engine.synthesize_market_analysis("agent systems", [{
        "vector": "technical", "title": "Primary paper", "url": "https://example.test/paper",
        "snippet": "A measured observation.", "fetched": False,
    }])
    report = engine.generate_executive_report("agent systems", analysis)
    text = report.read_text(encoding="utf-8")
    assert "A measured observation." in text
    assert "search excerpt only" in text
    assert "Nvidia" not in text
