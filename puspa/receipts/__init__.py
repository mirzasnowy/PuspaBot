"""Receipt photos (plan section 12.5).

Exports are lazy (PEP 562): importing this package must not import
its submodules, so ``python -m puspa.receipts.accuracy`` runs
without a double-import warning.
"""

from __future__ import annotations

import importlib

__all__ = [
    "ImageValidationError",
    "MappingOcrEngine",
    "OcrEngine",
    "OcrLine",
    "OcrResult",
    "ReceiptAccuracyReport",
    "ReceiptCase",
    "ReceiptCaseResult",
    "ReceiptDraft",
    "ReceiptLine",
    "ReceiptPipeline",
    "ReceiptUploadResult",
    "StubOcrEngine",
    "available_ocr_engines",
    "classify_document",
    "detect_installed_engines",
    "detect_out_of_scope",
    "evidence_id_for",
    "load_cases",
    "measure_accuracy",
    "parse_amount",
    "parse_receipt",
    "resolve_ocr_engine",
    "score_draft",
    "validate_image",
]

_LAZY_EXPORTS = {
    "MappingOcrEngine": (".ocr", "MappingOcrEngine"),
    "OcrEngine": (".ocr", "OcrEngine"),
    "OcrLine": (".ocr", "OcrLine"),
    "OcrResult": (".ocr", "OcrResult"),
    "StubOcrEngine": (".ocr", "StubOcrEngine"),
    "available_ocr_engines": (".ocr", "available_ocr_engines"),
    "detect_installed_engines": (".ocr", "detect_installed_engines"),
    "resolve_ocr_engine": (".ocr", "resolve_ocr_engine"),
    "ReceiptDraft": (".parser", "ReceiptDraft"),
    "ReceiptLine": (".parser", "ReceiptLine"),
    "evidence_id_for": (".parser", "evidence_id_for"),
    "parse_amount": (".parser", "parse_amount"),
    "parse_receipt": (".parser", "parse_receipt"),
    "classify_document": (".documents", "classify_document"),
    "detect_out_of_scope": (".documents", "detect_out_of_scope"),
    "ImageValidationError": (".pipeline", "ImageValidationError"),
    "ReceiptPipeline": (".pipeline", "ReceiptPipeline"),
    "ReceiptUploadResult": (".pipeline", "ReceiptUploadResult"),
    "validate_image": (".pipeline", "validate_image"),
    "ReceiptAccuracyReport": (".accuracy", "ReceiptAccuracyReport"),
    "ReceiptCase": (".accuracy", "ReceiptCase"),
    "ReceiptCaseResult": (".accuracy", "ReceiptCaseResult"),
    "load_cases": (".accuracy", "load_cases"),
    "measure_accuracy": (".accuracy", "measure_accuracy"),
    "score_draft": (".accuracy", "score_draft"),
}


def __getattr__(name: str):
    try:
        module_name, attr = _LAZY_EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    module = importlib.import_module(module_name, __package__)
    return getattr(module, attr)
