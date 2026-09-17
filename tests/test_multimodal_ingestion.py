import json

import pytest

from smara.multimodal_ingestion import YouTubeIngestionError, ingest_youtube_transcript, youtube_video_id


def test_youtube_video_id_accepts_single_video_urls_only():
    assert youtube_video_id("https://www.youtube.com/watch?v=abcdefghijk") == "abcdefghijk"
    assert youtube_video_id("https://youtu.be/abcdefghijk?t=10") == "abcdefghijk"
    assert youtube_video_id("abcdefghijk") == "abcdefghijk"
    with pytest.raises(YouTubeIngestionError):
        youtube_video_id("https://www.youtube.com/channel/example")


def test_youtube_transcript_is_timestamped_bounded_and_hashed():
    def fetcher(video_id, language):
        assert video_id == "abcdefghijk"
        assert language == "en"
        return type("Fetched", (), {
            "language_code": "en",
            "is_generated": False,
            "snippets": [
                {"text": "Hello world", "start": 0, "duration": 1.25},
                {"text": "Second line", "start": 1.25, "duration": 2},
            ],
        })()

    result = ingest_youtube_transcript(
        "https://www.youtube.com/watch?v=abcdefghijk",
        language="en",
        transcript_fetcher=fetcher,
    )
    assert result["kind"] == "youtube_transcript"
    assert result["canonical_url"].endswith("v=abcdefghijk")
    assert result["segment_count"] == 2
    assert result["duration_seconds"] == 3.25
    assert result["segments"][1]["start_seconds"] == 1.25
    assert len(result["transcript_sha256"]) == 64
    assert result["source_quality"] == "discovery_only"
    assert result["license_status"] == "unknown"


def test_youtube_transcript_rejects_over_limit_without_truncating():
    def fetcher(video_id, language):
        return [{"text": "x" * 1001, "start": 0, "duration": 1}]

    with pytest.raises(YouTubeIngestionError, match="character safety limit"):
        ingest_youtube_transcript("abcdefghijk", max_chars=1000, transcript_fetcher=fetcher)


def test_youtube_result_is_json_serializable():
    result = ingest_youtube_transcript(
        "abcdefghijk",
        transcript_fetcher=lambda _video_id, _language: [{"text": "ok", "start": 0, "duration": 1}],
    )
    assert json.loads(json.dumps(result, ensure_ascii=False))["text"].endswith("ok")
