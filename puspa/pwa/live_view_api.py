"""HTTP layer for the P-09 live view + takeover (plan section 12.9).

Plain handler functions over :mod:`puspa.pwa.live_view`, plus
:func:`register_routes`, which mounts them on a FastAPI app.
This module deliberately does not import ``puspa.pwa.app``:
the coordinator wires it into ``create_app`` with a few lines::

    from puspa.pwa import live_view_api
    live_view_api.register_routes(app, need_user)

``need_user`` is the app's own login resolver (``Request`` ->
user id or None); it is passed in rather than reimplemented
here, so login behaviour stays in exactly one place. The
takeover store lives on ``app.state.takeover_store`` (one
:class:`puspa.pwa.live_view.TakeoverStore` per process,
created by ``create_app``); the routes read it from there so
the hold the PWA sets is the hold the turn loop consults.

Contract (all responses are JSON; all routes require login):

- ``GET /api/live-view/{agent_id}`` -> the live frame for one
  of the logged-in user's agents: status (held / working /
  idle), the open task, the last tool call, the replayable
  ledger events (oldest first) and the ephemeral live tail.
  403 when the agent is not theirs.
- ``POST /api/live-view/{agent_id}/takeover`` (JSON:
  ``{"reason": "..."}``) -> takes over the agent: hold row +
  live work interrupted + ``takeover_started`` in the ledger.
  403 when the agent is not theirs, 409 when already held.
- ``POST /api/live-view/{agent_id}/release`` -> releases the
  takeover: hold row deleted + ``takeover_released`` in the
  ledger. 403 when the agent is not theirs, 404 when nothing
  is held.
- ``GET /api/live-view/{agent_id}/stream`` -> one SSE frame
  (the same frame as the GET, as a ``live_frame`` event) plus
  a heartbeat, then the stream ends — the same snapshot idiom
  as the app's ``/sse`` endpoint. The PWA polls the GET for
  fresh frames; the stream is for the first paint.
- ``401 {"error": "belum masuk"}`` when not logged in.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..i18n.server_lines import render as render_server_line
from ..ledger import Ledger
from ..runtime.subagents import SubagentPool
from .live_view import (
    TakeoverError,
    TakeoverStore,
    build_live_frame,
    release_takeover,
    takeover_for_agent,
)

__all__ = [
    "get_live_frame",
    "register_routes",
    "release",
    "takeover",
]

#: How many ledger events one frame carries at most.
FRAME_EVENT_LIMIT = 100


def _not_logged_in():
    return JSONResponse({"error": render_server_line("error.not_logged_in")}, status_code=401)


def _takeover_error(exc: TakeoverError):
    return JSONResponse({"error": str(exc)}, status_code=exc.status)


def _store(request: Request) -> TakeoverStore:
    """The app's takeover store, or a fresh one when the app has none."""
    store = getattr(request.app.state, "takeover_store", None)
    if store is None:  # pragma: no cover - create_app always sets it
        store = TakeoverStore(request.app.state.dsn)
        request.app.state.takeover_store = store
    return store


def _pool(request: Request) -> SubagentPool | None:
    """The PWA subagent pool when one exists (None when never used)."""
    return request.app.state.__dict__.get("pool")


def _live_events(request: Request) -> list:
    """The app's in-memory live event tail (empty when never used)."""
    return list(getattr(request.app.state, "event", []) or [])


def get_live_frame(
    user_id: str,
    agent_id: str,
    *,
    dsn=None,
    ledger: Ledger | None = None,
    store: TakeoverStore | None = None,
    live_events: list | None = None,
    task_id: str | None = None,
) -> dict:
    """The live frame for one of ``user_id``'s agents.

    Raises :class:`TakeoverError` (403) when the agent is not
    the user's.
    """
    frame = build_live_frame(
        user_id,
        agent_id,
        dsn=dsn,
        ledger=ledger if ledger is not None else Ledger(dsn),
        store=store,
        live_events=live_events,
        limit=FRAME_EVENT_LIMIT,
        task_id=task_id,
    )
    return frame.to_dict()


def takeover(
    user_id: str,
    agent_id: str,
    *,
    dsn=None,
    ledger: Ledger | None = None,
    store: TakeoverStore | None = None,
    pool: SubagentPool | None = None,
    reason: str = "",
) -> dict:
    """Take over one of ``user_id``'s agents; returns the report.

    Raises :class:`TakeoverError` (403 not theirs, 409 already
    held). ``pool`` is the PWA :class:`SubagentPool` when one
    exists; without it only the lifecycle children (none here)
    are interrupted and the hold still enforces new work.
    """
    return takeover_for_agent(
        user_id,
        agent_id,
        dsn=dsn,
        ledger=ledger if ledger is not None else Ledger(dsn),
        store=store,
        pool=pool,
        actor=user_id,
        reason=reason,
    )


def release(
    user_id: str,
    agent_id: str,
    *,
    dsn=None,
    ledger: Ledger | None = None,
    store: TakeoverStore | None = None,
) -> dict:
    """Release a takeover of one of ``user_id``'s agents.

    Raises :class:`TakeoverError` (403 not theirs, 404 nothing
    held).
    """
    return release_takeover(
        user_id,
        agent_id,
        dsn=dsn,
        ledger=ledger if ledger is not None else Ledger(dsn),
        store=store,
        actor=user_id,
    )


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the P-09 live-view routes on ``app`` using its login resolver."""

    @app.get("/api/live-view/{agent_id}")
    def live_frame_endpoint(request: Request, agent_id: str, task_id: str | None = None):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            return get_live_frame(
                user_id,
                agent_id,
                dsn=request.app.state.dsn,
                store=_store(request),
                live_events=_live_events(request),
                task_id=task_id,
            )
        except TakeoverError as exc:
            return _takeover_error(exc)

    @app.post("/api/live-view/{agent_id}/takeover")
    async def takeover_endpoint(request: Request, agent_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("takeover body must be a JSON object")
            reason = data.get("reason") or ""
            if not isinstance(reason, str):
                raise ValueError("reason must be a string")
            return takeover(
                user_id,
                agent_id,
                dsn=request.app.state.dsn,
                store=_store(request),
                pool=_pool(request),
                reason=reason,
            )
        except TakeoverError as exc:
            return _takeover_error(exc)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/live-view/{agent_id}/release")
    def release_endpoint(request: Request, agent_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            return release(
                user_id,
                agent_id,
                dsn=request.app.state.dsn,
                store=_store(request),
            )
        except TakeoverError as exc:
            return _takeover_error(exc)

    @app.get("/api/live-view/{agent_id}/stream")
    def live_stream_endpoint(request: Request, agent_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()

        def stream():
            try:
                frame = get_live_frame(
                    user_id,
                    agent_id,
                    dsn=request.app.state.dsn,
                    store=_store(request),
                    live_events=_live_events(request),
                )
            except TakeoverError as exc:
                yield (
                    "data: "
                    + json.dumps(
                        {"type": "error", "error": str(exc), "status": exc.status},
                        ensure_ascii=False,
                    )
                    + "\n\n"
                )
                return
            yield (
                "data: "
                + json.dumps(
                    {
                        "type": "live_frame",
                        "frame": frame,
                        "created_at": datetime.now(UTC).isoformat(),
                    },
                    ensure_ascii=False,
                )
                + "\n\n"
            )
            yield f"data: {json.dumps({'type': 'heartbeat', 'created_at': time.time()}, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    return app
