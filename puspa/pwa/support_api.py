"""Creator support API (``puspa/pwa/support_api.py``).

Routes (all require login, mounted by ``puspa.pwa.app``):

- ``POST /api/gallery/{work_id}/support`` — ``{"amount_idr": ...}``
  supports a published gallery work. The creator gets the share in
  Puspa credits only, worth strictly less than the price paid, and
  the platform keeps the commission. The purchase is recorded in
  the support table and in the hash-chained ledger, with status
  ``"prepared"``: beta follows the B8 prepare-only rule, so no real
  money moves here. Supporting your own work is allowed and always
  loses money by construction (see ``puspa/gallery/support.py``).
- ``GET  /api/gallery/{work_id}/support`` — support totals for one
  work: count, total amount, total commission, total credits.
- ``GET  /api/gallery/support/credits`` — the caller's own credits
  received as a creator, with how many supports granted them.

No cash-out: there is no route, field, or function here that turns
credits back into money. Commission applies only to digital gallery
works; a price check or authenticity check of a physical item can
never carry a commission through this API.
"""

from __future__ import annotations

import contextlib

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..gallery.support import SupportError, SupportStore, create_schema
from ..i18n.server_lines import render as render_server_line
from ..ledger import Ledger

__all__ = ["register_routes"]


def _store(request: Request) -> SupportStore:
    if not getattr(request.app.state, "support_schema_ready", False):
        with contextlib.suppress(Exception):
            create_schema(request.app.state.dsn)
        request.app.state.support_schema_ready = True
    return SupportStore(dsn=request.app.state.dsn)


def _unauthorized():
    return JSONResponse({"error": render_server_line("error.not_logged_in")}, status_code=401)


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the creator support routes on ``app`` using its login resolver."""

    @app.post("/api/gallery/{work_id}/support")
    async def support_work(request: Request, work_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "body must be JSON"}, status_code=400)
        if "amount_idr" not in body:
            return JSONResponse({"error": "amount_idr is required"}, status_code=400)
        try:
            return _store(request).support(
                user_id,
                work_id,
                body.get("amount_idr"),
                ledger=Ledger(dsn=request.app.state.dsn),
            )
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except SupportError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.get("/api/gallery/support/credits")
    def own_creator_credits(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        return _store(request).credits_for_creator(user_id)

    @app.get("/api/gallery/{work_id}/support")
    def work_support_totals(request: Request, work_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            return _store(request).totals_for_work(work_id)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    return app
