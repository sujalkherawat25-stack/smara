import asyncio,json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from smara.autonomous_agent import get_tool_schemas
from smara.harness import SessionEngine
from smara.research import fetch_public_source
from smara.research_analysis import ResearchAnalysisError,analyze_tabular
from smara.research_session import CanonicalResearchSession,ResearchStateError


ROWS=[
    {"date":"2025-01-01","region":"north","revenue":100,"cost":60},
    {"date":"2025-02-01","region":"north","revenue":120,"cost":70},
    {"date":"2025-03-01","region":"south","revenue":180,"cost":90},
    {"date":"2025-04-01","region":"south","revenue":200,"cost":100},
]


def test_tabular_analysis_is_exact_bounded_and_reproducible():
    first=analyze_tabular(ROWS,numeric_columns=["revenue","cost"],group_by="region",time_column="date",evidence_ids=["e1"])
    second=analyze_tabular(ROWS,numeric_columns=["revenue","cost"],group_by="region",time_column="date",evidence_ids=["e1"])
    assert first==second
    assert first["descriptive"]["revenue"]["mean"]==150
    assert first["groups"]["north"]["revenue"]["sum"]==220
    assert first["trends"]["revenue"]["percent_change"]==100
    assert first["correlations"][0]["pearson"]==pytest.approx(.9970544855)
    assert first["missing_policy"]=="exclude_per_metric_no_imputation"


def test_analysis_rejects_ambiguous_or_unbounded_inputs():
    with pytest.raises(ResearchAnalysisError,match="numeric column"):
        analyze_tabular([{"value":"unknown"}],numeric_columns=["value"])
    with pytest.raises(ResearchAnalysisError,match="10000 rows"):
        analyze_tabular(({"value":i} for i in range(10_001)),numeric_columns=["value"])


def test_forecast_causal_and_domain_diagnostics_are_bounded_and_explicit():
    rows = [
        {"date": f"2025-0{index}-01", "treated": "yes" if index >= 3 else "no", "outcome": index * 10 + (5 if index >= 3 else 0), "value": index + 1}
        for index in range(1, 7)
    ]
    result = analyze_tabular(
        rows,
        numeric_columns=["outcome", "value"],
        time_column="date",
        forecast_columns=["outcome"],
        forecast_horizon=2,
        treatment_column="treated",
        outcome_column="outcome",
        treatment_value="yes",
        domain_test="zscore",
        domain_column="value",
        evidence_ids=["e1"],
    )
    assert result["schema_version"] == 2
    assert len(result["forecasts"]["outcome"]["points"]) == 2
    assert result["causal"]["status"] == "ok"
    assert result["causal"]["warning"].startswith("association only")
    assert result["domain_tests"]["value"]["test"] == "zscore"
    assert "not causal" in result["methods_disclaimer"]


def test_forecast_requires_time_and_limits_horizon():
    with pytest.raises(ResearchAnalysisError, match="forecasting requires time_column"):
        analyze_tabular([{"value": 1}, {"value": 2}, {"value": 3}], numeric_columns=["value"], forecast_columns=["value"], forecast_horizon=1)
    with pytest.raises(ResearchAnalysisError, match="between 1 and 30"):
        analyze_tabular([{"date": f"2025-0{i}-01", "value": i} for i in range(1, 4)], numeric_columns=["value"], time_column="date", forecast_columns=["value"], forecast_horizon=31)


def test_canonical_analysis_requires_valid_artifact_provenance(tmp_path:Path):
    (tmp_path/"data.csv").write_text(
        "date,region,revenue,cost\n"
        "2025-01-01,north,100,60\n2025-02-01,north,120,70\n"
        "2025-03-01,south,180,90\n2025-04-01,south,200,100\n",
        encoding="utf-8",
    )
    engine=SessionEngine(tmp_path,"analysis")
    session=CanonicalResearchSession(session_engine=engine)
    session.plan("Analyze revenue",[{"id":"data","question":"What does revenue show?"}])
    ingested=session.ingest_file("data","data.csv");evidence_id=ingested["evidence"]["id"]
    result=session.analyze(None,numeric_columns=["revenue","cost"],group_by="region",time_column="date",evidence_ids=[evidence_id])
    assert result["analysis_artifact_id"]
    max_claim=next(item["claim"] for item in result["suggested_claims"] if item["column"]=="revenue" and item["metric"]=="max")
    assert max_claim=="The maximum of column revenue is 200.0."
    resolved=session.resolve("data",max_claim,[result["analysis_evidence_id"]])
    assert resolved["resolution"]["state"]=="supported"
    validated=session.validate([{"claim":max_claim,"evidence_ids":[result["analysis_evidence_id"]]}])
    assert validated["passed"]
    stored=json.loads(engine.resolve_artifact(result["analysis_artifact_id"]))
    assert stored["dataset_sha256"]==result["dataset_sha256"] and stored["row_count"]==4
    event_types=[item["type"] for item in engine.inspect()["events"]]
    assert "research_analyzed" in event_types and event_types[-1]=="research_validated"
    with pytest.raises(ResearchStateError,match="invalid"):
        session.analyze(ROWS,numeric_columns=["revenue"],evidence_ids=["missing"])


def test_research_inspect_can_focus_a_long_verified_passage(tmp_path:Path):
    phrase="Python Software Foundation License Version 2"
    (tmp_path/"license.txt").write_text("preamble "*800+phrase+" terms",encoding="utf-8")
    session=CanonicalResearchSession(session_engine=SessionEngine(tmp_path,"inspect"))
    session.plan("License",[{"id":"license","question":"Which license?"}])
    ingested=session.ingest_file("license","license.txt")
    result=session.inspect(ingested["evidence"]["id"],120,phrase)
    assert phrase in result["evidence"]["text"]
    assert result["evidence"]["text_start"]>0


def test_research_profile_exposes_analysis_tool():
    names={item["function"]["name"] for item in get_tool_schemas("research")}
    assert "research_analyze" in names


@pytest.mark.parametrize(("content_type","body"),[("application/json",'{"series":[1,2,3]}'),("text/csv","date,value\n2025-01-01,42\n")])
def test_safe_fetch_accepts_structured_public_data(monkeypatch,content_type,body):
    monkeypatch.setattr("smara.research._is_public_http_url",lambda url:True)
    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,headers={"content-type":content_type},text=body,request=request))) as client:
            return await fetch_public_source(client,"https://data.example/series")
    source=asyncio.run(execute())
    assert body.splitlines()[0] in source.excerpt and source.raw_content


def test_safe_fetch_still_rejects_binary_content(monkeypatch):
    monkeypatch.setattr("smara.research._is_public_http_url",lambda url:True)
    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,headers={"content-type":"application/octet-stream"},content=b"x"*100,request=request))) as client:
            return await fetch_public_source(client,"https://data.example/binary")
    with pytest.raises(ValueError,match="Unsupported source content type"):
        asyncio.run(execute())
