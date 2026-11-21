"""Real speech-to-text behind the voice pipeline (V2-30).

The P-03 recorder already uploads audio; until now the only
transcriber behind it was a test double, because no engine had
been chosen. V2-30 wires a real one the user chooses themselves:
an OpenAI-compatible ``/audio/transcriptions`` endpoint, paid
for with the user's own key (BYOK). The key lives only in the
vault (see :mod:`puspa.pwa.voice_settings`); this module only
ever receives it as a call argument and never logs it, never
writes it to a file, and never puts it in an error message.

The endpoint contract is the OpenAI audio-transcriptions shape,
which several providers speak (OpenAI, Groq, DeepSeek, Together,
and any local server that mimics it): a multipart POST with
``file`` (the recording), ``model`` (for example ``whisper-1``),
and optional ``language``, returning JSON with a ``text`` field.
No provider SDK is used, so any endpoint that speaks this shape
works by pointing ``base_url`` at it.
"""

from __future__ import annotations

import logging

import httpx

from .transcriber import Transcription

__all__ = ["ASRError", "OpenAICompatibleTranscriber"]

logger = logging.getLogger(__name__)


class ASRError(RuntimeError):
    """The speech endpoint failed (network, auth, or bad response)."""


class OpenAICompatibleTranscriber:
    """Transcribe through an OpenAI-compatible speech endpoint.

    ``base_url`` is the provider's ``/v1`` root (the
    ``/audio/transcriptions`` path is appended here, never stored
    with a key in it). ``api_key`` is sent only as an
    ``Authorization: Bearer`` header on this one request.
    ``http_client`` is an injectable :class:`httpx.Client` so
    tests can answer with a mock transport and no real network;
    without one a short-lived client is built per call.
    """

    name = "openai-compatible"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "whisper-1",
        *,
        language: str | None = None,
        http_client: httpx.Client | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("ASR base_url must be a non-empty string")
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("ASR api_key must be a non-empty string")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("ASR model must be a non-empty string")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.language = language
        self.timeout_seconds = timeout_seconds
        self._api_key = api_key
        self._http_client = http_client

    def transcribe(self, audio: bytes, *, mime_type: str) -> Transcription:
        """Send one recording to the endpoint and return its text."""
        if not audio:
            return Transcription(text="", language=self.language, engine=self.name)
        url = f"{self.base_url}/audio/transcriptions"
        # The filename extension follows the mime type so the
        # endpoint can guess the container; the bytes are unchanged.
        suffix = {
            "audio/webm": "webm",
            "audio/mp4": "m4a",
            "audio/mpeg": "mp3",
            "audio/wav": "wav",
            "audio/x-wav": "wav",
            "audio/ogg": "ogg",
            "audio/aac": "aac",
            "audio/3gpp": "3gp",
        }.get(mime_type, "webm")
        data = {"model": self.model}
        if self.language:
            data["language"] = self.language
        files = {"file": (f"recording.{suffix}", audio, mime_type or "audio/webm")}
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            if self._http_client is not None:
                response = self._http_client.post(
                    url, data=data, files=files, headers=headers, timeout=self.timeout_seconds
                )
            else:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.post(url, data=data, files=files, headers=headers)
        except httpx.HTTPError as exc:
            # Never include the request (which carries the key
            # header) in the message; only the failure kind.
            raise ASRError(f"ASR endpoint unreachable: {type(exc).__name__}") from exc
        if response.status_code in {401, 403}:
            raise ASRError("ASR endpoint refused the key (check the vault key)")
        if response.status_code >= 400:
            raise ASRError(f"ASR endpoint answered HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ASRError("ASR endpoint answered non-JSON") from exc
        text = payload.get("text") if isinstance(payload, dict) else None
        if not isinstance(text, str):
            raise ASRError("ASR endpoint answered without a text field")
        return Transcription(
            text=text,
            language=payload.get("language") or self.language,
            engine=self.name,
        )
