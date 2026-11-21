"""HTTP layer for the P-12 rich cards in Chat (plan section 12.10).

Plain handler functions over :mod:`puspa.pwa.chat_cards`, plus
:func:`register_routes`, which mounts them on a FastAPI app.
This module deliberately does not import ``puspa.pwa.app``: the
coordinator wires it into ``create_app`` later with a few lines::

    from puspa.pwa import chat_cards_api
    chat_cards_api.register_routes(app, need_user)

``need_user`` is the app's own login resolver (``Request`` ->
user id or None); it is passed in rather than reimplemented
here, so login behaviour stays in exactly one place.

Cards ride on the in-memory chat store (``app.state.chat``):
posting a card appends a ``"puspa"`` message carrying the
card, so the Chat screen shows it in the thread like any
other message. No new table, no migration.

Contract (all responses are JSON; all routes require login):

- ``POST /api/chat/cards`` (JSON: ``{"text": ..., "card":
  {...}}``) -> the appended message, card validated first.
  400 for a bad card (unknown kind, bad coordinates, empty
  draft body, fewer than 2 options, negative chart value).
- ``POST /api/chat/cards/draft/{draft_id}`` -> the stored B13
  draft as a draft card in a new chat message. 404 for an
  unknown draft or one that belongs to another user.
- ``GET /api/chat/cards/insights?days=N&metric=M`` -> the
  user's G28 Insights report as a chart card in a new chat
  message. ``metric`` is one of ``turns`` / ``tokens`` /
  ``cost_idr`` (default ``cost_idr``). 400 for ``days`` outside
  1..90 or an unknown metric.
- ``401 {"error": "belum masuk"}`` when not logged in.
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..billing.insights import insights_for_user, validate_days
from ..drafting import DraftingService, PostgresDraftStore
from ..drafting.errors import DraftNotFoundError
from ..i18n.server_lines import render as render_server_line
from ..memory.facts import PostgresFactStore
from .chat_cards import (
    CHART_METRICS,
    _require_dict,
    append_card_message,
    chart_card_from_insights,
    draft_card_from_draft,
    validate_card,
)

__all__ = [
    "get_insights_card",
    "post_card",
    "post_draft_card",
    "register_routes",
]


def _not_logged_in():
    return JSONResponse({"error": render_server_line("error.not_logged_in")}, status_code=401)


def post_card(user_id: str, *, body: dict, chat_state=None) -> dict:
    """Validate a card and append it as a ``"puspa"`` chat message.

    ``chat_state`` is the app's in-memory chat store
    (``app.state.chat``); a fresh dict when none is passed, for
    tests. Raises ``ValueError`` (400) for a bad body or card.
    """
    if not isinstance(body, dict):
        raise ValueError("card body must be a JSON object")
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a non-empty string")
    card = validate_card(_require_dict(body.get("card"), "card"))
    store = chat_state if chat_state is not None else {}
    return append_card_message(store, user_id, text, card)


def post_draft_card(user_id: str, draft_id: str, *, dsn=None, chat_state=None) -> dict:
    """Append the stored B13 draft as a draft card chat message.

    Raises :class:`DraftNotFoundError` (404) for an unknown
    draft or one that belongs to another user. Draft ids are
    UUIDs (``DraftingService`` mints them with ``uuid4``) and
    the ``drafts.id`` column is UUID-typed, so a non-UUID id
    can never match a stored draft: it is a 404 here, not a
    database error bubbling up as a 500.
    """
    try:
        uuid.UUID(draft_id)
    except (ValueError, TypeError) as exc:
        raise DraftNotFoundError(f"draft not found: {draft_id}") from exc
    service = DraftingService(
        dsn,
        draft_store=PostgresDraftStore(dsn),
        fact_store=PostgresFactStore(dsn),
    )
    draft = service.get_draft(draft_id, user_id=user_id)
    card = draft_card_from_draft(draft.to_dict())
    text = f"Draft: {draft.brief}" if draft.brief else "Draft"
    store = chat_state if chat_state is not None else {}
    return append_card_message(store, user_id, text, card)


def get_insights_card(
    user_id: str,
    *,
    days: int = 7,
    metric: str = "cost_idr",
    dsn=None,
    chat_state=None,
) -> dict:
    """Append the user's Insights report as a chart card message.

    Raises ``ValueError`` (400) for ``days`` outside 1..90 or
    an unknown metric.
    """
    days = validate_days(days)
    if metric not in CHART_METRICS:
        raise ValueError(f"metric must be one of {CHART_METRICS}")
    report = insights_for_user(user_id, days=days, dsn=dsn)
    card = chart_card_from_insights(report, metric=metric)
    store = chat_state if chat_state is not None else {}
    return append_card_message(store, user_id, card["title"], card)


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the P-12 chat-card routes on ``app`` using its login resolver."""

    @app.post("/api/chat/cards")
    async def post_card_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            body = await request.json()
            return post_card(user_id, body=body, chat_state=request.app.state.chat)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/chat/cards/draft/{draft_id}")
    def post_draft_card_endpoint(request: Request, draft_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            return post_draft_card(
                user_id,
                draft_id,
                dsn=request.app.state.dsn,
                chat_state=request.app.state.chat,
            )
        except DraftNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.get("/api/chat/cards/insights")
    def get_insights_card_endpoint(request: Request, days: int = 7, metric: str = "cost_idr"):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            return get_insights_card(
                user_id,
                days=days,
                metric=metric,
                dsn=request.app.state.dsn,
                chat_state=request.app.state.chat,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    return app
