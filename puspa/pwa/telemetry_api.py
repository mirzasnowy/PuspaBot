"""HTTP layer for the telemetry toggle in Settings (AST-09).

Contract (all routes require login):

- ``GET /api/telemetry/settings`` -> ``{enabled, env_override,
  effective_enabled}``. ``enabled`` is the user's stored
  Settings toggle (off when never set); ``env_override`` is the
  ``PUSPA_TELEMETRY`` environment flag (``true`` / ``false`` /
  ``null`` when unset); ``effective_enabled`` is the switch
  that actually decides whether anything is sent: the env flag
  wins when set, else the toggle, else off.
- ``POST /api/telemetry/settings`` (JSON ``enabled`` boolean)
  -> the saved state in the same shape. A non-boolean
  ``enabled`` is refused with 400: a privacy switch must never
  be set by a mistyped string. Saving the toggle never sends
  anything by itself; it only records the choice.

No usage data ever passes through these routes: they carry the
toggle only. The sender itself lives in
:mod:`puspa.telemetry.client` and posts aggregates to the
collector endpoint only when the effective switch is on.
"""

from __future__ import annotations

from puspa.db import Error as _db_Error
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..i18n.server_lines import render as render_server_line
from ..telemetry.config import env_flag, resolve_enabled
from ..telemetry.settings import get_settings, save_settings

__all__ = ["register_routes"]


def _not_logged_in() -> JSONResponse:
    return JSONResponse({"error": render_server_line("error.not_logged_in")}, status_code=401)


def _dsn(request: Request):
    return request.app.state.dsn


def _public_state(user_id, dsn) -> dict:
    """The toggle state the Settings screen shows for one user."""
    stored = get_settings(user_id, dsn=dsn).enabled
    override = env_flag()
    return {
        "enabled": stored,
        "env_override": override,
        "effective_enabled": resolve_enabled(env=override, stored=stored),
    }


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the telemetry-settings routes on ``app`` using its login resolver."""

    @app.get("/api/telemetry/settings")
    async def telemetry_get_settings(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            return _public_state(user_id, _dsn(request))
        except (ValueError, *_db_Error) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/telemetry/settings")
    async def telemetry_save_settings(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("telemetry settings body must be a JSON object")
            if "enabled" not in data:
                raise ValueError("telemetry settings need an 'enabled' boolean")
            saved = save_settings(user_id, data["enabled"], dsn=_dsn(request))
            return {
                "enabled": saved.enabled,
                "env_override": env_flag(),
                "effective_enabled": resolve_enabled(env=env_flag(), stored=saved.enabled),
            }
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except _db_Error as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    return app
