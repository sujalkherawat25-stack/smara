from smara.evidence_index import EvidenceIndex


def test_claim_checker_reads_facts_inside_long_flattened_web_page():
    index = EvidenceIndex()
    raw = (
        "Navigation and downloads " * 400
        + "Latest Python 3 Release - Python 3.14.7. "
        + "Stable Releases Python 3.14.7 - Aug. 5, 2026. "
        + "Older releases " * 400
    ).encode()
    record = index.add(
        kind="fetched_passage", url="https://python.org/downloads",
        content=raw, text=raw.decode(), start=0, end=len(raw),
    )
    assert index.judge(record.id, "Latest Python 3 Release - Python 3.14.7.").state == "supported"
    assert index.judge(record.id, "Stable Releases Python 3.14.7 - Aug. 5, 2026.").state == "supported"
    assert index.judge(record.id, "Latest Python 3 Release - Python 3.12.0.").state != "supported"


def test_release_date_must_belong_to_claimed_version():
    text = (
        "Latest Python 3 Release - Python 3.14.7 "
        "Stable Releases Python 3.11.16 - Aug. 12, 2026 "
        "Python 3.14.7 - Aug. 5, 2026"
    )
    index = EvidenceIndex()
    record = index.add(
        kind="fetched_passage", url="https://python.org/downloads",
        content=text.encode(), text=text, start=0, end=len(text),
    )
    assert index.judge(record.id, "Latest Python 3 Release - Python 3.14.7 - Aug. 5, 2026").state == "supported"
    assert index.judge(record.id, "Latest Python 3 Release - Python 3.14.7 - Aug. 12, 2026").state != "supported"
