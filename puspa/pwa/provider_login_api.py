"""HTTP layer for Puspa-native subscription login (V2-28).

Contract (all routes require login; the user is the app's own
``need_user`` resolver, the same as every other PWA API):

- ``GET /api/providers`` -> ``{"providers": [...]}``: one entry
  per known provider with ``provider``, ``flow`` (``"pkce"`` or
  ``"device"``), ``state`` (``logged_in`` / ``expiring`` /
  ``expired`` / ``not_logged_in``), ``expires_at`` (ISO string or
  null), and ``login_ready`` (False when the provider is not
  wired yet, e.g. Codex). No token value is ever in a response.
- ``POST /api/providers/{provider}/login/start`` (JSON
  ``{"redirect_uri"}`` for the PKCE flow) -> for PKCE
  ``{"flow": "pkce", "url", "state"}`` (open ``url`` in a tab);
  for the device flow ``{"flow": "device", "user_code",
  "verification_uri", "expires_in", "interval", "poll_key"}``
  (show the code and URL, then poll).
- ``GET /api/providers/callback?code=...&state=...`` -> exchanges
  the PKCE code, stores the token in the vault, and answers a
  small page saying the login is done (the tab can be closed).
  400 on an unknown or stale state.
- ``POST /api/providers/{provider}/device/poll`` (JSON
  ``{"poll_key"}``) -> ``{"status": "pending"}`` or
  ``{"status": "complete", ...provider status}``.
- ``POST /api/providers/{provider}/logout`` -> the provider
  status after the vault entry is deleted.

The service is built per request from ``app.state.dsn`` with the
injectable transport ``app.state.provider_login_http_client``
(test seam; None means a real httpx client). The pending PKCE
sessions and device codes live in the service, in memory only.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from puspa.i18n.server_lines import render as render_server_line
from puspa.router.provider_login import ProviderLoginError, ProviderLoginService

__all__ = ["register_routes"]


def _not_logged_in() -> JSONResponse:
    return JSONResponse({"error": render_server_line("error.not_logged_in")}, status_code=401)


def _service(app) -> ProviderLoginService:
    """One service per app, built lazily from the app's DSN."""
    service = getattr(app.state, "provider_login_service", None)
    if service is None:
        service = ProviderLoginService(
            dsn=app.state.dsn,
            http_client=getattr(app.state, "provider_login_http_client", None),
        )
        app.state.provider_login_service = service
    return service


def _error_response(exc: Exception, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=status)


def register_routes(app: FastAPI, need_user) -> FastAPI:
    @app.get("/api/providers")
    async def api_list_providers(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            return {"providers": _service(app).list_status(user_id)}
        except Exception as exc:  # pragma: no cover - defensive
            return _error_response(exc)

    @app.post("/api/providers/{provider}/login/start")
    async def api_start_login(request: Request, provider: str):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        service = _service(app)
        try:
            config = service.config(provider)
        except ProviderLoginError as exc:
            return _error_response(exc, status=404)
        try:
            if config.flow == "device":
                return {"flow": "device", **service.start_device(provider)}
            data = await request.json() if await request.body() else {}
            if isinstance(data, dict) and data.get("redirect_uri"):
                redirect_uri = str(data["redirect_uri"])
            else:
                redirect_uri = str(request.base_url).rstrip("/") + "/api/providers/callback"
            started = service.start_pkce(provider, redirect_uri)
            return {"flow": "pkce", "url": started["url"], "state": started["state"]}
        except ProviderLoginError as exc:
            return _error_response(exc)
        except Exception as exc:  # pragma: no cover - defensive
            return _error_response(exc)

    @app.get("/api/providers/callback", name="api_provider_callback")
    async def api_provider_callback(request: Request, code: str = "", state: str = ""):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        service = _service(app)
        try:
            status = service.finish_pkce(user_id, state, code)
        except ProviderLoginError as exc:
            return HTMLResponse(
                f"<html><body><p>Login gagal: {exc}</p>"
                "<p>Tutup tab ini dan coba lagi dari Pengaturan.</p></body></html>",
                status_code=400,
            )
        provider = status.get("provider", "")
        return HTMLResponse(
            "<html><body><p>Login berhasil untuk "
            f"{provider}. Token tersimpan di vault.</p>"
            "<p>Tutup tab ini dan kembali ke Puspa.</p></body></html>"
        )

    @app.post("/api/providers/{provider}/device/poll")
    async def api_poll_device(request: Request, provider: str):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        service = _service(app)
        try:
            data = await request.json()
            poll_key = data.get("poll_key") if isinstance(data, dict) else None
            if not poll_key:
                return _error_response(ValueError("poll_key is required"))
            return service.poll_device(user_id, provider, poll_key)
        except ProviderLoginError as exc:
            return _error_response(exc)

    @app.post("/api/providers/{provider}/logout")
    async def api_logout_provider(request: Request, provider: str):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        service = _service(app)
        try:
            service.logout(user_id, provider)
            return service.status(user_id, provider)
        except ProviderLoginError as exc:
            return _error_response(exc, status=404)

    return app
