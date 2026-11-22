"""HTTP layer for the skill marketplace hub (COW-03).

Plain handler functions over :mod:`puspa.marketplace.hub`, plus
:func:`register_routes`, which mounts them on a FastAPI app. This
module deliberately does not import ``puspa.pwa.app``: the
coordinator wires it into ``create_app`` with a few lines::

    from puspa.pwa import hub_api
    hub_api.register_routes(app, need_user)

``need_user`` is the app's own login resolver (``Request`` ->
user id or None); it is passed in rather than reimplemented
here, so login behaviour stays in exactly one place.

The registry is one per process (``app.state.hub_registry``),
built lazily on first use; tests may inject their own registry
and skills root on ``app.state`` (same seam pattern as
``memory_service``). Installed skills land under the user's
Puspa home (``<home>/skills``), never in the repo tree.

Contract (all responses are JSON unless noted; all routes
require login):

- ``GET /api/hub/skills?q=...`` -> the locally browsable
  skills (shipped + installed), filtered by the query.
- ``GET /api/hub/preview?source=...`` -> the skill a source
  points at (slug, name, description, licence, sha256), fetched
  but NOT installed. A failed fetch is a 502 with the reason,
  never an empty success.
- ``POST /api/hub/install {"source": ..., "expected_sha256": ...}``
  -> installs the previewed skill. ``expected_sha256`` is
  required: it is the preview the user confirmed, and content
  that changed since the preview is refused (409). The POST
  itself is the user's explicit approval for that exact
  slug + hash, so no separate approval round-trip is needed.
  A failed install is a 4xx with the reason, never a
  pretend-success.
- ``401 {"error": "belum masuk"}`` when not logged in.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from puspa.i18n.server_lines import render as render_server_line
from puspa.marketplace.errors import (
    ApprovalRequiredError,
    DiscoveryError,
    HashMismatchError,
    LicenseNotAllowedError,
    MarketplaceError,
)
from puspa.marketplace.hub import (
    install_from_source,
    list_hub_skills,
    preview_source,
)
from puspa.marketplace.registry import MarketplaceRegistry

__all__ = [
    "get_hub_registry",
    "get_hub_skills_root",
    "hub_screen_context",
    "install_skill",
    "list_skills",
    "preview_skill",
    "register_routes",
]


def get_hub_registry(app) -> MarketplaceRegistry:
    """The app's hub registry (one per process, built lazily)."""
    registry = getattr(app.state, "hub_registry", None)
    if registry is None:
        registry = MarketplaceRegistry()
        app.state.hub_registry = registry
    return registry


def get_hub_skills_root(app):
    """Where installed hub skills land on disk.

    Tests inject ``app.state.hub_skills_root``; otherwise the
    user's Puspa home (``PUSPA_HOME``, else ``~/.puspa``).
    """
    root = getattr(app.state, "hub_skills_root", None)
    if root is not None:
        return root
    from puspa.sessions.home import resolve_home

    return resolve_home() / "skills"


def get_hub_http_client(app):
    """Injected httpx client for tests (``None`` = live fetch)."""
    return getattr(app.state, "hub_http_client", None)


def hub_screen_context(app, *, query: str | None = None) -> list[dict]:
    """Skills for the hub Jinja template (same list the API serves)."""
    return [s.to_dict() for s in list_hub_skills(get_hub_registry(app), query=query)]


def list_skills(app, *, query: str | None = None) -> list[dict]:
    """Locally browsable skills, optionally filtered by query."""
    return hub_screen_context(app, query=query)


def preview_skill(app, *, source: str) -> dict:
    """Preview a skill source (fetch only, install nothing)."""
    fetched = preview_source(source, http_client=get_hub_http_client(app))
    return fetched.to_dict()


def install_skill(app, *, user_id: str, source: str, expected_sha256: str) -> dict:
    """Install the previewed skill (the POST is the approval)."""
    from puspa.marketplace.discovery import InstallApproval

    fetched = preview_source(source, http_client=get_hub_http_client(app))
    if fetched.sha256 != expected_sha256:
        raise HashMismatchError(
            f"skill {fetched.slug!r} SKILL.md sha256 {fetched.sha256[:12]}... does not match "
            f"the previewed hash {expected_sha256[:12]}..."
        )
    approval = InstallApproval.for_skill(fetched.slug, fetched.sha256)
    return install_from_source(
        get_hub_registry(app),
        source,
        user_id=user_id,
        approval=approval,
        expected_sha256=expected_sha256,
        skills_root=get_hub_skills_root(app),
        http_client=get_hub_http_client(app),
    )


def _not_logged_in():
    return JSONResponse({"error": render_server_line("error.not_logged_in")}, status_code=401)


def _hub_error(exc: MarketplaceError):
    if isinstance(exc, ApprovalRequiredError):
        status = 403
    elif isinstance(exc, (LicenseNotAllowedError, HashMismatchError)):
        status = 409
    elif isinstance(exc, DiscoveryError):
        status = 502
    else:
        status = 400
    return JSONResponse({"error": str(exc)}, status_code=status)


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the hub routes on ``app`` using its login resolver."""

    @app.get("/api/hub/skills")
    def hub_skills_endpoint(request: Request, q: str | None = None):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            return {"skills": list_skills(request.app, query=q), "query": q or ""}
        except MarketplaceError as exc:
            return _hub_error(exc)

    @app.get("/api/hub/preview")
    def hub_preview_endpoint(request: Request, source: str):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            return preview_skill(request.app, source=source)
        except MarketplaceError as exc:
            return _hub_error(exc)

    @app.post("/api/hub/install")
    async def hub_install_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "body must be JSON"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
        source = body.get("source")
        expected_sha256 = body.get("expected_sha256")
        if not isinstance(source, str) or not source.strip():
            return JSONResponse({"error": "source is required"}, status_code=400)
        if not isinstance(expected_sha256, str) or not expected_sha256.strip():
            return JSONResponse(
                {
                    "error": (
                        "expected_sha256 is required: preview the skill first "
                        "(/api/hub/preview) and confirm the hash you saw"
                    )
                },
                status_code=400,
            )
        try:
            installed = install_skill(
                request.app, user_id=user_id, source=source, expected_sha256=expected_sha256
            )
        except MarketplaceError as exc:
            return _hub_error(exc)
        return {"ok": True, "installed": installed}

    return app
