"""Tests for the real ASR engine (V2-30): OpenAI-compatible transcriber.

The endpoint is answered by an httpx mock transport: audio in,
text out, no real network, no real credentials. A fake key is
used throughout, and one test asserts it never appears in logs.
"""

import httpx
import pytest

from puspa.voice.asr import ASRError, OpenAICompatibleTranscriber

FAKE_KEY = "sk-test-asr-key"
BASE_URL = "https://speech.example.test/v1"
AUDIO = b"fake-webm-recording-bytes"


def _transport(answer=None, status=200, check=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if check is not None:
            check(request)
        return httpx.Response(status, json=answer if answer is not None else {"text": "halo dunia"})

    return httpx.MockTransport(handler)


def test_transcribes_audio_to_text():
    seen = {}

    def check(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = request.content

    client = httpx.Client(transport=_transport(check=check))
    transcriber = OpenAICompatibleTranscriber(BASE_URL, FAKE_KEY, http_client=client)
    result = transcriber.transcribe(AUDIO, mime_type="audio/webm")
    assert result.text == "halo dunia"
    assert result.engine == "openai-compatible"
    assert seen["url"] == f"{BASE_URL}/audio/transcriptions"
    assert seen["auth"] == f"Bearer {FAKE_KEY}"
    assert b"whisper-1" in seen["body"]
    assert AUDIO in seen["body"]


def test_sends_language_and_model_when_configured():
    seen = {}

    def check(request):
        seen["body"] = request.content

    client = httpx.Client(
        transport=_transport(answer={"text": "cek harga", "language": "id"}, check=check)
    )
    transcriber = OpenAICompatibleTranscriber(
        BASE_URL, FAKE_KEY, model="whisper-large-v3", language="id", http_client=client
    )
    result = transcriber.transcribe(AUDIO, mime_type="audio/webm")
    assert result.text == "cek harga"
    assert result.language == "id"
    assert b"whisper-large-v3" in seen["body"]
    assert b'"id"' in seen["body"] or b"id" in seen["body"]


def test_empty_audio_returns_empty_without_calling():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"text": "should not happen"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    transcriber = OpenAICompatibleTranscriber(BASE_URL, FAKE_KEY, http_client=client)
    result = transcriber.transcribe(b"", mime_type="audio/webm")
    assert result.text == ""
    assert calls == []


def test_auth_failure_is_a_loud_asr_error():
    client = httpx.Client(transport=_transport(status=401, answer={"error": "bad key"}))
    transcriber = OpenAICompatibleTranscriber(BASE_URL, FAKE_KEY, http_client=client)
    with pytest.raises(ASRError, match="refused the key"):
        transcriber.transcribe(AUDIO, mime_type="audio/webm")


def test_missing_text_field_is_an_asr_error():
    client = httpx.Client(transport=_transport(answer={"no_text": True}))
    transcriber = OpenAICompatibleTranscriber(BASE_URL, FAKE_KEY, http_client=client)
    with pytest.raises(ASRError, match="without a text field"):
        transcriber.transcribe(AUDIO, mime_type="audio/webm")


def test_network_failure_is_an_asr_error_without_the_key(caplog):
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    transcriber = OpenAICompatibleTranscriber(BASE_URL, FAKE_KEY, http_client=client)
    with caplog.at_level("WARNING"), pytest.raises(ASRError, match="unreachable"):
        transcriber.transcribe(AUDIO, mime_type="audio/webm")
    assert FAKE_KEY not in caplog.text


def test_empty_key_and_url_are_refused():
    with pytest.raises(ValueError, match="api_key"):
        OpenAICompatibleTranscriber(BASE_URL, "  ")
    with pytest.raises(ValueError, match="base_url"):
        OpenAICompatibleTranscriber("  ", FAKE_KEY)
