"""HTTP layer for the artifacts browser (COW-12).

Plain handler functions over :mod:`puspa.pwa.artifacts`, plus
:func:`register_routes`, which mounts them on a FastAPI app.
This module deliberately does not import ``puspa.pwa.app``:
the coordinator wires it into ``create_app`` with a few lines::

    from puspa.pwa import artifacts_api
    artifacts_api.register_routes(app, need_user)

``need_user`` is the app's own login resolver (``Request`` ->
user id or None); it is passed in rather than reimplemented
here, so login behaviour stays in exactly one place.

Contract (all responses are JSON unless noted; all routes
require login):

- ``GET /api/artifacts/{agent_id}`` -> the agent's
  artifacts, newest first: name, kind, media type, size,
  run, and time. Metadata only — no bytes. 403 when the
  agent is not theirs.
- ``GET /api/artifacts/{agent_id}/{artifact_id}`` -> the
  artifact's bytes (its own media type, downloaded as an
  attachment under its name). 403 when the agent is not
  theirs, 404 when the artifact is unknown or belongs to
  another user's agent.
- ``401 {"error": "belum masuk"}`` when not logged in.

The run-log half of the browser reuses the replay routes
(``GET /api/replay/{agent_id}/tasks`` + the timeline): a
run's log is its ledger events, so no second log endpoint
is needed here.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from ..i18n.server_lines import render as render_server_line
from .artifacts import ArtifactError, ArtifactStore

__all__ = [
    "download_artifact",
    "list_artifacts",
    "register_routes",
]


def _not_logged_in():
    return JSONResponse({"error": render_server_line("error.not_logged_in")}, status_code=401)


def _artifact_error(exc: ArtifactError):
    return JSONResponse({"error": str(exc)}, status_code=exc.status)


def list_artifacts(user_id: str, agent_id: str, *, dsn=None) -> list[dict]:
    """The agent's artifacts, newest first (metadata only).

    Raises :class:`ArtifactError` (403) when the agent is not
    the user's.
    """
    return ArtifactStore(dsn=dsn).list_artifacts(user_id, agent_id)


def download_artifact(
    user_id: str, agent_id: str, artifact_id: str, *, dsn=None
) -> tuple[bytes, str, str]:
    """One artifact's bytes, media type, and file name.

    Raises :class:`ArtifactError` (403 not theirs, 404
    unknown artifact).
    """
    store = ArtifactStore(dsn=dsn)
    store.require_agent_owner(user_id, agent_id)
    artifact = store.get_artifact(user_id, artifact_id)
    if artifact.agent_id != agent_id:
        raise ArtifactError(f"artifact {artifact_id!r} not found", status=404)
    content = artifact.content or b""
    return content, artifact.media_type, artifact.name


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the artifacts routes on ``app`` using its login resolver."""

    @app.get("/api/artifacts/{agent_id}")
    def artifacts_list_endpoint(request: Request, agent_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            return list_artifacts(user_id, agent_id, dsn=request.app.state.dsn)
        except ArtifactError as exc:
            return _artifact_error(exc)

    @app.get("/api/artifacts/{agent_id}/{artifact_id}")
    def artifacts_download_endpoint(request: Request, agent_id: str, artifact_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            content, media_type, name = download_artifact(
                user_id, agent_id, artifact_id, dsn=request.app.state.dsn
            )
        except ArtifactError as exc:
            return _artifact_error(exc)
        return Response(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    return app
