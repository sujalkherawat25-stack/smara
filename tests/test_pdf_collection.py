import json
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from smara.pdf_collection import PdfCollectionError, scan_pdf_collection
from smara.harness import SessionEngine
from smara.research_session import CanonicalResearchSession


def _pdf(path: Path, pages: list[str]) -> None:
    doc = canvas.Canvas(str(path))
    for text in pages:
        doc.drawString(72, 720, text)
        doc.showPage()
    doc.save()


def test_pdf_collection_is_sorted_hashed_and_resumable(tmp_path):
    _pdf(tmp_path / "b.pdf", ["second"])
    _pdf(tmp_path / "a.pdf", ["first", "page two"])
    manifest = scan_pdf_collection(tmp_path)
    assert [item["relative_path"] for item in manifest["files"]] == ["a.pdf", "b.pdf"]
    assert manifest["total_pages"] == 3
    assert all(item["text_sha256"] for file in manifest["files"] for item in file["pages"])
    resumed = scan_pdf_collection(tmp_path, resume_manifest=manifest)
    assert resumed["resumed_pages"] == 3
    assert resumed["files"][0]["pages"][0]["resumed"] is True
    assert json.dumps(manifest, sort_keys=True)


def test_pdf_collection_limits_are_explicit(tmp_path):
    _pdf(tmp_path / "a.pdf", ["a"])
    _pdf(tmp_path / "b.pdf", ["b"])
    manifest = scan_pdf_collection(tmp_path, max_files=1)
    assert manifest["status"] == "limit_exceeded"
    assert manifest["errors"][0]["kind"] == "max_files"


def test_pdf_collection_rejects_missing_root(tmp_path):
    with pytest.raises(PdfCollectionError):
        scan_pdf_collection(tmp_path / "missing")


def test_research_session_ingests_page_evidence_and_marks_scanned_pages(tmp_path):
    _pdf(tmp_path / "readable.pdf", ["A claim from the page."])
    session = CanonicalResearchSession(session_engine=SessionEngine(tmp_path, "pdf-session", constrained=False))
    session.plan("What claim?", [{"id": "docs", "question": "What claim?"}])
    result = session.ingest_pdf_collection("docs", ".", max_files=5, max_pages=10)
    assert result["status"] == "ok"
    assert result["evidence"]
    record = session.index.records[result["evidence"][0]["evidence_id"]]
    assert record.kind == "pdf_page"
    assert session.index.validate_artifact(record.id) == (True, "valid")
