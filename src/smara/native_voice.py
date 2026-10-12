"""Explicit Sarvam speech requests. No ambient recording or automatic sending."""
from __future__ import annotations

import argparse
import base64
import io
import json
from pathlib import Path
import wave

import httpx

API = "https://api.sarvam.ai"
MAX_AUDIO = 2 * 1024 * 1024
MAX_RESPONSE = 4 * 1024 * 1024
LANGUAGES = {"en-IN", "hi-IN", "bn-IN", "ta-IN", "te-IN", "kn-IN", "ml-IN", "mr-IN", "gu-IN", "pa-IN", "od-IN"}


class VoiceError(ValueError):
    """Fixed diagnostic, never raw provider text."""


def voice_key():
    from .native_documents import sarvam_key, DocumentError
    try:
        return sarvam_key("SMARA_VOICE_API_KEY")
    except DocumentError:
        raise VoiceError("Save a protected Sarvam key in Settings → Models before using voice. No audio was uploaded.") from None


def audio_bytes(encoded, mime):
    if not isinstance(encoded, str) or len(encoded) > ((MAX_AUDIO + 2) // 3) * 4:
        raise VoiceError("Audio exceeds the 2 MB upload limit. Record a shorter clip.")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        raise VoiceError("Invalid audio encoding") from None
    mime = str(mime).split(";", 1)[0].lower()
    valid = raw.startswith(b"RIFF") and raw[8:12] == b"WAVE" if mime == "audio/wav" else raw.startswith(b"\x1aE\xdf\xa3") if mime == "audio/webm" else raw.startswith(b"OggS") if mime == "audio/ogg" else False
    if not raw or len(raw) > MAX_AUDIO or not valid:
        raise VoiceError("Choose a WAV, WebM or Ogg recording with matching audio bytes")
    if mime == "audio/wav":
        try:
            with wave.open(io.BytesIO(raw)) as recording:
                if recording.getnframes() / recording.getframerate() > 30:
                    raise VoiceError("Quick transcription supports clips up to 30 seconds")
        except (wave.Error, EOFError):
            raise VoiceError("Invalid WAV recording") from None
    return raw, mime


def request(http, path, **kwargs):
    try:
        with http.stream("POST", API + path, headers={"api-subscription-key": voice_key()}, **kwargs) as response:
            if response.status_code != 200:
                raise VoiceError(f"Sarvam speech request failed (HTTP {response.status_code}). No automatic retry; check the saved key or try again later.")
            data = bytearray()
            for chunk in response.iter_bytes():
                if len(data) + len(chunk) > MAX_RESPONSE:
                    raise VoiceError("Speech response exceeds the 4 MB safety limit")
                data.extend(chunk)
            value = json.loads(data)
            if not isinstance(value, dict):
                raise ValueError
            return value
    except VoiceError:
        raise
    except (httpx.HTTPError, ValueError, TypeError):
        raise VoiceError("Speech provider or response is unavailable. No automatic retry.") from None


def operate(value, *, http=None):
    if not isinstance(value, dict):
        raise VoiceError("Invalid voice request")
    action = value.get("action")
    if action == "status":
        try:
            voice_key()
            ready = True
        except VoiceError:
            ready = False
        return {"configured": ready, "stt_model": "saaras:v3", "tts_model": "bulbul:v3", "max_clip_seconds": 25,
            "detail": "Voice sends only the clip or text you explicitly choose to Sarvam. Speech is billed separately."}
    if action not in {"transcribe", "synthesize"}:
        raise VoiceError("Unsupported voice action")
    language = value.get("language", "en-IN")
    if language not in LANGUAGES and not (action == "transcribe" and language == "unknown"):
        raise VoiceError("Choose a supported speech language")
    owns = http is None
    http = http or httpx.Client(timeout=25, follow_redirects=False, trust_env=False)
    try:
        if action == "transcribe":
            raw, mime = audio_bytes(value.get("audio"), value.get("mime"))
            extension = {"audio/wav": "wav", "audio/webm": "webm", "audio/ogg": "ogg"}[mime]
            result = request(http, "/speech-to-text", files={"file": ("recording." + extension, raw, mime)},
                data={"model": "saaras:v3", "mode": "transcribe", "language_code": language})
            transcript = result.get("transcript")
            if not isinstance(transcript, str) or len(transcript) > 30000:
                raise VoiceError("Speech provider returned no usable transcript")
            return {"transcript": transcript, "language": result.get("language_code"), "model": "saaras:v3"}
        text = value.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 2500:
            raise VoiceError("Read-aloud requests support 1..2500 characters per chunk")
        result = request(http, "/text-to-speech", json={"text": text, "language_code": language,
            "model": "bulbul:v3", "speaker": "shubh", "speech_sample_rate": 22050, "output_audio_codec": "wav"})
        audios = result.get("audios")
        if not isinstance(audios, list) or len(audios) != 1 or not isinstance(audios[0], str):
            raise VoiceError("Speech provider returned no playable audio")
        try:
            raw = base64.b64decode(audios[0], validate=True)
        except ValueError:
            raise VoiceError("Speech provider returned invalid audio") from None
        if len(raw) > MAX_AUDIO or not raw.startswith(b"RIFF") or raw[8:12] != b"WAVE":
            raise VoiceError("Speech provider returned unsupported or oversized audio")
        return {"audio": audios[0], "mime": "audio/wav", "model": "bulbul:v3"}
    finally:
        if owns:
            http.close()


def main(argv=None):
    from .native_runtime import configure_protocol_stdio
    import sys
    configure_protocol_stdio(sys.stdin, sys.stdout)
    parser = argparse.ArgumentParser(description="Explicit Sarvam voice input/output; audio or text is uploaded only by these commands")
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("status")
    stt = commands.add_parser("transcribe")
    stt.add_argument("file", type=Path)
    stt.add_argument("--language", default="unknown")
    tts = commands.add_parser("speak")
    tts.add_argument("text")
    tts.add_argument("--language", default="en-IN")
    tts.add_argument("--out", type=Path, required=True)
    args = vars(parser.parse_args(argv))
    try:
        if args["action"] == "transcribe":
            path = args.pop("file")
            if path.stat().st_size > MAX_AUDIO:
                raise VoiceError("Audio exceeds the 2 MB upload limit")
            args["audio"] = base64.b64encode(path.read_bytes()).decode()
            args["mime"] = {".wav": "audio/wav", ".webm": "audio/webm", ".ogg": "audio/ogg"}.get(path.suffix.lower(), "")
        output = args.pop("out", None)
        if output and output.exists():
            raise VoiceError("Output exists; choose a new audio filename")
        if output and (not output.parent.is_dir() or output.suffix.lower() != ".wav"):
            raise VoiceError("Choose a new .wav file in an existing output folder")
        if args["action"] == "speak":
            args["action"] = "synthesize"
        result = operate(args)
        if output:
            with output.open("xb") as stream:
                stream.write(base64.b64decode(result.pop("audio")))
            result["output"] = str(output.resolve())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (VoiceError, OSError) as exc:
        import sys
        print(str(exc) if isinstance(exc, VoiceError) else "Audio file is unavailable", file=sys.stderr)
        return 1
