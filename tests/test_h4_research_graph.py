import pytest
from smara.evidence_index import EvidenceIndex
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

def test_low_confidence_ocr_is_uncertain_and_duplicate_content_keeps_publications():
    index=EvidenceIndex(); raw=b"table value 9"
    ocr=index.add(kind="image_ocr",url="https://a.test/img",content=raw,text="table value 9",confidence=.5)
    index.add(kind="fetched_passage",url="https://b.test/page",content=raw,text="table value 9")
    assert index.support(ocr.id,"table value 9")[1]=="uncertain_ocr"
    assert len(index.content_publications[ocr.content_sha256])==2
