"""HTTP layer for voice messages in chat (P-03, plan section 12.4).

Voice messages are common in Indonesia, so the chat composer can
record audio — but the audio is never acted on directly. The flow
is always: record, transcribe, **show the transcript for
confirmation**, and only then send. The transcript is the message;
the user may edit it before confirming, or discard it.

Contract (all routes require login):

- ``POST /api/voice/upload`` (multipart ``audio``, optional
  ``keep_audio``) -> ``{ok, pending_id, transcript, language,
  engine, audio_deleted, audio_path, size_bytes, mime_type}``.
  The audio is transcribed through the app's transcriber seam and
  deleted right away; ``audio_deleted`` is true unless the caller
  explicitly asked to keep it. 400 on a bad recording (wrong type,
  empty, over 10 MiB).
- ``POST /api/voice/confirm`` (JSON ``pending_id``, optional
  ``text``) -> sends the transcript (or the user's edited text) as
  a chat message, exactly as ``POST /api/chat`` would, and drops
  the pending record. 404 for an unknown or foreign pending id;
  400 for empty text.
- ``POST /api/voice/discard`` (JSON ``pending_id``) -> drops the
  pending transcript without sending anything. 404 for an unknown
  or foreign pending id.
- ``GET /api/voice/status`` -> which transcriber is configured and
  whether engine selection is done. It is not done: no real engine
  is chosen until the 50-recording WER measurement
  (:mod:`puspa.voice.wer`) exists, and this endpoint says so
  instead of pretending otherwise.

Pending transcripts live in ``app.state.voice_pending`` (in-memory,
like the chat state in ``puspa.pwa.app``); they are never written
to the ledger, because an unconfirmed transcript is not a message.
"""

from __future__ import annotations

import time
import uuid
from typing import Annotated

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

from ..i18n.server_lines import render as render_server_line
from ..voice.pipeline import AudioValidationError, VoicePipeline
from ..voice.wer import REQUIRED_CASES

__all__ = ["register_routes"]


def _not_logged_in() -> JSONResponse:
    return JSONResponse({"error": render_server_line("error.not_logged_in")}, status_code=401)


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


def _pending_store(request: Request) -> dict:
    return request.app.state.voice_pending


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the voice routes on ``app`` using its login resolver."""
    transcriber = getattr(app.state, "transcriber", None)
    if transcriber is None:
        from ..voice.transcriber import resolve_transcriber

        transcriber = resolve_transcriber("stub")
    pipeline = VoicePipeline(transcriber)

    @app.post("/api/voice/upload")
    async def upload_voice(
        request: Request,
        audio: Annotated[UploadFile, File()],
        keep_audio: Annotated[bool, Form()] = False,
    ):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        content = await audio.read()
        keep_dir = None
        if keep_audio:
            base_dir = getattr(app.state, "share_inbox_dir", None)
            keep_dir = base_dir / user_id / "voice" if base_dir is not None else None
        try:
            result = pipeline.transcribe_upload(
                content,
                audio.content_type or "",
                keep_audio=keep_audio,
                keep_dir=keep_dir,
            )
        except AudioValidationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        pending_id = uuid.uuid4().hex
        _pending_store(request)[pending_id] = {
            "user_id": user_id,
            "transcript": result.transcript,
            "language": result.language,
            "engine": result.engine,
            "created_at": time.time(),
        }
        return {
            "ok": True,
            "pending_id": pending_id,
            "transcript": result.transcript,
            "language": result.language,
            "engine": result.engine,
            "audio_deleted": result.audio_deleted,
            "audio_path": result.audio_path,
            "size_bytes": result.size_bytes,
            "mime_type": result.mime_type,
        }

    @app.post("/api/voice/confirm")
    async def confirm_voice(request: Request):
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
            return JSONResponse({"error": "unknown voice message"}, status_code=404)
        text = str(data.get("text", "") or pending["transcript"]).strip()
        if not text:
            return JSONResponse({"error": "empty transcript"}, status_code=400)
        message = _post_chat_message(request.app, user_id, text)
        del _pending_store(request)[pending_id]
        return {"ok": True, "message": message}

    @app.post("/api/voice/discard")
    async def discard_voice(request: Request):
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
            return JSONResponse({"error": "unknown voice message"}, status_code=404)
        del _pending_store(request)[pending_id]
        return {"ok": True}

    @app.get("/api/voice/status")
    async def voice_status(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        return {
            "engine": transcriber.name,
            "engine_selected": False,
            "selection": "pending",
            "required_recordings": REQUIRED_CASES,
            "note": (
                "No speech-to-text engine is chosen yet. Engine selection "
                "needs a word error rate measurement on 50 real recordings "
                "(slang and regional mix); until then the test-double "
                "transcriber answers uploads."
            ),
        }

    return app
