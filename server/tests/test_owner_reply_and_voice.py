"""Replies stay on the owner's channel, files can go over the WhatsApp link, long tasks send a note, and voice
notes are converted to WAV before speech-to-text."""
import asyncio
import io
import shutil
import sys
import wave
from types import SimpleNamespace

import httpx
import pytest

from src.ingest import media
from src.ingest.media import MediaError
from src import agent_loop
from src.loop import owner_channel
from src.loop.owner_channel import CURRENT_REPLY_CHANNEL, effective_reply


def _wav(seconds: float = 0.1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * seconds))
    return buf.getvalue()


def test_an_explicit_channel_wins_and_otherwise_the_runs_channel_is_used():
    assert effective_reply("telegram") == "telegram"
    token = CURRENT_REPLY_CHANNEL.set("wa_link")
    try:
        assert effective_reply(None) == "wa_link"
        assert effective_reply("whatsapp") == "whatsapp"
    finally:
        CURRENT_REPLY_CHANNEL.reset(token)
    assert effective_reply(None) is None


async def test_a_long_task_sends_a_note_on_the_channel_the_owner_used(monkeypatch):
    monkeypatch.setattr(agent_loop, "PROGRESS_FIRST_S", 0)
    monkeypatch.setattr(agent_loop, "PROGRESS_EVERY_S", 0)
    monkeypatch.setattr(agent_loop, "PROGRESS_MAX", 2)
    calls = []

    async def fake_send(app, tenant, payload, **kw):
        calls.append((payload["text"]["body"], kw))
        return "wa_link"

    monkeypatch.setattr(agent_loop, "send_to_owner", fake_send)
    tenant = SimpleNamespace(owner_phone="919000000001")
    await agent_loop._progress(None, {"channel": "wa_link"}, tenant)
    assert len(calls) == 2
    assert all(kw["reply_channel"] == "wa_link" and kw["kind"] == "owner_text" for _, kw in calls)
    assert calls[0][0].startswith("Still working on it")


async def test_voice_notes_are_sent_to_speech_as_wav(monkeypatch):
    async def fake_to_wav(audio: bytes) -> bytes:
        return b"WAVDATA-" + audio

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("api-subscription-key")
        seen["body"] = request.content
        return httpx.Response(200, json={"transcript": "ek sau rupaye"})

    monkeypatch.setattr(media, "to_wav", fake_to_wav)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    text = await media.transcribe("k-test", b"OggS-opus", "audio/ogg", client=client, url="https://stt.test/x")
    assert text == "ek sau rupaye"
    assert seen["key"] == "k-test"
    assert b'filename="voice.wav"' in seen["body"] and b"audio/wav" in seen["body"]
    assert b"WAVDATA-OggS-opus" in seen["body"]


async def test_a_speech_refusal_raises_and_is_logged_without_the_key(monkeypatch, caplog):
    async def fake_to_wav(audio: bytes) -> bytes:
        return _wav()

    monkeypatch.setattr(media, "to_wav", fake_to_wav)
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(400, json={"error": {"message": "Failed to read the file"}})))
    with pytest.raises(httpx.HTTPStatusError):
        await media.transcribe("k-secret-value", b"x", client=client, url="https://stt.test/x")
    assert "k-secret-value" not in caplog.text


async def test_missing_ffmpeg_gives_a_clear_message_not_a_crash(monkeypatch):
    async def no_ffmpeg(*args, **kwargs):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(media.asyncio, "create_subprocess_exec", no_ffmpeg)
    with pytest.raises(MediaError) as err:
        await media.to_wav(b"OggS")
    assert "could not read that voice note" in str(err.value)


@pytest.mark.skipif(sys.platform == "win32" or shutil.which("ffmpeg") is None,
                    reason="needs ffmpeg and a subprocess-capable event loop; the app image has both (Linux)")
async def test_to_wav_makes_a_real_wav_file():
    out = await media.to_wav(_wav(0.5))
    assert out[:4] == b"RIFF" and out[8:12] == b"WAVE"


class _FakeLink:
    def __init__(self):
        self.files = []

    async def send_file(self, tenant_id, to, filename, mime, caption, data):
        self.files.append((to, filename, mime, caption, data))
        return "F-1"


async def _record(*args, **kwargs):
    return None


async def test_a_file_for_an_owner_on_the_link_goes_over_the_link(monkeypatch):
    monkeypatch.setattr(owner_channel, "record", _record)
    link = _FakeLink()
    app = SimpleNamespace(state=SimpleNamespace(wa_link=link, pool=None, tg=None))
    tenant = SimpleNamespace(id="t1", owner_phone="919000000001", telegram_chat_id=None, owner_last_channel="telegram")
    where = await owner_channel.send_document_to_owner(app, tenant, b"%PDF-1", "statement.pdf", "application/pdf",
                                                       "Ravi", reply_channel="wa_link")
    assert where == "wa_link"
    assert link.files == [("919000000001", "statement.pdf", "application/pdf", "Ravi", b"%PDF-1")]


async def test_a_run_on_the_link_sends_its_file_there_even_if_telegram_was_used_last(monkeypatch):
    monkeypatch.setattr(owner_channel, "record", _record)
    link = _FakeLink()
    app = SimpleNamespace(state=SimpleNamespace(wa_link=link, pool=None, tg=None))
    tenant = SimpleNamespace(id="t1", owner_phone="919000000001", telegram_chat_id=None, owner_last_channel="telegram")
    token = CURRENT_REPLY_CHANNEL.set("wa_link")
    try:
        where = await owner_channel.send_document_to_owner(app, tenant, b"photo-bytes", "bill.jpg", "image/jpeg", "")
    finally:
        CURRENT_REPLY_CHANNEL.reset(token)
    assert where == "wa_link"
    assert link.files and link.files[0][1] == "bill.jpg"
