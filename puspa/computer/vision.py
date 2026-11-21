"""Visual grounding: OCR text on a screenshot (V2-32, visual fallback).

When the V2-31 accessibility tree is empty or too poor (a canvas,
a game, some Electron apps, a remote desktop), the agent falls
back to seeing the screen: take a screenshot, read the text on it
with OCR, and locate an element by its appearance. A located
element is a box on the screen, never a ref: clicks by coordinates
go through the same permission gate as ref clicks (see
:mod:`puspa.computer.fallback`), and only a re-read of the screen
can verify that a click did anything.

The seams, same discipline as V2-31:

- :class:`OcrBackend`: read words with boxes from PNG bytes.
  :class:`FakeOcrBackend` is the in-memory backend for tests and
  demos. :class:`TesseractBackend` shells out to the ``tesseract``
  CLI when it exists; on this build VM it does not, so the backend
  fails closed with a plain error instead of pretending to read.
- :class:`VisionModelBackend`: locate an element by a free-form
  description with a vision model. That is a seam for later, not a
  working model call: no vision model is wired up here, and
  :class:`UnavailableVisionBackend` says so plainly. OCR text
  grounding is the verifiable core of this item.

Grounding is :func:`ground_text`: given OCR words and a query, find
the words that spell the query (one word, or a phrase across
neighbouring words) and return their union box as a
:class:`VisualElement`. Matching is case-insensitive and
whitespace-insensitive; nothing is guessed when the text is not
on the screen (the result is None).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Protocol

from .tree import Snapshot

__all__ = [
    "FakeOcrBackend",
    "OcrBackend",
    "OcrWord",
    "TesseractBackend",
    "UnavailableVisionBackend",
    "VisionModelBackend",
    "VisualElement",
    "VisualFallbackError",
    "ground_text",
    "tree_is_poor",
]

#: A tree with fewer nodes than this counts as empty: a bare
#: application root, or nothing at all. A root plus one element
#: is enough to act on when that element offers an action.
MIN_NODES_FOR_TREE = 2


class VisualFallbackError(Exception):
    """The visual fallback failed: no screenshot, no OCR, no
    matching text, or a backend that is not available here."""


@dataclass(frozen=True)
class OcrWord:
    """One word OCR found, with its box in screen pixels."""

    text: str
    x: int
    y: int
    width: int
    height: int
    confidence: float = 0.0

    @property
    def center(self) -> tuple[int, int]:
        """Pixel in the middle of the word's box."""
        return (self.x + self.width // 2, self.y + self.height // 2)

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "bounds": {"x": self.x, "y": self.y, "width": self.width, "height": self.height},
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class VisualElement:
    """An element located by appearance: a label and a box.

    ``source`` says how it was found: ``"ocr"`` (OCR text),
    ``"dom"`` (a web page's own layout, see
    :mod:`puspa.computer.web`), or ``"vision"`` (a vision model,
    not wired up yet). ``confidence`` is 0..1 when the source
    reports one, else 0.0.
    """

    label: str
    x: int
    y: int
    width: int
    height: int
    source: str = "ocr"
    confidence: float = 0.0

    @property
    def center(self) -> tuple[int, int]:
        """Pixel in the middle of the element's box: where a
        coordinate click aims."""
        return (self.x + self.width // 2, self.y + self.height // 2)

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "bounds": {"x": self.x, "y": self.y, "width": self.width, "height": self.height},
            "center": {"x": self.center[0], "y": self.center[1]},
            "source": self.source,
            "confidence": self.confidence,
        }


class OcrBackend(Protocol):
    """The port every OCR backend implements."""

    name: str

    def is_available(self) -> bool:
        """True when this backend can read text right now."""
        ...

    def read(self, image_png: bytes) -> list[OcrWord]:
        """Read the words on one screenshot (PNG bytes).

        Raises VisualFallbackError when there is no OCR to run
        or the image cannot be read."""
        ...


class FakeOcrBackend:
    """In-memory OCR: returns the words it was built with, no
    matter what image it is handed. This is what the tests and
    the CLI demo run on; it never touches a real OCR engine.

    ``on_read`` is a test seam (V2-34): when set, it is called
    with the read count before each read returns, so a test can
    move or delete the target between one read and the next and
    prove a stale coordinate is never pressed.
    """

    name = "fake"

    def __init__(self, words: list[OcrWord] | None = None, *, on_read=None) -> None:
        self._words: list[OcrWord] = list(words) if words is not None else []
        self.words_seen: list[bytes] = []
        self.on_read = on_read
        self.read_count = 0

    def set_words(self, words: list[OcrWord]) -> None:
        """Replace the words the next read returns (how a fake
        screen changes after a click)."""
        self._words = list(words)

    def is_available(self) -> bool:
        return True

    def read(self, image_png: bytes) -> list[OcrWord]:
        self.words_seen.append(image_png)
        self.read_count += 1
        if self.on_read is not None:
            self.on_read(self.read_count)
        return list(self._words)


def _parse_tsv(tsv: str) -> list[OcrWord]:
    """Parse tesseract TSV output (``--psm 11 tsv``) into words.

    TSV columns: level, page_num, block_num, par_num, line_num,
    word_num, left, top, width, height, conf, text. Only level-5
    rows (words) with non-empty text count.
    """
    words: list[OcrWord] = []
    lines = tsv.splitlines()
    if not lines:
        return words
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) < 12:
            continue
        try:
            level = int(parts[0])
            left, top = int(parts[6]), int(parts[7])
            width, height = int(parts[8]), int(parts[9])
            conf = float(parts[10])
        except ValueError:
            continue
        text = parts[11].strip()
        if level != 5 or not text:
            continue
        words.append(
            OcrWord(
                text=text,
                x=left,
                y=top,
                width=width,
                height=height,
                confidence=max(0.0, min(1.0, conf / 100.0)),
            )
        )
    return words


class TesseractBackend:
    """Real OCR through the ``tesseract`` command-line tool.

    The image is piped to tesseract's stdin and TSV comes back on
    stdout (``tesseract stdin stdout --psm 11 tsv``). No tesseract
    binary, no OCR: :meth:`is_available` is False and
    :meth:`read` raises instead of pretending. Tesseract itself is
    not installed on the build VM, so only the fail-closed half is
    verified live here; the TSV parsing is unit-tested.
    """

    name = "tesseract"

    def is_available(self) -> bool:
        return shutil.which("tesseract") is not None

    def read(self, image_png: bytes) -> list[OcrWord]:
        if not self.is_available():
            raise VisualFallbackError(
                "no OCR available: the 'tesseract' binary is not installed on this machine, "
                "so text on the screen cannot be read. Install tesseract-ocr (apt install "
                "tesseract-ocr) and retry, or run with the fake OCR backend for a demo."
            )
        if not image_png:
            raise VisualFallbackError("cannot OCR an empty screenshot")
        try:
            proc = subprocess.run(
                ["tesseract", "stdin", "stdout", "--psm", "11", "tsv"],
                input=image_png,
                capture_output=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise VisualFallbackError(f"tesseract failed to run ({exc})") from exc
        if proc.returncode != 0:
            detail = proc.stderr.decode("utf-8", "replace").strip()[:200]
            raise VisualFallbackError(f"tesseract failed (exit {proc.returncode}): {detail}")
        return _parse_tsv(proc.stdout.decode("utf-8", "replace"))


class VisionModelBackend(Protocol):
    """The port a vision-model grounding backend will implement.

    A model takes a screenshot plus a free-form description
    ("the red Save button") and returns the element's box. No
    model is wired up in V2-32: this is the seam V2-33+ or a
    later item fills in, with the provider call, its cost, and
    its prompt kept behind it.
    """

    name: str

    def is_available(self) -> bool:
        """True when a vision model is configured and reachable."""
        ...

    def ground(self, image_png: bytes, query: str) -> VisualElement | None:
        """Locate ``query`` in the screenshot, or None when the
        model cannot find it."""
        ...


class UnavailableVisionBackend:
    """The vision-model seam with nothing behind it yet.

    It exists so callers can ask for vision grounding and get a
    plain answer ("not wired up") instead of a crash, and so a
    real model backend plugs into the same port later.
    """

    name = "vision-unavailable"

    def is_available(self) -> bool:
        return False

    def ground(self, image_png: bytes, query: str) -> VisualElement | None:
        raise VisualFallbackError(
            "vision-model grounding is not wired up: no vision model is configured in V2-32, "
            "so elements are located by OCR text only. A model backend plugs into the "
            "VisionModelBackend seam when one is chosen."
        )


def _normalise(text: str) -> str:
    """Lower-case and collapse whitespace, for matching."""
    return re.sub(r"\s+", " ", text.strip().lower())


def ground_text(words: list[OcrWord], query: str) -> VisualElement | None:
    """Locate ``query`` among OCR ``words``.

    Tries, in order: a phrase match across neighbouring words
    ("Save file" spans two words), then a single word that equals
    the query, then a single word that contains it. Returns the
    union box of the matched words, or None when the text is not
    on the screen. Matching is case-insensitive; nothing is
    guessed.
    """
    want = _normalise(query)
    if not want or not words:
        return None
    # Phrase match: join the words with single spaces and map the
    # character span of the match back to the words it covers.
    joined: list[tuple[int, int, OcrWord]] = []
    cursor = 0
    for word in words:
        text = _normalise(word.text)
        if not text:
            continue
        joined.append((cursor, cursor + len(text), word))
        cursor += len(text) + 1
    full = " ".join(_normalise(word.text) for _, _, word in joined)
    at = full.find(want)
    if at >= 0:
        end = at + len(want)
        hit = [word for start, stop, word in joined if start < end and stop > at]
        if hit:
            return _union(hit, " ".join(word.text for word in hit))
    # Single-word exact, then substring.
    for exact in (True, False):
        for word in words:
            text = _normalise(word.text)
            if not text:
                continue
            if (exact and text == want) or (not exact and want in text):
                return VisualElement(
                    label=word.text,
                    x=word.x,
                    y=word.y,
                    width=word.width,
                    height=word.height,
                    source="ocr",
                    confidence=word.confidence,
                )
    return None


def _union(words: list[OcrWord], label: str) -> VisualElement:
    left = min(word.x for word in words)
    top = min(word.y for word in words)
    right = max(word.x + word.width for word in words)
    bottom = max(word.y + word.height for word in words)
    return VisualElement(
        label=label,
        x=left,
        y=top,
        width=right - left,
        height=bottom - top,
        source="ocr",
        confidence=min((word.confidence for word in words), default=0.0),
    )


def tree_is_poor(snapshot: Snapshot, *, min_nodes: int = MIN_NODES_FOR_TREE) -> bool:
    """True when the accessibility tree is too poor to act on.

    Poor means either tiny (fewer than ``min_nodes`` nodes in
    total: a bare application root, or nothing at all) or
    actionless (no element anywhere offers an action, so there
    is nothing to click by ref). Either way the agent cannot
    drive the app from the tree and the visual fallback takes
    over. A small tree with a clickable element is not poor,
    even when the element the agent wants is missing: that is a
    missing element, not a missing tree.
    """
    if snapshot.total_nodes < min_nodes:
        return True
    return all(not _has_action(node) for node in snapshot.nodes)


def _has_action(node) -> bool:
    if node.actions:
        return True
    return any(_has_action(child) for child in node.children)
