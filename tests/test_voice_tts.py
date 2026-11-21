"""Tests for the BYOK TTS engines (V2-30): ElevenLabs, OpenAI-compatible, Azure.

Every provider is answered by an httpx mock transport: text in,
audio bytes out, no real network, no real credentials. A fake key
is used throughout, and one test asserts it never appears in logs
or in an error message.
"""

import json

import httpx
import pytest

from puspa.voice.tts import (
    AzureTTS,
    ElevenLabsTTS,
    OpenAICompatibleTTS,
    TTSError,
)

FAKE_KEY = "sk-test-tts-key"


def test_elevenlabs_posts_text_and_returns_audio():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("xi-api-key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=b"ELEVEN-MP3", headers={"content-type": "audio/mpeg"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    engine = ElevenLabsTTS(FAKE_KEY, "voice-abc", http_client=client)
    result = engine.synthesize("Halo, ini balasan.")
    assert result.audio == b"ELEVEN-MP3"
    assert result.mime_type == "audio/mpeg"
    assert result.engine == "elevenlabs"
    assert seen["url"].startswith("https://api.elevenlabs.io/v1/text-to-speech/voice-abc")
    assert seen["key"] == FAKE_KEY
    assert seen["body"]["text"] == "Halo, ini balasan."
    assert seen["body"]["model_id"] == "eleven_multilingual_v2"


def test_elevenlabs_auth_failure_names_the_key_problem_not_the_key():
    def handler(request):
        return httpx.Response(401, json={"detail": "bad key"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    engine = ElevenLabsTTS(FAKE_KEY, "voice-abc", http_client=client)
    with pytest.raises(TTSError) as excinfo:
        engine.synthesize("halo")
    assert FAKE_KEY not in str(excinfo.value)


def test_openai_compatible_tts_posts_input_voice_model():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=b"OAI-MP3")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    engine = OpenAICompatibleTTS(
        "https://api.openai.com/v1", FAKE_KEY, model="tts-1-hd", voice="nova", http_client=client
    )
    result = engine.synthesize("Harga beras turun.")
    assert result.audio == b"OAI-MP3"
    assert result.engine == "openai-compatible"
    assert seen["url"] == "https://api.openai.com/v1/audio/speech"
    assert seen["auth"] == f"Bearer {FAKE_KEY}"
    assert seen["body"] == {
        "model": "tts-1-hd",
        "input": "Harga beras turun.",
        "voice": "nova",
        "response_format": "mp3",
    }


def test_azure_posts_ssml_to_the_region_endpoint():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("ocp-apim-subscription-key")
        seen["body"] = request.content.decode()
        return httpx.Response(200, content=b"AZURE-MP3")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    engine = AzureTTS(FAKE_KEY, "southeastasia", voice="id-ID-GadisNeural", http_client=client)
    result = engine.synthesize("Selamat pagi <semua> & teman.")
    assert result.audio == b"AZURE-MP3"
    assert result.engine == "azure"
    assert seen["url"] == "https://southeastasia.tts.speech.microsoft.com/cognitiveservices/v1"
    assert seen["key"] == FAKE_KEY
    assert 'name="id-ID-GadisNeural"' in seen["body"]
    assert "&lt;semua&gt;" in seen["body"]


def test_network_failure_never_leaks_the_key(caplog):
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    engine = ElevenLabsTTS(FAKE_KEY, "voice-abc", http_client=client)
    with caplog.at_level("WARNING"), pytest.raises(TTSError, match="unreachable"):
        engine.synthesize("halo")
    assert FAKE_KEY not in caplog.text


def test_empty_key_and_voice_are_refused():
    with pytest.raises(ValueError, match="api_key"):
        ElevenLabsTTS("  ", "voice-abc")
    with pytest.raises(ValueError, match="voice_id"):
        ElevenLabsTTS(FAKE_KEY, "  ")
    with pytest.raises(ValueError, match="region"):
        AzureTTS(FAKE_KEY, "  ")
