"""HTTP layer for voice messages in chat (P-03, plan section 12.4; V2-30).

Voice messages are common in Indonesia, so the chat composer can
record audio, but the audio is never acted on directly. The flow
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
  empty, over 10 MiB). Which transcriber runs: an explicitly
  injected one (tests) wins; otherwise the user's own ASR choice
  from Settings (``puspa.pwa.voice_settings``): an
  OpenAI-compatible BYOK endpoint when configured, else the stub.
- ``POST /api/voice/confirm`` (JSON ``pending_id``, optional
  ``text``) -> sends the transcript (or the user's edited text) as
  a chat message, exactly as ``POST /api/chat`` would, and drops
  the pending record. 404 for an unknown or foreign pending id;
  400 for empty text.
- ``POST /api/voice/discard`` (JSON ``pending_id``) -> drops the
  pending transcript without sending anything. 404 for an unknown
  or foreign pending id.
- ``GET /api/voice/status`` -> which transcriber and TTS provider
  this user has configured, and whether an engine is chosen.
  The app default is still unchosen (no engine is picked for the
  user until the 50-recording WER measurement
  (:mod:`puspa.voice.wer`) exists); a user who configured their
  own BYOK endpoint sees that choice reported honestly.
- ``GET /api/voice/settings`` / ``POST /api/voice/settings`` ->
  the user's voice choice (ASR + TTS providers, voices, endpoint
  overrides). Carries no key material: a key field in the POST
  body is refused with 400.
- ``POST /api/voice/key`` (JSON ``kind``, ``provider``, ``key``)
  / ``DELETE /api/voice/key?kind=&provider=`` -> store / remove
  a voice key in the vault only.
- ``POST /api/voice/speak`` (JSON ``text``) -> spoken audio for
  a reply, as ``audio/mpeg`` bytes. Default chain: Edge TTS
  (free, no key); when Edge fails the response is 502 with
  ``{"fallback": "browser"}`` and the PWA speaks with the
  browser's built-in voice instead. When the user picked
  ``browser`` in Settings, the response is JSON
  ``{"fallback": "browser"}`` without calling any server engine.
  Text is capped at 2000 characters per call.

Pending transcripts live in ``app.state.voice_pending`` (in-memory,
like the chat state in ``puspa.pwa.app``); they are never written
to the ledger, because an unconfirmed transcript is not a message.
"""

from __future__ import annotations

import time
import uuid
from typing import Annotated

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from ..i18n.server_lines import render as render_server_line
from ..voice.pipeline import AudioValidationError, VoicePipeline
from ..voice.wer import REQUIRED_CASES

__all__ = ["register_routes"]


def _not_logged_in() -> JSONResponse:
    return JSONResponse({"error": render_server_line("error.not_logged_in")}, status_code=401)


def _post_chat_message(
    app,
    user_id: str,
    text: str,
    *,
    audio_path=None,
    mime_type=None,
    transcript: str | None = None,
) -> dict:
    """Append a message to the user's chat, as ``POST /api/chat`` does.

    ``audio_path`` is a kept voice recording: it is adopted as
    an audio attachment on the message, carrying the transcript
    with it, so the voice note stays playable and retrievable
    instead of vanishing once transcribed.
    """
    message: dict = {
        "sender": "users",
        "text": text,
        "created_at": time.time(),
        "attachments": [],
    }
    if audio_path is not None:
        from ..multimodal.attachments import AttachmentError

        try:
            attachment = app.state.attachment_store.adopt_file(
                user_id,
                audio_path,
                mime_type or "audio/webm",
                filename="voice-note",
                transcript=transcript if transcript is not None else text,
            )
            message["attachments"] = [attachment.to_public_dict()]
        except (AttachmentError, AttributeError):
            # The recording is gone or the store is unavailable:
            # the transcript still sends, without an attachment.
            pass
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


def _dsn(request: Request):
    return request.app.state.dsn


def _resolve_upload_transcriber(app, user_id: str):
    """The transcriber one upload runs through.

    An explicitly injected transcriber (tests, or an operator
    override) wins. Otherwise the user's own ASR choice from
    Settings applies. Raises ``KeyError`` / ``ValueError`` when
    that choice cannot run (no key stored yet); the caller falls
    back to the stub then, and the ``engine`` field says so.
    """
    if getattr(app.state, "transcriber_explicit", False):
        transcriber = getattr(app.state, "transcriber", None)
        if transcriber is None:
            from ..voice.transcriber import resolve_transcriber

            transcriber = resolve_transcriber("stub")
        return transcriber
    from ..pwa.voice_settings import resolve_asr

    return resolve_asr(
        user_id, dsn=app.state.dsn, http_client=getattr(app.state, "voice_http_client", None)
    )


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the voice routes on ``app`` using its login resolver."""

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
            upload_transcriber = _resolve_upload_transcriber(request.app, user_id)
        except (KeyError, ValueError):
            from ..voice.transcriber import StubTranscriber

            upload_transcriber = StubTranscriber()
        try:
            result = VoicePipeline(upload_transcriber).transcribe_upload(
                content,
                audio.content_type or "",
                keep_audio=keep_audio,
                keep_dir=keep_dir,
            )
        except AudioValidationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:
            # A configured BYOK endpoint that fails must not
            # silently answer with an empty stub transcript: the
            # user hears that transcription failed and can retry.
            from ..voice.asr import ASRError

            if isinstance(exc, ASRError):
                return JSONResponse({"error": str(exc)}, status_code=502)
            raise
        pending_id = uuid.uuid4().hex
        pending: dict = {
            "user_id": user_id,
            "transcript": result.transcript,
            "language": result.language,
            "engine": result.engine,
            "created_at": time.time(),
        }
        # COW-19: a kept recording is a voice note, not just a
        # transcript source — the confirm step adopts it as an
        # audio attachment on the sent message. A deleted
        # recording leaves no audio behind, as before.
        if result.audio_path is not None:
            pending["audio_path"] = result.audio_path
            pending["mime_type"] = result.mime_type
            pending["size_bytes"] = result.size_bytes
        _pending_store(request)[pending_id] = pending
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
        message = _post_chat_message(
            request.app,
            user_id,
            text,
            audio_path=pending.get("audio_path"),
            mime_type=pending.get("mime_type"),
            transcript=pending.get("transcript"),
        )
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
        from ..pwa.voice_settings import get_settings

        settings = get_settings(user_id, dsn=_dsn(request))
        if getattr(request.app.state, "transcriber_explicit", False):
            engine = getattr(request.app.state, "transcriber", None)
            engine = engine.name if engine is not None else "stub"
            engine_selected = False
        elif settings.asr_provider == "none":
            engine = "stub"
            engine_selected = False
        else:
            engine = settings.asr_provider
            engine_selected = settings.has_asr_key
        if engine_selected:
            note = (
                "Your own ASR endpoint is configured; uploads are "
                "transcribed through it with your vault key."
            )
        else:
            note = (
                "No speech-to-text engine is chosen yet. Engine selection "
                "needs a word error rate measurement on 50 real recordings "
                "(slang and regional mix); until then the test-double "
                "transcriber answers uploads, unless you point Settings "
                "at your own OpenAI-compatible endpoint."
            )
        return {
            "engine": engine,
            "engine_selected": engine_selected,
            "selection": "done" if engine_selected else "pending",
            "required_recordings": REQUIRED_CASES,
            "tts_provider": settings.tts_provider,
            "tts_voice": settings.tts_voice,
            "has_asr_key": settings.has_asr_key,
            "has_tts_key": settings.has_tts_key,
            "note": note,
        }

    @app.get("/api/voice/settings")
    async def voice_get_settings(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        from ..pwa.voice_settings import get_settings

        try:
            return get_settings(user_id, dsn=_dsn(request)).to_public_dict()
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/voice/settings")
    async def voice_save_settings(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        from ..pwa.voice_settings import save_settings

        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("voice settings body must be a JSON object")
            saved = save_settings(
                user_id,
                {
                    "asr_provider": data.get("asr_provider", "none"),
                    "asr_base_url": data.get("asr_base_url"),
                    "asr_model": data.get("asr_model"),
                    "tts_provider": data.get("tts_provider", "edge"),
                    "tts_voice": data.get("tts_voice"),
                    "tts_model": data.get("tts_model"),
                    "tts_base_url": data.get("tts_base_url"),
                    "tts_region": data.get("tts_region"),
                    # Any key field the client sends is passed
                    # through so save_settings refuses it loudly
                    # instead of silently dropping it.
                    **{
                        k: v
                        for k, v in data.items()
                        if k
                        not in (
                            "asr_provider",
                            "asr_base_url",
                            "asr_model",
                            "tts_provider",
                            "tts_voice",
                            "tts_model",
                            "tts_base_url",
                            "tts_region",
                        )
                    },
                },
                dsn=_dsn(request),
            )
            return saved.to_public_dict()
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/voice/key")
    async def voice_save_key(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        from ..pwa.voice_settings import save_voice_key

        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("voice key body must be a JSON object")
            save_voice_key(
                user_id,
                str(data.get("kind", "")),
                str(data.get("provider", "")),
                str(data.get("key", "")),
                dsn=_dsn(request),
            )
            return {"ok": True}
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.delete("/api/voice/key")
    async def voice_delete_key(request: Request, kind: str = "", provider: str = ""):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        from ..pwa.voice_settings import delete_voice_key

        try:
            delete_voice_key(user_id, kind, provider, dsn=_dsn(request))
            return {"ok": True}
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/voice/speak")
    async def speak_voice(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        from ..pwa.voice_settings import get_settings, resolve_tts
        from ..voice.edge_tts import EdgeTTSError
        from ..voice.tts import MAX_SPEAK_CHARS, TTSError, synthesize_with_edge

        try:
            data = await request.json()
        except ValueError:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        if not isinstance(data, dict):
            return JSONResponse({"error": "speak body must be a JSON object"}, status_code=400)
        text = str(data.get("text", "") or "").strip()
        if not text:
            return JSONResponse({"error": "empty text"}, status_code=400)
        if len(text) > MAX_SPEAK_CHARS:
            return JSONResponse(
                {"error": f"text over {MAX_SPEAK_CHARS} characters"}, status_code=400
            )
        try:
            settings = get_settings(user_id, dsn=_dsn(request))
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if settings.tts_provider == "browser":
            return {"ok": True, "fallback": "browser", "engine": "browser"}
        try:
            resolved = resolve_tts(
                user_id,
                settings,
                dsn=_dsn(request),
                http_client=getattr(request.app.state, "voice_http_client", None),
            )
        except KeyError as exc:
            return JSONResponse({"error": str(exc), "fallback": "browser"}, status_code=400)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if isinstance(resolved, tuple) and resolved[0] == "edge":
            try:
                result = await synthesize_with_edge(
                    text,
                    resolved[1],
                    edge_connect=getattr(request.app.state, "voice_edge_connect", None),
                )
            except EdgeTTSError as exc:
                return JSONResponse(
                    {"error": str(exc), "fallback": "browser", "engine": "edge"},
                    status_code=502,
                )
            return Response(
                result.audio,
                media_type=result.mime_type,
                headers={"X-Voice-Engine": "edge"},
            )
        engine = resolved
        try:
            result = engine.synthesize(text)
        except TTSError as exc:
            return JSONResponse(
                {"error": str(exc), "fallback": "browser", "engine": engine.name},
                status_code=502,
            )
        return Response(
            result.audio,
            media_type=result.mime_type,
            headers={"X-Voice-Engine": engine.name},
        )

    return app
