import pytest
from smara.evidence_index import EvidenceIndex
from smara.harness import ArtifactStore
from smara.research_eval import ClaimCheck,score_claims
from smara.research_graph import ResearchGraph,ResearchNode

def test_dependencies_resolve_before_dependent_question():
    graph=ResearchGraph(); graph.add(ResearchNode("paper","Which paper?")); graph.add(ResearchNode("value","Which dataset value?",("paper",)))
    assert [node.id for node in graph.ready()]==["paper"]
    graph.resolve("paper","supported",["e1"])
    assert [node.id for node in graph.ready()]==["value"]

def test_unknown_dependency_cycle_and_expansion_are_rejected():
    graph=ResearchGraph(max_nodes=1)
    with pytest.raises(ValueError):graph.add(ResearchNode("x","x",("missing",)))
    graph.add(ResearchNode("x","x"))
    with pytest.raises(ValueError):graph.add(ResearchNode("y","y"))

def test_snippet_cannot_support_fetched_claim_and_offsets_are_verified():
    index=EvidenceIndex(); raw=b"Measured value is 42 kilograms."
    snippet=index.add(kind="search_snippet",url="https://example.test/?utm_source=x",content=raw,text=raw.decode())
    assert index.support(snippet.id,"value 42")[0] is False
    passage=index.add(kind="fetched_passage",url="https://example.test",content=raw,text="value is 42",start=9,end=20)
    assert index.support(passage.id,"value 42")[0] is True
    assert index.validate_location(passage.id,raw)
    assert not index.validate_location(passage.id,b"changed")

def test_claim_checker_rejects_wrong_number_and_opposite_polarity():
    index=EvidenceIndex();raw=b"The value is 42 kilograms."
    record=index.add(kind="fetched_passage",url="https://facts.test/value",content=raw,text=raw.decode(),start=0,end=len(raw))
    assert index.judge(record.id,"The value is 42 kilograms.").state=="supported"
    assert index.judge(record.id,"The value is 99 kilograms.").state=="refuted"
    assert index.judge(record.id,"The value is not 42 kilograms.").state=="refuted"
    assert index.judge(record.id,"42").state=="insufficient"

def test_claim_checker_preserves_entities_dates_units_and_causal_relations():
    index=EvidenceIndex();raw=b"On 2026-09-08, Alice measured the sample at 42 kilograms. Rain and low output were also observed."
    record=index.add(kind="fetched_passage",url="https://facts.test/report",content=raw,text=raw.decode(),start=0,end=len(raw))
    assert index.judge(record.id,"On 2026-09-08 Alice measured the sample at 42000 grams.").state=="supported"
    assert index.judge(record.id,"On 2025-09-08 Alice measured the sample at 42 kilograms.").state=="refuted"
    assert index.judge(record.id,"Bob measured the sample at 42 kilograms.").state=="insufficient"
    assert index.judge(record.id,"Rain caused low output.").state=="insufficient"

def test_claim_checker_requires_current_recoverable_original_artifact(tmp_path):
    store=ArtifactStore(tmp_path/"artifacts");index=EvidenceIndex(store);raw=b"The value is 42 kilograms."
    record=index.add(kind="fetched_passage",url="https://facts.test/value",content=raw,text=raw.decode(),start=0,end=len(raw))
    assert index.validate_artifact(record.id)==(True,"valid")
    source=next(store.root.glob(f"{record.source_artifact_id}.*"));source.write_bytes(b"tampered")
    assert index.validate_artifact(record.id)==(False,"stale_source_artifact")
    assert index.validate_artifact("missing")==(False,"missing_evidence")

def test_low_confidence_ocr_is_uncertain_and_duplicate_content_keeps_publications():
    index=EvidenceIndex(); raw=b"table value 9"
    ocr=index.add(kind="image_ocr",url="https://a.test/img",content=raw,text="table value 9",confidence=.5)
    index.add(kind="fetched_passage",url="https://b.test/page",content=raw,text="table value 9")
    assert index.support(ocr.id,"table value 9")[1]=="uncertain_ocr"
    assert len(index.content_publications[ocr.content_sha256])==2

def test_multimodal_locations_and_original_artifacts_are_verified(tmp_path):
    store=ArtifactStore(tmp_path/"artifacts");index=EvidenceIndex(store);pdf=b"%PDF sealed bytes";image=b"PNG sealed bytes"
    table=index.add(kind="pdf_table",url="https://sealed.test/report.pdf",content=pdf,text="2026 total 42 kg",page=3,row=4,column=2,extraction_version="table-v2")
    ocr=index.add(kind="image_ocr",url="https://sealed.test/scan.png",content=image,text="approximately 42 kg",bbox=(10,20,90,40),confidence=.91,extraction_version="ocr-v3")
    assert table.source_artifact_id and index.validate_location(table.id,pdf)
    assert ocr.source_artifact_id and index.validate_location(ocr.id,image)
    invalid=index.add(kind="pdf_table",url="https://sealed.test/bad.pdf",content=pdf,text="42 kg",page=0)
    assert not index.validate_location(invalid.id,pdf)

def test_conflicting_units_remain_distinct_evidence_not_silent_agreement():
    index=EvidenceIndex();a=index.add(kind="fetched_passage",url="https://a.test",content=b"Mass is 42 kilograms",text="Mass is 42 kilograms",start=0,end=22);b=index.add(kind="fetched_passage",url="https://b.test",content=b"Mass is 42 pounds",text="Mass is 42 pounds",start=0,end=17)
    assert index.support(a.id,"42 kilograms")[0]
    assert not index.support(b.id,"42 kilograms")[0]
    assert a.content_sha256!=b.content_sha256

def test_sealed_corpus_multihop_contradiction_failure_and_scorecard(tmp_path):
    index=EvidenceIndex(ArtifactStore(tmp_path/"originals"));graph=ResearchGraph(max_nodes=6);graph.add(ResearchNode("origin","Which report defines the measure?"));graph.add(ResearchNode("value","What value was reported?",("origin",)))
    passage=b"The 2026 primary report measured mass at 42 kilograms.";primary=index.add(kind="fetched_passage",url="https://primary.test/report",content=passage,text=passage.decode(),start=0,end=len(passage))
    graph.resolve("origin","supported",[primary.id]);assert graph.ready()[0].id=="value"
    table=index.add(kind="pdf_table",url="https://primary.test/table.pdf",content=b"sealed-pdf",text="2026 mass 42 kilograms",page=2,row=3,column=1)
    conflicting=index.add(kind="fetched_passage",url="https://independent.test/report",content=b"The mass was 42 pounds.",text="The mass was 42 pounds.",start=0,end=23)
    graph.resolve("value","refuted",[table.id,conflicting.id]);target=graph.expand_contradiction("value","Were the units kilograms or pounds?");assert target in graph.ready()
    duplicate=index.add(kind="fetched_passage",url="https://mirror.test/report",content=passage,text=passage.decode(),start=0,end=len(passage));assert len(index.content_publications[duplicate.content_sha256])==2
    index.record_failure("https://offline.test/report","sealed fixture unavailable")
    ocr=index.add(kind="image_ocr",url="https://scan.test/image",content=b"scan",text="42 kilograms",bbox=(0,0,10,10),confidence=.4)
    score=score_claims(index,[ClaimCheck("2026 primary report",(primary.id,)),ClaimCheck("42 kilograms",(table.id,)),ClaimCheck("42 kilograms",(ocr.id,))])
    assert score["evidence_precision"]==pytest.approx(2/3) and score["evidence_coverage"]==pytest.approx(2/3)
    assert score["retrieval_failures"][0]["canonical_url"]=="https://offline.test/report"
