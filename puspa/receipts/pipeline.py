"""Receipt photo upload pipeline.

One uploaded photo goes through four steps, in order:

1. **Validate** — the mime type must be a photo type phone
   cameras produce, the photo must be non-empty and no larger
   than :data:`MAX_PHOTO_BYTES`, and the bytes must decode as an
   image (Pillow check; a renamed text file is refused).
2. **Read** — through the injected OCR seam
   (:mod:`puspa.receipts.ocr`); the pipeline never picks an
   engine itself.
3. **Classify** — identity cards, family cards, and official
   letters are out of scope (see :mod:`puspa.receipts.documents`)
   and are refused with an explanation. The photo is not stored
   in that case.
4. **Parse** — the OCR text becomes a confirmable expense draft
   (:mod:`puspa.receipts.parser`), every amount carrying an
   evidence id for its photo line.

Only the draft leaves this module: the caller stores the photo
as evidence and shows the draft to the user for confirmation
before anything is recorded (see :mod:`puspa.pwa.receipt_api`).
The elapsed time is measured end to end (validate + read +
parse) so the plan's under-20-seconds bar can be checked
honestly — with the stub engine the number is milliseconds, and
the status endpoint says the engine is a stub.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from .documents import classify_document, detect_out_of_scope
from .ocr import OcrEngine, OcrResult
from .parser import ReceiptDraft, parse_receipt

__all__ = [
    "ALLOWED_PHOTO_MIME_TYPES",
    "MAX_PHOTO_BYTES",
    "ImageValidationError",
    "ReceiptPipeline",
    "ReceiptUploadResult",
    "validate_image",
]

#: Largest photo the pipeline accepts (10 MiB — phone photos fit).
MAX_PHOTO_BYTES = 10 * 1024 * 1024

#: Mime types phone cameras and galleries produce.
ALLOWED_PHOTO_MIME_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
)

#: File extension per accepted mime type (for the stored photo).
_MIME_SUFFIX = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heif",
}

#: Magic bytes per type, used when Pillow is unavailable.
_MAGIC_BYTES = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"RIFF": "image/webp",
}


class ImageValidationError(ValueError):
    """An uploaded photo failed validation (type, size, or content)."""


def validate_image(image: bytes, mime_type: str) -> None:
    """Reject photos the pipeline cannot or should not read."""
    if mime_type not in ALLOWED_PHOTO_MIME_TYPES:
        raise ImageValidationError(
            f"unsupported photo type {mime_type!r}; "
            f"accepted: {', '.join(sorted(ALLOWED_PHOTO_MIME_TYPES))}"
        )
    if not image:
        raise ImageValidationError("empty photo upload")
    if len(image) > MAX_PHOTO_BYTES:
        raise ImageValidationError(
            f"photo is {len(image)} bytes, over the {MAX_PHOTO_BYTES} byte limit"
        )
    try:
        from PIL import Image
    except ImportError:
        Image = None  # type: ignore[assignment]
    if Image is not None:
        try:
            from io import BytesIO

            with Image.open(BytesIO(image)) as photo:
                photo.verify()
        except Exception as exc:
            raise ImageValidationError("photo bytes do not decode as an image") from exc
    elif not any(image.startswith(magic) for magic in _MAGIC_BYTES):
        raise ImageValidationError("photo bytes do not look like an image")


@dataclass(frozen=True)
class ReceiptUploadResult:
    """One processed photo: the draft and what the pipeline found.

    ``rejected`` is true for identity/official documents: then
    ``draft`` is ``None``, the photo was not stored, and
    ``reject_reason`` explains why. ``elapsed_seconds`` is the
    measured end-to-end time of this upload.
    """

    photo_id: str
    ocr_text: str
    ocr_lines: list[dict]
    draft: ReceiptDraft | None
    document_kind: str
    rejected: bool
    reject_reason: str | None
    engine: str
    size_bytes: int
    mime_type: str
    elapsed_seconds: float


class ReceiptPipeline:
    """Validate, read, classify, and parse one receipt photo."""

    def __init__(self, ocr_engine: OcrEngine) -> None:
        self._ocr_engine = ocr_engine

    def process_upload(
        self, image: bytes, mime_type: str, *, photo_id: str | None = None
    ) -> ReceiptUploadResult:
        """Process one upload; refuse identity documents outright."""
        started = time.monotonic()
        validate_image(image, mime_type)
        photo_id = photo_id or uuid.uuid4().hex[:12]
        ocr: OcrResult = self._ocr_engine.read(image, mime_type=mime_type)
        text = ocr.text
        rejected, reason = detect_out_of_scope(text)
        kind = classify_document(text)
        draft = None if rejected else parse_receipt(text, photo_id=photo_id)
        elapsed = time.monotonic() - started
        return ReceiptUploadResult(
            photo_id=photo_id,
            ocr_text=text,
            ocr_lines=[
                {
                    "text": line.text,
                    "box": list(line.box) if line.box else None,
                    "confidence": line.confidence,
                }
                for line in ocr.lines
            ],
            draft=draft,
            document_kind=kind,
            rejected=rejected,
            reject_reason=reason,
            engine=ocr.engine,
            size_bytes=len(image),
            mime_type=mime_type,
            elapsed_seconds=elapsed,
        )
