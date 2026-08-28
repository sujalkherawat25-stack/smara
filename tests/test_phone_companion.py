from pathlib import Path

import asyncio
import io
import httpx
import zipfile

from smara.store import TaskStore
from smara.capture_processing import process_capture
from smara.worker import run_once


def test_capture_and_push_subscription_are_account_scoped(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    capture = store.create_capture("acct_1", "text", "Phone note", "Remember this safe idea")
    assert capture["task"]["workspace_id"] == "inbox"
    assert capture["artifact"]["kind"].startswith("capture:text")
    store.save_push_subscription("acct_1", "https://push.example/subscription", "p256dh-key", "auth-key")
    assert len(store.push_subscriptions("acct_1")) == 1
    assert store.push_subscriptions("acct_2") == []


def test_capture_worker_has_safe_local_fallback(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    store.create_capture("acct_1", "voice", "Voice note", "aGVsbG8=", "audio/webm")
    assert asyncio.run(run_once(store, None))
    task = store.list("acct_1")[0]
    assert task["status"] == "completed"
    completed = next(event for event in store.events(task["id"], "acct_1") if event["type"] == "step.completed")
    assert "not configured" in completed["payload"]


def test_voice_capture_uses_openai_compatible_transcription(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    capture = store.create_capture("acct_1", "voice", "Voice note", "aGVsbG8=", "audio/webm")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"text": "Remember the launch checklist."})

    async def exercise() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await process_capture(store, capture["task"], client, transcription_base_url="https://speech.example/v1", transcription_api_key="speech-key", transcription_model="small-transcriber")

    assert asyncio.run(exercise()).startswith("Voice capture transcribed")
    assert requests[0].url == "https://speech.example/v1/audio/transcriptions"
    assert requests[0].headers["Authorization"] == "Bearer speech-key"
    assert len(store.artifacts(capture["task"]["id"], "acct_1")) == 2
    assert store.artifacts(capture["task"]["id"], "acct_1")[1]["content"] == "Remember the launch checklist."


def test_photo_capture_uses_vision_provider_without_leaking_key(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    capture = store.create_capture("acct_1", "photo", "Receipt", "aGVsbG8=", "image/png")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = request.read()
        assert request.headers["Authorization"] == "Bearer vision-key"
        return httpx.Response(200, json={"choices": [{"message": {"content": "A receipt on a desk."}}]})

    async def exercise() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await process_capture(store, capture["task"], client, vision_base_url="https://vision.example/v1", vision_api_key="vision-key", vision_model="vision-small")

    assert asyncio.run(exercise()).startswith("Photo capture described")
    assert b"vision-key" not in captured["json"]


def test_document_capture_uses_sarvam_document_ai_and_stores_ocr(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    capture = store.create_capture("acct_1", "document", "Invoice", "cGRmLWJ5dGVz", "application/pdf")
    requests: list[httpx.Request] = []
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        output.writestr("invoice.md", "# Invoice\n\nTotal due: INR 1,200")
    archive_bytes = archive.getvalue()

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/job/digitise"):
            assert request.headers["api-subscription-key"] == "sarvam-key"
            return httpx.Response(202, json={"job_id": "job-1", "status": "completed"})
        if request.url.path.endswith("/job/job-1/status"):
            return httpx.Response(200, json={"job_id": "job-1", "status": "completed"})
        if request.url.path.endswith("/job/job-1/download-url"):
            return httpx.Response(200, json={"download_url": "https://download.example/invoice.zip"})
        if request.url.host == "download.example":
            return httpx.Response(200, content=archive_bytes)
        return httpx.Response(404)

    async def exercise() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await process_capture(
                store,
                capture["task"],
                client,
                ocr_base_url="https://api.sarvam.ai/doc-ai/v1",
                ocr_api_key="sarvam-key",
                ocr_model="sarvam-vision-v1",
                ocr_language="en-IN",
                ocr_auth_header="api-subscription-key",
            )

    assert asyncio.run(exercise()).startswith("Document OCR complete")
    artifacts = store.artifacts(capture["task"]["id"], "acct_1")
    assert artifacts[-1]["kind"] == "capture.ocr"
    assert "INR 1,200" in artifacts[-1]["content"]
    assert [request.url.path for request in requests] == [
        "/doc-ai/v1/job/digitise",
        "/doc-ai/v1/job/job-1/download-url",
        "/invoice.zip",
    ]
