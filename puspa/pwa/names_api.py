"""Own display-name API (V2-27, group chat names).

Two small routes over ``puspa.rooms.names``:

- ``GET /api/profile/display-name`` returns the caller's own display
  name (``null`` when none is set).
- ``POST /api/profile/display-name`` sets the caller's own
  display name (JSON ``{"display_name": "..."}``). The store
  refuses an edit by anyone but the user themself, and the
  routes only ever act on the logged-in user, so there is no
  path here that renames someone else.

The edit takes effect on the next render: the store reads the
database on every call and caches nothing, and the Rooms screen
re-fetches the room detail after a successful save.

The room member lists and room memory themselves are served by
the hosted service's rooms APIs (see ``puspa/rooms/__init__``);
this module only covers the name a user sets for themself.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from puspa.rooms.names import DisplayNameStore

from ..i18n.server_lines import render as render_server_line

__all__ = ["register_routes"]


def _unauthorized():
    return JSONResponse({"error": render_server_line("error.not_logged_in")}, status_code=401)


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the display-name routes on ``app`` using its login resolver."""

    def _store(request: Request) -> DisplayNameStore:
        return DisplayNameStore(app.state.dsn)

    @app.get("/api/profile/display-name")
    def get_display_name_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            name = _store(request).get_display_name(user_id)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"user_id": user_id, "display_name": name}

    @app.post("/api/profile/display-name")
    async def set_display_name_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"error": "request body must be JSON"}, status_code=400)
        if not isinstance(data, dict):
            return JSONResponse({"error": "request body must be a JSON object"}, status_code=400)
        try:
            name = _store(request).set_display_name(user_id, data.get("display_name", ""))
        except (ValueError, PermissionError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"user_id": user_id, "display_name": name}

    return app
