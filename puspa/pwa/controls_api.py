"""HTTP layer for the P-02 controls (plan section 12.7).

Plain handler functions over :mod:`puspa.autonomy.controls`,
:mod:`puspa.pwa.stop_all`, and :mod:`puspa.pwa.weekly_report`,
plus :func:`register_routes`, which mounts them on a FastAPI
app. This module deliberately does not import
``puspa.pwa.app``: the coordinator wires it into ``create_app``
with a few lines::

    from puspa.pwa import controls_api
    controls_api.register_routes(app, need_user)

``need_user`` is the app's own login resolver (``Request`` ->
user id or None); it is passed in rather than reimplemented
here, so login behaviour stays in exactly one place. The
autonomy controls live on ``app.state.autonomy_controls``
(one :class:`puspa.autonomy.controls.AutonomyControls` per
process, created by ``create_app``); the routes read it from
there so the ceilings the user sets are the ceilings the app
enforces.

Contract (all responses are JSON; all routes require login):

- ``POST /api/stop-all`` -> stops everything for the logged-in
  user (every agent they own, every live subagent task) and
  returns the stop report, with the audit-trail link.
- ``GET /api/autonomy-dial`` -> the five dial areas with their
  scores, ceilings, streaks, and raise suggestions.
- ``POST /api/autonomy-dial/{area}/ceiling`` (JSON:
  ``{"ceiling": "ask_first" | "balanced" | "autonomous"}``) ->
  moves one area's ceiling. 400 on an unknown area or
  ceiling, 403 when the area is locked (Money), 409 when a
  raise needs evidence first (fewer than 10 accepted drafts
  in a row).
- ``GET /api/weekly-report?days=N`` -> the weekly report for
  the logged-in user over the last N days (default 7, 1..90).
  400 when ``days`` is outside that range.
- ``401 {"error": "belum masuk"}`` when not logged in.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..autonomy.controls import (
    AutonomyControls,
    CeilingRefusedError,
    signals_from_drafts,
    signals_from_events,
)
from ..autonomy.dial import AutonomyDial
from ..billing.insights import validate_days
from ..drafting import PostgresDraftStore
from ..evidence.proof import event_belongs_to_user, user_agent_ids
from ..i18n.server_lines import render as render_server_line
from ..ledger import Ledger
from ..policy.stop import StopError
from .stop_all import stop_all_for_user
from .weekly_report import weekly_report_for_user

__all__ = [
    "get_dial",
    "get_weekly_report",
    "register_routes",
    "set_dial_ceiling",
    "stop_all",
]

#: How far back streak evidence is read (days). Older approvals
#: do not count toward "10 accepted drafts in a row".
EVIDENCE_WINDOW_DAYS = 90


def _not_logged_in():
    return JSONResponse({"error": render_server_line("error.not_logged_in")}, status_code=401)


def _controls(request: Request) -> AutonomyControls:
    """The app's controls, or a fresh one when the app has none."""
    controls = getattr(request.app.state, "autonomy_controls", None)
    if controls is None:  # pragma: no cover - create_app always sets it
        controls = AutonomyControls(AutonomyDial())
        request.app.state.autonomy_controls = controls
    return controls


def load_signals(user_id: str, *, dsn=None, now: datetime | None = None) -> list:
    """Streak signals for one user from the ledger and the drafts.

    Reads the last :data:`EVIDENCE_WINDOW_DAYS` days of the
    user's ledger events plus their drafts, oldest first. A
    store that cannot be read contributes no signals (the
    streak stays zero) instead of failing the whole read:
    missing evidence must never be reported as evidence.
    """
    now = now or datetime.now(UTC)
    since = now - timedelta(days=EVIDENCE_WINDOW_DAYS)
    signals: list = []
    try:
        agent_ids = user_agent_ids(user_id, dsn=dsn)
        events = Ledger(dsn).recent_events(since)
        own = [
            event for event in events if event_belongs_to_user(event, user_id, agent_ids, dsn=dsn)
        ]
        signals.extend(signals_from_events(own))
    except psycopg.Error:
        pass
    try:
        drafts = PostgresDraftStore(dsn).list_drafts(user_id)
        signals.extend(signals_from_drafts(drafts))
    except psycopg.Error:
        pass
    return signals


def get_dial(user_id: str, *, dsn=None, controls: AutonomyControls | None = None) -> dict:
    """The dial state for one user (scores, ceilings, streaks, suggestions)."""
    if controls is None:
        controls = AutonomyControls(AutonomyDial())
    return controls.state(load_signals(user_id, dsn=dsn))


def set_dial_ceiling(
    user_id: str,
    area: str,
    ceiling: str,
    *,
    dsn=None,
    controls: AutonomyControls | None = None,
) -> dict:
    """Move one area's ceiling (see :meth:`AutonomyControls.set_ceiling`).

    Raises ``ValueError`` for an unknown area or ceiling and
    ``CeilingRefusedError`` when the move is locked or needs
    evidence first.
    """
    if controls is None:
        controls = AutonomyControls(AutonomyDial())
    return controls.set_ceiling(area, ceiling, load_signals(user_id, dsn=dsn))


def stop_all(user_id: str, *, dsn=None, pool=None) -> dict:
    """Stop everything for one user; returns the stop report."""
    ledger = Ledger(dsn)
    report = stop_all_for_user(
        user_id,
        dsn=dsn,
        pool=pool,
        ledger=ledger,
        actor=user_id,
    )
    return report.to_dict()


def get_weekly_report(user_id: str, *, days: int = 7, dsn=None) -> dict:
    """The weekly report for one user (see ``weekly_report_for_user``).

    Raises ValueError when ``days`` is outside 1..``MAX_DAYS``.
    """
    return weekly_report_for_user(user_id, days=validate_days(days), dsn=dsn)


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the P-02 control routes on ``app`` using its login resolver."""

    @app.post("/api/stop-all")
    def stop_all_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            pool = request.app.state.__dict__.get("pool")
            return stop_all(user_id, dsn=request.app.state.dsn, pool=pool)
        except StopError as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    @app.get("/api/autonomy-dial")
    def dial_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        return get_dial(user_id, dsn=request.app.state.dsn, controls=_controls(request))

    @app.post("/api/autonomy-dial/{area}/ceiling")
    async def dial_ceiling_endpoint(request: Request, area: str):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("ceiling body must be a JSON object")
            return set_dial_ceiling(
                user_id,
                area,
                str(data.get("ceiling") or ""),
                dsn=request.app.state.dsn,
                controls=_controls(request),
            )
        except CeilingRefusedError as exc:
            status = 403 if exc.reason == "locked" else 409
            return JSONResponse({"error": str(exc)}, status_code=status)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.get("/api/weekly-report")
    def weekly_report_endpoint(request: Request, days: int = 7):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            return get_weekly_report(user_id, days=days, dsn=request.app.state.dsn)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    return app
