"""Unit and integration tests for Sarvam OCR service and CLI integration."""
import io
import json
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from smara.ocr_service import SarvamOCRClient, OCRError, resolve_ocr_credentials, digitize_file_sync
from smara.research_session import CanonicalResearchSession
from smara.harness import Budget, SessionEngine


def _make_sample_image(path: Path) -> Path:
    # 1x1 PNG header
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    path.write_bytes(png_bytes)
    return path


def _make_zip_archive(text: str, filename: str = "result.md") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(filename, text.encode("utf-8"))
    return buf.getvalue()


@pytest.mark.anyio
async def test_sarvam_ocr_job_digitize_and_download(tmp_path: Path):
    img_path = _make_sample_image(tmp_path / "invoice.png")
    extracted_md = "# Invoice #1042\nTotal Amount: $450.00\nDate: 2026-09-12"
    zip_bytes = _make_zip_archive(extracted_md)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/job/digitise" in url:
            return httpx.Response(200, json={"job_id": "job_ocr_99", "status": "pending"})
        elif "/job/job_ocr_99/status" in url:
            return httpx.Response(200, json={"job_id": "job_ocr_99", "status": "completed"})
        elif "/job/job_ocr_99/download-url" in url:
            return httpx.Response(200, json={"download_url": "https://storage.sarvam.ai/results/job_ocr_99.zip", "method": "GET"})
        elif "storage.sarvam.ai/results" in url:
            return httpx.Response(200, content=zip_bytes)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        ocr_client = SarvamOCRClient(
            base_url="https://api.sarvam.ai/v2",
            api_key="test_sarvam_key",
            model="sarvam-vision-v1",
            http_client=client,
        )
        res = await ocr_client.digitize(img_path, language="en-IN", output_format="md")

    assert res.provider == "sarvam"
    assert res.job_id == "job_ocr_99"
    assert "# Invoice #1042" in res.text
    assert "Total Amount: $450.00" in res.text
    assert res.content_sha256 != ""
    assert res.text_sha256 != ""
    assert res.character_count == len(res.text)


@pytest.mark.anyio
async def test_sarvam_ocr_handles_job_failure(tmp_path: Path):
    img_path = _make_sample_image(tmp_path / "corrupt.png")

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/job/digitise" in url:
            return httpx.Response(200, json={"job_id": "job_ocr_fail", "status": "pending"})
        elif "/job/job_ocr_fail/status" in url:
            return httpx.Response(200, json={"job_id": "job_ocr_fail", "status": "failed"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        ocr_client = SarvamOCRClient(
            base_url="https://api.sarvam.ai/v2",
            api_key="test_sarvam_key",
            http_client=client,
        )
        with pytest.raises(OCRError, match="failed with status: failed"):
            await ocr_client.digitize(img_path)


@pytest.mark.anyio
async def test_sarvam_ocr_falls_back_to_gemma4_for_image_when_document_ai_is_unavailable(tmp_path: Path):
    img_path = _make_sample_image(tmp_path / "scan.png")
    requests: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/job/digitise" in url:
            return httpx.Response(404, json={"code": "not_found_error"})
        if url.endswith("/v2/chat/completions"):
            payload = json.loads(request.content.decode("utf-8"))
            requests.append((url, payload))
            return httpx.Response(200, json={"choices": [{"message": {"content": "Invoice total: ₹42"}}]})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        ocr_client = SarvamOCRClient(
            base_url="https://api.sarvam.ai/v2",
            api_key="test_sarvam_key",
            http_client=client,
        )
        result = await ocr_client.digitize(img_path)

    assert result.provider == "sarvam-gemma4"
    assert result.model == "gemma4"
    assert result.text == "Invoice total: ₹42"
    assert len(requests) == 1
    assert requests[0][1]["model"] == "gemma4"
    assert requests[0][1]["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.anyio
async def test_sarvam_ocr_does_not_use_gemma4_fallback_for_auth_failure(tmp_path: Path):
    img_path = _make_sample_image(tmp_path / "scan.png")
    chat_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal chat_calls
        url = str(request.url)
        if url.endswith("/v2/chat/completions"):
            chat_calls += 1
        if "/job/digitise" in url:
            return httpx.Response(403, json={"code": "invalid_api_key_error"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        ocr_client = SarvamOCRClient(
            base_url="https://api.sarvam.ai/v2",
            api_key="bad-key",
            http_client=client,
        )
        with pytest.raises(OCRError, match="HTTP 403"):
            await ocr_client.digitize(img_path)

    assert chat_calls == 0


def test_research_session_ingest_image_with_sarvam_ocr(tmp_path: Path, monkeypatch):
    img_path = _make_sample_image(tmp_path / "table.png")
    extracted_text = "Year | Revenue\n2025 | $10M\n2026 | $25M"

    engine = SessionEngine(tmp_path, "test-ocr-session", budget=Budget())
    session = CanonicalResearchSession(session_engine=engine)
    session.plan("Extract revenue", [{"id": "rev", "question": "What is 2026 revenue?"}])

    mock_digitize = AsyncMock()
    from smara.ocr_service import OCRResult
    mock_digitize.return_value = OCRResult(
        text=extracted_text,
        content_sha256="fake_sha",
        text_sha256="fake_text_sha",
        pages=1,
        format="md",
        language="en-IN",
        provider="sarvam",
        job_id="mock_job",
        character_count=len(extracted_text),
    )

    monkeypatch.setenv("SARVAM_API_KEY", "test_key")
    with patch("smara.ocr_service.SarvamOCRClient.digitize", mock_digitize):
        ingest_res = session.ingest_file("rev", str(img_path))

    assert ingest_res["status"] == "ok"
    assert ingest_res["evidence"]["kind"] == "image_ocr"
    assert "2026 | $25M" in ingest_res["evidence"]["text"]


def test_cli_ocr_subcommand_invocation(tmp_path: Path, monkeypatch, capsys):
    from smara.cli import main
    img_path = _make_sample_image(tmp_path / "scan.png")
    out_file = tmp_path / "extracted.md"

    mock_digitize = AsyncMock()
    from smara.ocr_service import OCRResult
    mock_digitize.return_value = OCRResult(
        text="Digitized Receipt: Total $99.50",
        content_sha256="img_sha",
        text_sha256="txt_sha",
        pages=1,
        format="md",
        language="en-IN",
        provider="sarvam",
        job_id="job_cli_1",
        character_count=31,
    )

    with patch("smara.ocr_service.SarvamOCRClient.digitize", mock_digitize):
        exit_code = main(["--plain", "ocr", str(img_path), "--output", str(out_file)])

    assert exit_code == 0
    assert out_file.exists()
    assert out_file.read_text(encoding="utf-8") == "Digitized Receipt: Total $99.50"
    captured = capsys.readouterr().out
    assert "Digitized Receipt: Total $99.50" in captured
