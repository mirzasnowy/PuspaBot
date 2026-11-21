"""HTTP layer for the redacted diagnostics export.

Plain handler functions over :mod:`puspa.diagnostics.export`,
plus :func:`register_routes`, which mounts them on a FastAPI
app. This module deliberately does not import
``puspa.pwa.app``: the coordinator wires it into
``create_app`` with a few lines::

    from puspa.pwa import diagnostics_api
    diagnostics_api.register_routes(app, need_user)

``need_user`` is the app's own login resolver (``Request`` ->
user id or None); it is passed in rather than reimplemented
here, so login behaviour stays in exactly one place.

Contract (all routes require login):

- ``GET /api/diagnostics/export`` -> the user's redacted
  diagnostics bundle as a JSON download (``Content-Disposition:
  attachment``): recent log lines, the message trace (spans
  for the user's recent tasks), and the recent ledger events
  for the user's agents. Every string is redacted before it
  is sent; secrets, email addresses, and vault values never
  leave the server. Optional ``?task_id=...`` scopes the
  bundle to one task.
- ``401 {"error": "belum masuk"}`` when not logged in.

The app event log (``app.state.event``) is the log source
here: those entries are already in memory, so the download
needs no log file on disk. The CLI (``puspa diagnostics
export``) takes a ``--log-file`` instead.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from puspa.diagnostics.export import collect_diagnostics
from puspa.i18n.server_lines import render as render_server_line

__all__ = ["export_diagnostics_bundle", "register_routes"]


def _not_logged_in():
    return JSONResponse({"error": render_server_line("error.not_logged_in")}, status_code=401)


def export_diagnostics_bundle(
    user_id: str, *, dsn: str | None = None, task_id: str | None = None, log_lines=None
) -> dict:
    """The redacted bundle for ``user_id`` (no file written)."""
    return collect_diagnostics(user_id, dsn=dsn, task_id=task_id, log_lines=list(log_lines or []))


def register_routes(app: FastAPI, need_user, audit_dir: str | Path | None = None) -> None:
    """Mount the diagnostics export route on ``app``."""

    @app.get("/api/diagnostics/export")
    def export_bundle(request: Request, task_id: str | None = None):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        log_lines = [
            json.dumps(event, ensure_ascii=False, default=str)
            for event in getattr(request.app.state, "event", [])
        ]
        try:
            document = collect_diagnostics(
                user_id,
                dsn=request.app.state.dsn,
                task_id=task_id or None,
                log_lines=log_lines,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if audit_dir is not None:
            from puspa.ledger.audit import AuditExportLog

            AuditExportLog(audit_dir).record(
                exporter=user_id,
                rows=sum(document["counts"].values()),
                truncated=any(document["truncated"].values()),
                filters={"kind": "diagnostics", "task_id": task_id},
            )
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        body = json.dumps(document, indent=2, ensure_ascii=False)
        return Response(
            content=body,
            media_type="application/json",
            headers={
                "Content-Disposition": (f'attachment; filename="puspa-diagnostics-{stamp}.json"')
            },
        )
