import pytest

from smara.evidence_index import EvidenceIndex


def record(index, text, **kwargs):
    return index.add(url="https://docs.example.org/support", content=text.encode(), text=text,
                     kind=kwargs.pop("kind", "fetched_passage"), **kwargs)


def test_identical_adjacent_sentences_can_have_different_polarity():
    index = EvidenceIndex()
    quote = "The library has reached end of life. It will no longer receive public security fixes."
    source = record(index, "Other navigation. " + quote + " Vendor support periods can differ.")
    checked = index.judge(source.id, quote)
    assert checked.state == "supported"
    assert checked.reason in {"verbatim_bounded_passage", "structured_passage_match"}
    assert checked.passage_sha256 == source.text_sha256


@pytest.mark.parametrize("claim", [
    "It will receive public security fixes.",
    "The library has not reached end of life.",
    "The library has reached end of life because vendor support expired.",
])
def test_changed_polarity_or_invented_causation_is_not_verbatim_support(claim):
    index = EvidenceIndex()
    source = record(index, "The library has reached end of life. It will no longer receive public security fixes.")
    assert index.judge(source.id, claim).state != "supported"


@pytest.mark.parametrize("kind,extra", [("search_snippet", {}), ("image_ocr", {"confidence": .5})])
def test_verbatim_quote_does_not_bypass_discovery_or_ocr_guards(kind, extra):
    index = EvidenceIndex()
    quote = "The library has reached end of life. It will no longer receive public security fixes."
    source = record(index, quote, kind=kind, **extra)
    assert index.judge(source.id, quote).state == "insufficient"
