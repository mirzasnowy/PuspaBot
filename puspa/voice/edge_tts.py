"""Free default text-to-speech: Microsoft Edge TTS (V2-30).

Edge TTS is the free default voice: Microsoft neural voices
(300+, including Indonesian), no API key, no signup. The
endpoint is unofficial (reverse-engineered from the Edge
browser's read-aloud feature), so Microsoft can block or change
it at any time, so the caller must treat every failure as "fall
back to the browser voice", never as a hard error.

This is a small client of our own, written from the endpoint's
observable behaviour: a WebSocket to the read-aloud synthesis
endpoint, a JSON ``speech.config`` message, then one SSML
message, then binary ``Path:audio`` frames until ``turn.end``.
It is NOT the ``edge-tts`` library (GPL-3.0, incompatible with
this repo's Apache-2.0 license): no code was copied from it, and
this package does not depend on it. The only dependency is
``websockets``, used as a plain WebSocket client.

Honest limit: no live check against the real endpoint is
possible from this sandbox (no live browser, no real
credentials, egress may block the endpoint), so the client is
verified here against a fake WebSocket peer only. A live
endpoint check is an open follow-up, not a claim made here.
"""

from __future__ import annotations

import asyncio
import uuid
from xml.sax.saxutils import escape as xml_escape

__all__ = [
    "DEFAULT_EDGE_VOICE",
    "EDGE_TTS_VOICES",
    "EdgeTTSClient",
    "EdgeTTSError",
    "build_ssml",
    "voice_language",
]

#: The synthesis endpoint. ``TrustedClientToken`` is the public
#: constant the Edge read-aloud client sends; ``ConnectionId`` is
#: a fresh UUID per connection.
_EDGE_WS_URL = (
    "wss://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1"
    "?TrustedClientToken=6A5AA1D4EAFF4E9FB9E31F416EFF76A7&ConnectionId={connection_id}"
)

#: Origin the endpoint expects from a browser-like client.
_EDGE_ORIGIN = "chrome-extension://jdiccldimpdaibmpoljnfkjfaaabklfll"

#: Voices offered in Settings. Indonesian first (the default),
#: then a small English set; the full catalogue is 300+ voices.
EDGE_TTS_VOICES: tuple[tuple[str, str, str], ...] = (
    ("id-ID-GadisNeural", "Gadis (Indonesia, perempuan)", "id-ID"),
    ("id-ID-ArdiNeural", "Ardi (Indonesia, laki-laki)", "id-ID"),
    ("en-US-AriaNeural", "Aria (Amerika, perempuan)", "en-US"),
    ("en-US-GuyNeural", "Guy (Amerika, laki-laki)", "en-US"),
    ("en-US-JennyNeural", "Jenny (Amerika, perempuan)", "en-US"),
    ("en-GB-SoniaNeural", "Sonia (Inggris, perempuan)", "en-GB"),
    ("en-GB-RyanNeural", "Ryan (Inggris, laki-laki)", "en-GB"),
    ("en-AU-NatashaNeural", "Natasha (Australia, perempuan)", "en-AU"),
)

#: The voice a user who never opened Settings hears.
DEFAULT_EDGE_VOICE = "id-ID-GadisNeural"

#: Audio format requested in the config message.
_OUTPUT_FORMAT = "audio-24khz-48kbitrate-mono-mp3"


class EdgeTTSError(RuntimeError):
    """Edge TTS failed or was blocked; the caller falls back."""


def voice_language(voice: str) -> str:
    """The ``xx-YY`` language tag a voice name starts with."""
    return voice[:5]


def build_ssml(text: str, voice: str) -> str:
    """One SSML document for ``text`` in ``voice``.

    The text is XML-escaped, so a reply containing ``<`` or
    ``&`` cannot break out of the voice element.
    """
    lang = voice_language(voice)
    return (
        f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        f'xml:lang="{lang}"><voice name="{voice}">{xml_escape(text)}</voice></speak>'
    )


def _config_message() -> str:
    return (
        "Content-Type:application/json; charset=utf-8\r\n"
        "Path:speech.config\r\n\r\n"
        '{"context":{"synthesis":{"audio":{"metadataoptions":'
        '{"sentenceBoundaryEnabled":"false","wordBoundaryEnabled":"false"},'
        f'"outputFormat":"{_OUTPUT_FORMAT}"}}}}'
    )


def _ssml_message(text: str, voice: str) -> str:
    return (
        f"X-RequestId:{uuid.uuid4().hex}\r\n"
        "Content-Type:application/ssml+xml\r\n"
        "Path:ssml\r\n\r\n" + build_ssml(text, voice)
    )


def _audio_from_frame(frame: bytes) -> bytes:
    """Audio bytes after the frame headers, or empty when the frame is not audio."""
    if b"Path:audio\r\n" not in frame:
        return b""
    _, _, audio = frame.partition(b"\r\n\r\n")
    return audio


class EdgeTTSClient:
    """Minimal Edge TTS client over one WebSocket connection.

    ``connect`` is an injectable callable taking the URL
    and headers and returning an async context manager with
    ``send(str)`` / ``recv()`` (bytes or str); tests inject a
    fake peer, so no real network is needed. Without one, the
    ``websockets`` client connects to the real endpoint.
    """

    def __init__(self, *, connect=None, timeout_seconds: float = 30.0) -> None:
        self._connect = connect
        self.timeout_seconds = timeout_seconds

    async def synthesize(self, text: str, voice: str = DEFAULT_EDGE_VOICE) -> bytes:
        """Synthesize ``text`` to MP3 bytes in ``voice``.

        Raises :class:`EdgeTTSError` on any failure (connect,
        close, timeout, empty audio): the caller falls back to
        the browser voice instead of surfacing the error.
        """
        if not text or not text.strip():
            raise EdgeTTSError("nothing to speak")
        if voice not in {name for name, _, _ in EDGE_TTS_VOICES}:
            raise EdgeTTSError(f"unknown Edge voice {voice!r}")
        url = _EDGE_WS_URL.format(connection_id=str(uuid.uuid4()))
        headers = {
            "Origin": _EDGE_ORIGIN,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0"
            ),
        }
        connect = self._connect or _default_connect
        try:
            async with connect(url, headers) as ws:
                await ws.send(_config_message())
                await ws.send(_ssml_message(text, voice))
                return await self._collect_audio(ws)
        except EdgeTTSError:
            raise
        except Exception as exc:  # connect error, close, timeout
            raise EdgeTTSError(f"Edge TTS failed: {type(exc).__name__}") from exc

    async def _collect_audio(self, ws) -> bytes:
        chunks: list[bytes] = []
        try:
            while True:
                frame = await asyncio.wait_for(ws.recv(), timeout=self.timeout_seconds)
                if isinstance(frame, bytes):
                    chunks.append(_audio_from_frame(frame))
                elif isinstance(frame, str) and "Path:turn.end" in frame:
                    break
        except TimeoutError as exc:
            raise EdgeTTSError("Edge TTS timed out waiting for audio") from exc
        audio = b"".join(chunks)
        if not audio:
            raise EdgeTTSError("Edge TTS answered without audio")
        return audio


def _default_connect(url: str, headers: dict):
    """Open the real endpoint with the ``websockets`` client."""
    from websockets.asyncio.client import connect

    return connect(url, additional_headers=headers)
