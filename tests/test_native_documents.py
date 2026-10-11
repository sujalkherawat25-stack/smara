"""Synthetic Sarvam API fixtures, not a claim of live OCR quality."""
import asyncio
import io
import json
import zipfile
import httpx
import pytest
from smara.native_documents import DocumentError, archive_text, describe_image, digitise, read_document


def test_active_sarvam_key_precedes_stale_shared_alias_but_not_explicit_ocr_key(monkeypatch):
    from smara.native_documents import sarvam_key
    profiles = [{"id": "old", "base_url": "https://api.sarvam.ai"}, {"id": "active", "base_url": "https://api.sarvam.ai/v2"}]
    explicit = [""]
    monkeypatch.setattr("smara.native_profiles.load_profiles", lambda: (profiles, "active", {}))
    monkeypatch.setattr("smara.native_profiles.resolve_profile_key", lambda profile, *_args, **_kwargs: profile["id"] + "-fixture")
    monkeypatch.setattr("smara.native_profiles.resolve_credential", lambda name, *_args, **_kwargs: explicit[0] if name == "SMARA_OCR_API_KEY" else "stale-fixture")
    assert sarvam_key() == "active-fixture"
    explicit[0] = "explicit-fixture"
    assert sarvam_key() == "explicit-fixture"


def image(tmp_path):
    path = tmp_path / "synthetic.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    return path


def test_document_paths_types_and_size_are_checked_before_upload(tmp_path):
    image(tmp_path)
    assert read_document(tmp_path, "synthetic.png")[2] == "image/png"
    outside = tmp_path.parent / "outside-document.png"
    outside.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    with pytest.raises(DocumentError, match="inside"):
        read_document(tmp_path, str(outside))
    (tmp_path / "fake.pdf").write_text("not a PDF")
    with pytest.raises(DocumentError, match="bytes"):
        read_document(tmp_path, "fake.pdf")
    with pytest.raises(DocumentError, match="supported"):
        read_document(tmp_path, "fake.pdf", image_only=True)


def test_doc_ai_submits_once_uses_host_route_and_preserves_partial_status(tmp_path, monkeypatch):
    image(tmp_path)
    monkeypatch.setattr("smara.research._is_public_http_url", lambda url: url == "https://result.example/output")
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/digitise"):
            assert request.headers["api-subscription-key"] == "fixture-key"
            assert b'name="file"' in request.content
            assert b'name="output_format"' not in request.content
            return httpx.Response(201, json={"job_id": "synthetic-job", "status": "partially_completed"})
        if request.url.path.endswith("/download-url"):
            return httpx.Response(200, json={"url": "https://result.example/output", "method": "GET", "headers": {}})
        assert "api-subscription-key" not in request.headers
        return httpx.Response(200, text="synthetic OCR output")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = asyncio.run(digitise(tmp_path, "synthetic.png", client=client, key="fixture-key"))
    assert result["text"] == "synthetic OCR output" and result["status"] == "partially_completed"
    assert len(calls) == 3 and calls[0].url.path == "/doc-ai/v1/job/digitise"
    asyncio.run(client.aclose())


@pytest.mark.parametrize("status", [401, 403, 404, 429, 503])
def test_ocr_failure_never_falls_back_or_reuploads_and_withholds_raw_error(tmp_path, status):
    image(tmp_path)
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="synthetic-provider-secret")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(DocumentError) as caught:
        asyncio.run(digitise(tmp_path, "synthetic.png", client=client, key="fixture-key"))
    assert "synthetic-provider-secret" not in str(caught.value)
    assert len(calls) == 1
    asyncio.run(client.aclose())


def test_pending_ocr_has_bounded_polling_and_keeps_job_identity(tmp_path):
    image(tmp_path)
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(201, json={"job_id": "synthetic-job", "status": "pending"})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(DocumentError, match="synthetic-job.*polling limit"):
        asyncio.run(digitise(tmp_path, "synthetic.png", client=client, key="fixture-key", max_wait=0))
    assert len(calls) == 1
    asyncio.run(client.aclose())


def test_image_uses_documented_base64_gemma4_route(tmp_path):
    image(tmp_path)
    def handler(request):
        payload = json.loads(request.content)
        assert str(request.url) == "https://api.sarvam.ai/v2/chat/completions"
        assert payload["model"] == "gemma4"
        assert payload["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
        return httpx.Response(200, json={"choices": [{"message": {"content": "synthetic answer"}, "finish_reason": "stop"}]})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = asyncio.run(describe_image(tmp_path, "synthetic.png", "What text?", client=client, key="fixture-key"))
    assert result["text"] == "synthetic answer" and result["model"] == "gemma4"
    asyncio.run(client.aclose())


def test_ocr_archive_does_not_extract_paths_or_duplicate_formats(tmp_path):
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        archive.writestr("../outside.md", "synthetic text")
        archive.writestr("outside.html", "duplicate format")
    assert archive_text(raw.getvalue()) == "synthetic text"
    assert list(tmp_path.iterdir()) == []


def test_live_document_archive_shape_uses_ordered_blocks_not_css_and_metadata():
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        archive.writestr("page1.html", "<style>huge stylesheet</style><p>duplicated text</p>")
        archive.writestr("page1.json", json.dumps({"blocks": [{"reading_order": 2, "text": "Total: 391"}, {"reading_order": 1, "text": "SMARA SYNTHETIC TEST"}]}))
        archive.writestr("report.json", '{"page_count":1}')
    assert archive_text(raw.getvalue()) == "SMARA SYNTHETIC TEST\nTotal: 391"
    assert archive_text(b"<!DOCTYPE html><html><head><style>css</style></head><body><p>text<br/>next</p></body></html>") == "text\nnext"


def test_unsafe_result_url_is_rejected_before_download(tmp_path, monkeypatch):
    image(tmp_path)
    monkeypatch.setattr("smara.research._is_public_http_url", lambda url: False)
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(201, json={"job_id": "job", "status": "completed"}) if len(calls) == 1 else httpx.Response(200, json={"url": "http://127.0.0.1/private"})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(DocumentError, match="safe public GET"):
        asyncio.run(digitise(tmp_path, "synthetic.png", client=client, key="fixture-key"))
    assert len(calls) == 2
    asyncio.run(client.aclose())
