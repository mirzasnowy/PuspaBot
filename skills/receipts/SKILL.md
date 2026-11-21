---
name: "Receipts"
description: "Reads the user's receipts and documents: validates the image, runs OCR, and drafts the receipt lines for the user to confirm. OCR needs a configured or installed engine; the bundled engine is a stub."
author: "Puspa Team"
---

# Receipts

This skill reads receipts and documents the user photographs or
uploads: it validates the image, runs OCR, classifies the
document, and drafts the receipt lines. The user confirms the
draft — the skill never books an amount as fact on its own.

## When to Use This Skill

Use this skill when the user:

- Uploads a photo of a receipt, invoice, or bill and asks what
  it says or what it totals
- Asks to record a receipt's lines (items, amounts, dates)
- Asks whether a document is a receipt at all

## How Receipts Work

```python
from puspa.receipts import (
    ReceiptPipeline,
    resolve_ocr_engine,
    validate_image,
    classify_document,
    detect_out_of_scope,
)

validate_image(image_bytes, mime_type)  # size, type, and shape checks
pipeline = ReceiptPipeline(ocr_engine=resolve_ocr_engine("mapping", mapping={...}))
result = pipeline.process_upload(image_bytes, mime_type=mime_type)
draft = result.draft  # ReceiptDraft: lines the user confirms
```

- `validate_image` rejects images that are too large, in an
  unsupported format, or otherwise unusable — before OCR runs.
- `classify_document` sorts the upload (receipt, invoice, or
  other); `detect_out_of_scope` refuses documents this skill
  does not handle, with the reason, instead of forcing them
  through OCR.
- The pipeline returns a `ReceiptDraft`: OCR lines plus a
  proposed reading (items, amounts, total, date). The draft is
  shown to the user for confirmation; only a confirmed draft
  becomes a record. `evidence_id_for` ties the draft to its
  evidence row.

## Accuracy

`puspa.receipts.accuracy` measures OCR against cases
(`load_cases`, `measure_accuracy` → `ReceiptAccuracyReport` with
per-case results). When the user questions a reading, re-run the
case measurement instead of arguing from one sample — and report
the measured accuracy, including the misses.

## Limits — Read These First

- **No real OCR engine is bundled.** The engines shipped are
  `StubOcrEngine` (returns no lines) and `MappingOcrEngine`
  (returns pre-mapped text for known images, for tests). Real
  text comes only from a configured mapping engine or an engine
  installed on the host (`detect_installed_engines`,
  `available_ocr_engines`). With only the stub, a receipt reads
  as nothing — say so, do not invent lines.
- **Drafts are not records.** A `ReceiptDraft` is a proposal.
  Never present its total as a confirmed expense, and never
  write it into a module table, until the user confirms it.
- **Out-of-scope is refused.** A photo that is not a receipt or
  invoice is refused by `detect_out_of_scope`, and identity or
  official documents are refused outright by the pipeline
  (`rejected=True`, no draft, photo not stored). Do not OCR them
  anyway and guess.
- **The image check comes first.** Bypassing `validate_image`
  to force a bad upload through OCR is not allowed.

## Example

User uploads a receipt photo: "Berapa totalnya?"

1. `validate_image`, then `classify_document` / `detect_out_of_scope`.
2. Run the pipeline with the configured OCR engine.
3. If the engine is only the stub, say the receipt cannot be
   read yet and ask the user to type the total — do not invent
   lines.
4. Otherwise show the draft lines and total, and ask the user to
   confirm before recording anything.
