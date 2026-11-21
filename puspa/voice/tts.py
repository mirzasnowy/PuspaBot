"""Text-to-speech engines behind the voice reply (V2-30).

The TTS chain, in the order the user experiences it:

1. **Edge TTS (default, no key)**: :mod:`puspa.voice.edge_tts`,
   Microsoft neural voices, free. When it fails or is blocked,
   the server says so and the PWA falls back to the browser's
   built-in voice (``speechSynthesis``); that fallback lives in
   the PWA (``apps/pwa/src/state/speech.ts``), not here.
2. **BYOK, chosen explicitly in Settings**: ElevenLabs first,
   then any OpenAI-compatible TTS endpoint, then Azure Speech
   tier F0 (500k TTS characters + 5 hours STT a month, permanent,
   but needs an Azure account). A provider the user picked in
   Settings wins over the Edge default: the choice is theirs,
   and the key for it lives only in the vault.

Every engine here takes its key as a call argument only, never
logs it, and never puts it in an error message. HTTP goes
through an injectable :class:`httpx.Client` so tests answer
with a mock transport and no real network.
"""

from __future__ import annotations

import httpx

from .edge_tts import DEFAULT_EDGE_VOICE, EdgeTTSClient

__all__ = [
    "AzureTTS",
    "ElevenLabsTTS",
    "OpenAICompatibleTTS",
    "SynthesisResult",
    "TTSError",
]

#: Longest reply the speak endpoint synthesizes in one call
#: (bounds the cost of one TTS call; longer replies are cut
#: by the caller before they get here).
MAX_SPEAK_CHARS = 2000


class TTSError(RuntimeError):
    """A TTS provider failed (network, auth, or bad response)."""


class SynthesisResult:
    """Spoken audio from one engine."""

    def __init__(self, audio: bytes, *, mime_type: str, engine: str) -> None:
        self.audio = audio
        self.mime_type = mime_type
        self.engine = engine


def _post_audio(
    client: httpx.Client | None,
    url: str,
    *,
    headers: dict,
    json: dict | None = None,
    content: bytes | None = None,
    timeout_seconds: float,
) -> httpx.Response:
    """One audio POST; builds a short-lived client when none is injected."""
    if client is not None:
        return client.post(
            url, headers=headers, json=json, content=content, timeout=timeout_seconds
        )
    with httpx.Client(timeout=timeout_seconds) as owned:
        return owned.post(url, headers=headers, json=json, content=content)


def _check_audio_response(response: httpx.Response, engine: str) -> bytes:
    if response.status_code in {401, 403}:
        raise TTSError(f"{engine} refused the key (check the vault key)")
    if response.status_code >= 400:
        raise TTSError(f"{engine} answered HTTP {response.status_code}")
    if not response.content:
        raise TTSError(f"{engine} answered without audio")
    return response.content


class ElevenLabsTTS:
    """ElevenLabs text-to-speech (BYOK).

    ``voice_id`` is the ElevenLabs voice id the user picked in
    Settings (not a display name). The key is sent only as the
    ``xi-api-key`` header on this one request.
    """

    name = "elevenlabs"

    def __init__(
        self,
        api_key: str,
        voice_id: str,
        model: str = "eleven_multilingual_v2",
        *,
        http_client: httpx.Client | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("ElevenLabs api_key must be a non-empty string")
        if not isinstance(voice_id, str) or not voice_id.strip():
            raise ValueError("ElevenLabs voice_id must be a non-empty string")
        self.voice_id = voice_id
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._api_key = api_key
        self._http_client = http_client

    def synthesize(self, text: str) -> SynthesisResult:
        """Speak ``text``; returns MP3 bytes."""
        url = (
            "https://api.elevenlabs.io/v1/text-to-speech/"
            f"{self.voice_id}?output_format=mp3_44100_128"
        )
        try:
            response = _post_audio(
                self._http_client,
                url,
                headers={"xi-api-key": self._api_key, "Content-Type": "application/json"},
                json={"text": text, "model_id": self.model},
                timeout_seconds=self.timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise TTSError(f"ElevenLabs unreachable: {type(exc).__name__}") from exc
        audio = _check_audio_response(response, "ElevenLabs")
        return SynthesisResult(audio, mime_type="audio/mpeg", engine=self.name)


class OpenAICompatibleTTS:
    """Any OpenAI-compatible ``/audio/speech`` endpoint (BYOK).

    Same shape as the ASR side: ``base_url`` is the provider's
    ``/v1`` root, ``model`` for example ``tts-1``, ``voice`` the
    provider's voice name.
    """

    name = "openai-compatible"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "tts-1",
        voice: str = "alloy",
        *,
        http_client: httpx.Client | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("TTS base_url must be a non-empty string")
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("TTS api_key must be a non-empty string")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.voice = voice
        self.timeout_seconds = timeout_seconds
        self._api_key = api_key
        self._http_client = http_client

    def synthesize(self, text: str) -> SynthesisResult:
        """Speak ``text``; returns MP3 bytes."""
        url = f"{self.base_url}/audio/speech"
        try:
            response = _post_audio(
                self._http_client,
                url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self.model,
                    "input": text,
                    "voice": self.voice,
                    "response_format": "mp3",
                },
                timeout_seconds=self.timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise TTSError(f"TTS endpoint unreachable: {type(exc).__name__}") from exc
        audio = _check_audio_response(response, "TTS endpoint")
        return SynthesisResult(audio, mime_type="audio/mpeg", engine=self.name)


class AzureTTS:
    """Azure Speech TTS, tier F0 (BYOK).

    The F0 tier is free forever (500k characters a month), but
    it needs an Azure account and a Speech resource, so it is
    offered as a choice in Settings, never the default. The
    request body is SSML; the key is sent only as the
    ``Ocp-Apim-Subscription-Key`` header on this one request.
    """

    name = "azure"

    def __init__(
        self,
        api_key: str,
        region: str,
        voice: str = DEFAULT_EDGE_VOICE,
        *,
        http_client: httpx.Client | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("Azure api_key must be a non-empty string")
        if not isinstance(region, str) or not region.strip():
            raise ValueError("Azure region must be a non-empty string")
        if not isinstance(voice, str) or not voice.strip():
            raise ValueError("Azure voice must be a non-empty string")
        self.region = region
        self.voice = voice
        self.timeout_seconds = timeout_seconds
        self._api_key = api_key
        self._http_client = http_client

    def synthesize(self, text: str) -> SynthesisResult:
        """Speak ``text``; returns MP3 bytes."""
        from xml.sax.saxutils import escape as xml_escape

        url = f"https://{self.region}.tts.speech.microsoft.com/cognitiveservices/v1"
        lang = self.voice[:5]
        ssml = (
            f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
            f'xml:lang="{lang}"><voice name="{self.voice}">'
            f"{xml_escape(text)}</voice></speak>"
        )
        try:
            response = _post_audio(
                self._http_client,
                url,
                headers={
                    "Ocp-Apim-Subscription-Key": self._api_key,
                    "Content-Type": "application/ssml+xml",
                    "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
                },
                content=ssml.encode(),
                timeout_seconds=self.timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise TTSError(f"Azure Speech unreachable: {type(exc).__name__}") from exc
        audio = _check_audio_response(response, "Azure Speech")
        return SynthesisResult(audio, mime_type="audio/mpeg", engine=self.name)


#: TTS providers a user can pick in Settings. ``edge`` is the
#: free default (no key); ``browser`` means the PWA speaks with
#: the built-in voice and the server is never called for it.
TTS_PROVIDERS = ("edge", "browser", "elevenlabs", "openai-compatible", "azure")


async def synthesize_with_edge(
    text: str, voice: str = DEFAULT_EDGE_VOICE, *, edge_connect=None
) -> SynthesisResult:
    """Speak ``text`` through Edge TTS; raises EdgeTTSError on failure."""
    audio = await EdgeTTSClient(connect=edge_connect).synthesize(text, voice)
    return SynthesisResult(audio, mime_type="audio/mpeg", engine="edge")
