"""HTTP layer for per-capability model routing (COW-08).

Contract (all routes require login; the user is the app's own
``need_user`` resolver, the same as every other PWA API):

- ``GET /api/routing`` -> ``{"capabilities": {...}, "providers": {...}}``:
  one entry per capability with ``capability``, ``provider``,
  ``model``, ``base_url``, ``has_key`` (boolean only) and
  ``is_default`` (True when no row is stored), plus the
  providers that can serve each capability. No key material is
  ever in a response.
- ``POST /api/routing/{capability}`` (JSON ``{"provider",
  "model", "base_url"}``) -> the saved route. One click in the
  console swaps one capability's provider; the other four
  routes are untouched. 400 when the provider cannot serve the
  capability (the error names the providers that can), when no
  model is given for a provider that needs one, or when a key
  field is sent in the body; 404 for an unknown capability.
- ``DELETE /api/routing/{capability}`` -> the capability's
  default route again. Idempotent.
- ``POST /api/routing/key`` (JSON ``{"provider", "key"}``) /
  ``DELETE /api/routing/key?provider=`` -> store / remove a
  provider key in the vault only (the same ``byok:<provider>``
  entry the model settings use, so one key serves every
  capability routed to that provider).
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from puspa.i18n.server_lines import render as render_server_line
from puspa.pwa.capability_routing import (
    CAPABILITIES,
    CAPABILITY_PROVIDERS,
    delete_routing_key,
    get_routing,
    reset_route,
    save_route_from_payload,
    save_routing_key,
)

__all__ = ["register_routes"]


def _not_logged_in() -> JSONResponse:
    return JSONResponse({"error": render_server_line("error.not_logged_in")}, status_code=401)


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the routing routes on ``app`` using its login resolver."""

    @app.get("/api/routing")
    async def api_get_routing(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            routing = get_routing(user_id, dsn=app.state.dsn)
            return {
                "capabilities": {
                    capability: route.to_public_dict() for capability, route in routing.items()
                },
                "providers": {
                    capability: list(CAPABILITY_PROVIDERS[capability])
                    for capability in CAPABILITIES
                },
            }
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    # The literal "key" routes register BEFORE the
    # "/api/routing/{capability}" routes: FastAPI matches in
    # registration order, and the capability route would swallow
    # "/api/routing/key" as capability="key" (404) otherwise.
    @app.post("/api/routing/key")
    async def api_save_routing_key(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("routing key body must be a JSON object")
            save_routing_key(
                user_id,
                str(data.get("provider", "")),
                str(data.get("key", "")),
                dsn=app.state.dsn,
            )
            return {"ok": True}
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.delete("/api/routing/key")
    async def api_delete_routing_key(request: Request, provider: str = ""):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            delete_routing_key(user_id, provider, dsn=app.state.dsn)
            return {"ok": True}
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/routing/{capability}")
    async def api_save_route(request: Request, capability: str):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        if capability not in CAPABILITIES:
            return JSONResponse(
                {"error": f"unknown capability: {capability!r} (known: {', '.join(CAPABILITIES)})"},
                status_code=404,
            )
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("route body must be a JSON object")
            saved = save_route_from_payload(
                user_id,
                {
                    "capability": capability,
                    "provider": data.get("provider", ""),
                    "model": data.get("model"),
                    "base_url": data.get("base_url"),
                    # Any key field the client sends is passed
                    # through so save_route_from_payload refuses it
                    # loudly instead of silently dropping it.
                    **{
                        k: v
                        for k, v in data.items()
                        if k not in ("capability", "provider", "model", "base_url")
                    },
                },
                dsn=app.state.dsn,
            )
            return saved.to_public_dict()
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.delete("/api/routing/{capability}")
    async def api_reset_route(request: Request, capability: str):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        if capability not in CAPABILITIES:
            return JSONResponse(
                {"error": f"unknown capability: {capability!r} (known: {', '.join(CAPABILITIES)})"},
                status_code=404,
            )
        try:
            return reset_route(user_id, capability, dsn=app.state.dsn).to_public_dict()
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    return app
