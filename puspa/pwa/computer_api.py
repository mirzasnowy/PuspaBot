"""HTTP layer for the V2-35 computer controls (tiers + allowlist
+ stop + audit).

Plain handler functions over :mod:`puspa.computer.allowlist`,
:mod:`puspa.computer.stop`, and :mod:`puspa.computer.audit`,
plus :func:`register_routes`, which mounts them on a FastAPI
app. This module deliberately does not import
``puspa.pwa.app``: the coordinator wires it into ``create_app``
with a few lines::

    from puspa.pwa import computer_api
    computer_api.register_routes(app, need_user)

``need_user`` is the app's own login resolver (``Request`` ->
user id or None); it is passed in rather than reimplemented
here, so login behaviour stays in exactly one place. The
allowlist and the stop flag live in the user's Puspa state dir
(``~/.local/share/puspa/computer/``), the same files the
``puspa computer`` CLI reads and writes, so the PWA panel and
the CLI always agree. The audit entries come from the ledger.

Contract (all responses are JSON; all routes require login):

- ``GET /api/computer/allowlist`` -> ``{"apps": [...]}``.
- ``POST /api/computer/allowlist`` (JSON: ``{"app": "..."}``)
  -> allowlists the app. 400 on an empty app name.
- ``DELETE /api/computer/allowlist/{app}`` -> revokes it.
- ``GET /api/computer/stop`` -> the stop flag (active, actor,
  reason, updated_at).
- ``POST /api/computer/stop`` (JSON: ``{"reason": "..."}``)
  -> requests the stop (halts the hands immediately).
- ``POST /api/computer/stop/clear`` (JSON: ``{"reason": "..."}``)
  -> clears the stop.
- ``GET /api/computer/audit?limit=N`` -> newest-first audit
  entries (``{"entries": [...], "ledger_available": bool}``).
- ``401 {"error": "belum masuk"}`` when not logged in.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..computer.allowlist import Allowlist
from ..computer.audit import LedgerComputerAudit
from ..computer.stop import ComputerStop
from ..i18n.server_lines import render as render_server_line

__all__ = [
    "clear_stop",
    "get_allowlist",
    "get_audit",
    "get_stop",
    "register_routes",
    "request_stop",
]

#: Default page size for the audit list.
DEFAULT_AUDIT_LIMIT = 50

#: Hard cap: one audit page never returns more than this.
MAX_AUDIT_LIMIT = 200


def _not_logged_in():
    return JSONResponse({"error": render_server_line("error.not_logged_in")}, status_code=401)


def _allowlist(request: Request) -> Allowlist:
    """The app's allowlist, or the default file-backed one."""
    allowlist = getattr(request.app.state, "computer_allowlist", None)
    if allowlist is None:  # pragma: no cover - create_app always sets it
        allowlist = Allowlist()
        request.app.state.computer_allowlist = allowlist
    return allowlist


def _stop(request: Request) -> ComputerStop:
    """The app's stop flag, or the default file-backed one."""
    stop = getattr(request.app.state, "computer_stop", None)
    if stop is None:  # pragma: no cover - create_app always sets it
        stop = ComputerStop()
        request.app.state.computer_stop = stop
    return stop


def _audit(request: Request) -> LedgerComputerAudit:
    """The app's audit sink, or a ledger-backed one."""
    audit = getattr(request.app.state, "computer_audit", None)
    if audit is None:  # pragma: no cover - create_app always sets it
        audit = LedgerComputerAudit()
        request.app.state.computer_audit = audit
    return audit


def get_allowlist(*, allowlist: Allowlist | None = None) -> dict:
    """The allowlisted apps."""
    return {"apps": (allowlist or Allowlist()).apps()}


def add_allowlist(app: str, *, allowlist: Allowlist | None = None) -> dict:
    """Allowlist one app. Raises ValueError on an empty name."""
    store = allowlist or Allowlist()
    return {"app": app.strip(), "allowed": True, "changed": store.allow(app)}


def remove_allowlist(app: str, *, allowlist: Allowlist | None = None) -> dict:
    """Revoke one app from the allowlist."""
    store = allowlist or Allowlist()
    return {"app": app.strip(), "allowed": False, "changed": store.revoke(app)}


def get_stop(*, stop: ComputerStop | None = None) -> dict:
    """The stop flag state."""
    return (stop or ComputerStop()).state()


def request_stop(
    reason: str = "",
    *,
    stop: ComputerStop | None = None,
    audit: LedgerComputerAudit | None = None,
) -> dict:
    """Request the stop and record it in the ledger (best
    effort: a missing ledger never blocks the stop itself)."""
    flag = stop or ComputerStop()
    state = flag.request(reason=reason)
    (audit or LedgerComputerAudit()).record_stop_requested(reason=reason)
    return state


def clear_stop(
    reason: str = "",
    *,
    stop: ComputerStop | None = None,
    audit: LedgerComputerAudit | None = None,
) -> dict:
    """Clear the stop and record it in the ledger (best
    effort)."""
    flag = stop or ComputerStop()
    state = flag.clear(reason=reason)
    (audit or LedgerComputerAudit()).record_stop_cleared(reason=reason)
    return state


def get_audit(
    limit: int = DEFAULT_AUDIT_LIMIT, *, audit: LedgerComputerAudit | None = None
) -> dict:
    """Newest-first computer audit entries from the ledger."""
    if limit < 0 or limit > MAX_AUDIT_LIMIT:
        raise ValueError(f"limit must be between 0 and {MAX_AUDIT_LIMIT}")
    sink = audit or LedgerComputerAudit()
    entries = sink.recent(limit)
    return {"entries": entries, "ledger_available": sink.last_error is None}


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the V2-35 computer routes on ``app`` using its login resolver."""

    @app.get("/api/computer/allowlist")
    def allowlist_endpoint(request: Request):
        if need_user(request) is None:
            return _not_logged_in()
        return get_allowlist(allowlist=_allowlist(request))

    @app.post("/api/computer/allowlist")
    async def allowlist_add_endpoint(request: Request):
        if need_user(request) is None:
            return _not_logged_in()
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("allowlist body must be a JSON object")
            return add_allowlist(str(data.get("app") or ""), allowlist=_allowlist(request))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.delete("/api/computer/allowlist/{app}")
    def allowlist_remove_endpoint(request: Request, app: str):
        if need_user(request) is None:
            return _not_logged_in()
        return remove_allowlist(app, allowlist=_allowlist(request))

    @app.get("/api/computer/stop")
    def stop_endpoint(request: Request):
        if need_user(request) is None:
            return _not_logged_in()
        return get_stop(stop=_stop(request))

    @app.post("/api/computer/stop")
    async def stop_request_endpoint(request: Request):
        if need_user(request) is None:
            return _not_logged_in()
        data = await request.json() if await request.body() else {}
        if not isinstance(data, dict):
            return JSONResponse({"error": "stop body must be a JSON object"}, status_code=400)
        return request_stop(
            str(data.get("reason") or ""), stop=_stop(request), audit=_audit(request)
        )

    @app.post("/api/computer/stop/clear")
    async def stop_clear_endpoint(request: Request):
        if need_user(request) is None:
            return _not_logged_in()
        data = await request.json() if await request.body() else {}
        if not isinstance(data, dict):
            return JSONResponse({"error": "stop body must be a JSON object"}, status_code=400)
        return clear_stop(str(data.get("reason") or ""), stop=_stop(request), audit=_audit(request))

    @app.get("/api/computer/audit")
    def audit_endpoint(request: Request, limit: int = DEFAULT_AUDIT_LIMIT):
        if need_user(request) is None:
            return _not_logged_in()
        try:
            return get_audit(limit, audit=_audit(request))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    return app
