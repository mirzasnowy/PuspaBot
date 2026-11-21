"""HTTP layer for receipt photos in chat (plan section 12.5).

A photo of a receipt, shop note, bill, delivery slip, or menu
becomes a recorded expense — but only after the user confirms
it. The flow is always: upload the photo, read it, **show the
draft for confirmation** (editable), and only then record it.
Nothing is ever recorded from a photo alone.

Contract (all routes require login):

- ``POST /api/receipts/upload`` (multipart ``photo``) ->
  ``{ok, pending_id, photo_id, draft, ocr_lines, engine,
  elapsed_seconds, document_kind}``. The photo is stored in
  the per-user share inbox under ``receipts/``: the photo
  itself is the evidence for every number in the draft, so
  unlike voice audio it is kept, not deleted. 400 on a bad
  photo (wrong type, empty, over 10 MiB, bytes that do not
  decode as an image). 422 when the photo looks like an
  identity card, family card, or official letter: refused with
  an explanation, and nothing is stored.
- ``POST /api/receipts/confirm`` (JSON ``pending_id``, optional
  edited ``merchant`` / ``total`` / ``expense_date`` /
  ``lines``) -> records the expense with every amount carrying
  its evidence id, posts a summary to chat, and drops the
  pending record. 404 for an unknown or foreign pending id;
  400 for an empty merchant or a non-positive total. An edited
  total keeps the photo-level evidence id and is marked
  ``edited_by_user``: the photo stays the evidence, the edit is
  visible.
- ``POST /api/receipts/discard`` (JSON ``pending_id``) -> drops
  the pending draft and deletes the stored photo. 404 for an
  unknown or foreign pending id.
- ``GET /api/receipts/expenses`` -> the user's confirmed
  expenses, each amount with its evidence id.
- ``GET /api/receipts/status`` -> which OCR engine is
  configured and whether engine selection is done. It is not
  done: no real engine is chosen until the accuracy measurement
  (:mod:`puspa.receipts.accuracy`) exists on 20 real receipt
  photos, and this endpoint says so instead of pretending
  otherwise.

Pending drafts live in ``app.state.receipt_pending``
(in-memory, like the voice pending transcripts in
``puspa.pwa.voice_api``); confirmed expenses live in
``app.state.receipt_expenses`` (in-memory per user) and are
announced in chat. There is no database table for expenses
yet: adding one would renumber the migration series the
migration tests pin at 0023, so persistence is recorded as a
limitation in ``docs/receipts-photo.md`` instead of smuggled
in here.
"""

from __future__ import annotations

import contextlib
import time
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import JSONResponse

from ..i18n.server_lines import render as render_server_line
from ..receipts.accuracy import REQUIRED_CASES
from ..receipts.ocr import resolve_ocr_engine
from ..receipts.pipeline import (
    MAX_PHOTO_BYTES,
    ImageValidationError,
    ReceiptPipeline,
)

__all__ = ["register_routes"]

_MIME_SUFFIX = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heif",
}


def _not_logged_in() -> JSONResponse:
    return JSONResponse({"error": render_server_line("error.not_logged_in")}, status_code=401)


def _pending_store(request: Request) -> dict:
    return request.app.state.receipt_pending


def _expense_store(request: Request) -> dict:
    return request.app.state.receipt_expenses


def _post_chat_message(app, user_id: str, text: str) -> dict:
    """Append a message to the user's chat, as ``POST /api/chat`` does."""
    message = {"sender": "users", "text": text, "created_at": time.time()}
    app.state.chat[user_id].append(message)
    app.state.event.append(
        {
            "type": "user_message",
            "task_id": "",
            "agent_id": "puspa",
            "content": {"sender": user_id, "text": text},
            "created_at": time.time(),
        }
    )
    return message


def _format_amount(amount: int, currency: str) -> str:
    return f"{currency} {amount:,}".replace(",", ".")


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the receipt routes on ``app`` using its login resolver."""
    ocr_engine = getattr(app.state, "ocr_engine", None)
    if ocr_engine is None:
        ocr_engine = resolve_ocr_engine("stub")
    pipeline = ReceiptPipeline(ocr_engine)

    @app.post("/api/receipts/upload")
    async def upload_receipt(
        request: Request,
        photo: Annotated[UploadFile, File()],
    ):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        content = await photo.read()
        try:
            result = pipeline.process_upload(content, photo.content_type or "")
        except ImageValidationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if result.rejected:
            return JSONResponse(
                {
                    "ok": False,
                    "rejected": True,
                    "document_kind": result.document_kind,
                    "reason": result.reject_reason,
                },
                status_code=422,
            )
        if result.draft is None:
            # Unreachable in practice: the pipeline sets a draft
            # whenever the photo is not rejected. Guarded so the
            # draft below is known to exist.
            return JSONResponse({"error": "receipt draft missing"}, status_code=500)
        base_dir = getattr(app.state, "share_inbox_dir", None)
        photo_path: str | None = None
        if base_dir is not None:
            user_dir = Path(base_dir) / user_id / "receipts"
            user_dir.mkdir(parents=True, exist_ok=True)
            suffix = _MIME_SUFFIX.get(result.mime_type, ".jpg")
            stored_name = f"{result.photo_id}{suffix}"
            stored_path = user_dir / stored_name
            stored_path.write_bytes(content)
            photo_path = str(stored_path)
        pending_id = uuid.uuid4().hex
        _pending_store(request)[pending_id] = {
            "user_id": user_id,
            "photo_id": result.photo_id,
            "photo_path": photo_path,
            "draft": result.draft.to_dict(),
            "ocr_lines": result.ocr_lines,
            "engine": result.engine,
            "document_kind": result.document_kind,
            "created_at": time.time(),
        }
        return {
            "ok": True,
            "pending_id": pending_id,
            "photo_id": result.photo_id,
            "photo_path": photo_path,
            "draft": result.draft.to_dict(),
            "ocr_lines": result.ocr_lines,
            "engine": result.engine,
            "elapsed_seconds": round(result.elapsed_seconds, 3),
            "document_kind": result.document_kind,
            "size_bytes": result.size_bytes,
            "mime_type": result.mime_type,
        }

    @app.post("/api/receipts/confirm")
    async def confirm_receipt(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            data = await request.json()
        except ValueError:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        pending_id = str(data.get("pending_id", "") or "")
        pending = _pending_store(request).get(pending_id)
        if pending is None or pending["user_id"] != user_id:
            return JSONResponse({"error": "unknown receipt"}, status_code=404)
        draft = dict(pending["draft"])
        edited = False
        merchant = str(data.get("merchant", "") or draft.get("merchant") or "").strip()
        if not merchant:
            return JSONResponse({"error": "empty merchant"}, status_code=400)
        if merchant != draft.get("merchant"):
            edited = True
        total = data.get("total", draft.get("total"))
        try:
            total = int(total)
        except (TypeError, ValueError):
            return JSONResponse({"error": "total must be a number"}, status_code=400)
        if total <= 0:
            return JSONResponse({"error": "total must be positive"}, status_code=400)
        if total != draft.get("total"):
            edited = True
        expense_date = (
            str(data.get("expense_date", "") or draft.get("expense_date") or "").strip() or None
        )
        if expense_date != draft.get("expense_date"):
            edited = True
        lines = data.get("lines", draft.get("lines"))
        if not isinstance(lines, list):
            return JSONResponse({"error": "lines must be a list"}, status_code=400)
        clean_lines: list[dict] = []
        for position, line in enumerate(lines):
            if not isinstance(line, dict):
                continue
            raw_amount = line.get("amount")
            if raw_amount is None:
                continue
            try:
                amount = int(raw_amount)
            except (TypeError, ValueError):
                continue
            if amount <= 0:
                continue
            name = str(line.get("name", "") or "").strip()
            if not name:
                continue
            # An edited line keeps the evidence id of the line it
            # came from when one is given; a brand-new line is
            # evidence-backed by the photo itself.
            try:
                line_index = int(line.get("line_index", position))
            except (TypeError, ValueError):
                line_index = position
            clean_lines.append(
                {
                    "name": name[:120],
                    "amount": amount,
                    "evidence_id": str(
                        line.get("evidence_id")
                        or f"receipt-{pending['photo_id']}-line-{line_index}"
                    ),
                    "line_index": line_index,
                }
            )
        draft_pairs = [
            (str(item.get("name", "")), int(item.get("amount", 0)))
            for item in draft.get("lines", [])
            if isinstance(item, dict)
        ]
        clean_pairs = [(line["name"], line["amount"]) for line in clean_lines]
        if clean_pairs != draft_pairs:
            edited = True
        expense = {
            "expense_id": uuid.uuid4().hex[:12],
            "photo_id": pending["photo_id"],
            "photo_path": pending["photo_path"],
            "merchant": merchant,
            "expense_date": expense_date,
            "currency": draft.get("currency", "Rp"),
            "lines": clean_lines,
            "total": total,
            "total_evidence_id": draft.get("total_evidence_id"),
            "edited_by_user": edited,
            "engine": pending["engine"],
            "document_kind": pending["document_kind"],
            "created_at": time.time(),
        }
        _expense_store(request).setdefault(user_id, []).append(expense)
        summary_lines = [
            f"Pengeluaran dicatat: {merchant} — "
            f"{_format_amount(total, expense['currency'])}"
            + (f" ({expense_date})" if expense_date else ""),
            (f"Bukti: foto {pending['photo_id']} (total: {expense['total_evidence_id']})"),
        ]
        if edited:
            summary_lines.append("Diedit pengguna sebelum disimpan.")
        _post_chat_message(request.app, user_id, "\n".join(summary_lines))
        del _pending_store(request)[pending_id]
        return {"ok": True, "expense": expense}

    @app.post("/api/receipts/discard")
    async def discard_receipt(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            data = await request.json()
        except ValueError:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        pending_id = str(data.get("pending_id", "") or "")
        pending = _pending_store(request).get(pending_id)
        if pending is None or pending["user_id"] != user_id:
            return JSONResponse({"error": "unknown receipt"}, status_code=404)
        photo_path = pending.get("photo_path")
        if photo_path:
            with contextlib.suppress(OSError):
                Path(photo_path).unlink(missing_ok=True)
        del _pending_store(request)[pending_id]
        return {"ok": True}

    @app.get("/api/receipts/expenses")
    async def list_expenses(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        return {
            "ok": True,
            "expenses": list(_expense_store(request).get(user_id, [])),
        }

    @app.get("/api/receipts/status")
    async def receipt_status(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        return {
            "engine": ocr_engine.name,
            "engine_selected": False,
            "selection": "pending",
            "required_cases": REQUIRED_CASES,
            "max_photo_bytes": MAX_PHOTO_BYTES,
            "note": (
                "No OCR engine is chosen yet. Engine selection "
                "needs a total-receipt accuracy measurement on 20 "
                "real receipt photos; until then the test-double "
                "OCR engine answers uploads. No OCR engine is "
                "installed on this host (no tesseract binary, no "
                "pytesseract, easyocr, rapidocr, or paddleocr)."
            ),
        }

    return app
