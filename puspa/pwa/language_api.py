"""HTTP layer for the language switch in Settings (COW-18).

Contract:

- ``GET /api/language`` (login required) -> ``{locale,
  available_locales}``. ``locale`` is the user's stored choice,
  Indonesian (``"id"``) when nothing is stored.
- ``POST /api/language`` (login required, JSON ``locale``) -> the
  saved state in the same shape. An unknown locale is refused
  with 400: a typo must never store a language the catalogs do
  not have.
- ``GET /api/i18n/catalog`` (no login needed: UI strings carry no
  user data) -> ``{locale, strings}``. ``strings`` is the merged
  catalog for the locale: the requested locale's strings win and
  any key it is missing is filled from Indonesian, so the client
  never receives a blank. ``?locale=en`` previews one locale;
  without it, a logged-in user gets their stored locale and an
  anonymous user gets Indonesian.

Follows the same shape as :mod:`puspa.pwa.telemetry_api`:
``register_routes(app, need_user)`` mounts the routes on the app
using its login resolver.
"""

from __future__ import annotations

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..i18n import DEFAULT_LOCALE, available_locales, catalog
from ..i18n.server_lines import render as render_server_line
from .language import SUPPORTED_LOCALES, get_language, save_language

__all__ = ["register_routes"]


def _not_logged_in() -> JSONResponse:
    return JSONResponse({"error": render_server_line("error.not_logged_in")}, status_code=401)


def _dsn(request: Request):
    return request.app.state.dsn


def _public_state(user_id, dsn) -> dict:
    """The language state the Settings screen shows for one user."""
    return {
        "locale": get_language(user_id, dsn=dsn),
        "available_locales": list(SUPPORTED_LOCALES),
    }


def _resolve_catalog_locale(request: Request, need_user) -> str:
    """Which locale the catalog serves: ``?locale=`` wins, else the
    logged-in user's stored choice, else Indonesian. An unknown
    ``?locale=`` value falls back to Indonesian (never an error:
    the catalog endpoint must never fail a page load).
    """
    requested = request.query_params.get("locale")
    if isinstance(requested, str) and requested.strip().lower() in available_locales():
        return requested.strip().lower()
    user_id = need_user(request)
    if user_id is None:
        return DEFAULT_LOCALE
    try:
        return get_language(user_id, dsn=_dsn(request))
    except (ValueError, psycopg.Error):
        return DEFAULT_LOCALE


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the language routes on ``app`` using its login resolver."""

    @app.get("/api/language")
    async def language_get(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            return _public_state(user_id, _dsn(request))
        except (ValueError, psycopg.Error) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/language")
    async def language_save(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("language settings body must be a JSON object")
            if "locale" not in data:
                raise ValueError("language settings need a 'locale' string")
            saved = save_language(user_id, data["locale"], dsn=_dsn(request))
            return saved.to_public_dict()
        except (ValueError, psycopg.Error) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.get("/api/i18n/catalog")
    async def i18n_catalog(request: Request):
        locale = _resolve_catalog_locale(request, need_user)
        return {"locale": locale, "strings": catalog(locale)}

    return app
