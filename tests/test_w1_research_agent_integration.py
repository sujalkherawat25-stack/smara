import json
from pathlib import Path

import pytest

from smara.autonomous_agent import SmaraAutonomousAgent
from smara.harness import Budget,SessionEngine
from smara.research import RetrievedSource
from smara.research_tools import SearchHit


class Response:
    def __init__(self,payload,index):self.payload=payload;self.headers={"x-request-id":f"research-{index}"}
    def __enter__(self):return self
    def __exit__(self,*_):return None
    def read(self):return json.dumps(self.payload).encode()


def tool(name,args,index):
    return {"choices":[{"finish_reason":"tool_calls","message":{"content":"","tool_calls":[{"id":f"{name}-{index}","type":"function","function":{"name":name,"arguments":json.dumps(args)}}]}}],"usage":{"total_tokens":20}}


def final(answer):
    return {"choices":[{"finish_reason":"stop","message":{"content":f"FINAL ANSWER: {answer}"}}],"usage":{"total_tokens":10}}


class Fetcher:
    def __init__(self,sources):self.sources=dict(sources)
    async def fetch(self,url):
        text=self.sources[url];raw=f"<html><body><p>{text}</p></body></html>".encode()
        return RetrievedSource("Fixture",text,"", "2026-09-08T00:00:00+00:00",None,raw,url,(),"text/html")


class Searcher:
    async def search(self,query,*,max_results=5):
        return [SearchHit("https://fixture.test/snippet","Snippet","The value is 42 kilograms.","fixture")]


def make_agent(tmp_path,session_id="research",sources=None):
    session=SessionEngine(tmp_path,session_id,budget=Budget(120,30,30,500_000,2),constrained=False)
    agent=SmaraAutonomousAgent(api_key="fixture",profile="research",workspace_root=tmp_path,session_engine=session,max_iterations=12)
    agent._research.fetcher=Fetcher(sources or {"https://fixture.test/fact":"The value is 42 kilograms."})
    agent._research.searcher=Searcher()
    return agent,session


def drive(monkeypatch,agent,steps,answer,max_iterations=12):
    cursor={"value":0}
    def urlopen(*_args,**_kwargs):
        index=cursor["value"];cursor["value"]+=1
        action=steps(agent,index) if callable(steps) else steps[min(index,len(steps)-1)]
        return Response(action,index)
    monkeypatch.setattr("urllib.request.urlopen",urlopen)
    return agent.run("Research the sealed fixture and cite only validated evidence.",max_iterations=max_iterations)


@pytest.mark.parametrize(("source","claim","expected"),[
    ("The value is 42 kilograms.","The value is 42 kilograms.",True),
    ("The value is 42 kilograms.","The value is 99 kilograms.",False),
    ("The value is 42 kilograms.","The value is not 42 kilograms.",False),
    ("The sample mass is 42 kilograms.","The sample mass is 42000 grams.",True),
    ("On 2026-09-08 Alice measured 42 kilograms.","On 2025-09-08 Alice measured 42 kilograms.",False),
    ("On 2026-09-08 Alice measured 42 kilograms.","On 2026-09-08 Bob measured 42 kilograms.",False),
    ("Rain and low output were observed.","Rain caused low output.",False),
    ("The value is 42 kilograms.","42",False),
],ids=["direct-fact","wrong-number","negation","unit-conversion","wrong-date","wrong-entity","unsupported-causal","numeric-only"])
def test_agent_loop_accepts_only_supported_structured_claims(tmp_path,monkeypatch,source,claim,expected):
    url="https://fixture.test/fact";agent,session=make_agent(tmp_path,sources={url:source})
    def steps(agent,index):
        evidence_id=next(iter(agent._research.index.records),"")
        sequence=[
            tool("research_plan",{"question":"What is the fact?","nodes":[{"id":"fact","question":"What is the fact?"}]},0),
            tool("research_fetch",{"node_id":"fact","url":url},1),
            tool("research_resolve",{"node_id":"fact","claim":claim,"evidence_ids":[evidence_id]},2),
            tool("research_validate",{"claims":[{"claim":claim,"evidence_ids":[evidence_id]}]},3),
            final(claim),
        ]
        return sequence[min(index,4)]
    result=drive(monkeypatch,agent,steps,claim)
    assert result["completed"] is expected
    assert session.get("research_validation")["passed"] is expected
    assert [item["name"] for item in session.inspect()["calls"]][:4]==["research_plan","research_fetch","research_resolve","research_validate"]


def test_agent_loop_rejects_search_snippet_as_final_evidence(tmp_path,monkeypatch):
    agent,session=make_agent(tmp_path)
    claim="The value is 42 kilograms."
    def steps(agent,index):
        evidence_id=next(iter(agent._research.index.records),"")
        sequence=[tool("research_plan",{"question":"Value?","nodes":[{"id":"fact","question":"Value?"}]},0),tool("research_search",{"node_id":"fact","query":"value"},1),tool("research_resolve",{"node_id":"fact","claim":claim,"evidence_ids":[evidence_id]},2),tool("research_validate",{"claims":[{"claim":claim,"evidence_ids":[evidence_id]}]},3),final(claim)]
        return sequence[min(index,4)]
    result=drive(monkeypatch,agent,steps,claim)
    assert not result["completed"] and session.get("research_validation")["score"]["claims"][0]["citations"][0]["reason"]=="snippet_is_discovery_only"


def test_agent_loop_preserves_duplicate_publications_without_double_claiming(tmp_path,monkeypatch):
    urls=["https://fixture.test/a","https://mirror.test/a"];claim="The mass is 42 kilograms."
    agent,session=make_agent(tmp_path,sources={url:claim for url in urls})
    def steps(agent,index):
        ids=list(agent._research.index.records)
        sequence=[tool("research_plan",{"question":"Mass?","nodes":[{"id":"mass","question":"Mass?"}]},0),tool("research_fetch",{"node_id":"mass","url":urls[0]},1),tool("research_fetch",{"node_id":"mass","url":urls[1]},2),tool("research_resolve",{"node_id":"mass","claim":claim,"evidence_ids":ids},3),tool("research_validate",{"claims":[{"claim":claim,"evidence_ids":ids}]},4),final(claim)]
        return sequence[min(index,5)]
    result=drive(monkeypatch,agent,steps,claim)
    assert result["completed"]
    publications=next(iter(agent._research.index.content_publications.values()))
    assert len(publications)==2 and session.get("research_validation")["score"]["claim_count"]==1


def test_agent_loop_expands_contradiction_and_refuses_completion(tmp_path,monkeypatch):
    urls=["https://fixture.test/kg","https://fixture.test/lb"];claim="The mass is 42 kilograms."
    agent,session=make_agent(tmp_path,sources={urls[0]:claim,urls[1]:"The mass is 42 pounds."})
    def steps(agent,index):
        ids=list(agent._research.index.records)
        sequence=[tool("research_plan",{"question":"Mass?","nodes":[{"id":"mass","question":"Mass?"}]},0),tool("research_fetch",{"node_id":"mass","url":urls[0]},1),tool("research_fetch",{"node_id":"mass","url":urls[1]},2),tool("research_resolve",{"node_id":"mass","claim":claim,"evidence_ids":ids},3),tool("research_validate",{"claims":[{"claim":claim,"evidence_ids":ids}]},4),final(claim)]
        return sequence[min(index,5)]
    result=drive(monkeypatch,agent,steps,claim)
    assert not result["completed"]
    assert any(node.id.startswith("conflict-") for node in agent._research.graph.nodes.values())


def test_agent_loop_resolves_dependency_chain_before_final_answer(tmp_path,monkeypatch):
    urls={"https://fixture.test/report":"The Atlas report defines sample mass.","https://fixture.test/value":"The Atlas sample mass is 42 kilograms."};claim="The Atlas sample mass is 42 kilograms."
    agent,session=make_agent(tmp_path,sources=urls)
    def steps(agent,index):
        ids=list(agent._research.index.records);first=ids[:1];second=ids[1:2]
        sequence=[tool("research_plan",{"question":"Mass?","nodes":[{"id":"origin","question":"Which report?"},{"id":"value","question":"What value?","dependencies":["origin"]}]},0),tool("research_fetch",{"node_id":"origin","url":list(urls)[0]},1),tool("research_resolve",{"node_id":"origin","claim":"The Atlas report defines sample mass.","evidence_ids":first},2),tool("research_fetch",{"node_id":"value","url":list(urls)[1]},3),tool("research_resolve",{"node_id":"value","claim":claim,"evidence_ids":second},4),tool("research_validate",{"claims":[{"claim":"The Atlas report defines sample mass.","evidence_ids":first},{"claim":claim,"evidence_ids":second}]},5),final("The Atlas report defines sample mass. "+claim)]
        return sequence[min(index,6)]
    result=drive(monkeypatch,agent,steps,claim)
    assert result["completed"] and all(node.state=="supported" for node in agent._research.graph.nodes.values())


def test_research_state_and_evidence_survive_agent_reconstruction(tmp_path,monkeypatch):
    url="https://fixture.test/fact";claim="The value is 42 kilograms.";agent,session=make_agent(tmp_path,"resume",{url:claim})
    first=[tool("research_plan",{"question":"Value?","nodes":[{"id":"fact","question":"Value?"}]},0),tool("research_fetch",{"node_id":"fact","url":url},1)]
    first_result=drive(monkeypatch,agent,first,claim,max_iterations=2)
    assert not first_result["completed"] and session.get("research_state_artifact_id")
    session.close();resumed_engine=SessionEngine(tmp_path,"resume",budget=Budget(120,30,30,500_000,2),constrained=False)
    resumed=SmaraAutonomousAgent(api_key="fixture",profile="research",workspace_root=tmp_path,session_engine=resumed_engine,max_iterations=8)
    evidence_id=next(iter(resumed._research.index.records))
    steps=[tool("research_resolve",{"node_id":"fact","claim":claim,"evidence_ids":[evidence_id]},0),tool("research_validate",{"claims":[{"claim":claim,"evidence_ids":[evidence_id]}]},1),final(claim)]
    result=drive(monkeypatch,resumed,steps,claim,max_iterations=5)
    assert result["completed"] and resumed._research.can_finalize(claim)==(True,"validated")


def test_agent_loop_ingests_real_pdf_table_and_validates_cell(tmp_path,monkeypatch):
    from reportlab.pdfgen import canvas
    pdf=tmp_path/"measurements.pdf";document=canvas.Canvas(str(pdf));document.drawString(72,750,"Metric | Value | Unit");document.drawString(72,730,"Sample mass | 42 kilograms | verified");document.save()
    claim="42 kilograms";agent,session=make_agent(tmp_path,"pdf")
    def steps(agent,index):
        evidence_id=next(iter(agent._research.index.records),"")
        sequence=[tool("research_plan",{"question":"What is the sample mass?","nodes":[{"id":"table","question":"What is the sample mass?"}]},0),tool("research_ingest_file",{"node_id":"table","path":"measurements.pdf","page":1,"row":2,"column":2},1),tool("research_resolve",{"node_id":"table","claim":claim,"evidence_ids":[evidence_id]},2),tool("research_validate",{"claims":[{"claim":claim,"evidence_ids":[evidence_id]}]},3),final(claim)]
        return sequence[min(index,4)]
    result=drive(monkeypatch,agent,steps,claim)
    assert result["completed"]
    record=next(iter(agent._research.index.records.values()))
    assert record.kind=="pdf_table" and record.page==1 and record.row==2 and record.column==2
    assert agent._research.index.validate_artifact(record.id)==(True,"valid")


def test_agent_tool_reports_ocr_unavailable_without_fabricated_evidence(tmp_path):
    # This test asserts the unavailable branch and must not depend on a real
    # Sarvam credential stored in the developer's desktop vault.
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("smara.ocr_service.resolve_ocr_credentials", lambda: ("https://api.sarvam.ai", "", "sarvam-vision-1.5"))
    try:
        from PIL import Image
        image=tmp_path/"scan.png";Image.new("RGB",(80,30),"white").save(image)
        agent,session=make_agent(tmp_path,"ocr")
        plan=json.loads(agent.execute_tool("research_plan",{"question":"Read scan","nodes":[{"id":"scan","question":"What does the scan say?"}]},call_id="ocr-plan"))
        outcome=json.loads(agent.execute_tool("research_ingest_file",{"node_id":"scan","path":"scan.png"},call_id="ocr-ingest"))
        assert plan["status"]=="ok" and outcome=={"capability":"ocr","node_id":"scan","reason":"pytesseract is not installed","status":"unavailable"}
    finally:
        monkeypatch.undo()
    assert not agent._research.index.records and agent._research.graph.nodes["scan"].state=="unresolved"
    assert session.finish_incremental("completed","invented")["status"]=="needs_input"


def test_research_profile_cannot_complete_without_using_evidence_tools(tmp_path,monkeypatch):
    agent,session=make_agent(tmp_path,"bypass")
    result=drive(monkeypatch,agent,[final("An unsupported answer")],"",max_iterations=2)
    assert not result["completed"] and result["status"]=="budget_exhausted"
    assert not session.get("research_validation",{}).get("passed",False)


def test_provider_api_error_is_not_reported_as_completed(tmp_path,monkeypatch):
    agent, session = make_agent(tmp_path, "provider-error")
    def fail(*_args, **_kwargs):
        raise RuntimeError("provider unavailable")
    monkeypatch.setattr(agent, "_call_model_api", fail)
    result = agent.run("Research the fixture and cite validated evidence.", max_iterations=2)
    assert result["status"] == "tool_error"
    assert result["completed"] is False
    assert result["session"]["status"] == "tool_error"


def test_corrupt_research_state_artifact_invalidates_prior_validation(tmp_path,monkeypatch):
    claim="The value is 42 kilograms.";agent,session=make_agent(tmp_path,"corrupt")
    def steps(agent,index):
        evidence_id=next(iter(agent._research.index.records),"")
        sequence=[tool("research_plan",{"question":"Value?","nodes":[{"id":"fact","question":"Value?"}]},0),tool("research_fetch",{"node_id":"fact","url":"https://fixture.test/fact"},1),tool("research_resolve",{"node_id":"fact","claim":claim,"evidence_ids":[evidence_id]},2),tool("research_validate",{"claims":[{"claim":claim,"evidence_ids":[evidence_id]}]},3),final(claim)]
        return sequence[min(index,4)]
    assert drive(monkeypatch,agent,steps,claim)["completed"]
    artifact_id=session.get("research_state_artifact_id");next(session.artifacts.glob(f"{artifact_id}.*")).write_bytes(b"corrupt")
    assert session.finish_incremental("completed",claim)["status"]=="needs_input"
