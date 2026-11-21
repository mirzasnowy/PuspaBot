"""Voice upload pipeline (P-03, plan section 12.4).

One uploaded recording goes through three steps, in order:

1. **Validate** — the mime type must be an audio type the
   recorders produce, and the recording must be non-empty and no
   larger than :data:`MAX_AUDIO_BYTES`.
2. **Transcribe** — through the injected transcriber seam
   (:mod:`puspa.voice.transcriber`); the pipeline never picks an
   engine itself.
3. **Delete the audio** — the recorded bytes are written to a
   temporary file only so the lifecycle is explicit (a real engine
   may need a path), and the file is removed as soon as the
   transcript exists. Deletion is the default; ``keep_audio=True``
   keeps the file in the caller's directory instead, for the rare
   case where the user explicitly asks to keep it.

Only the transcript leaves this module: the caller stores the
pending transcript and shows it to the user for confirmation
before anything is sent (see :mod:`puspa.pwa.voice_api`).
"""

from __future__ import annotations

import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from .transcriber import Transcriber

__all__ = [
    "ALLOWED_AUDIO_MIME_TYPES",
    "MAX_AUDIO_BYTES",
    "AudioValidationError",
    "VoicePipeline",
    "VoiceUploadResult",
    "validate_audio",
]

#: Largest recording the pipeline accepts (10 MiB — minutes of speech
#: at the bitrates phone recorders produce; larger uploads are almost
#: certainly a wrong file, not a voice message).
MAX_AUDIO_BYTES = 10 * 1024 * 1024

#: Mime types phone browsers record with (MediaRecorder output).
ALLOWED_AUDIO_MIME_TYPES = frozenset(
    {
        "audio/webm",
        "audio/mp4",
        "audio/mpeg",
        "audio/wav",
        "audio/x-wav",
        "audio/ogg",
        "audio/aac",
        "audio/3gpp",
    }
)


#: File extension per accepted mime type (for kept audio files).
_MIME_SUFFIX = {
    "audio/webm": ".webm",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/ogg": ".ogg",
    "audio/aac": ".aac",
    "audio/3gpp": ".3gp",
}


class AudioValidationError(ValueError):
    """An uploaded recording failed validation (type, size, or empty)."""


def validate_audio(audio: bytes, mime_type: str) -> None:
    """Reject audio the pipeline cannot or should not transcribe."""
    if mime_type not in ALLOWED_AUDIO_MIME_TYPES:
        raise AudioValidationError(
            f"unsupported audio type {mime_type!r}; "
            f"accepted: {', '.join(sorted(ALLOWED_AUDIO_MIME_TYPES))}"
        )
    if not audio:
        raise AudioValidationError("empty audio upload")
    if len(audio) > MAX_AUDIO_BYTES:
        raise AudioValidationError(
            f"audio is {len(audio)} bytes, over the {MAX_AUDIO_BYTES} byte limit"
        )


@dataclass(frozen=True)
class VoiceUploadResult:
    """One transcribed upload: the transcript and what happened to
    the audio. ``audio_path`` is set only when the audio was kept."""

    transcript: str
    language: str | None
    engine: str
    size_bytes: int
    mime_type: str
    audio_deleted: bool
    audio_path: str | None = None


class VoicePipeline:
    """Validate, transcribe, and (by default) delete one recording."""

    def __init__(self, transcriber: Transcriber) -> None:
        self._transcriber = transcriber

    def transcribe_upload(
        self,
        audio: bytes,
        mime_type: str,
        *,
        keep_audio: bool = False,
        keep_dir: str | Path | None = None,
    ) -> VoiceUploadResult:
        """Transcribe one upload; delete the audio unless asked to keep it.

        ``keep_dir`` is required when ``keep_audio`` is true: the
        kept file lands there under a random name, never under a name
        the uploader chose.
        """
        validate_audio(audio, mime_type)
        if keep_audio and keep_dir is None:
            raise AudioValidationError("keep_dir is required when keep_audio is true")
        suffix = _MIME_SUFFIX[mime_type]
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=suffix, prefix="puspa-voice-"
            ) as tmp:
                tmp.write(audio)
                tmp_path = Path(tmp.name)
            transcription = self._transcriber.transcribe(audio, mime_type=mime_type)
            if keep_audio:
                if keep_dir is None:
                    # Unreachable: the method raises above when
                    # keep_audio is set without a keep_dir. Guarded
                    # so the directory below is known to exist.
                    raise AudioValidationError("keep_dir is required when keep_audio is true")
                kept = Path(keep_dir) / f"{uuid.uuid4().hex}{suffix}"
                kept.parent.mkdir(parents=True, exist_ok=True)
                os.replace(tmp_path, kept)
                tmp_path = None
                return VoiceUploadResult(
                    transcript=transcription.text,
                    language=transcription.language,
                    engine=transcription.engine,
                    size_bytes=len(audio),
                    mime_type=mime_type,
                    audio_deleted=False,
                    audio_path=str(kept),
                )
            return VoiceUploadResult(
                transcript=transcription.text,
                language=transcription.language,
                engine=transcription.engine,
                size_bytes=len(audio),
                mime_type=mime_type,
                audio_deleted=True,
            )
        finally:
            if tmp_path is not None and tmp_path.exists():
                tmp_path.unlink()
