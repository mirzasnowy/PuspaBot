"""Community gallery API (``puspa/pwa/gallery_api.py``).

Routes (all require login, mounted by ``puspa.pwa.app``):

- ``POST /api/gallery/publish`` — ``{"mockup_id": ...}`` publishes one
  of the caller's B7 mockups to the shared gallery (snapshot + version
  + owner + provenance). A work flagged ``restricted_customary_use``
  is refused: restricted customary use can never be published as a
  generic pattern.
- ``GET  /api/gallery`` — published works, newest first. Every work
  carries its aggregate as a pair, ``average_rating`` together with
  ``rating_count``, never a score on its own.
- ``GET  /api/gallery/{work_id}`` — one work with its aggregate and
  its written reviews.
- ``GET  /api/gallery/{work_id}/render.svg`` — the published snapshot
  rendered, so the gallery shows the work itself, not just its title.
- ``POST /api/gallery/{work_id}/rate`` — ``{"rating": 1-5,
  "review": "..."}``. Exactly one rating per user per work: a second
  rating updates the first. Rating spam is rate-limited (429).
- ``POST /api/gallery/{work_id}/unpublish`` — the owner takes the
  work down; it disappears from the listing immediately.

No cash payouts, no paid promotion, no anonymous ratings: every
rating carries the rater's user id, and there is no money path here
at all (creator support is B17, credit-only, and lives elsewhere).
"""

from __future__ import annotations

import contextlib

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from ..gallery.store import GalleryError, GalleryStore, RateLimitedError, create_schema
from ..i18n.server_lines import render as render_server_line
from ..mockup.render import render_svg
from ..mockup.store import MockupStore
from ..mockup.store import create_schema as create_mockup_schema

__all__ = ["register_routes"]


def _store(request: Request) -> GalleryStore:
    if not getattr(request.app.state, "gallery_schema_ready", False):
        with contextlib.suppress(Exception):
            create_schema(request.app.state.dsn)
        request.app.state.gallery_schema_ready = True
    return GalleryStore(dsn=request.app.state.dsn)


def _mockup_store(request: Request) -> MockupStore:
    if not getattr(request.app.state, "mockup_schema_ready", False):
        with contextlib.suppress(Exception):
            create_mockup_schema(request.app.state.dsn)
        request.app.state.mockup_schema_ready = True
    return MockupStore(dsn=request.app.state.dsn)


def _unauthorized():
    return JSONResponse({"error": render_server_line("error.not_logged_in")}, status_code=401)


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the gallery routes on ``app`` using its login resolver."""

    @app.post("/api/gallery/publish")
    async def publish_work(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "body must be JSON"}, status_code=400)
        mockup_id = str(body.get("mockup_id", ""))
        if not mockup_id:
            return JSONResponse({"error": "mockup_id is required"}, status_code=400)
        try:
            doc = _mockup_store(request).get(user_id, mockup_id)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        try:
            return _store(request).publish(
                user_id,
                doc,
                restricted_customary_use=bool(body.get("restricted_customary_use", False)),
            )
        except GalleryError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.get("/api/gallery")
    def list_works(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        return {"works": _store(request).list_published()}

    @app.get("/api/gallery/{work_id}")
    def get_work(request: Request, work_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            return _store(request).get(work_id, user_id)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.get("/api/gallery/{work_id}/render.svg")
    def render_work(request: Request, work_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            doc = _store(request).snapshot(work_id)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        return Response(render_svg(doc), media_type="image/svg+xml")

    @app.post("/api/gallery/{work_id}/rate")
    async def rate_work(request: Request, work_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "body must be JSON"}, status_code=400)
        try:
            return _store(request).rate(
                user_id, work_id, body.get("rating"), str(body.get("review", ""))
            )
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except RateLimitedError as exc:
            return JSONResponse(
                {"error": str(exc), "retry_after_seconds": exc.retry_after_seconds},
                status_code=429,
            )
        except GalleryError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/gallery/{work_id}/unpublish")
    def unpublish_work(request: Request, work_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            return _store(request).unpublish(user_id, work_id)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    return app
