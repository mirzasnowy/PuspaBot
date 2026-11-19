"""HTTP layer for the trust / proof surface (B4).

Plain handler functions over :mod:`puspa.evidence.proof`, plus
:func:`register_routes`, which mounts them on a FastAPI app. This
module deliberately does not import ``puspa.pwa.app``: the
coordinator wires it into ``create_app`` with a few lines::

    from puspa.pwa import proof_api
    proof_api.register_routes(app, need_user)

``need_user`` is the app's own login resolver (``Request`` ->
user id or None); it is passed in rather than reimplemented here,
so login behaviour stays in exactly one place.

Contract (all responses are JSON unless noted; all routes require
login):

- ``GET /api/proof/receipts/{event_id}`` -> the receipt for one
  ledger event: what happened, when, the event hash, its
  position in the task hash chain, whether the chain verifies,
  and the linked evidence. 403 when the event belongs to
  another user, 404 when it does not exist.
- ``GET /api/proof/outcomes/{outcome_id}`` -> one B2 outcome
  record with its proof attached (evidence + receipt). 403
  for another user's outcome, 404 for an unknown id.
- ``GET /api/proof/audit-trail?limit=&offset=`` -> the
  requesting user's own audit trail, paginated: every event
  with its receipt. ``limit`` defaults to 100 and clamps to
  5000; the ``pagination`` block carries the total.
- ``GET /api/proof/audit-trail/export?limit=&offset=`` -> the
  same document as a JSON download (``Content-Disposition:
  attachment``), so the user can keep their own trail.
- ``GET /proof`` -> a minimal read page (Jinja, no React
  build needed): the user's recent receipts with hashes and
  chain positions, plus the export link. The page is a thin
  view over the same receipts the API serves.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader

from ..evidence.outcomes import get_outcome
from ..evidence.proof import (
    build_audit_trail_export,
    build_receipt,
    event_belongs_to_user,
    get_event,
    list_user_events,
    outcome_with_proof,
)
from ..i18n.server_lines import render as render_server_line
from ..ledger import Ledger

__all__ = ["get_receipt", "register_routes"]

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATE_DIR = BASE_DIR / "apps" / "pwa" / "templates"


def _jinja():
    return Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)


def _not_logged_in():
    return JSONResponse({"error": render_server_line("error.not_logged_in")}, status_code=401)


def get_receipt(event_id: str, *, user_id: str, dsn=None) -> dict:
    """The receipt for one ledger event, for its owner.

    Raises ``ValueError`` when the event does not exist and
    ``PermissionError`` when it belongs to another user.
    """
    event = get_event(dsn, event_id)
    if event is None:
        raise ValueError(f"unknown event: {event_id}")
    if not event_belongs_to_user(event, user_id, dsn=dsn):
        raise PermissionError("event belongs to another user")
    ledger = Ledger(dsn)
    receipt = build_receipt(ledger, event_id)
    if receipt is None:  # pragma: no cover - get_event just found it
        raise ValueError(f"unknown event: {event_id}")
    return receipt.to_dict()


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the proof-surface routes on ``app`` using its login resolver."""

    def _ledger(request: Request) -> Ledger:
        return Ledger(request.app.state.dsn)

    @app.get("/api/proof/receipts/{event_id}")
    def receipt_endpoint(request: Request, event_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            return get_receipt(event_id, user_id=user_id, dsn=request.app.state.dsn)
        except PermissionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.get("/api/proof/outcomes/{outcome_id}")
    def outcome_endpoint(request: Request, outcome_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        record = get_outcome(request.app.state.dsn, outcome_id)
        if record is None:
            return JSONResponse({"error": f"unknown outcome: {outcome_id}"}, status_code=404)
        if record.user_id != user_id:
            return JSONResponse({"error": "outcome belongs to another user"}, status_code=403)
        return outcome_with_proof(record, _ledger(request))

    def _page_params(request: Request) -> tuple[int, int]:
        try:
            limit = int(request.query_params.get("limit", "100"))
        except ValueError:
            raise ValueError("limit must be an integer") from None
        try:
            offset = int(request.query_params.get("offset", "0"))
        except ValueError:
            raise ValueError("offset must be an integer") from None
        return limit, offset

    @app.get("/api/proof/audit-trail")
    def audit_trail_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            limit, offset = _page_params(request)
            return build_audit_trail_export(
                user_id, dsn=request.app.state.dsn, limit=limit, offset=offset
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.get("/api/proof/audit-trail/export")
    def audit_trail_export_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            limit, offset = _page_params(request)
            document = build_audit_trail_export(
                user_id, dsn=request.app.state.dsn, limit=limit, offset=offset
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(
            content=document,
            headers={"Content-Disposition": 'attachment; filename="puspa-audit-trail.json"'},
        )

    @app.get("/proof", response_class=HTMLResponse)
    def proof_page(request: Request):
        user_id = need_user(request)
        if user_id is None:
            from fastapi.responses import RedirectResponse

            return RedirectResponse("/masuk")
        events, total = list_user_events(user_id, dsn=request.app.state.dsn, limit=100, offset=0)
        ledger = _ledger(request)
        receipts = []
        for event in events:
            receipt = build_receipt(ledger, event["id"])
            if receipt is not None:
                receipts.append(receipt.to_dict())
        jinja = _jinja()
        return HTMLResponse(
            jinja.get_template("proof.html").render(
                {
                    "request": request,
                    "screen": "proof",
                    "user_id": user_id,
                    "receipts": receipts,
                    "total": total,
                }
            )
        )

    return app
