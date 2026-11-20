"""HTTP layer for agent payments, phase 1: prepare only (B8).

Plain handler functions over :mod:`puspa.payments.prepare`,
plus :func:`register_routes`, which mounts them on a FastAPI
app. This module deliberately does not import
``puspa.pwa.app``: the coordinator wires it into
``create_app`` later with a few lines::

    from puspa.pwa import payments_api
    payments_api.register_routes(app, need_user)

``need_user`` is the app's own login resolver (``Request`` ->
user id or None); it is passed in rather than reimplemented
here, so login behaviour stays in exactly one place.

Contract (all responses are JSON; all routes require login):

- ``GET /api/payments`` -> the logged-in user's prepared
  payments, oldest first (every status). 401 when anonymous.
- ``GET /api/payments/payable`` -> the preparations the user
  can see as payable: approved and not expired.
- ``GET /api/payments/{preparation_id}`` -> one preparation
  with recipient + amount + rail + evidence + expiry and the
  notice that Puspa never pays. 404 when unknown or another
  user's.
- ``POST /api/payments`` (JSON: ``recipient`` (``name``,
  ``account_ref``, ``verified``, ``verified_by``),
  ``amount_rupiah`` (positive integer Rupiah), ``rail`` (a
  rail id or a mention, e.g. ``"qris"`` or ``"bayar pake
  qris"``), ``evidence`` (a non-empty list of ``{"kind": ...}``
  entries naming the recipient), optional
  ``idempotency_key``, ``expires_at`` ISO timestamp,
  ``room_id``) -> prepare the payment. 400 on bad input
  (unverified recipient, bad amount, unnamed rail, missing
  evidence, conflicting idempotency key). The preparation
  starts as ``awaiting_approval``: it is not payable until
  approved.
- ``POST /api/payments/{preparation_id}/approve`` and
  ``.../reject`` -> the owner approves or rejects the
  preparation. 400 when it is expired or already decided.
- ``POST /api/payments/{preparation_id}/mark-paid`` (JSON,
  optional ``assertion``) -> the user asserts they paid in
  their own bank or e-wallet app. The assertion is recorded
  as such, never as an execution; only an approved,
  unexpired preparation can be marked.
- ``POST /api/payments/{preparation_id}/execute`` -> always
  403: execution is refused, the refusal is recorded to the
  ledger, and nothing moves. There is no executor to call.

Every preparation the API makes is written to the ledger
(the B2 outcome record plus the G4 approval record), because
the API always passes a ledger and the user's Puspa agent id.
"""

from __future__ import annotations

import contextlib
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..i18n.server_lines import render as render_server_line
from ..ledger import Ledger
from ..payments.models import render_prepared_payment
from ..payments.prepare import (
    ExecutionRefused,
    PaymentPreparationError,
    PreparationService,
    PreparedPayment,
)
from ..payments.schema import create_schema
from ..runtime.agents import ensure_puspa_agent

__all__ = [
    "get_preparation",
    "list_payable",
    "list_preparations",
    "preparation_to_dict",
    "register_routes",
]


def _service(request: Request) -> PreparationService:
    _ensure_schema(request)
    return PreparationService(request.app.state.dsn)


def _ensure_schema(request: Request) -> None:
    """Create the payments tables on first use, once per app.

    The B7 mockup pattern: the module schema is not part of
    migration 0001_core (it lives in its own migration file), so the API creates it lazily the first time a
    request arrives, instead of failing on a missing table.
    A failure to create is swallowed on purpose: the request
    then fails loudly on the missing table, the same way it
    would without this helper.
    """
    if not getattr(request.app.state, "payments_schema_ready", False):
        with contextlib.suppress(Exception):
            create_schema(request.app.state.dsn)
        request.app.state.payments_schema_ready = True


def _ledger(request: Request) -> Ledger:
    return Ledger(request.app.state.dsn)


def _agent_id(request: Request, user_id: str) -> str:
    return ensure_puspa_agent(user_id, dsn=request.app.state.dsn)


def preparation_to_dict(preparation: PreparedPayment) -> dict:
    """One preparation as the PWA serves it: recipient +
    amount + rail + evidence + expiry + status, plus the
    plain-text summary and the notice that Puspa never pays."""
    payload = preparation.to_dict()
    payload["summary"] = render_prepared_payment(preparation)
    return payload


def list_preparations(user_id: str, *, dsn=None) -> list[dict]:
    """The user's prepared payments, oldest first."""
    service = PreparationService(dsn)
    return [
        preparation_to_dict(preparation) for preparation in service.list_prepared_payments(user_id)
    ]


def list_payable(user_id: str, *, dsn=None) -> list[dict]:
    """The user's payable preparations: approved, unexpired."""
    service = PreparationService(dsn)
    return [
        preparation_to_dict(preparation) for preparation in service.list_payable_payments(user_id)
    ]


def get_preparation(preparation_id: str, *, user_id: str, dsn=None) -> dict:
    """One preparation, or raises ``ValueError`` when it does
    not exist or belongs to another user."""
    service = PreparationService(dsn)
    preparation = service.get_prepared_payment(preparation_id, user_id=user_id)
    if preparation is None:
        raise ValueError(f"unknown prepared payment: {preparation_id}")
    return preparation_to_dict(preparation)


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the payment-preparation routes on ``app`` using
    its login resolver."""

    @app.get("/api/payments")
    def payments_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        _ensure_schema(request)
        return {"payments": list_preparations(user_id, dsn=request.app.state.dsn)}

    @app.get("/api/payments/payable")
    def payable_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        _ensure_schema(request)
        return {"payments": list_payable(user_id, dsn=request.app.state.dsn)}

    @app.get("/api/payments/{preparation_id}")
    def preparation_endpoint(request: Request, preparation_id: str):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        _ensure_schema(request)
        try:
            return get_preparation(preparation_id, user_id=user_id, dsn=request.app.state.dsn)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.post("/api/payments")
    async def prepare_payment_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        service = _service(request)
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("payment body must be a JSON object")
            recipient = data.get("recipient")
            if not isinstance(recipient, dict):
                raise ValueError("recipient must be an object")
            amount_rupiah = data.get("amount_rupiah")
            if isinstance(amount_rupiah, bool) or not isinstance(amount_rupiah, int):
                raise ValueError("amount_rupiah must be a positive integer number of Rupiah")
            rail = data.get("rail")
            if not isinstance(rail, str) or not rail.strip():
                raise ValueError("rail must name a payment rail")
            evidence = data.get("evidence")
            expires_at = None
            if data.get("expires_at"):
                expires_at = datetime.fromisoformat(str(data["expires_at"]))
            preparation = service.prepare_payment(
                user_id=user_id,
                recipient=recipient,
                amount_rupiah=amount_rupiah,
                rail=rail,
                evidence=evidence,
                agent_id=_agent_id(request, user_id),
                ledger=_ledger(request),
                idempotency_key=data.get("idempotency_key"),
                expires_at=expires_at,
                room_id=data.get("room_id"),
                task_id=data.get("task_id"),
            )
            return preparation_to_dict(preparation)
        except (PaymentPreparationError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/payments/{preparation_id}/approve")
    def approve_payment_endpoint(request: Request, preparation_id: str):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        service = _service(request)
        try:
            preparation = service.approve_preparation(
                preparation_id,
                user_id=user_id,
                agent_id=_agent_id(request, user_id),
                ledger=_ledger(request),
            )
            return preparation_to_dict(preparation)
        except PaymentPreparationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/payments/{preparation_id}/reject")
    def reject_payment_endpoint(request: Request, preparation_id: str):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        service = _service(request)
        try:
            preparation = service.reject_preparation(
                preparation_id,
                user_id=user_id,
                agent_id=_agent_id(request, user_id),
                ledger=_ledger(request),
            )
            return preparation_to_dict(preparation)
        except PaymentPreparationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/payments/{preparation_id}/mark-paid")
    async def mark_paid_endpoint(request: Request, preparation_id: str):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        service = _service(request)
        try:
            try:
                data = await request.json()
            except ValueError:
                data = {}
            if not isinstance(data, dict):
                raise ValueError("payment body must be a JSON object")
            assertion = data.get("assertion")
            preparation = service.mark_paid_by_user(
                preparation_id,
                user_id=user_id,
                assertion=assertion,
                agent_id=_agent_id(request, user_id),
                ledger=_ledger(request),
            )
            return preparation_to_dict(preparation)
        except (PaymentPreparationError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/payments/{preparation_id}/execute")
    def execute_payment_endpoint(request: Request, preparation_id: str):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        service = _service(request)
        try:
            service.execute_payment(
                preparation_id,
                user_id=user_id,
                agent_id=_agent_id(request, user_id),
                ledger=_ledger(request),
            )
            # Unreachable: execute_payment always raises.
            return JSONResponse({"error": "unreachable"}, status_code=500)  # pragma: no cover
        except ExecutionRefused as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)

    return app
