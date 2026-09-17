"""Bounded, provenance-aware ingestion helpers for multimodal sources."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable


class YouTubeIngestionError(RuntimeError):
    """A YouTube transcript could not be safely ingested."""


_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_URL = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:[^#]*&)?v=|shorts/|embed/)|youtu\.be/)([A-Za-z0-9_-]{11})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class YouTubeTranscript:
    video_id: str
    canonical_url: str
    language: str
    is_generated: bool | None
    segments: tuple[dict[str, Any], ...]
    text: str
    transcript_sha256: str
    duration_seconds: float
    source_quality: str = "discovery_only"
    license_status: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "youtube_transcript",
            "video_id": self.video_id,
            "canonical_url": self.canonical_url,
            "language": self.language,
            "is_generated": self.is_generated,
            "segment_count": len(self.segments),
            "duration_seconds": self.duration_seconds,
            "segments": list(self.segments),
            "text": self.text,
            "transcript_sha256": self.transcript_sha256,
            "source_quality": self.source_quality,
            "license_status": self.license_status,
            "license_notice": "Transcript reuse and redistribution remain subject to the video's copyright and YouTube terms.",
        }


def youtube_video_id(value: str) -> str:
    """Extract exactly one YouTube video ID from a URL or bare ID."""
    raw = str(value or "").strip()
    if _YOUTUBE_ID.fullmatch(raw):
        return raw
    match = _YOUTUBE_URL.search(raw)
    if match:
        return match.group(1)
    raise YouTubeIngestionError("Only a single YouTube watch, shorts, embed, or youtu.be URL is accepted.")


def _segment_field(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _normalize_segments(items: Iterable[Any], *, max_segments: int, max_chars: int) -> tuple[tuple[dict[str, Any], ...], str, float]:
    segments: list[dict[str, Any]] = []
    total_chars = 0
    duration = 0.0
    for index, item in enumerate(items):
        if index >= max_segments:
            raise YouTubeIngestionError(f"Transcript exceeds the {max_segments}-segment safety limit.")
        text = str(_segment_field(item, "text", "") or "").strip()
        if not text:
            continue
        try:
            start = max(0.0, float(_segment_field(item, "start", 0.0) or 0.0))
            segment_duration = max(0.0, float(_segment_field(item, "duration", 0.0) or 0.0))
        except (TypeError, ValueError) as exc:
            raise YouTubeIngestionError("Transcript contains a segment with an invalid timestamp.") from exc
        end = start + segment_duration
        total_chars += len(text)
        if total_chars > max_chars:
            raise YouTubeIngestionError(f"Transcript exceeds the {max_chars}-character safety limit.")
        duration = max(duration, end)
        segments.append({
            "index": len(segments),
            "start_seconds": round(start, 3),
            "duration_seconds": round(segment_duration, 3),
            "end_seconds": round(end, 3),
            "text": text,
        })
    if not segments:
        raise YouTubeIngestionError("The transcript provider returned no readable segments.")
    text = "\n".join(f"[{item['start_seconds']:.3f}-{item['end_seconds']:.3f}] {item['text']}" for item in segments)
    return tuple(segments), text, duration


def _default_fetcher(video_id: str, language: str | None) -> Any:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError as exc:
        raise YouTubeIngestionError("youtube-transcript-api is not installed; transcript-first ingestion is unavailable.") from exc
    api = YouTubeTranscriptApi()
    try:
        if language:
            return api.fetch(video_id, languages=[language])
        return api.fetch(video_id)
    except TypeError:
        # Older library releases do not accept the languages keyword.
        return api.fetch(video_id)
    except Exception as exc:
        raise YouTubeIngestionError(f"YouTube transcript retrieval failed: {exc}") from exc


def ingest_youtube_transcript(
    url_or_id: str,
    *,
    language: str | None = None,
    max_segments: int = 5000,
    max_chars: int = 120_000,
    transcript_fetcher: Callable[[str, str | None], Any] | None = None,
) -> dict[str, Any]:
    """Fetch and normalize a bounded YouTube transcript without downloading media."""
    if max_segments < 1 or max_segments > 20_000:
        raise ValueError("max_segments must be between 1 and 20000")
    if max_chars < 1_000 or max_chars > 2_000_000:
        raise ValueError("max_chars must be between 1000 and 2000000")
    video_id = youtube_video_id(url_or_id)
    fetched = (transcript_fetcher or _default_fetcher)(video_id, language)
    items = getattr(fetched, "snippets", fetched)
    segments, text, duration = _normalize_segments(items, max_segments=max_segments, max_chars=max_chars)
    actual_language = str(getattr(fetched, "language_code", None) or language or "unknown")
    generated = getattr(fetched, "is_generated", None)
    if generated is not None:
        generated = bool(generated)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return YouTubeTranscript(
        video_id=video_id,
        canonical_url=f"https://www.youtube.com/watch?v={video_id}",
        language=actual_language,
        is_generated=generated,
        segments=segments,
        text=text,
        transcript_sha256=digest,
        duration_seconds=round(duration, 3),
    ).to_dict()


def youtube_result_json(result: dict[str, Any]) -> str:
    """Serialize an ingestion result deterministically for tool observations."""
    return json.dumps(result, ensure_ascii=False, sort_keys=True)


__all__ = ["YouTubeIngestionError", "YouTubeTranscript", "ingest_youtube_transcript", "youtube_result_json", "youtube_video_id"]
