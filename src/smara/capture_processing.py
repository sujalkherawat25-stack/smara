"""Bounded processing for phone/web captures.

Captures are always stored first.  Provider calls are optional, explicit, and
never receive more bytes than the API upload limits.  If no provider is
configured the task still completes with a clear local-only result.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
import zipfile
from typing import Any

import httpx


MAX_TRANSCRIPT_CHARS = 20_000
MAX_DESCRIPTION_CHARS = 8_000


def _provider_error(response: httpx.Response) -> RuntimeError:
    # Do not copy a provider response into task data: it can contain secrets or
    # very large HTML.  The task only needs a stable, actionable error.
    return RuntimeError(f"Capture provider returned HTTP {response.status_code}.")


def _auth_headers(api_key: str, auth_header: str) -> dict[str, str]:
    """Build a provider header without ever putting the secret in a payload."""
    if auth_header.strip().lower() in {"api-subscription-key", "api_subscription_key"}:
        return {"api-subscription-key": api_key}
    return {"Authorization": f"Bearer {api_key}"}


def _ocr_text_from_download(raw: bytes) -> str:
    """Extract the first useful text file from Sarvam's result archive."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            candidates = [
                item for item in archive.infolist()
                if not item.is_dir() and item.filename.lower().endswith((".md", ".markdown", ".html", ".txt", ".json"))
            ]
            candidates.sort(key=lambda item: (0 if item.filename.lower().endswith((".md", ".markdown")) else 1, item.filename.lower()))
            chunks: list[str] = []
            for item in candidates[:32]:
                try:
                    text = archive.read(item).decode("utf-8", errors="replace").strip()
                except (KeyError, RuntimeError, OSError):
                    continue
                if text:
                    chunks.append(text)
            return "\n\n".join(chunks).strip()
    except zipfile.BadZipFile:
        # A deployment or future API version may return plain text directly.
        return raw.decode("utf-8", errors="replace").strip()


def _decode_artifact(artifact: dict[str, Any]) -> tuple[str, bytes]:
    kind = str(artifact.get("kind", ""))
    parts = kind.split(":", 2)
    if len(parts) != 3 or parts[0] != "capture":
        raise ValueError("Capture artifact has an invalid kind.")
    try:
        raw = base64.b64decode(str(artifact.get("content", "")), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Capture media is not valid base64.") from exc
    return parts[1], raw


def _message_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return " ".join(str(part.get("text", "")) for part in content if isinstance(part, dict)).strip()
    return ""


async def process_capture(
    store: Any,
    task: dict[str, Any],
    client: httpx.AsyncClient,
    *,
    transcription_base_url: str = "",
    transcription_api_key: str = "",
    transcription_model: str = "",
    transcription_auth_header: str = "Authorization",
    vision_base_url: str = "",
    vision_api_key: str = "",
    vision_model: str = "",
    vision_auth_header: str = "Authorization",
    ocr_base_url: str = "",
    ocr_api_key: str = "",
    ocr_model: str = "sarvam-vision-v1",
    ocr_language: str = "en-IN",
    ocr_auth_header: str = "api-subscription-key",
) -> str:
    """Process the first capture artifact for a claimed task.

    The resulting transcript/description is an ordinary Smara artifact, so it
    is visible in the same task graph and can later be fed to an agent step.
    """
    artifacts = store.artifacts(task["id"], task["account_id"])
    capture = next((item for item in artifacts if str(item.get("kind", "")).startswith("capture:")), None)
    if capture is None:
        raise ValueError("Capture task has no capture artifact.")
    kind, raw = _decode_artifact(capture)
    if kind == "text":
        # Text is already usable and should not incur a provider call.
        return "Text capture stored and ready for the next task step."
    if kind == "voice":
        if not (transcription_base_url and transcription_api_key and transcription_model):
            return "Voice capture stored; transcription is not configured on this worker."
        if len(raw) > 10 * 1024 * 1024:
            raise ValueError("Voice capture exceeds the processing limit.")
        mime = str(capture["kind"]).rsplit(":", 1)[-1] or "audio/webm"
        response = await client.post(
            f"{transcription_base_url.rstrip('/')}/audio/transcriptions",
            headers=_auth_headers(transcription_api_key, transcription_auth_header),
            data={"model": transcription_model},
            files={"file": (str(capture.get("name") or "capture.bin"), raw, mime)},
        )
        if response.status_code >= 400:
            raise _provider_error(response)
        payload = response.json()
        text = str(payload.get("text", "")).strip() if isinstance(payload, dict) else ""
        if not text:
            raise RuntimeError("Transcription provider returned no text.")
        text = text[:MAX_TRANSCRIPT_CHARS]
        store.create_artifact(task["id"], task["account_id"], kind="capture.transcript", name=f"{capture['name']} transcript", content=text)
        return f"Voice capture transcribed ({len(text)} characters)."
    if kind == "document":
        if not (ocr_base_url and ocr_api_key):
            return "Document capture stored; OCR is not configured on this worker."
        if len(raw) > 20 * 1024 * 1024:
            raise ValueError("Document capture exceeds the processing limit.")
        mime = str(capture["kind"]).rsplit(":", 1)[-1] or "application/pdf"
        headers = _auth_headers(ocr_api_key, ocr_auth_header)
        data = {
            "language": ocr_language or "en-IN",
            "output_format": "md",
            "content_type": "printed",
        }
        if ocr_model:
            data["model"] = ocr_model
        response = await client.post(
            f"{ocr_base_url.rstrip('/')}/job/digitise",
            headers=headers,
            data=data,
            files={"file": (str(capture.get("name") or "capture.bin"), raw, mime)},
        )
        if response.status_code >= 400:
            raise _provider_error(response)
        try:
            job = response.json()
        except ValueError as exc:
            raise RuntimeError("OCR provider returned an invalid job response.") from exc
        job_id = job.get("job_id") if isinstance(job, dict) else None
        if not isinstance(job_id, str) or not job_id.strip():
            raise RuntimeError("OCR provider did not return a job id.")
        status = str(job.get("status", "pending")).lower() if isinstance(job, dict) else "pending"
        for attempt in range(24):
            if status not in {"completed", "partially_completed", "failed", "rejected"}:
                if attempt:
                    await asyncio.sleep(5)
                status_response = await client.get(f"{ocr_base_url.rstrip('/')}/job/{job_id}/status", headers=headers)
                if status_response.status_code >= 400:
                    raise _provider_error(status_response)
                try:
                    status_payload = status_response.json()
                except ValueError as exc:
                    raise RuntimeError("OCR provider returned an invalid status response.") from exc
                status = str(status_payload.get("status", "pending")).lower() if isinstance(status_payload, dict) else "pending"
            if status in {"failed", "rejected"}:
                raise RuntimeError("OCR provider could not process this document.")
            if status in {"completed", "partially_completed"}:
                break
        else:
            raise RuntimeError("OCR provider did not finish within the processing window.")
        download_response = await client.get(f"{ocr_base_url.rstrip('/')}/job/{job_id}/download-url", headers=headers)
        if download_response.status_code >= 400:
            raise _provider_error(download_response)
        try:
            download_payload = download_response.json()
        except ValueError as exc:
            raise RuntimeError("OCR provider returned an invalid download response.") from exc
        download_url = download_payload.get("download_url") or download_payload.get("url") if isinstance(download_payload, dict) else None
        if not isinstance(download_url, str) or not download_url.strip():
            raise RuntimeError("OCR provider did not return a download URL.")
        result_headers = download_payload.get("headers") if isinstance(download_payload, dict) else None
        if not isinstance(result_headers, dict):
            result_headers = {}
        result_method = str(download_payload.get("method", "GET")).upper() if isinstance(download_payload, dict) else "GET"
        result_response = await client.request(result_method, download_url, headers={str(k): str(v) for k, v in result_headers.items()})
        if result_response.status_code >= 400:
            raise _provider_error(result_response)
        text = _ocr_text_from_download(result_response.content)[:MAX_TRANSCRIPT_CHARS]
        if not text:
            raise RuntimeError("OCR provider returned no readable text.")
        store.create_artifact(task["id"], task["account_id"], kind="capture.ocr", name=f"{capture['name']} OCR", content=text)
        return f"Document OCR complete ({len(text)} characters)."
    if kind == "photo":
        if not (vision_base_url and vision_api_key and vision_model):
            return "Photo capture stored; image analysis is not configured on this worker."
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("Photo capture exceeds the processing limit.")
        mime = str(capture["kind"]).rsplit(":", 1)[-1] or "image/jpeg"
        data_url = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
        response = await client.post(
            f"{vision_base_url.rstrip('/')}/chat/completions",
            headers=_auth_headers(vision_api_key, vision_auth_header),
            json={
                "model": vision_model,
                "max_tokens": 500,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": "Describe this image briefly and objectively for the user's task context."},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]}],
            },
        )
        if response.status_code >= 400:
            raise _provider_error(response)
        description = _message_text(response.json())[:MAX_DESCRIPTION_CHARS]
        if not description:
            raise RuntimeError("Vision provider returned no description.")
        store.create_artifact(task["id"], task["account_id"], kind="capture.description", name=f"{capture['name']} description", content=description)
        return f"Photo capture described ({len(description)} characters)."
    raise ValueError("Unsupported capture type.")
