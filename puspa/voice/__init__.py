"""Speech-to-text seam for voice messages (P-03, plan section 12.4).

Exports are lazy (PEP 562): importing this package must not import
its submodules, so ``python -m puspa.voice.wer`` runs without a
double-import warning.
"""

from __future__ import annotations

import importlib

__all__ = [
    "REQUIRED_CASES",
    "MappingTranscriber",
    "StubTranscriber",
    "Transcriber",
    "Transcription",
    "VoicePipeline",
    "VoiceUploadResult",
    "WerCase",
    "WerCaseResult",
    "WerReport",
    "available_transcribers",
    "load_cases",
    "measure_wer",
    "resolve_transcriber",
    "validate_audio",
    "word_error_rate",
]

_LAZY_EXPORTS = {
    "MappingTranscriber": (".transcriber", "MappingTranscriber"),
    "StubTranscriber": (".transcriber", "StubTranscriber"),
    "Transcriber": (".transcriber", "Transcriber"),
    "Transcription": (".transcriber", "Transcription"),
    "available_transcribers": (".transcriber", "available_transcribers"),
    "resolve_transcriber": (".transcriber", "resolve_transcriber"),
    "VoicePipeline": (".pipeline", "VoicePipeline"),
    "VoiceUploadResult": (".pipeline", "VoiceUploadResult"),
    "validate_audio": (".pipeline", "validate_audio"),
    "REQUIRED_CASES": (".wer", "REQUIRED_CASES"),
    "WerCase": (".wer", "WerCase"),
    "WerCaseResult": (".wer", "WerCaseResult"),
    "WerReport": (".wer", "WerReport"),
    "load_cases": (".wer", "load_cases"),
    "measure_wer": (".wer", "measure_wer"),
    "word_error_rate": (".wer", "word_error_rate"),
}


def __getattr__(name: str):
    try:
        module_name, attr = _LAZY_EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    module = importlib.import_module(module_name, __package__)
    return getattr(module, attr)
