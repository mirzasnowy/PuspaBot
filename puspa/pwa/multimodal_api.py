"""HTTP layer for multimodal chat (COW-19).

The chat composer can already send text; these routes add
what makes a message multimodal, all behind login:

- ``GET /api/chat/attachments/{attachment_id}`` -> the
  attachment's bytes (or a redirect for a provider-hosted
  image). Only the owner can download it; anyone else gets
  404, so an id cannot be used to probe another user's files.
- ``POST /api/multimodal/describe`` (JSON ``attachment_id``,
  optional ``prompt``) -> ``{ok, description, provider,
  model}``: the routed vision model describes the attached
  image. 404 for an unknown or foreign attachment, 400 when
  it is not an image or no vision route/key is configured,
  502 when the vision provider fails.
- ``POST /api/multimodal/generate-image`` (JSON ``prompt``,
  optional ``size``) -> ``{ok, message, attachment}``: the
  routed image provider generates the picture, it is stored
  as an attachment, and it lands on the chat as an assistant
  message carrying the image. 400 when no image route/key is
  configured or the prompt is empty, 502 when the provider
  fails.

Sending an attachment rides on ``POST /api/chat`` itself (see
:mod:`puspa.pwa.app`): a multipart post with ``text`` and
optional ``files`` stores each file as an attachment on the
message. A voice note rides on the voice flow (see
:mod:`puspa.pwa.voice_api`): uploading with ``keep_audio``
keeps the recording, and confirming attaches it to the sent
message with its transcript.

No route here takes a provider key: vision and image calls
resolve the key from the vault at call time, and the
``httpx`` clients are the app's injected seams
(``app.state.vision_http_client`` /
``app.state.image_http_client``), so tests answer with a mock
transport and the suite never touches a real provider.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from ..i18n.server_lines import render as render_server_line
from ..multimodal.attachments import AttachmentError, AttachmentStore
from ..multimodal.generation import generate_image_attachment
from ..multimodal.turns import assistant_message_with_image
from ..multimodal.vision import describe_image
from ..router.complete import CompletionError

__all__ = ["register_routes"]


def _not_logged_in() -> JSONResponse:
    return JSONResponse({"error": render_server_line("error.not_logged_in")}, status_code=401)


def _store(request: Request) -> AttachmentStore:
    return request.app.state.attachment_store


def _dsn(request: Request):
    return request.app.state.dsn


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the multimodal routes on ``app`` using its login resolver."""

    @app.get("/api/chat/attachments/{attachment_id}")
    async def download_attachment(request: Request, attachment_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        attachment = _store(request).get(user_id, attachment_id)
        if attachment is None:
            return JSONResponse({"error": "unknown attachment"}, status_code=404)
        if attachment.storage.get("type") == "url":
            return RedirectResponse(url=attachment.storage.get("url") or "", status_code=302)
        try:
            data = _store(request).read_bytes(attachment)
        except AttachmentError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        return Response(data, media_type=attachment.mime_type)

    @app.post("/api/multimodal/describe")
    async def describe(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            data = await request.json()
        except ValueError:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        if not isinstance(data, dict):
            return JSONResponse({"error": "describe body must be a JSON object"}, status_code=400)
        attachment_id = str(data.get("attachment_id", "") or "")
        attachment = _store(request).get(user_id, attachment_id)
        if attachment is None:
            return JSONResponse({"error": "unknown attachment"}, status_code=404)
        if attachment.kind != "image":
            return JSONResponse(
                {"error": "only an image attachment can be described"}, status_code=400
            )
        try:
            result = describe_image(
                str(data.get("prompt", "") or ""),
                user_id=user_id,
                attachment=attachment,
                store=_store(request),
                dsn=_dsn(request),
                client=getattr(request.app.state, "vision_http_client", None),
            )
        except (AttachmentError, ValueError, KeyError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except CompletionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return {
            "ok": True,
            "description": result.text,
            "provider": result.provider,
            "model": result.model,
            "attachment_id": attachment_id,
        }

    @app.post("/api/multimodal/generate-image")
    async def generate(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            data = await request.json()
        except ValueError:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        if not isinstance(data, dict):
            return JSONResponse(
                {"error": "generate-image body must be a JSON object"}, status_code=400
            )
        prompt = str(data.get("prompt", "") or "").strip()
        if not prompt:
            return JSONResponse({"error": "prompt must be a non-empty string"}, status_code=400)
        size = str(data.get("size", "") or "1024x1024")
        try:
            attachment, result = generate_image_attachment(
                prompt,
                user_id=user_id,
                store=_store(request),
                size=size,
                dsn=_dsn(request),
                client=getattr(request.app.state, "image_http_client", None),
            )
        except (AttachmentError, ValueError, KeyError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        message = assistant_message_with_image(
            result.revised_prompt or prompt,
            attachment,
        )
        request.app.state.chat[user_id].append(message)
        request.app.state.event.append(
            {
                "type": "assistant_message",
                "task_id": "",
                "agent_id": "puspa",
                "content": {"sender": user_id, "text": message["text"]},
                "created_at": message["created_at"],
            }
        )
        return {"ok": True, "message": message, "attachment": attachment.to_public_dict()}

    return app
