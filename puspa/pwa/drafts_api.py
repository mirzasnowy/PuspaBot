"""HTTP layer for co-drafting with context memory (B13).

Plain handler functions over :mod:`puspa.drafting`, plus
:func:`register_routes`, which mounts them on a FastAPI app. This
module deliberately does not import ``puspa.pwa.app``: the
coordinator wires it into ``create_app`` later with a few lines::

    from puspa.pwa import drafts_api
    drafts_api.register_routes(app, need_user)

``need_user`` is the app's own login resolver (``Request`` ->
user id or None); it is passed in rather than reimplemented here,
so login behaviour stays in exactly one place.

Contract (all responses are JSON; all routes require login):

- ``POST /api/drafts`` (JSON: ``kind`` one of ``message`` /
  ``document`` / ``email``, ``brief``, optional ``recipient``,
  ``channel``) -> the created draft in the user's voice, with
  any decision flags. 400 on bad input.
- ``GET /api/drafts`` -> the user's drafts, oldest first.
- ``GET /api/drafts/{draft_id}`` -> one draft. 404 for an
  unknown draft or one that belongs to another user.
- ``POST /api/drafts/{draft_id}/approve`` -> the user's explicit
  approval to send this draft (single-use, 24 hours, bound to
  the draft text right now). Approving does not send anything.
- ``POST /api/drafts/{draft_id}/send`` (JSON: ``approval_id``)
  -> sends the draft through the app's delivery channel.
  403 when there is no usable approval for this draft text;
  501 when no delivery channel is wired into the app (the
  approval is kept, nothing is sent, nothing is claimed as
  sent). A draft never sends without approval.
- ``GET /api/voice/profile`` -> the user's voice profile as the
  agent reads it: greeting, closing, rules, style notes,
  preferences, and recorded decisions.
- ``POST /api/voice/corrections`` (JSON: ``correction``) ->
  records a voice correction as a memory fact, so later drafts
  change. 400 on bad input.

The delivery channel is ``app.state.draft_sender`` (a callable
taking the send payload, set by the app that hosts these
routes). ``create_app`` leaves it unset: until a channel
(Telegram / WhatsApp / email) is wired in, sends are refused
with 501 — the honest state, not a silent no-op.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..drafting import DraftingService, PostgresDraftStore
from ..drafting.errors import (
    DraftNotApprovedError,
    DraftNotFoundError,
    DraftValidationError,
    NoDeliveryChannelError,
)
from ..i18n.server_lines import render as render_server_line
from ..memory.facts import PostgresFactStore

__all__ = [
    "draft_to_dict",
    "register_routes",
    "voice_profile_to_dict",
]


def draft_to_dict(draft) -> dict:
    """One draft, as served by the drafts routes."""
    return draft.to_dict()


def voice_profile_to_dict(profile) -> dict:
    """One voice profile, as served by ``GET /api/voice/profile``."""
    return {
        "user_id": profile.user_id,
        "greeting": profile.greeting,
        "closing": profile.closing,
        "rules": [rule.describe() for rule in profile.rules],
        "style_notes": list(profile.style_notes),
        "preferences": list(profile.preferences),
        "decisions": list(profile.decisions),
    }


def _service(request: Request) -> DraftingService:
    dsn = request.app.state.dsn
    return DraftingService(
        dsn,
        draft_store=PostgresDraftStore(dsn),
        fact_store=PostgresFactStore(dsn),
    )


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the drafting routes on ``app`` using its login resolver."""

    @app.post("/api/drafts")
    async def create_draft_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise DraftValidationError("draft body must be a JSON object")
            draft = _service(request).draft(
                user_id,
                kind=str(data.get("kind") or ""),
                brief=str(data.get("brief") or ""),
                recipient=data.get("recipient"),
                channel=data.get("channel"),
            )
            return draft_to_dict(draft)
        except DraftValidationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.get("/api/drafts")
    def list_drafts_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        drafts = _service(request).list_drafts(user_id)
        return {"drafts": [draft_to_dict(draft) for draft in drafts]}

    @app.get("/api/drafts/{draft_id}")
    def get_draft_endpoint(request: Request, draft_id: str):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        try:
            return draft_to_dict(_service(request).get_draft(draft_id, user_id=user_id))
        except DraftNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.post("/api/drafts/{draft_id}/approve")
    def approve_draft_endpoint(request: Request, draft_id: str):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        try:
            approval = _service(request).approve_draft(draft_id, user_id=user_id)
            return {"approval": approval.to_dict()}
        except DraftNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except DraftValidationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/drafts/{draft_id}/send")
    async def send_draft_endpoint(request: Request, draft_id: str):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise DraftValidationError("send body must be a JSON object")
            sender = getattr(request.app.state, "draft_sender", None)
            return _service(request).send_draft(
                draft_id,
                user_id=user_id,
                approval_id=str(data.get("approval_id") or ""),
                sender=sender,
            )
        except DraftNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except DraftNotApprovedError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except NoDeliveryChannelError as exc:
            return JSONResponse({"error": str(exc)}, status_code=501)
        except DraftValidationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.get("/api/voice/profile")
    def voice_profile_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        profile = _service(request).voice_profile(user_id)
        return voice_profile_to_dict(profile)

    @app.post("/api/voice/corrections")
    async def correct_voice_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise DraftValidationError("correction body must be a JSON object")
            correction = data.get("correction")
            if not isinstance(correction, str):
                raise DraftValidationError("correction must be a string")
            service = _service(request)
            fact = service.correct_voice(user_id, correction)
            return {
                "fact": fact,
                "voice": voice_profile_to_dict(service.voice_profile(user_id)),
            }
        except DraftValidationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    return app
