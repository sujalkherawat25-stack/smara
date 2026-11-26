"""Voice notes longer than the speech service's 30 seconds, and server errors staying visible in the logs."""
import array
import asyncio
import io
import logging
import math
import struct
import subprocess
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from src import health_watch, server_logs
from src.agent import reader
from src.ingest import media, owner_flow
from src.ingest.media import MediaError

SERVER = Path(__file__).resolve().parents[1]
RATE = 16000


def _tone(seconds: float, quiet: tuple = ()) -> bytes:
    """16 kHz mono samples: a steady tone, silent between each (from, to) pair of seconds."""
    samples = array.array("h", (int(8000 * math.sin(i / 5)) for i in range(int(RATE * seconds))))
    for begin, end in quiet:
        for i in range(int(RATE * begin), int(RATE * end)):
            samples[i] = 0
    return samples.tobytes()


def _wav(pcm: bytes) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return out.getvalue()


def _pcm(wav: bytes) -> bytes:
    with wave.open(io.BytesIO(wav)) as w:
        return w.readframes(w.getnframes())


def _seconds(wav: bytes) -> float:
    return len(_pcm(wav)) / 2 / RATE


def _uploaded_wav(request: httpx.Request) -> bytes:
    body = request.content
    begin = body.index(b"RIFF")
    return body[begin:begin + 8 + struct.unpack("<I", body[begin + 4:begin + 8])[0]]


@pytest.fixture
def no_ffmpeg_needed(monkeypatch):
    async def same(audio: bytes) -> bytes:
        return audio

    async def no_wait(seconds):
        return None

    monkeypatch.setattr(media, "to_wav", same)
    monkeypatch.setattr(media.asyncio, "sleep", no_wait)


def test_a_short_recording_is_sent_exactly_as_it_is():
    wav = _wav(_tone(29))
    assert media.split_wav(wav) == [wav]


@pytest.mark.parametrize("seconds", [29.1, 31, 58.5, 75, 300])
def test_a_long_recording_is_cut_into_pieces_the_service_takes_without_losing_sound(seconds):
    pcm = _tone(seconds)
    pieces = media.split_wav(_wav(pcm))
    assert len(pieces) > 1
    assert all(2 <= _seconds(p) <= media.STT_PIECE_S for p in pieces), [_seconds(p) for p in pieces]
    assert b"".join(_pcm(p) for p in pieces) == pcm


def test_the_cut_falls_in_a_pause_so_words_are_not_split():
    pieces = media.split_wav(_wav(_tone(70, quiet=((23.0, 23.6), (47.0, 47.6)))))
    first, second = _seconds(pieces[0]), _seconds(pieces[1])
    assert 23.0 <= first <= 23.6
    assert 47.0 <= first + second <= 47.6


def test_a_piped_ffmpeg_file_with_no_length_in_its_header_is_still_measured():
    wav = bytearray(_wav(_tone(65)))
    wav[4:8] = wav[40:44] = b"\xff\xff\xff\xff"
    pieces = media.split_wav(bytes(wav))
    assert len(pieces) == 3
    assert sum(_seconds(p) for p in pieces) == pytest.approx(65)


async def test_a_75_second_note_is_transcribed_whole_by_a_service_that_refuses_over_30(no_ffmpeg_needed):
    lengths = []

    def service(request: httpx.Request):
        lengths.append(_seconds(_uploaded_wav(request)))
        if lengths[-1] > 30:
            return httpx.Response(400, json={"error": {"message": "Audio duration exceeds the maximum limit of 30 seconds"}})
        return httpx.Response(200, json={"transcript": "words"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(service)) as client:
        text = await media.transcribe("k-test", _wav(_tone(75)), client=client, url="https://stt.test/x")
    assert text == " ".join(["words"] * len(lengths)) and len(lengths) >= 3
    assert sum(lengths) == pytest.approx(75)


async def test_the_words_stay_in_order_even_when_a_later_piece_answers_first(monkeypatch, no_ffmpeg_needed):
    monkeypatch.setattr(media, "split_wav", lambda wav: [b"RIFF-one", b"RIFF-two", b"RIFF-three", b"RIFF-quiet"])
    later_done = asyncio.Event()

    async def service(request: httpx.Request):
        if b"RIFF-one" in request.content:
            await later_done.wait()
            return httpx.Response(200, json={"transcript": " Send the file "})
        if b"RIFF-two" in request.content:
            return httpx.Response(200, json={"transcript": "to nobody"})
        later_done.set()
        return httpx.Response(200, json={"transcript": "until I say" if b"RIFF-three" in request.content else ""})

    async with httpx.AsyncClient(transport=httpx.MockTransport(service)) as client:
        text = await asyncio.wait_for(media.transcribe("k-test", b"x", client=client, url="https://stt.test/x"), 5)
    assert text == "Send the file to nobody until I say"


async def test_if_one_piece_fails_nothing_is_returned(monkeypatch, no_ffmpeg_needed):
    monkeypatch.setattr(media, "split_wav", lambda wav: [b"RIFF-one", b"RIFF-two"])

    def service(request: httpx.Request):
        if b"RIFF-two" in request.content:
            return httpx.Response(400, json={"error": {"message": "bad"}})
        return httpx.Response(200, json={"transcript": "send the money report"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(service)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await media.transcribe("k-test", b"x", client=client, url="https://stt.test/x")


@pytest.mark.parametrize("status", [429, 503])
async def test_a_busy_speech_service_is_asked_again(no_ffmpeg_needed, status):
    calls = []

    def service(request: httpx.Request):
        calls.append(1)
        return httpx.Response(status) if len(calls) < 3 else httpx.Response(200, json={"transcript": "ok"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(service)) as client:
        assert await media.transcribe("k-test", b"x", client=client, url="https://stt.test/x") == "ok"
    assert len(calls) == 3


async def test_a_service_that_stays_busy_is_not_asked_forever(no_ffmpeg_needed):
    calls = []

    def service(request: httpx.Request):
        calls.append(1)
        return httpx.Response(429)

    async with httpx.AsyncClient(transport=httpx.MockTransport(service)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await media.transcribe("k-test", b"x", client=client, url="https://stt.test/x")
    assert len(calls) == media.ATTEMPTS


async def test_a_note_over_five_minutes_is_refused_in_plain_words_and_costs_nothing(no_ffmpeg_needed):
    def service(request: httpx.Request):
        raise AssertionError("sent to the speech service")

    async with httpx.AsyncClient(transport=httpx.MockTransport(service)) as client:
        with pytest.raises(MediaError, match="5 min 20 s long. I can take up to 5 minutes"):
            await media.transcribe("k-test", _wav(_tone(320)), client=client, url="https://stt.test/x")


@pytest.mark.parametrize("problem, expected", [
    (MediaError("That voice note is 6 min 0 s long. I can take up to 5 minutes: please send it in shorter parts, or type it."), None),
    (RuntimeError("service down"), owner_flow.VOICE_FAILED)])
async def test_the_owner_is_told_what_to_do_about_their_voice_note(monkeypatch, problem, expected):
    async def fetched(app, msg):
        return b"OggS", "audio/ogg"

    async def broken(*args, **kwargs):
        raise problem

    monkeypatch.setattr(owner_flow, "fetch_media", fetched)
    monkeypatch.setattr(owner_flow, "transcribe", broken)
    app = SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(sarvam_api_key="k", sarvam_stt_url="u")))
    assert await owner_flow._transcribe(app, SimpleNamespace()) == ("", expected or str(problem))


async def test_an_attached_recording_that_is_too_long_says_so(monkeypatch):
    async def fetched(app, ref, ctx):
        return b"audio", "audio/mpeg", "meeting.mp3"

    async def too_long(*args, **kwargs):
        raise MediaError("That voice note is 40 min 0 s long.")

    monkeypatch.setattr(reader, "_fetch", fetched)
    monkeypatch.setattr(reader, "transcribe", too_long)
    app = SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(sarvam_api_key="k", sarvam_stt_url="u")))
    result = await reader.read_attachment(app, None, {"file_ref": "local:x"})
    assert result["error"] == "That voice note is 40 min 0 s long."


# ---------------------------------------------------------------- logs

def test_warnings_and_tracebacks_still_reach_the_logs_after_the_error_counter_is_installed():
    """In a separate Python, as the server runs: pytest's own handlers would hide the fault."""
    script = ("import logging\n"
              "from src import health_watch\n"
              "health_watch.install(); health_watch.install()\n"
              "log = logging.getLogger('src.ingest.owner_flow')\n"
              "log.warning('speech service answered %s', 400)\n"
              "try:\n"
              "    raise RuntimeError('call failed for 919000000001')\n"
              "except RuntimeError:\n"
              "    log.exception('owner voice transcription failed')\n"
              "log.info('chatter')\n"
              "print(health_watch.summary())\n")
    done = subprocess.run([sys.executable, "-c", script], cwd=SERVER, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stderr.count("speech service answered 400") == 1
    assert done.stderr.count("owner voice transcription failed") == 1
    assert "Traceback" in done.stderr and "RuntimeError: call failed for ...0001" in done.stderr
    assert "chatter" not in done.stderr
    assert "919000000001" not in done.stderr + done.stdout
    assert "owner voice transcription failed" in done.stdout, "the hourly report still counts it"


@pytest.mark.parametrize("line, secret", [
    ("Client error '401' for url 'https://api.telegram.org/bot12345:made-up-token_x/sendMessage'", "made-up-token_x"),
    ("https://api.telegram.org/file/bot12345:made-up-token_x/voice.oga", "made-up-token_x"),
    ("could not connect to postgresql://smara:p%40ss-word@db:5432/smara", "p%40ss-word"),
    ("connection failed: host=db user=smara password=hunter2hunter dbname=smara", "hunter2hunter"),
    ("GET https://graph.example/v20.0/me?access_token=made-up-graph-1&x=1", "made-up-graph-1"),
    ("GET /webhook?hub.mode=subscribe&hub.verify_token=verify-me-1234", "verify-me-1234"),
    ("sent with Authorization: Bearer made-up-bearer-123456", "made-up-bearer-123456"),
    ("DETAIL: Key (phone)=(919000000001) already exists.", "919000000001"),
    ("no reply from +919000000002", "919000000002"),
])
def test_secrets_and_phone_numbers_are_hidden(line, secret):
    assert secret not in server_logs.redact(line)


def test_any_configured_secret_is_hidden_wherever_it_appears(monkeypatch):
    monkeypatch.setenv("SARVAM_API_KEY", "plain-looking-value-77")
    monkeypatch.setenv("LLM_API_KEYS", "first-key-000111, second-key-000222")
    text = server_logs.redact("bad value plain-looking-value-77 then second-key-000222")
    assert text == "bad value <hidden> then <hidden>"


def test_what_is_needed_to_find_a_fault_is_kept():
    line = ('File "/app/src/ingest/media.py", line 104: 400 Bad Request for url https://api.sarvam.ai/speech-to-text '
            "tenant 1a2b3c4d-1111-4c1e-9b2f-123456789012 task 0a1b2c3d amount 12500 at 2026-11-26 14:09:44,952")
    assert server_logs.redact(line) == line


def test_the_hourly_report_hides_secrets_in_its_examples():
    health_watch.reset()
    try:
        health_watch.tool_failed("send", ValueError("https://api.telegram.org/bot1:SECRETSECRET/x refused 919000000001"))
        text = health_watch.summary()
        assert "SECRETSECRET" not in text and "919000000001" not in text
    finally:
        health_watch.reset()
        logging.getLogger().removeHandler(health_watch._handler)
