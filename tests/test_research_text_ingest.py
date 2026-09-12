from smara.autonomous_agent import SmaraAutonomousAgent
from smara.harness import SessionEngine


def test_research_agent_ingests_plain_text_with_exact_provenance(tmp_path):
    evidence="Mass is 42 kilograms."
    (tmp_path/"evidence.txt").write_text(evidence,encoding="utf-8")
    session=SessionEngine(tmp_path,"plain-text",constrained=False)
    agent=SmaraAutonomousAgent(api_key="fake",workspace_root=tmp_path,session_engine=session)
    agent._research.plan("What mass is stated?",[{"id":"mass","question":"What mass is stated?"}])
    ingested=agent._research.ingest_file("mass","evidence.txt")
    record=ingested["evidence"]
    assert ingested["status"]=="ok" and record["kind"]=="fetched_passage"
    assert record["text"]==evidence
    assert agent._research.index.validate_artifact(record["id"])==(True,"valid")
