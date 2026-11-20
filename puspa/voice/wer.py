"""Word error rate (WER) measurement harness (P-03, plan section 12.4).

The plan requires measuring WER on 50 real recordings (slang +
regional mix) before choosing a speech-to-text engine. This module
is the harness that runs that measurement once the recordings
exist; it chooses nothing by itself.

A case is one recording plus the exact words spoken in it. The
harness transcribes each recording through the injected transcriber,
scores the transcript against the reference, and reports the overall
WER plus one line per case. ``selection_ready`` is true only when at
least :data:`REQUIRED_CASES` cases were measured: with fewer
recordings the number is a hint, not a basis for choosing an
engine, and the report says so in plain words.

Run it from the command line over a directory of pairs — one audio
file plus a ``.txt`` file with the same stem holding the reference
text::

    python -m puspa.voice.wer recordings/ --transcriber stub
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .transcriber import Transcriber, resolve_transcriber

__all__ = [
    "REQUIRED_CASES",
    "WerCase",
    "WerCaseResult",
    "WerReport",
    "load_cases",
    "measure_wer",
    "word_error_rate",
]

#: Recordings the plan requires before an engine may be chosen.
REQUIRED_CASES = 50

#: Audio extensions the harness reads (matched case-insensitively).
AUDIO_EXTENSIONS = (".webm", ".wav", ".mp3", ".ogg", ".m4a", ".aac")


def _words(text: str) -> list[str]:
    """Words of a transcript, lowercased, split on whitespace."""
    return text.lower().split()


def _levenshtein(reference: list[str], hypothesis: list[str]) -> int:
    """Word-level edit distance (substitutions + deletions + insertions)."""
    previous = list(range(len(hypothesis) + 1))
    for i, ref_word in enumerate(reference, start=1):
        current = [i]
        for j, hyp_word in enumerate(hypothesis, start=1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (ref_word != hyp_word),
                )
            )
        previous = current
    return previous[-1]


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Word error rate: edit distance over reference word count.

    Identical texts score 0.0; a completely wrong transcript scores
    1.0 or more (insertions can push it past 1.0). An empty reference
    scores 0.0 only against an empty hypothesis, 1.0 otherwise.
    """
    ref_words = _words(reference)
    hyp_words = _words(hypothesis)
    if not ref_words:
        return 0.0 if not hyp_words else 1.0
    return _levenshtein(ref_words, hyp_words) / len(ref_words)


@dataclass(frozen=True)
class WerCase:
    """One recording plus the exact words spoken in it."""

    audio_path: Path
    reference_text: str
    label: str = ""


@dataclass(frozen=True)
class WerCaseResult:
    """One case scored: the transcript, its WER, and what it cost."""

    case: WerCase
    transcript: str
    wer: float


@dataclass
class WerReport:
    """The whole measurement: overall WER, per-case lines, and whether
    the measurement is big enough to choose an engine from."""

    engine: str
    cases_measured: int
    overall_wer: float
    per_case: list[WerCaseResult] = field(default_factory=list)
    required_cases: int = REQUIRED_CASES

    @property
    def selection_ready(self) -> bool:
        """True only with at least the required number of cases."""
        return self.cases_measured >= self.required_cases

    @property
    def verdict(self) -> str:
        """One plain sentence about what this report can and cannot do."""
        if self.selection_ready:
            return (
                f"Measured on {self.cases_measured} recordings "
                f"(overall WER {self.overall_wer:.3f}); "
                "large enough to compare engines."
            )
        return (
            f"Only {self.cases_measured} of {self.required_cases} "
            "recordings measured; too few to choose an engine — "
            "collect more real recordings first."
        )

    def to_dict(self) -> dict:
        """JSON-ready report (per-case lines included)."""
        return {
            "engine": self.engine,
            "cases_measured": self.cases_measured,
            "required_cases": self.required_cases,
            "overall_wer": self.overall_wer,
            "selection_ready": self.selection_ready,
            "verdict": self.verdict,
            "per_case": [
                {
                    "label": result.case.label or result.case.audio_path.name,
                    "reference": result.case.reference_text,
                    "transcript": result.transcript,
                    "wer": result.wer,
                }
                for result in self.per_case
            ],
        }


def measure_wer(
    cases: list[WerCase], transcriber: Transcriber, *, mime_type: str = "audio/webm"
) -> WerReport:
    """Transcribe every case and score it against its reference.

    The overall WER is the total edit distance over the total
    reference word count (not the mean of per-case rates), so a long
    recording counts more than a short one.
    """
    results: list[WerCaseResult] = []
    total_errors = 0
    total_words = 0
    for case in cases:
        audio = case.audio_path.read_bytes()
        transcript = transcriber.transcribe(audio, mime_type=mime_type).text
        ref_words = _words(case.reference_text)
        errors = _levenshtein(ref_words, _words(transcript))
        total_errors += errors
        total_words += len(ref_words)
        results.append(
            WerCaseResult(
                case=case,
                transcript=transcript,
                wer=word_error_rate(case.reference_text, transcript),
            )
        )
    overall = total_errors / total_words if total_words else 0.0
    return WerReport(
        engine=getattr(transcriber, "name", "unknown"),
        cases_measured=len(cases),
        overall_wer=overall,
        per_case=results,
    )


def load_cases(directory: str | Path) -> list[WerCase]:
    """Load every audio + ``.txt`` pair in a directory.

    A pair is an audio file whose stem has a matching ``.txt`` file
    holding the reference text. Audio without a reference is skipped
    (it cannot be scored); a reference without audio is skipped too.
    """
    root = Path(directory)
    cases: list[WerCase] = []
    for audio_path in sorted(root.iterdir()):
        if not audio_path.is_file():
            continue
        if audio_path.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        reference_path = audio_path.with_suffix(".txt")
        if not reference_path.exists():
            continue
        cases.append(
            WerCase(
                audio_path=audio_path,
                reference_text=reference_path.read_text(encoding="utf-8").strip(),
                label=audio_path.stem,
            )
        )
    return cases


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: measure WER over a directory of cases."""
    parser = argparse.ArgumentParser(
        description="Measure word error rate over a directory of recordings."
    )
    parser.add_argument("directory", help="directory of audio + .txt pairs")
    parser.add_argument(
        "--transcriber",
        default="stub",
        help="transcriber name (see puspa.voice.transcriber)",
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)
    cases = load_cases(args.directory)
    if not cases:
        print(f"no scorable cases found in {args.directory}", file=sys.stderr)
        return 2
    report = measure_wer(cases, resolve_transcriber(args.transcriber))
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(report.verdict)
        for result in report.per_case:
            label = result.case.label or result.case.audio_path.name
            print(f"{label}: WER {result.wer:.3f} — {result.transcript!r}")
    return 0 if report.selection_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
