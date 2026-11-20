"""Optical character recognition seam for receipt photos.

The pipeline in :mod:`puspa.receipts.pipeline` reads text through
this seam and never talks to an OCR engine directly, so the engine
can be swapped once it is chosen. Choosing is a measurement
decision, not a guess: plan 12.5 requires measuring total-receipt
accuracy on real receipt photos before picking an engine, and no
such photos exist yet. Until that measurement exists, the only
registered engines are test doubles — there is deliberately no
real engine here, and :func:`resolve_ocr_engine` refuses any other
name instead of silently falling back to something unmeasured.

Environment check on this host (20 Nov 2026): no OCR engine is
installed — no ``tesseract`` binary, no ``pytesseract``,
``easyocr``, ``rapidocr`` or ``paddleocr`` package. The stub +
harness + seam is therefore the honest result for this item; see
``docs/receipts-photo.md``.
"""

from __future__ import annotations

import hashlib
import importlib.util
import shutil
from dataclasses import dataclass
from typing import Protocol

__all__ = [
    "MappingOcrEngine",
    "OcrEngine",
    "OcrLine",
    "OcrResult",
    "StubOcrEngine",
    "available_ocr_engines",
    "detect_installed_engines",
    "resolve_ocr_engine",
]


@dataclass(frozen=True)
class OcrLine:
    """One recognised text line, with its place on the photo.

    ``box`` is the line's bounding box as fractions of the photo
    (``x``, ``y``, ``width``, ``height`` in 0..1), or ``None`` when
    the engine does not report geometry. The box is what ties a
    parsed amount back to a visible area of the photo — the
    evidence for that number.
    """

    text: str
    box: tuple[float, float, float, float] | None = None
    confidence: float | None = None


@dataclass(frozen=True)
class OcrResult:
    """What one OCR pass produced."""

    lines: tuple[OcrLine, ...] = ()
    language: str | None = None
    engine: str = "stub"

    @property
    def text(self) -> str:
        """The lines joined as plain text, in reading order."""
        return "\n".join(line.text for line in self.lines)


class OcrEngine(Protocol):
    """One OCR engine behind the receipt pipeline seam."""

    name: str

    def read(self, image: bytes, *, mime_type: str) -> OcrResult:
        """Read the text of one photo; never raises for empty input."""
        ...


class StubOcrEngine:
    """Test double: always returns the same fixed lines.

    Used by the pipeline tests and as the app default until a real
    engine is chosen from the accuracy measurement (see
    :mod:`puspa.receipts.accuracy`). It touches no model and no
    network.
    """

    name = "stub"

    def __init__(self, text: str = "", *, language: str = "id") -> None:
        self._lines = tuple(OcrLine(text=line) for line in text.splitlines() if line.strip())
        self._language = language

    def read(self, image: bytes, *, mime_type: str) -> OcrResult:
        return OcrResult(lines=self._lines, language=self._language, engine=self.name)


class MappingOcrEngine:
    """Test double keyed by the photo bytes: each known photo maps
    to the OCR text the test expects for it.

    Unknown photos fall back to ``fallback`` (empty by default), so
    a test that feeds an unregistered photo fails loudly with an
    empty result instead of passing with a stale one.
    """

    name = "stub-mapping"

    def __init__(self, mapping: dict[bytes, str], *, fallback: str = "") -> None:
        self._by_hash = {hashlib.sha256(image).hexdigest(): text for image, text in mapping.items()}
        self._fallback = fallback

    def read(self, image: bytes, *, mime_type: str) -> OcrResult:
        digest = hashlib.sha256(image).hexdigest()
        text = self._by_hash.get(digest, self._fallback)
        lines = tuple(OcrLine(text=line) for line in text.splitlines() if line.strip())
        return OcrResult(lines=lines, language="id", engine=self.name)


#: Engines the app can resolve by name. Real engines are added
#: here only after the accuracy measurement picks one.
_OCR_ENGINES: dict[str, type[StubOcrEngine | MappingOcrEngine]] = {
    "stub": StubOcrEngine,
    "stub-mapping": MappingOcrEngine,
}


def available_ocr_engines() -> list[str]:
    """Names :func:`resolve_ocr_engine` accepts, in registry order."""
    return list(_OCR_ENGINES)


def resolve_ocr_engine(name: str, **kwargs) -> StubOcrEngine | MappingOcrEngine:
    """Build the OCR engine registered under ``name``.

    Raises ``ValueError`` for an unknown name: guessing an engine
    is exactly what plan 12.5 forbids, so an unregistered name is
    a configuration error, never a silent default.
    """
    try:
        factory = _OCR_ENGINES[name]
    except KeyError:
        raise ValueError(
            f"unknown OCR engine {name!r}; available: {', '.join(available_ocr_engines())}"
        ) from None
    return factory(**kwargs)


#: Candidate real engines and how to spot them on a host. Used by
#: :func:`detect_installed_engines` only; none of these is wired
#: into :func:`resolve_ocr_engine` until the accuracy measurement
#: picks one.
_CANDIDATES: tuple[tuple[str, str, str | None], ...] = (
    ("tesseract", "binary", "tesseract"),
    ("pytesseract", "module", "pytesseract"),
    ("easyocr", "module", "easyocr"),
    ("rapidocr-onnxruntime", "module", "rapidocr_onnxruntime"),
    ("paddleocr", "module", "paddleocr"),
)


@dataclass(frozen=True)
class InstalledEngine:
    """One candidate engine found (or not found) on this host."""

    name: str
    installed: bool


def detect_installed_engines() -> list[InstalledEngine]:
    """Report which real OCR engines exist on this host.

    Detection only: finding an engine here does not select it.
    Selection needs the accuracy measurement in
    :mod:`puspa.receipts.accuracy`.
    """
    found: list[InstalledEngine] = []
    for name, kind, target in _CANDIDATES:
        if kind == "binary":
            installed = shutil.which(target or "") is not None
        else:
            installed = importlib.util.find_spec(target or "") is not None
        found.append(InstalledEngine(name=name, installed=installed))
    return found
