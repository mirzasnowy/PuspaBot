"""Receipt accuracy measurement harness.

The plan requires measuring total-receipt accuracy on real
receipt photos before choosing an OCR engine. This module is
the harness that runs that measurement once the photos exist;
it chooses nothing by itself.

A case is one photo plus the expense the photo should produce.
Two shapes of case are supported:

- **Image cases**: a photo file plus a ``.json`` file with the
  same stem holding the expected draft (merchant, date, total,
  line items). The harness reads the photo through the injected
  OCR engine, parses the result, and scores each field.
- **Text cases**: a ``.txt`` file holding the OCR text a photo
  would produce plus a ``.json`` expected draft. These score the
  parser alone — useful while no real OCR engine exists, and
  labelled as parser-only in the report so nobody mistakes the
  number for end-to-end OCR accuracy.

Scoring is per field: merchant (case-insensitive exact match),
date (exact), total (exact), line items (name + amount pairs).
``fully_correct`` is true only when every scored field matches:
that is the plan's "total receipt accuracy". ``selection_ready``
is true only when at least :data:`REQUIRED_CASES` image cases
were measured: with fewer photos the number is a hint, not a
basis for choosing an engine, and the report says so in plain
words.

Run it from the command line over a directory of pairs::

    python -m puspa.receipts.accuracy fixtures/ --engine stub-mapping

The stub engines answer from their mapping, so a real run needs
a real engine registered first.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .ocr import OcrEngine, resolve_ocr_engine
from .parser import ReceiptDraft, parse_receipt

__all__ = [
    "REQUIRED_CASES",
    "ReceiptAccuracyReport",
    "ReceiptCase",
    "ReceiptCaseResult",
    "load_cases",
    "measure_accuracy",
    "score_draft",
]

#: Receipt photos the harness needs before an engine may be chosen.
REQUIRED_CASES = 20

#: Photo extensions the harness reads (matched case-insensitively).
PHOTO_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif")

#: The plan's recording bar: end to end, photo to confirmed draft.
MAX_RECORD_SECONDS = 20.0


@dataclass(frozen=True)
class ReceiptCase:
    """One fixture: a photo (or its OCR text) and the expected draft."""

    label: str
    image_path: Path | None
    ocr_text: str | None
    expected: dict

    @property
    def is_image_case(self) -> bool:
        return self.image_path is not None


@dataclass(frozen=True)
class ReceiptCaseResult:
    """One scored case: which fields matched and how long it took."""

    case: ReceiptCase
    field_scores: dict[str, bool]
    fully_correct: bool
    elapsed_seconds: float
    parsed: dict

    @property
    def label(self) -> str:
        return self.case.label


@dataclass
class ReceiptAccuracyReport:
    """The measurement over all cases, plus whether it can choose."""

    engine: str
    cases_measured: int
    image_cases: int
    fully_correct: int
    field_accuracy: dict[str, float]
    total_accuracy: float
    max_elapsed_seconds: float
    under_20_seconds: bool
    selection_ready: bool
    results: list[ReceiptCaseResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "engine": self.engine,
            "cases_measured": self.cases_measured,
            "image_cases": self.image_cases,
            "fully_correct": self.fully_correct,
            "field_accuracy": self.field_accuracy,
            "total_accuracy": self.total_accuracy,
            "max_elapsed_seconds": round(self.max_elapsed_seconds, 3),
            "under_20_seconds": self.under_20_seconds,
            "selection_ready": self.selection_ready,
            "note": (
                "Selection needs 20 image cases through a real OCR "
                "engine. Text cases score the parser only and never "
                "make selection_ready true."
                if not self.selection_ready
                else "Enough image cases were measured to choose an engine."
            ),
            "cases": [
                {
                    "label": result.label,
                    "fully_correct": result.fully_correct,
                    "field_scores": result.field_scores,
                    "elapsed_seconds": round(result.elapsed_seconds, 3),
                }
                for result in self.results
            ],
        }


def _norm(text: str | None) -> str:
    return " ".join((text or "").lower().split())


def score_draft(parsed: ReceiptDraft, expected: dict) -> dict[str, bool]:
    """Score one parsed draft against the expected draft, per field."""
    expected_lines = expected.get("lines", [])
    parsed_lines = {(line.name.lower(), line.amount) for line in parsed.lines}
    expected_pairs = {
        (str(item.get("name", "")).lower(), int(item.get("amount", 0))) for item in expected_lines
    }
    return {
        "merchant": _norm(parsed.merchant) == _norm(expected.get("merchant")),
        "expense_date": (parsed.expense_date or None) == (expected.get("expense_date") or None),
        "total": parsed.total == expected.get("total"),
        "line_items": parsed_lines == expected_pairs,
    }


def measure_accuracy(
    cases: list[ReceiptCase],
    ocr_engine: OcrEngine,
    *,
    mime_type: str = "image/jpeg",
) -> ReceiptAccuracyReport:
    """Score every case through the engine + parser and report."""
    results: list[ReceiptCaseResult] = []
    field_hits: dict[str, int] = {}
    field_counts: dict[str, int] = {}
    fully_correct = 0
    max_elapsed = 0.0
    image_cases = 0
    for case in cases:
        started = time.monotonic()
        if case.image_path is not None:
            image_cases += 1
            text = ocr_engine.read(case.image_path.read_bytes(), mime_type=mime_type).text
        else:
            text = case.ocr_text or ""
        parsed = parse_receipt(text, photo_id=case.label)
        elapsed = time.monotonic() - started
        max_elapsed = max(max_elapsed, elapsed)
        scores = score_draft(parsed, case.expected)
        for name, hit in scores.items():
            field_hits[name] = field_hits.get(name, 0) + (1 if hit else 0)
            field_counts[name] = field_counts.get(name, 0) + 1
        all_hit = all(scores.values())
        fully_correct += 1 if all_hit else 0
        results.append(
            ReceiptCaseResult(
                case=case,
                field_scores=scores,
                fully_correct=all_hit,
                elapsed_seconds=elapsed,
                parsed=parsed.to_dict(),
            )
        )
    total = len(cases)
    field_accuracy = {
        name: (field_hits[name] / field_counts[name] if field_counts[name] else 0.0)
        for name in ("merchant", "expense_date", "total", "line_items")
    }
    selection_ready = image_cases >= REQUIRED_CASES
    return ReceiptAccuracyReport(
        engine=getattr(ocr_engine, "name", "unknown"),
        cases_measured=total,
        image_cases=image_cases,
        fully_correct=fully_correct,
        field_accuracy=field_accuracy,
        total_accuracy=(fully_correct / total if total else 0.0),
        max_elapsed_seconds=max_elapsed,
        under_20_seconds=max_elapsed < MAX_RECORD_SECONDS,
        selection_ready=selection_ready,
        results=results,
    )


def load_cases(directory: str | Path) -> list[ReceiptCase]:
    """Load every photo + ``.json`` or text + ``.json`` pair in a directory.

    A pair is a photo (or ``.txt`` OCR text) whose stem has a
    matching ``.json`` file holding the expected draft. Files
    without an expected draft are skipped (they cannot be
    scored); an expected draft without a photo or text is
    skipped too.
    """
    root = Path(directory)
    cases: list[ReceiptCase] = []
    for path in sorted(root.iterdir()):
        if not path.is_file():
            continue
        expected_path = path.with_suffix(".json")
        if not expected_path.exists():
            continue
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
        suffix = path.suffix.lower()
        if suffix in PHOTO_EXTENSIONS:
            cases.append(
                ReceiptCase(
                    label=path.stem,
                    image_path=path,
                    ocr_text=None,
                    expected=expected,
                )
            )
        elif suffix == ".txt":
            cases.append(
                ReceiptCase(
                    label=path.stem,
                    image_path=None,
                    ocr_text=path.read_text(encoding="utf-8"),
                    expected=expected,
                )
            )
    return cases


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m puspa.receipts.accuracy fixtures/``."""
    parser = argparse.ArgumentParser(description="Measure receipt OCR accuracy.")
    parser.add_argument("directory", help="directory of photo/text + .json pairs")
    parser.add_argument("--engine", default="stub", help="OCR engine name")
    parser.add_argument(
        "--mapping",
        help="JSON file mapping photo filename to the OCR text the "
        "stub-mapping engine should return for it",
    )
    args = parser.parse_args(argv)
    kwargs: dict = {}
    if args.mapping:
        raw = json.loads(Path(args.mapping).read_text(encoding="utf-8"))
        directory = Path(args.directory)
        kwargs["mapping"] = {
            (directory / filename).read_bytes(): text for filename, text in raw.items()
        }
    engine = resolve_ocr_engine(args.engine, **kwargs)
    cases = load_cases(args.directory)
    report = measure_accuracy(cases, engine)
    print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
