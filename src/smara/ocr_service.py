"""Sarvam and local OCR service for document & image digitization."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import mimetypes
import os
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .config import settings


class OCRError(RuntimeError):
    """Safe, user-actionable OCR extraction failure."""


@dataclass(frozen=True)
class OCRResult:
    text: str
    content_sha256: str
    text_sha256: str
    pages: int
    format: str
    language: str
    provider: str
    model: str = "sarvam-vision-1.5"
    job_id: str | None = None
    character_count: int = 0


def _auth_headers(api_key: str, auth_header: str = "api-subscription-key") -> dict[str, str]:
    if auth_header.strip().lower() in {"api-subscription-key", "api_subscription_key"}:
        return {"api-subscription-key": api_key}
    return {"Authorization": f"Bearer {api_key}"}


def _extract_text_from_archive(raw: bytes) -> str:
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
        return raw.decode("utf-8", errors="replace").strip()


def resolve_ocr_credentials() -> tuple[str, str, str]:
    """Resolve active OCR base URL, API key, and model."""
    api_key = os.getenv("SARVAM_API_KEY") or os.getenv("SMARA_OCR_API_KEY") or os.getenv("SMARA_LLM_API_KEY") or ""
    if not api_key:
        try:
            from .desktop_executor import resolve_local_credential
            for alias in ("SARVAM_API_KEY", "SMARA_OCR_API_KEY", "SMARA_LLM_API_KEY", "sarvam"):
                val = resolve_local_credential(alias)
                if val and isinstance(val, str):
                    api_key = val
                    break
        except Exception:
            pass

    # Document AI is rooted at the host (not the chat `/v2` namespace).
    # Keep an explicit override for compatible private gateways.
    base_url = os.getenv("SARVAM_DOC_AI_BASE_URL") or os.getenv("SMARA_OCR_BASE_URL") or os.getenv("SARVAM_BASE_URL") or "https://api.sarvam.ai"
    model = os.getenv("SMARA_OCR_MODEL", "sarvam-vision-1.5")
    return base_url.rstrip("/"), api_key, model


class SarvamOCRClient:
    """Document and image digitization using Sarvam's OCR API with local pytesseract fallback."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ):
        resolved_url, resolved_key, resolved_model = resolve_ocr_credentials()
        self.base_url = (base_url or resolved_url).rstrip("/")
        self.api_key = api_key if api_key is not None else resolved_key
        self.model = model or resolved_model
        self._http = http_client

    async def digitize(
        self,
        file_path: str | Path,
        *,
        language: str = "en-IN",
        output_format: str = "md",
        content_type: str = "printed",
        max_wait_seconds: int = 60,
    ) -> OCRResult:
        path = Path(file_path).resolve()
        if not path.is_file():
            raise OCRError(f"OCR target file does not exist: {path}")

        raw_bytes = path.read_bytes()
        if not raw_bytes:
            raise OCRError(f"OCR target file is empty: {path}")
        if len(raw_bytes) > 20 * 1024 * 1024:
            raise OCRError("Document exceeds the 20 MB processing limit.")

        content_sha = hashlib.sha256(raw_bytes).hexdigest()
        mime_type, _ = mimetypes.guess_type(str(path))
        if not mime_type:
            suffix = path.suffix.lower()
            mime_type = {
                ".pdf": "application/pdf",
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
            }.get(suffix, "application/octet-stream")

        # If Sarvam key is configured, use Sarvam Cloud OCR
        if self.api_key:
            try:
                return await self._digitize_sarvam(
                    path.name,
                    raw_bytes,
                    mime_type=mime_type,
                    language=language,
                    output_format=output_format,
                    content_type=content_type,
                    content_sha=content_sha,
                    max_wait_seconds=max_wait_seconds,
                )
            except OCRError as exc:
                # Gemma 4 is a visual chat model, not a Parse/Document AI
                # entitlement. Use it only for image files and only when the
                # purpose-built endpoint is unavailable (404); auth, quota,
                # validation, and provider failures must remain fail-closed.
                if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} and "endpoint unavailable" in str(exc).lower():
                    return await self._digitize_gemma4_visual(
                        path.name,
                        raw_bytes,
                        mime_type=mime_type,
                        language=language,
                        output_format=output_format,
                        content_sha=content_sha,
                    )
                raise

        # Fallback to local pytesseract if installed
        return self._digitize_local_pytesseract(path, raw_bytes, content_sha)

    async def _digitize_sarvam(
        self,
        file_name: str,
        raw_bytes: bytes,
        mime_type: str,
        language: str,
        output_format: str,
        content_type: str,
        content_sha: str,
        max_wait_seconds: int,
    ) -> OCRResult:
        owns_client = self._http is None
        client = self._http or httpx.AsyncClient(timeout=httpx.Timeout(20.0))
        headers = _auth_headers(self.api_key, "api-subscription-key")

        data = {
            "language": language or "en-IN",
            "output_format": output_format or "md",
        }
        try:
            # Sarvam retired the earlier `/v2/job/digitise` route in favour of
            # Document AI. Strip a legacy `/v2` suffix from shared chat
            # profiles so OCR still reaches the host-level API.
            api_root = self.base_url
            if "api.sarvam.ai" in api_root and api_root.rstrip("/").endswith("/v2"):
                api_root = api_root.rstrip("/")[:-3].rstrip("/")
            job_prefix = f"{api_root}/doc-ai/v1/job"
            active_job_prefix = job_prefix
            # 1. Submit digitise job
            response = await client.post(
                f"{job_prefix}/digitise",
                headers=headers,
                data=data,
                files={"file": (file_name, raw_bytes, mime_type)},
            )
            # Keep compatibility with accounts still pinned to the legacy
            # digitisation route. A 404 is the only safe fallback trigger;
            # auth, quota, and validation failures must surface unchanged.
            if response.status_code == 404:
                legacy_root = self.base_url
                if "api.sarvam.ai" in legacy_root and not legacy_root.rstrip("/").endswith("/v2"):
                    legacy_root = legacy_root.rstrip("/") + "/v2"
                response = await client.post(
                    f"{legacy_root}/job/digitise",
                    headers=headers,
                    data={**data, "content_type": content_type or "printed"},
                    files={"file": (file_name, raw_bytes, mime_type)},
                )
                active_job_prefix = f"{legacy_root}/job"
            if response.status_code >= 400:
                detail = response.text[:200]
                if response.status_code == 404:
                    detail = "Document AI endpoint unavailable for this credential or account."
                raise OCRError(f"Sarvam OCR submission failed: HTTP {response.status_code} ({detail})")

            job = response.json()
            job_id = job.get("job_id") if isinstance(job, dict) else None
            if not isinstance(job_id, str) or not job_id.strip():
                raise OCRError("Sarvam OCR did not return a valid job ID.")

            # 2. Poll job status
            status = str(job.get("status", "pending")).lower()
            poll_interval = 2.0
            elapsed = 0.0
            while elapsed < max_wait_seconds:
                if status in {"completed", "partially_completed"}:
                    break
                if status in {"failed", "rejected"}:
                    raise OCRError(f"Sarvam OCR job {job_id} failed with status: {status}")

                await asyncio.sleep(poll_interval)
                elapsed += poll_interval

                status_resp = await client.get(f"{active_job_prefix}/{job_id}/status", headers=headers)
                if status_resp.status_code >= 400:
                    raise OCRError(f"Sarvam OCR status check failed: HTTP {status_resp.status_code}")
                status_payload = status_resp.json()
                status = str(status_payload.get("status", "pending")).lower()
            else:
                raise OCRError(f"Sarvam OCR job {job_id} timed out after {max_wait_seconds}s.")

            # 3. Retrieve download URL
            dl_resp = await client.get(f"{active_job_prefix}/{job_id}/download-url", headers=headers)
            if dl_resp.status_code >= 400:
                raise OCRError(f"Sarvam OCR download-url failed: HTTP {dl_resp.status_code}")
            dl_payload = dl_resp.json()
            download_url = dl_payload.get("download_url") or dl_payload.get("url")
            if not download_url:
                raise OCRError("Sarvam OCR did not return a valid download URL.")

            # 4. Download extracted text
            dl_headers = dl_payload.get("headers") or {}
            method = str(dl_payload.get("method", "GET")).upper()
            result_resp = await client.request(method, download_url, headers=dl_headers)
            if result_resp.status_code >= 400:
                raise OCRError(f"Failed to download OCR results: HTTP {result_resp.status_code}")

            extracted_text = _extract_text_from_archive(result_resp.content)
            if not extracted_text:
                raise OCRError("Sarvam OCR returned no readable text.")

            text_sha = hashlib.sha256(extracted_text.encode("utf-8")).hexdigest()
            return OCRResult(
                text=extracted_text,
                content_sha256=content_sha,
                text_sha256=text_sha,
                pages=max(1, extracted_text.count("\n---\n") + 1),
                format=output_format,
                language=language,
                provider="sarvam",
                job_id=job_id,
                character_count=len(extracted_text),
            )
        finally:
            if owns_client:
                await client.aclose()

    async def _digitize_gemma4_visual(
        self,
        file_name: str,
        raw_bytes: bytes,
        *,
        mime_type: str,
        language: str,
        output_format: str,
        content_sha: str,
    ) -> OCRResult:
        """Best-effort visual transcription for images when Document AI is absent.

        This deliberately does not accept PDFs or claim structured Parse
        semantics. Gemma 4 receives a base64 data URI through the OpenAI-
        compatible chat endpoint and the result is labelled separately from
        Sarvam Vision/Document AI so callers cannot mistake it for a verified
        document extraction.
        """
        if len(raw_bytes) > 7_500_000:
            raise OCRError("Gemma 4 visual OCR image exceeds the safe 10 MB encoded request budget.")
        supported_mimes = {"image/png", "image/jpeg", "image/webp"}
        if mime_type not in supported_mimes:
            raise OCRError(f"Gemma 4 visual OCR does not support image type: {mime_type}")

        owns_client = self._http is None
        client = self._http or httpx.AsyncClient(timeout=httpx.Timeout(35.0))
        try:
            base_url = self.base_url.rstrip("/")
            if base_url.endswith("/v1"):
                base_url = base_url[:-3].rstrip("/")
            if not base_url.endswith("/v2"):
                base_url = f"{base_url}/v2"
            url = f"{base_url}/chat/completions"
            prompt = (
                "Transcribe all visible text in this image exactly. Preserve line breaks "
                f"and numbers. The requested language is {language or 'en-IN'}. "
                "Do not infer missing text, summarize, or invent content. "
                "If there is no readable text, reply exactly NO_TEXT_FOUND."
            )
            payload = {
                "model": "gemma4",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64.b64encode(raw_bytes).decode('ascii')}"}},
                    ],
                }],
                "temperature": 0,
                "max_tokens": 2000,
            }
            response = await client.post(
                url,
                headers=_auth_headers(self.api_key, "api-subscription-key") | {"Content-Type": "application/json"},
                json=payload,
            )
            if response.status_code >= 400:
                detail = response.text[:240]
                raise OCRError(f"Gemma 4 visual OCR failed: HTTP {response.status_code} ({detail})")
            try:
                data = response.json()
            except ValueError as exc:
                raise OCRError("Gemma 4 visual OCR returned invalid JSON.") from exc
            message = ((data.get("choices") or [{}])[0] or {}).get("message") or {}
            content = message.get("content")
            if isinstance(content, list):
                content = "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict)).strip()
            extracted_text = str(content or "").strip()
            if not extracted_text or extracted_text == "NO_TEXT_FOUND":
                raise OCRError("Gemma 4 visual OCR returned no readable text.")
            text_sha = hashlib.sha256(extracted_text.encode("utf-8")).hexdigest()
            return OCRResult(
                text=extracted_text,
                content_sha256=content_sha,
                text_sha256=text_sha,
                pages=1,
                format=output_format,
                language=language,
                provider="sarvam-gemma4",
                model="gemma4",
                job_id=None,
                character_count=len(extracted_text),
            )
        finally:
            if owns_client:
                await client.aclose()

    def _digitize_local_pytesseract(self, path: Path, raw_bytes: bytes, content_sha: str) -> OCRResult:
        try:
            import pytesseract
            from PIL import Image
        except ImportError:
            raise OCRError("No Sarvam OCR key configured, and local 'pytesseract' is not installed.")

        try:
            image = Image.open(path)
            extracted_text = pytesseract.image_to_string(image).strip()
            if not extracted_text:
                raise OCRError("Local OCR returned empty text from image.")
            text_sha = hashlib.sha256(extracted_text.encode("utf-8")).hexdigest()
            return OCRResult(
                text=extracted_text,
                content_sha256=content_sha,
                text_sha256=text_sha,
                pages=1,
                format="txt",
                language="eng",
                provider="pytesseract",
                job_id=None,
                character_count=len(extracted_text),
            )
        except Exception as exc:
            raise OCRError(f"Local OCR processing failed: {exc}") from exc


def digitize_file_sync(file_path: str | Path, **kwargs) -> OCRResult:
    """Synchronous convenience wrapper for OCR digitization."""
    client = SarvamOCRClient()
    return asyncio.run(client.digitize(file_path, **kwargs))
