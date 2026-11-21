"""Speech-to-text seam for voice messages (P-03, plan section 12.4).

The pipeline in :mod:`puspa.voice.pipeline` transcribes through this
seam and never talks to a speech engine directly, so the engine can
be swapped once it is chosen. Choosing a *default* engine is a
measurement decision, not a guess: plan 12.4 requires measuring
the word error rate on 50 real recordings (slang + regional mix)
before picking one, and no such recordings exist yet, so the app
default stays the stub.

V2-30 adds one exception that does not violate that rule: a user
may point the app at their *own* OpenAI-compatible speech
endpoint with their own key (``"openai-compatible"``). That is
the user's own choice of engine, not Puspa picking a default,
so no WER measurement gates it. The engine itself lives in
:mod:`puspa.voice.asr`; it is resolved lazily here to keep the
import graph free of cycles.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

__all__ = [
    "MappingTranscriber",
    "StubTranscriber",
    "Transcriber",
    "Transcription",
    "available_transcribers",
    "resolve_transcriber",
]


@dataclass(frozen=True)
class Transcription:
    """What one transcription produced."""

    text: str
    language: str | None = None
    duration_seconds: float | None = None
    engine: str = "stub"


class Transcriber(Protocol):
    """One speech-to-text engine behind the pipeline seam."""

    name: str

    def transcribe(self, audio: bytes, *, mime_type: str) -> Transcription:
        """Transcribe raw audio bytes; never raises for empty audio."""
        ...


class StubTranscriber:
    """Test double: always returns the same fixed transcript.

    Used by the pipeline tests and as the app default until a real
    engine is chosen from the WER measurement (see
    :mod:`puspa.voice.wer`). It touches no model and no network.
    """

    name = "stub"

    def __init__(self, text: str = "", *, language: str = "id") -> None:
        self._text = text
        self._language = language

    def transcribe(self, audio: bytes, *, mime_type: str) -> Transcription:
        return Transcription(text=self._text, language=self._language, engine=self.name)


class MappingTranscriber:
    """Test double keyed by the audio bytes: each known recording maps
    to the transcript the test expects for it.

    Unknown audio falls back to ``fallback`` (empty by default), so a
    test that feeds an unregistered recording fails loudly with an
    empty transcript instead of passing with a stale one.
    """

    name = "stub-mapping"

    def __init__(self, mapping: dict[bytes, str], *, fallback: str = "") -> None:
        self._by_hash = {hashlib.sha256(audio).hexdigest(): text for audio, text in mapping.items()}
        self._fallback = fallback

    def transcribe(self, audio: bytes, *, mime_type: str) -> Transcription:
        digest = hashlib.sha256(audio).hexdigest()
        return Transcription(
            text=self._by_hash.get(digest, self._fallback),
            language="id",
            engine=self.name,
        )


#: Transcribers the app can resolve by name. The test doubles
#: stay here; the one real engine (a user-chosen BYOK endpoint)
#: is resolved lazily in :func:`resolve_transcriber` so this
#: module never imports it at load time.
_TRANSCRIBERS: dict[str, type[StubTranscriber | MappingTranscriber]] = {
    "stub": StubTranscriber,
    "stub-mapping": MappingTranscriber,
}

#: Real engines, resolved lazily (name -> module attribute path).
_REAL_TRANSCRIBERS: dict[str, str] = {
    "openai-compatible": "puspa.voice.asr.OpenAICompatibleTranscriber",
}


def available_transcribers() -> list[str]:
    """Names :func:`resolve_transcriber` accepts, in registry order."""
    return list(_TRANSCRIBERS) + list(_REAL_TRANSCRIBERS)


def resolve_transcriber(name: str, **kwargs) -> StubTranscriber | MappingTranscriber:
    """Build the transcriber registered under ``name``.

    Raises ``ValueError`` for an unknown name: guessing an engine is
    exactly what plan 12.4 forbids, so an unregistered name is a
    configuration error, never a silent default.
    """
    if name in _REAL_TRANSCRIBERS:
        import importlib

        module_path, _, attr = _REAL_TRANSCRIBERS[name].rpartition(".")
        factory = getattr(importlib.import_module(module_path), attr)
        return factory(**kwargs)
    try:
        factory = _TRANSCRIBERS[name]
    except KeyError:
        raise ValueError(
            f"unknown transcriber {name!r}; available: {', '.join(available_transcribers())}"
        ) from None
    return factory(**kwargs)
