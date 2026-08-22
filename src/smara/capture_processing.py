"""Bounded processing for phone/web captures.

Captures are always stored first.  Provider calls are optional, explicit, and
never receive more bytes than the API upload limits.  If no provider is
configured the task still completes with a clear local-only result.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any

import httpx


MAX_TRANSCRIPT_CHARS = 20_000
MAX_DESCRIPTION_CHARS = 8_000


def _provider_error(response: httpx.Response) -> RuntimeError:
    # Do not copy a provider response into task data: it can contain secrets or
    # very large HTML.  The task only needs a stable, actionable error.
    return RuntimeError(f"Capture provider returned HTTP {response.status_code}.")


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
    vision_base_url: str = "",
    vision_api_key: str = "",
    vision_model: str = "",
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
            headers={"Authorization": f"Bearer {transcription_api_key}"},
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
    if kind == "photo":
        if not (vision_base_url and vision_api_key and vision_model):
            return "Photo capture stored; image analysis is not configured on this worker."
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("Photo capture exceeds the processing limit.")
        mime = str(capture["kind"]).rsplit(":", 1)[-1] or "image/jpeg"
        data_url = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
        response = await client.post(
            f"{vision_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {vision_api_key}"},
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
