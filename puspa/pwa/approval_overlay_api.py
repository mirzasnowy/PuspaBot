"""HTTP layer for the P-11 visual approval overlay (plan section 12.10).

Plain handler functions over :mod:`puspa.pwa.approval_overlay`,
plus :func:`register_routes`, which mounts them on a FastAPI app.
This module deliberately does not import ``puspa.pwa.app``:
the coordinator wires it into ``create_app`` with a few lines::

    from puspa.pwa import approval_overlay_api
    approval_overlay_api.register_routes(app, need_user)

``need_user`` is the app's own login resolver (``Request`` ->
user id or None); it is passed in rather than reimplemented
here, so login behaviour stays in exactly one place. The
overlay store lives on ``app.state.overlay_store`` (one
:class:`puspa.pwa.approval_overlay.OverlayStore` per process,
created by ``create_app``); the routes read it from there.

Contract (all responses are JSON; all routes require login):

- ``GET /api/live-view/{agent_id}/approvals`` -> pending +
  decided overlays for one of the logged-in user's agents, each
  with its target element and its highlight resolved against
  the agent's current live frame. 403 when the agent is not
  theirs.
- ``POST /api/live-view/{agent_id}/approvals`` (JSON:
  ``{"tool_name": ..., "content": {...}, "button_label": ...,
  "task_id": ..., "request_id": ...}``) -> propose one action
  for approval: builds the overlay and records
  ``approval_requested`` in the ledger. 400 for a bad body (or
  a credential change, which can never be approved), 403 when
  the agent is not theirs, 409 for a duplicate request id.
- ``POST /api/live-view/{agent_id}/approvals/{request_id}/approve``
  -> approve that one action (``{"reason": ...}`` optional):
  records ``approval_granted`` in the ledger. 404 unknown,
  409 already decided, 403 not theirs.
- ``POST /api/live-view/{agent_id}/approvals/{request_id}/reject``
  -> reject that one action (``{"reason": ...}`` optional):
  cancels it — when the store is constructed with the turn
  loop's gate the gate request is marked rejected, so it can
  never execute — and records ``approval_rejected`` in the
  ledger. 404 unknown, 409 already decided, 403 not theirs.
- ``401 {"error": "belum masuk"}`` when not logged in.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..i18n.server_lines import render as render_server_line
from ..ledger import Ledger
from .approval_overlay import OverlayError, OverlayStore, list_overlays
from .live_view import TakeoverError, TakeoverStore, build_live_frame

__all__ = [
    "approve_overlay",
    "decide_overlay",
    "get_overlays",
    "propose_overlay",
    "register_routes",
    "reject_overlay",
]


def _not_logged_in():
    return JSONResponse({"error": render_server_line("error.not_logged_in")}, status_code=401)


def _overlay_error(exc: OverlayError):
    return JSONResponse({"error": str(exc)}, status_code=exc.status)


def _takeover_error(exc: TakeoverError):
    return JSONResponse({"error": str(exc)}, status_code=exc.status)


def _store(request: Request) -> OverlayStore:
    """The app's overlay store, or a fresh one when the app has none."""
    store = getattr(request.app.state, "overlay_store", None)
    if store is None:  # pragma: no cover - create_app always sets it
        store = OverlayStore(request.app.state.dsn)
        request.app.state.overlay_store = store
    return store


def _takeover_store(request: Request) -> TakeoverStore:
    """The app's takeover store, for the frame the highlight reads."""
    store = getattr(request.app.state, "takeover_store", None)
    if store is None:  # pragma: no cover - create_app always sets it
        store = TakeoverStore(request.app.state.dsn)
        request.app.state.takeover_store = store
    return store


def _live_events(request: Request) -> list:
    """The app's in-memory live event tail (empty when never used)."""
    return list(getattr(request.app.state, "event", []) or [])


def get_overlays(
    user_id: str,
    agent_id: str,
    *,
    dsn=None,
    store: OverlayStore | None = None,
    ledger: Ledger | None = None,
    takeover_store: TakeoverStore | None = None,
    live_events: list | None = None,
) -> dict:
    """Pending + decided overlays for one of ``user_id``'s agents.

    Each overlay carries its highlight resolved against the
    agent's current live frame. Raises :class:`OverlayError`
    (403) when the agent is not the user's, :class:`TakeoverError`
    (403) when the frame itself refuses the agent.
    """
    store = store if store is not None else OverlayStore(dsn)
    frame = build_live_frame(
        user_id,
        agent_id,
        dsn=dsn,
        store=takeover_store,
        live_events=live_events,
        limit=100,
    )
    return list_overlays(user_id, agent_id, dsn=dsn, store=store, frame=frame)


def propose_overlay(
    user_id: str,
    agent_id: str,
    *,
    body: dict,
    dsn=None,
    store: OverlayStore | None = None,
    ledger: Ledger | None = None,
) -> dict:
    """Propose one action for approval; returns the overlay.

    Raises ``ValueError`` (400) for a bad body, :class:`OverlayError`
    (403 not theirs, 409 duplicate request id).
    """
    if not isinstance(body, dict):
        raise ValueError("overlay body must be a JSON object")
    tool_name = body.get("tool_name")
    content = body.get("content")
    button_label = body.get("button_label")
    page_text = body.get("page_text")
    task_id = body.get("task_id")
    request_id = body.get("request_id")
    if not isinstance(tool_name, str) or not tool_name.strip():
        raise ValueError("tool_name must be a non-empty string")
    if not isinstance(content, dict):
        raise ValueError("content must be a dict of structured arguments")
    if button_label is not None and not isinstance(button_label, str):
        raise ValueError("button_label must be a string")
    if page_text is not None and not isinstance(page_text, str):
        raise ValueError("page_text must be a string")
    if task_id is not None and not isinstance(task_id, str):
        raise ValueError("task_id must be a string")
    if request_id is not None and not isinstance(request_id, str):
        raise ValueError("request_id must be a string")
    store = store if store is not None else OverlayStore(dsn)
    overlay = store.propose(
        user_id,
        agent_id,
        tool_name=tool_name,
        content=content,
        button_label=button_label,
        page_text=page_text,
        task_id=task_id,
        request_id=request_id,
        dsn=dsn,
        ledger=ledger if ledger is not None else Ledger(dsn),
    )
    return overlay.to_dict(highlight=None)


def decide_overlay(
    user_id: str,
    agent_id: str,
    request_id: str,
    *,
    decision: str,
    reason: str = "",
    dsn=None,
    store: OverlayStore | None = None,
    ledger: Ledger | None = None,
) -> dict:
    """Decide one pending overlay; returns the decided overlay.

    Raises ``ValueError`` (400) for a bad decision,
    :class:`OverlayError` (403 not theirs, 404 unknown, 409
    already decided).
    """
    if decision not in ("approved", "rejected"):
        raise ValueError("decision must be 'approved' or 'rejected'")
    if not isinstance(reason, str):
        raise ValueError("reason must be a string")
    store = store if store is not None else OverlayStore(dsn)
    # The URL names the agent: a request that exists but belongs
    # to another agent of the same user is not found here — and
    # the check runs before deciding, so a wrong-agent URL can
    # never decide someone else's overlay.
    existing = store.get(request_id)
    if existing is not None and existing.agent_id != agent_id:
        raise OverlayError(f"unknown approval request {request_id!r}", status=404)
    overlay = store.decide(
        user_id,
        request_id,
        decision=decision,
        reason=reason,
        dsn=dsn,
        ledger=ledger if ledger is not None else Ledger(dsn),
    )
    return overlay.to_dict(highlight=None)


def approve_overlay(
    user_id: str,
    agent_id: str,
    request_id: str,
    *,
    reason: str = "",
    dsn=None,
    store: OverlayStore | None = None,
    ledger: Ledger | None = None,
) -> dict:
    """Approve one pending overlay; returns the decided overlay."""
    return decide_overlay(
        user_id,
        agent_id,
        request_id,
        decision="approved",
        reason=reason,
        dsn=dsn,
        store=store,
        ledger=ledger,
    )


def reject_overlay(
    user_id: str,
    agent_id: str,
    request_id: str,
    *,
    reason: str = "",
    dsn=None,
    store: OverlayStore | None = None,
    ledger: Ledger | None = None,
) -> dict:
    """Reject one pending overlay; returns the decided overlay."""
    return decide_overlay(
        user_id,
        agent_id,
        request_id,
        decision="rejected",
        reason=reason,
        dsn=dsn,
        store=store,
        ledger=ledger,
    )


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the P-11 overlay routes on ``app`` using its login resolver."""

    @app.get("/api/live-view/{agent_id}/approvals")
    def overlays_endpoint(request: Request, agent_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            return get_overlays(
                user_id,
                agent_id,
                dsn=request.app.state.dsn,
                store=_store(request),
                takeover_store=_takeover_store(request),
                live_events=_live_events(request),
            )
        except OverlayError as exc:
            return _overlay_error(exc)
        except TakeoverError as exc:
            return _takeover_error(exc)

    @app.post("/api/live-view/{agent_id}/approvals")
    async def propose_endpoint(request: Request, agent_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            body = await request.json()
            return propose_overlay(
                user_id,
                agent_id,
                body=body,
                dsn=request.app.state.dsn,
                store=_store(request),
            )
        except OverlayError as exc:
            return _overlay_error(exc)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/live-view/{agent_id}/approvals/{request_id}/approve")
    async def approve_endpoint(request: Request, agent_id: str, request_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("decision body must be a JSON object")
            reason = body.get("reason") or ""
            if not isinstance(reason, str):
                raise ValueError("reason must be a string")
            return approve_overlay(
                user_id,
                agent_id,
                request_id,
                reason=reason,
                dsn=request.app.state.dsn,
                store=_store(request),
            )
        except OverlayError as exc:
            return _overlay_error(exc)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/live-view/{agent_id}/approvals/{request_id}/reject")
    async def reject_endpoint(request: Request, agent_id: str, request_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("decision body must be a JSON object")
            reason = body.get("reason") or ""
            if not isinstance(reason, str):
                raise ValueError("reason must be a string")
            return reject_overlay(
                user_id,
                agent_id,
                request_id,
                reason=reason,
                dsn=request.app.state.dsn,
                store=_store(request),
            )
        except OverlayError as exc:
            return _overlay_error(exc)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    return app
