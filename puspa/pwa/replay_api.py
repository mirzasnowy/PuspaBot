"""HTTP layer for the P-10 replay timeline (plan section 12.10).

Plain handler functions over :mod:`puspa.pwa.replay`, plus
:func:`register_routes`, which mounts them on a FastAPI app.
This module deliberately does not import ``puspa.pwa.app``:
the coordinator wires it into ``create_app`` with a few lines::

    from puspa.pwa import replay_api
    replay_api.register_routes(app, need_user)

``need_user`` is the app's own login resolver (``Request`` ->
user id or None); it is passed in rather than reimplemented
here, so login behaviour stays in exactly one place.

Contract (all responses are JSON unless noted; all routes
require login):

- ``GET /api/replay/{agent_id}/tasks`` -> the agent's tasks
  that have ledger events, newest first: step count, first and
  last event time, status. 403 when the agent is not theirs.
- ``GET /api/replay/{agent_id}/{task_id}`` -> the replay
  timeline: every step (oldest first), each with its reason,
  result, and hash-chain link; the chain verification
  (``chain_ok`` + ``broken_event_ids``); the task status.
  403 when the agent is not theirs, 404 when the task has no
  recorded events, 400 when it has more steps than the replay
  limit.
- ``GET /api/replay/{agent_id}/{task_id}/steps/{index}`` ->
  one step (0-based), for scrubbing to an exact position.
  404 when the index is out of range.
- ``GET /api/replay/{agent_id}/{task_id}/export.gif`` -> the
  whole timeline as an animated GIF (``image/gif``), one
  rendered frame per step. The frames are drawn from the
  steps' own data, not desktop screenshots — see
  :mod:`puspa.pwa.replay`.
- ``401 {"error": "belum masuk"}`` when not logged in.

Route order matters: ``/tasks`` is registered before the bare
``/{task_id}`` route, so the literal segment wins over the
task-id parameter.
"""

from __future__ import annotations

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from ..i18n.server_lines import render as render_server_line
from ..ledger import Ledger
from .artifacts import ArtifactError, ArtifactStore
from .replay import (
    ReplayError,
    build_replay,
    export_replay_gif,
    list_tasks_for_agent,
)

__all__ = [
    "export_gif",
    "get_replay_step",
    "get_replay_tasks",
    "get_replay_timeline",
    "register_routes",
]


def _not_logged_in():
    return JSONResponse({"error": render_server_line("error.not_logged_in")}, status_code=401)


def _replay_error(exc: ReplayError):
    return JSONResponse({"error": str(exc)}, status_code=exc.status)


def get_replay_tasks(user_id: str, agent_id: str, *, dsn=None) -> list[dict]:
    """The agent's replayable tasks, newest first.

    Raises :class:`ReplayError` (403) when the agent is not the
    user's.
    """
    return list_tasks_for_agent(user_id, agent_id, dsn=dsn)


def get_replay_timeline(
    user_id: str,
    agent_id: str,
    task_id: str,
    *,
    dsn=None,
    ledger: Ledger | None = None,
) -> dict:
    """The replay timeline for one task of one of ``user_id``'s agents.

    Raises :class:`ReplayError` (403 not theirs, 404 unknown
    task, 400 too many steps).
    """
    timeline = build_replay(
        user_id,
        agent_id,
        task_id,
        dsn=dsn,
        ledger=ledger if ledger is not None else Ledger(dsn),
    )
    return timeline.to_dict()


def get_replay_step(
    user_id: str,
    agent_id: str,
    task_id: str,
    index: int,
    *,
    dsn=None,
    ledger: Ledger | None = None,
) -> dict:
    """One step of a replay, by 0-based index (scrubbing).

    Raises :class:`ReplayError` (403 not theirs, 404 unknown
    task or step out of range).
    """
    timeline = build_replay(
        user_id,
        agent_id,
        task_id,
        dsn=dsn,
        ledger=ledger if ledger is not None else Ledger(dsn),
    )
    return timeline.step(index).to_dict()


def export_gif(
    user_id: str,
    agent_id: str,
    task_id: str,
    *,
    dsn=None,
    ledger: Ledger | None = None,
) -> bytes:
    """The replay as an animated GIF (rendered frames).

    The generated GIF is also recorded as an artifact of the
    run (see :func:`_record_export_artifact`): the export is
    the first real producer of artifacts, so the browser has
    something genuine to show without a second storage path.

    Raises :class:`ReplayError` (403 not theirs, 404 unknown
    task, 400 too many steps).
    """
    timeline = build_replay(
        user_id,
        agent_id,
        task_id,
        dsn=dsn,
        ledger=ledger if ledger is not None else Ledger(dsn),
    )
    gif = export_replay_gif(timeline)
    _record_export_artifact(user_id, agent_id, task_id, gif, dsn=dsn)
    return gif


def _record_export_artifact(
    user_id: str, agent_id: str, task_id: str, gif: bytes, *, dsn=None
) -> None:
    """Record an exported replay GIF as an artifact of its run.

    Best-effort on purpose: the download is the user's file
    and must not fail because the optional artifact record
    failed (for example, a database that has not applied
    migration 0035 yet). Only the two error kinds a record
    can raise are caught — a validation error and a database
    error — never a bare except.
    """
    try:
        ArtifactStore(dsn=dsn).record_artifact(
            user_id=user_id,
            agent_id=agent_id,
            run_id=task_id,
            name=f"replay-{task_id}.gif",
            kind="gif",
            media_type="image/gif",
            content=gif,
        )
    except (ArtifactError, psycopg.Error):
        return


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the P-10 replay routes on ``app`` using its login resolver."""

    @app.get("/api/replay/{agent_id}/tasks")
    def replay_tasks_endpoint(request: Request, agent_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            return get_replay_tasks(user_id, agent_id, dsn=request.app.state.dsn)
        except ReplayError as exc:
            return _replay_error(exc)

    @app.get("/api/replay/{agent_id}/{task_id}/export.gif")
    def replay_gif_endpoint(request: Request, agent_id: str, task_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            gif = export_gif(user_id, agent_id, task_id, dsn=request.app.state.dsn)
        except ReplayError as exc:
            return _replay_error(exc)
        return Response(
            content=gif,
            media_type="image/gif",
            headers={
                "Content-Disposition": f'attachment; filename="replay-{task_id}.gif"',
            },
        )

    @app.get("/api/replay/{agent_id}/{task_id}/steps/{index}")
    def replay_step_endpoint(request: Request, agent_id: str, task_id: str, index: int):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            return get_replay_step(user_id, agent_id, task_id, index, dsn=request.app.state.dsn)
        except ReplayError as exc:
            return _replay_error(exc)

    @app.get("/api/replay/{agent_id}/{task_id}")
    def replay_timeline_endpoint(request: Request, agent_id: str, task_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            return get_replay_timeline(user_id, agent_id, task_id, dsn=request.app.state.dsn)
        except ReplayError as exc:
            return _replay_error(exc)

    return app
