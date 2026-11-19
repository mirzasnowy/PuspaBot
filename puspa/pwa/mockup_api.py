"""B7 mockup editor API (``puspa/pwa/mockup_api.py``).

Routes (all require login, mounted by ``puspa.pwa.app``):

- ``GET  /api/mockups/templates`` — the five beta templates + slots.
- ``POST /api/mockups/generate`` — ``{"prompt": ...}`` -> 2-3 variants
  (deterministic; no live model call).
- ``POST /api/mockups`` — store a doc (``{"doc": ...}``) or build one
  from a template (``{"template": ..., "fields": {...}}``).
- ``GET  /api/mockups`` / ``GET /api/mockups/{id}`` — list / fetch.
- ``POST /api/mockups/{id}/ops`` — apply one numbered op.
- ``POST /api/mockups/{id}/patch`` — word-change instruction
  ("besarkan harga, ganti warna jadi hijau") -> ops -> applied.
- ``POST /api/mockups/{id}/undo`` / ``/redo`` — session undo/redo.
- ``POST /api/mockups/{id}/assets`` — add a scanned image asset
  (data URI only) to the workspace doc.
- ``GET  /api/mockups/{id}/render.svg`` / ``/render.html`` — the
  deterministic render; HTML is served with the strict CSP.
- ``GET  /api/mockups/{id}/export.png`` / ``/export.pdf``.
- ``DELETE /api/mockups/{id}``.

Undo/redo live in a per-app in-memory session per mockup (the same
:class:`DesignSession` the editor screen uses client-side), seeded
from the stored doc and re-seeded when the stored version moves.
The durable record is the stored doc with its numbered op log, so
a restart loses only the redo stack, never the design.
"""

from __future__ import annotations

import contextlib

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from ..i18n.server_lines import render as render_server_line
from ..mockup.doc import DesignError, DesignSession, validate_doc
from ..mockup.export import export_pdf, export_png
from ..mockup.generate import critique_doc, generate_mockup, parse_word_patch
from ..mockup.render import csp_header, render_html, render_svg
from ..mockup.store import MockupStore, create_schema
from ..mockup.templates import TEMPLATES, fill_template, template_fields

__all__ = ["register_routes"]


def _store(request: Request) -> MockupStore:
    if not getattr(request.app.state, "mockup_schema_ready", False):
        with contextlib.suppress(Exception):
            create_schema(request.app.state.dsn)
        request.app.state.mockup_schema_ready = True
    return MockupStore(dsn=request.app.state.dsn)


def _unauthorized():
    return JSONResponse({"error": render_server_line("error.not_logged_in")}, status_code=401)


def _session(request: Request, user_id: str, mockup_id: str) -> DesignSession:
    """In-memory undo/redo session for one mockup, seeded from storage."""
    sessions = getattr(request.app.state, "mockup_sessions", None)
    if sessions is None:
        sessions = request.app.state.mockup_sessions = {}
    key = (user_id, mockup_id)
    store = _store(request)
    doc = store.get(user_id, mockup_id)
    session = sessions.get(key)
    if session is None or session.doc.get("version") != doc.get("version"):
        session = DesignSession(doc)
        sessions[key] = session
    return session


def _save_session(request: Request, user_id: str, session: DesignSession) -> dict:
    return _store(request).save(user_id, session.doc)


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the mockup routes on ``app`` using its login resolver."""

    @app.get("/api/mockups/templates")
    def list_templates(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        return {
            "templates": [{"id": name, "fields": list(template_fields(name))} for name in TEMPLATES]
        }

    @app.post("/api/mockups/generate")
    async def generate(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "body must be JSON"}, status_code=400)
        try:
            result = generate_mockup(str(body.get("prompt", "")))
        except (DesignError, KeyError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return result

    @app.post("/api/mockups")
    async def create_mockup(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "body must be JSON"}, status_code=400)
        try:
            if isinstance(body.get("doc"), dict):
                doc = validate_doc(body["doc"])
            else:
                doc = fill_template(str(body.get("template", "")), body.get("fields") or {})
        except (DesignError, KeyError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        try:
            return _store(request).create(user_id, doc)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.get("/api/mockups")
    def list_mockups(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        return {"mockups": _store(request).list(user_id)}

    @app.get("/api/mockups/{mockup_id}")
    def get_mockup(request: Request, mockup_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            return _store(request).get(user_id, mockup_id)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.post("/api/mockups/{mockup_id}/ops")
    async def apply_op_endpoint(request: Request, mockup_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "body must be JSON"}, status_code=400)
        try:
            session = _session(request, user_id, mockup_id)
            session.apply(body.get("op") or body)
            return _save_session(request, user_id, session)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except DesignError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/mockups/{mockup_id}/patch")
    async def patch_endpoint(request: Request, mockup_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "body must be JSON"}, status_code=400)
        try:
            doc = _store(request).get(user_id, mockup_id)
            ops = parse_word_patch(doc, str(body.get("instruction", "")))
            session = _session(request, user_id, mockup_id)
            for op in ops:
                session.apply(op)
            return _save_session(request, user_id, session)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except DesignError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/mockups/{mockup_id}/undo")
    def undo_endpoint(request: Request, mockup_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            session = _session(request, user_id, mockup_id)
            session.undo()
            return _save_session(request, user_id, session)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except DesignError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/mockups/{mockup_id}/redo")
    def redo_endpoint(request: Request, mockup_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            session = _session(request, user_id, mockup_id)
            session.redo()
            return _save_session(request, user_id, session)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except DesignError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/mockups/{mockup_id}/assets")
    async def add_asset(request: Request, mockup_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "body must be JSON"}, status_code=400)
        try:
            store = _store(request)
            doc = store.get(user_id, mockup_id)
            asset_id = str(body.get("asset_id", ""))
            if not asset_id:
                return JSONResponse({"error": "asset_id is required"}, status_code=400)
            doc.setdefault("assets", {})[asset_id] = {
                "kind": "image",
                "data_uri": str(body.get("data_uri", "")),
                "name": str(body.get("name", asset_id)),
            }
            validate_doc(doc)
            return store.save(user_id, doc)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except DesignError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.get("/api/mockups/{mockup_id}/critique")
    def critique_endpoint(request: Request, mockup_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            doc = _store(request).get(user_id, mockup_id)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        return {"issues": critique_doc(doc)}

    @app.get("/api/mockups/{mockup_id}/render.svg")
    def render_svg_endpoint(request: Request, mockup_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            doc = _store(request).get(user_id, mockup_id)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        return Response(render_svg(doc), media_type="image/svg+xml")

    @app.get("/api/mockups/{mockup_id}/render.html")
    def render_html_endpoint(request: Request, mockup_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            doc = _store(request).get(user_id, mockup_id)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        return HTMLResponse(render_html(doc), headers={"Content-Security-Policy": csp_header()})

    @app.get("/api/mockups/{mockup_id}/export.png")
    def export_png_endpoint(request: Request, mockup_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            doc = _store(request).get(user_id, mockup_id)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        return Response(export_png(doc), media_type="image/png")

    @app.get("/api/mockups/{mockup_id}/export.pdf")
    def export_pdf_endpoint(request: Request, mockup_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            doc = _store(request).get(user_id, mockup_id)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        return Response(export_pdf(doc), media_type="application/pdf")

    @app.delete("/api/mockups/{mockup_id}")
    def delete_mockup(request: Request, mockup_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            _store(request).delete(user_id, mockup_id)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        sessions = getattr(request.app.state, "mockup_sessions", {})
        sessions.pop((user_id, mockup_id), None)
        return {"deleted": mockup_id}

    return app
