"""Switchable-personas API (multiple personas, one active).

Routes over ``puspa.persona.switcher`` — the same three persona
files per persona (SOUL.md / IDENTITY.md / USER.md, plus an
optional reply style), one folder per persona under
``<home>/personas/``, the home's own files as the ``"default"``
persona:

- ``GET /api/personas`` lists every persona with the active slug.
- ``POST /api/personas`` creates one (JSON ``{"name", "soul",
  "identity", "user", "style"}``; only ``name`` is required).
- ``GET /api/personas/{slug}`` reads one persona, file text
  included.
- ``PATCH /api/personas/{slug}`` edits the given fields only.
- ``DELETE /api/personas/{slug}`` deletes one (the default
  persona cannot be deleted; deleting the active one falls back
  to the default).
- ``POST /api/personas/{slug}/activate`` makes ``slug`` active;
  the turn loop picks it up on the next turn, no restart.

The home directory comes from ``app.state.persona_home`` (a
``create_app`` test seam); when unset, the switcher resolves the
user's real Puspa home. Every route acts on the logged-in user
only — the home is single-user local storage, so there is no
other user's persona to reach.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from puspa.persona.switcher import (
    create_persona,
    delete_persona,
    get_active_slug,
    get_persona,
    list_personas,
    persona_dir,
    set_active_persona,
    slugify_name,
    update_persona,
)

from ..i18n.server_lines import render as render_server_line

__all__ = ["register_routes"]


def _unauthorized():
    return JSONResponse({"error": render_server_line("error.not_logged_in")}, status_code=401)


def _bad_request(message: str):
    return JSONResponse({"error": message}, status_code=400)


def _not_found(message: str):
    return JSONResponse({"error": message}, status_code=404)


def _home(request: Request):
    return getattr(request.app.state, "persona_home", None)


def _list_payload(home):
    return {"personas": list_personas(home), "active": get_active_slug(home)}


def _text_field(data: dict, field: str):
    """An optional string field: None when absent, else the string."""
    value = data.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} harus string")
    return value


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the personas routes on ``app`` using its login resolver."""

    @app.get("/api/personas")
    def list_personas_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        return _list_payload(_home(request))

    @app.post("/api/personas")
    async def create_persona_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            data = await request.json()
        except Exception:
            return _bad_request("request body must be JSON")
        if not isinstance(data, dict):
            return _bad_request("request body must be a JSON object")
        try:
            name = _text_field(data, "name")
            if name is None or not name.strip():
                return _bad_request("name harus string tak kosong")
            slug = slugify_name(name)
            if persona_dir(_home(request), slug).exists():
                return JSONResponse({"error": f"persona sudah ada: {slug!r}"}, status_code=409)
            record = create_persona(
                _home(request),
                name,
                soul=_text_field(data, "soul"),
                identity=_text_field(data, "identity"),
                user=_text_field(data, "user"),
                style=_text_field(data, "style"),
            )
        except ValueError as exc:
            return _bad_request(str(exc))
        return record

    @app.get("/api/personas/{slug}")
    def get_persona_endpoint(request: Request, slug: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            return get_persona(_home(request), slug)
        except ValueError as exc:
            return _bad_request(str(exc))
        except KeyError as exc:
            return _not_found(str(exc))

    @app.patch("/api/personas/{slug}")
    async def update_persona_endpoint(request: Request, slug: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            data = await request.json()
        except Exception:
            return _bad_request("request body must be JSON")
        if not isinstance(data, dict):
            return _bad_request("request body must be a JSON object")
        try:
            record = update_persona(
                _home(request),
                slug,
                name=_text_field(data, "name"),
                soul=_text_field(data, "soul"),
                identity=_text_field(data, "identity"),
                user=_text_field(data, "user"),
                style=_text_field(data, "style"),
            )
        except ValueError as exc:
            return _bad_request(str(exc))
        except KeyError as exc:
            return _not_found(str(exc))
        return record

    @app.delete("/api/personas/{slug}")
    def delete_persona_endpoint(request: Request, slug: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            deleted = delete_persona(_home(request), slug)
        except ValueError as exc:
            return _bad_request(str(exc))
        except KeyError as exc:
            return _not_found(str(exc))
        return {"deleted": deleted, "active": get_active_slug(_home(request))}

    @app.post("/api/personas/{slug}/activate")
    def activate_persona_endpoint(request: Request, slug: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            active = set_active_persona(_home(request), slug)
        except ValueError as exc:
            return _bad_request(str(exc))
        except KeyError as exc:
            return _not_found(str(exc))
        return {"active": active, "personas": list_personas(_home(request))}

    return app
