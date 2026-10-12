import base64
import io
import json
import wave
import httpx
import pytest
from smara.native_voice import operate, VoiceError


def wav(seconds=1):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(8000); audio.writeframes(b"\0\0" * 8000 * seconds)
    return base64.b64encode(stream.getvalue()).decode()


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setattr("smara.native_voice.voice_key", lambda: "synthetic-not-a-secret")


def test_contract_roundtrip_offline(key):
    calls = []
    def transport(request):
        calls.append(request)
        assert request.headers["api-subscription-key"] == "synthetic-not-a-secret"
        if request.url.path == "/text-to-speech":
            value = json.loads(request.content)
            assert value["model"] == "bulbul:v3" and value["language_code"] == "en-IN"
            assert "pitch" not in value and "enable_preprocessing" not in value
            return httpx.Response(200, json={"audios": [wav()]})
        assert request.url.path == "/speech-to-text"
        assert b"saaras:v3" in request.content and b"filename=\"recording.wav\"" in request.content
        return httpx.Response(200, json={"transcript": "Synthetic speech", "language_code": "en-IN"})
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        audio = operate({"action": "synthesize", "text": "Synthetic speech"}, http=client)
        result = operate({"action": "transcribe", "audio": audio["audio"], "mime": "audio/wav", "language": "unknown"}, http=client)
    assert result["transcript"] == "Synthetic speech" and len(calls) == 2


@pytest.mark.parametrize("value", [{"action": "synthesize", "text": "x" * 2501}, {"action": "transcribe", "audio": "bad", "mime": "audio/webm"}, {"action": "transcribe", "audio": wav(31), "mime": "audio/wav"}, {"action": "synthesize", "text": "hi", "language": "unsupported"}])
def test_invalid_inputs_never_upload(key, value):
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected upload"))) as client:
        with pytest.raises(VoiceError):
            operate(value, http=client)


def test_provider_error_is_redacted_and_not_retried(key):
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(401, text="private provider body synthetic-not-a-secret")
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        with pytest.raises(VoiceError, match="HTTP 401") as error:
            operate({"action": "synthesize", "text": "Hello"}, http=client)
    assert "private" not in str(error.value) and len(calls) == 1


def test_voice_does_not_inherit_dedicated_ocr_alias(monkeypatch):
    aliases = []
    monkeypatch.setattr("smara.native_documents.sarvam_key", lambda alias: aliases.append(alias) or "synthetic")
    assert operate({"action": "status"})["configured"]
    assert aliases == ["SMARA_VOICE_API_KEY"]


def test_cli_will_not_overwrite_or_upload_before_output_validation(tmp_path, monkeypatch):
    from smara.native_voice import main
    monkeypatch.setattr("smara.native_voice.operate", lambda *_args, **_kwargs: pytest.fail("Unexpected paid request"))
    existing = tmp_path / "existing.wav"
    existing.write_bytes(b"synthetic existing file")
    assert main(["speak", "Synthetic text", "--out", str(existing)]) == 1
    assert main(["speak", "Synthetic text", "--out", str(tmp_path / "missing" / "new.wav")]) == 1
    assert existing.read_bytes() == b"synthetic existing file"
