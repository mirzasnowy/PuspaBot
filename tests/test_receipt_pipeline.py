"""Tests for the receipt pipeline and the accuracy harness.

The pipeline validates the photo, reads it through the OCR
seam, refuses identity documents, and parses the rest into a
confirmable draft — measuring the end-to-end time, which the
plan caps at 20 seconds. The harness scores fixtures per field
and only calls a measurement selection-ready on 20 image cases
through a real engine.
"""

from pathlib import Path

import pytest

from puspa.receipts.accuracy import (
    REQUIRED_CASES,
    load_cases,
    measure_accuracy,
)
from puspa.receipts.ocr import (
    MappingOcrEngine,
    StubOcrEngine,
    available_ocr_engines,
    detect_installed_engines,
    resolve_ocr_engine,
)
from puspa.receipts.pipeline import (
    MAX_PHOTO_BYTES,
    ImageValidationError,
    ReceiptPipeline,
    validate_image,
)

FIXTURES = Path(__file__).parent / "fixtures" / "receipts"
WARUNG_TEXT = (FIXTURES / "warung.txt").read_text(encoding="utf-8")
WARUNG_PNG = (FIXTURES / "photo-warung.png").read_bytes()
KTP_PNG = (FIXTURES / "ktp.png").read_bytes()
KTP_TEXT = "KARTU TANDA PENDUDUK\nNIK: 3201234567890001\nNama: Budi Santoso"


def test_resolve_ocr_engine_refuses_unknown_names():
    with pytest.raises(ValueError, match="unknown OCR engine"):
        resolve_ocr_engine("tesseract")
    assert available_ocr_engines() == ["stub", "stub-mapping"]


def test_no_real_ocr_engine_is_installed_on_this_host():
    installed = {engine.name: engine.installed for engine in detect_installed_engines()}
    assert installed == {
        "tesseract": False,
        "pytesseract": False,
        "easyocr": False,
        "rapidocr-onnxruntime": False,
        "paddleocr": False,
    }


def test_stub_engine_returns_fixed_text():
    result = StubOcrEngine(WARUNG_TEXT).read(b"anything", mime_type="image/png")
    assert result.text == WARUNG_TEXT.strip()
    assert result.engine == "stub"


def test_mapping_engine_falls_back_to_empty_for_unknown_photos():
    engine = MappingOcrEngine({WARUNG_PNG: WARUNG_TEXT})
    assert engine.read(WARUNG_PNG, mime_type="image/png").text.strip() == WARUNG_TEXT.strip()
    assert engine.read(b"other bytes", mime_type="image/png").text == ""


def test_validate_image_rejects_wrong_type_empty_and_oversize():
    with pytest.raises(ImageValidationError, match="unsupported photo type"):
        validate_image(b"x", "application/pdf")
    with pytest.raises(ImageValidationError, match="empty photo"):
        validate_image(b"", "image/jpeg")
    with pytest.raises(ImageValidationError, match="over the"):
        validate_image(b"x" * (MAX_PHOTO_BYTES + 1), "image/jpeg")


def test_validate_image_rejects_bytes_that_are_not_an_image():
    with pytest.raises(ImageValidationError, match="do not decode as an image"):
        validate_image(b"this is plain text, not a photo", "image/png")


def test_validate_image_accepts_a_real_png():
    validate_image(WARUNG_PNG, "image/png")


def test_pipeline_parses_a_receipt_photo_end_to_end():
    pipeline = ReceiptPipeline(MappingOcrEngine({WARUNG_PNG: WARUNG_TEXT}))
    result = pipeline.process_upload(WARUNG_PNG, "image/png")
    assert result.rejected is False
    assert result.document_kind == "receipt"
    assert result.engine == "stub-mapping"
    assert result.draft is not None
    assert result.draft.merchant == "WARUNG KOPI SEDUH"
    assert result.draft.total == 51000
    assert result.elapsed_seconds < 20.0
    assert result.size_bytes == len(WARUNG_PNG)


def test_pipeline_refuses_identity_documents_and_stores_nothing():
    pipeline = ReceiptPipeline(MappingOcrEngine({KTP_PNG: KTP_TEXT}))
    result = pipeline.process_upload(KTP_PNG, "image/png")
    assert result.rejected is True
    assert result.document_kind == "id_document"
    assert result.draft is None
    assert result.reject_reason
    assert "identity" in result.reject_reason.lower()


def test_pipeline_rejects_bad_photos():
    pipeline = ReceiptPipeline(StubOcrEngine())
    with pytest.raises(ImageValidationError):
        pipeline.process_upload(b"not a photo", "image/png")


def test_load_cases_finds_text_fixtures_and_skips_unpaired_photos():
    cases = load_cases(FIXTURES)
    labels = sorted(case.label for case in cases)
    assert labels == ["hostile", "supermarket", "warung"]
    assert all(not case.is_image_case for case in cases)


def test_accuracy_harness_scores_parser_fixtures_fully_correct():
    cases = load_cases(FIXTURES)
    report = measure_accuracy(cases, StubOcrEngine())
    assert report.total_accuracy == 1.0
    assert report.fully_correct == 3
    assert report.field_accuracy == {
        "merchant": 1.0,
        "expense_date": 1.0,
        "total": 1.0,
        "line_items": 1.0,
    }
    assert report.under_20_seconds is True
    assert report.max_elapsed_seconds < 20.0
    # Text cases score the parser only: never selection-ready.
    assert report.selection_ready is False
    assert report.image_cases == 0


def test_accuracy_harness_needs_twenty_image_cases_to_select():
    assert REQUIRED_CASES == 20
    cases = load_cases(FIXTURES)
    report = measure_accuracy(cases, StubOcrEngine())
    assert report.selection_ready is False


def test_accuracy_harness_reports_a_wrong_total():
    cases = load_cases(FIXTURES)
    for case in cases:
        if case.label == "warung":
            case.expected["total"] = 99999
    report = measure_accuracy(cases, StubOcrEngine())
    assert report.total_accuracy < 1.0
    assert report.field_accuracy["total"] < 1.0
    assert report.field_accuracy["merchant"] == 1.0
