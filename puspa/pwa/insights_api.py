"""HTTP layer for the Insights screen (G28).

Plain handler functions over :mod:`puspa.billing.insights`, plus
:func:`register_routes`, which mounts them on a FastAPI app. This
module deliberately does not import ``puspa.pwa.app``: the
coordinator wires it into ``create_app`` later with a few lines::

    from puspa.pwa import insights_api
    insights_api.register_routes(app, need_user)

``need_user`` is the app's own login resolver (``Request`` ->
user id or None); it is passed in rather than reimplemented here,
so login behaviour stays in exactly one place.

Contract (all responses are JSON; all routes require login):

- ``GET /api/insights?days=N`` -> the insights report for the
  logged-in user over the last N days (default 7, 1..90).
  400 when ``days`` is outside that range.
- ``401 {"error": "belum masuk"}`` when not logged in.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..billing.insights import insights_for_user, validate_days
from ..i18n.server_lines import render as render_server_line

__all__ = ["get_insights", "register_routes"]


def get_insights(user_id, *, days: int = 7, dsn=None, store=None, now=None) -> dict:
    """Insights report for one user (see ``insights_for_user``).

    Raises ValueError when ``days`` is outside 1..``MAX_DAYS``.
    """
    return insights_for_user(user_id, days=days, dsn=dsn, store=store, now=now)


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the insights routes on ``app`` using its login resolver."""

    @app.get("/api/insights")
    def insights_endpoint(request: Request, days: int = 7):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        try:
            validate_days(days)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return get_insights(user_id, days=days, dsn=app.state.dsn)

    return app
