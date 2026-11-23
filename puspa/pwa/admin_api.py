"""HTTP layer for the local admin dashboard (AST-10).

The operator surface for a self-hosted instance: instance
configuration, sessions, usage statistics, and plugin/skill
management. The PWA is the user home; this is the screen for the
person who runs the machine. It is deliberately local-only: in
``hosted`` serve mode every route answers 403, because the
operator surface of a hosted deployment lives in the private
cloud backend, not in this repo (see ``docs/serve-modes.md``).
Nothing here touches hosted billing or multi-tenant features.

Contract (all routes require login; all responses are JSON
unless noted):

- ``GET /api/admin/config`` -> the instance configuration:
  instance name (editable), serve mode (read-only: changing it
  needs ``PUSPA_SERVE_MODE`` plus a restart), the model
  selection, the telemetry switch, and the embedding snapshot.
- ``POST /api/admin/config`` (JSON, any of ``instance_name``,
  ``model``, ``telemetry_enabled``) -> the saved configuration
  in the same shape. Each part is validated and stored by its
  owning module (``admin_settings``, ``model_settings``,
  ``telemetry.settings``); a bad value is a 400 naming the
  problem, and an unknown key is a 400, never silently ignored.
- ``GET /api/admin/sessions`` -> the operator's login sessions
  (device label, created/last-seen times, revoked flag), the
  live pairing-store snapshot, and the active/revoked counts.
  Tokens are never exposed: only their hashes are stored, and
  this listing does not even carry those.
- ``POST /api/admin/sessions/{id}/revoke`` -> revoke one
  session by its row id (404 for an unknown id or another
  user's session; both read the same). Idempotent.
- ``POST /api/admin/sessions/revoke-all`` -> revoke every
  session of the operator, including this browser: the operator
  signs themselves out everywhere.
- ``GET /api/admin/usage`` -> usage statistics counted from
  the existing ledger (``events`` table) and the existing
  stores: total events, events by type, events in the last
  24 hours / 7 days, user count, active sessions, skill
  counts, and chat messages held in memory this run. No new
  telemetry is collected: these are counts of data the
  instance already keeps.
- ``GET /api/admin/skills`` -> the installed skills (the same
  list the hub serves: shipped builtins plus registry skills).
- ``POST /api/admin/skills/{id}/enable`` /
  ``POST /api/admin/skills/{id}/disable`` -> enable or disable
  an installed library skill through the existing skill
  registry (``MarketplaceRegistry.install`` /
  ``uninstall``; the operator's POST is the explicit approval,
  mirroring the hub install). Builtin skills ship with Puspa
  and are always on: toggling one is a 409 with that reason,
  never a pretend-success. An unknown id is a 404.

- ``401 {"error": "belum masuk"}`` when not logged in.
- ``403`` in hosted serve mode (see above).

The ``/admin`` page itself (Jinja-only, like ``/trial``) is
wired in :mod:`puspa.pwa.app`; :func:`admin_page_context`
builds its server-rendered context from the same readers.
"""

from __future__ import annotations

from puspa.db import Error as _db_Error
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from puspa.accounts.ports import (
    _connect,
    list_sessions,
    revoke_all_sessions,
    revoke_session_by_id,
)
from puspa.i18n.server_lines import render as render_server_line
from puspa.marketplace.errors import MarketplaceError
from puspa.pwa import admin_settings, hub_api, model_settings
from puspa.pwa.serve_modes import operator_panels_visible, pairing_status
from puspa.telemetry.config import env_flag, resolve_enabled
from puspa.telemetry.settings import get_settings as get_telemetry_settings
from puspa.telemetry.settings import save_settings as save_telemetry_settings

__all__ = [
    "HOSTED_REFUSAL",
    "admin_page_context",
    "read_config",
    "read_sessions",
    "read_skills",
    "read_usage",
    "register_routes",
]

#: Keys the config POST accepts. Anything else is a 400: a
#: mistyped key must never silently do nothing.
_CONFIG_KEYS = ("instance_name", "model", "telemetry_enabled")

#: Why the admin surface refuses hosted mode (shown on the page
#: and in the API error alike).
HOSTED_REFUSAL = (
    "Dasbor admin hanya tersedia di instalasi lokal; di mode hosted, "
    "panel operator ada di backend cloud, bukan di sini."
)


def _not_logged_in() -> JSONResponse:
    return JSONResponse({"error": render_server_line("error.not_logged_in")}, status_code=401)


def _hosted() -> JSONResponse:
    return JSONResponse({"error": HOSTED_REFUSAL}, status_code=403)


def _bad_request(exc: Exception) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=400)


def _dsn(request: Request):
    return request.app.state.dsn


def _local_only(request: Request) -> JSONResponse | None:
    """The hosted-mode guard: None when the admin surface exists here."""
    if not operator_panels_visible(request.app.state.serve_mode):
        return _hosted()
    return None


def _telemetry_state(user_id, dsn) -> dict:
    """The telemetry switch as the Settings screen shows it."""
    stored = get_telemetry_settings(user_id, dsn=dsn).enabled
    override = env_flag()
    return {
        "enabled": stored,
        "env_override": override,
        "effective_enabled": resolve_enabled(env=override, stored=stored),
    }


def read_config(app, user_id: str) -> dict:
    """The instance configuration the admin screen shows and edits."""
    dsn = app.state.dsn
    return {
        "instance_name": admin_settings.get_instance_name(dsn=dsn),
        "serve_mode": str(app.state.serve_mode),
        "serve_mode_change": (
            "Mode serve hanya bisa diubah lewat PUSPA_SERVE_MODE (atau "
            "berkas modenya) lalu restart server; tidak ada tombol di sini."
        ),
        "model": model_settings.get_selection(user_id, dsn=dsn).to_public_dict(),
        "telemetry": _telemetry_state(user_id, dsn),
        "embedding": getattr(app.state, "embedding_health", None),
    }


def read_sessions(app, user_id: str) -> dict:
    """The operator's login sessions plus the pairing store snapshot."""
    dsn = app.state.dsn
    sessions = list_sessions(user_id, dsn=dsn)
    store = getattr(app.state, "pairing_store", None)
    return {
        "sessions": sessions,
        "active": sum(1 for s in sessions if not s["revoked"]),
        "revoked": sum(1 for s in sessions if s["revoked"]),
        "pairing": pairing_status(store) if store is not None else None,
    }


def read_usage(app, user_id: str) -> dict:
    """Usage statistics counted from the ledger and the live stores.

    Every number comes from data the instance already keeps: the
    append-only ledger (``events``), the account tables, the hub
    skill list, and the in-memory chat store. Nothing new is
    recorded for this endpoint.
    """
    dsn = app.state.dsn
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM events")
        events_total = int(cur.fetchone()[0])
        cur.execute("SELECT type, COUNT(*) FROM events GROUP BY type ORDER BY COUNT(*) DESC")
        by_type = [{"type": row[0], "count": int(row[1])} for row in cur.fetchall()]
        cur.execute("SELECT COUNT(*) FROM events WHERE created_at >= now() - interval '24 hours'")
        events_24h = int(cur.fetchone()[0])
        cur.execute("SELECT COUNT(*) FROM events WHERE created_at >= now() - interval '7 days'")
        events_7d = int(cur.fetchone()[0])
        cur.execute("SELECT COUNT(*) FROM users")
        users = int(cur.fetchone()[0])
        cur.execute("SELECT COUNT(*) FROM sessions WHERE NOT revoked")
        sessions_active = int(cur.fetchone()[0])
    skills = read_skills(app)
    chat = getattr(app.state, "chat", {}) or {}
    return {
        "events_total": events_total,
        "events_by_type": by_type,
        "events_last_24h": events_24h,
        "events_last_7d": events_7d,
        "users": users,
        "sessions_active": sessions_active,
        "skills": {
            "total": len(skills),
            "installed": sum(1 for s in skills if s["installed"]),
            "builtin": sum(1 for s in skills if s["origin"] == "builtin"),
            "library": sum(1 for s in skills if s["origin"] != "builtin"),
        },
        # In-memory only: the chat store resets on restart, so this
        # counts messages held this run, not all messages ever.
        "messages_in_memory": sum(len(v) for v in chat.values()),
    }


def read_skills(app) -> list[dict]:
    """Installed skills: the same list the hub serves."""
    return hub_api.list_skills(app)


def admin_page_context(app, user_id: str) -> dict:
    """Server-rendered context for the ``/admin`` Jinja page.

    Each section is read on its own: a failing reader leaves an
    honest ``{"error": ...}`` in that section instead of taking
    the whole page down with it.
    """
    context: dict = {}
    for name, reader in (
        ("config", read_config),
        ("sessions", read_sessions),
        ("usage", read_usage),
        ("skills", lambda app, user_id: {"skills": read_skills(app)}),
    ):
        try:
            context[name] = reader(app, user_id)
        except Exception as exc:  # surfaced in the page, not hidden
            context[name] = {"error": str(exc)}
    return context


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the admin routes on ``app`` using its login resolver."""

    @app.get("/api/admin/config")
    def admin_get_config(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        if (refused := _local_only(request)) is not None:
            return refused
        try:
            return read_config(request.app, user_id)
        except (ValueError, *_db_Error) as exc:
            return _bad_request(exc)

    @app.post("/api/admin/config")
    async def admin_save_config(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        if (refused := _local_only(request)) is not None:
            return refused
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "body must be JSON"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
        unknown = sorted(set(body) - set(_CONFIG_KEYS))
        if unknown:
            return JSONResponse(
                {"error": f"unknown config key(s): {', '.join(unknown)}"}, status_code=400
            )
        if not body:
            return JSONResponse({"error": "nothing to save"}, status_code=400)
        try:
            if "instance_name" in body:
                admin_settings.save_instance_name(body["instance_name"], dsn=_dsn(request))
            if "model" in body:
                if not isinstance(body["model"], dict):
                    raise ValueError("model must be a JSON object")
                model_settings.save_selection(user_id, body["model"], dsn=_dsn(request))
            if "telemetry_enabled" in body:
                if not isinstance(body["telemetry_enabled"], bool):
                    raise ValueError("telemetry_enabled must be a boolean")
                save_telemetry_settings(user_id, body["telemetry_enabled"], dsn=_dsn(request))
            return read_config(request.app, user_id)
        except (ValueError, *_db_Error) as exc:
            return _bad_request(exc)

    @app.get("/api/admin/sessions")
    def admin_get_sessions(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        if (refused := _local_only(request)) is not None:
            return refused
        try:
            return read_sessions(request.app, user_id)
        except (ValueError, *_db_Error) as exc:
            return _bad_request(exc)

    @app.post("/api/admin/sessions/{session_id}/revoke")
    def admin_revoke_session(request: Request, session_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        if (refused := _local_only(request)) is not None:
            return refused
        try:
            revoked = revoke_session_by_id(session_id, user_id, dsn=_dsn(request))
        except (ValueError, *_db_Error):
            return JSONResponse({"error": f"no session {session_id!r}"}, status_code=404)
        if not revoked:
            return JSONResponse({"error": f"no session {session_id!r}"}, status_code=404)
        return {"ok": True, "session_id": session_id, "revoked": True}

    @app.post("/api/admin/sessions/revoke-all")
    def admin_revoke_all_sessions(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        if (refused := _local_only(request)) is not None:
            return refused
        try:
            revoke_all_sessions(user_id, dsn=_dsn(request))
        except (ValueError, *_db_Error) as exc:
            return _bad_request(exc)
        return {"ok": True, "revoked_all": True}

    @app.get("/api/admin/usage")
    def admin_get_usage(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        if (refused := _local_only(request)) is not None:
            return refused
        try:
            return read_usage(request.app, user_id)
        except (ValueError, *_db_Error) as exc:
            return _bad_request(exc)

    @app.get("/api/admin/skills")
    def admin_get_skills(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        if (refused := _local_only(request)) is not None:
            return refused
        try:
            return {"skills": read_skills(request.app)}
        except MarketplaceError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    def _toggle_skill(request: Request, skill_id: str, *, enable: bool):
        """Shared guard + registry call for the enable/disable pair."""
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        if (refused := _local_only(request)) is not None:
            return refused
        known = {s["id"]: s for s in read_skills(request.app)}
        skill = known.get(skill_id)
        if skill is None:
            return JSONResponse({"error": f"no skill {skill_id!r}"}, status_code=404)
        if skill["origin"] == "builtin":
            return JSONResponse(
                {
                    "error": (
                        f"builtin skill {skill_id!r} ships with Puspa and is always on; "
                        "only installed library skills can be enabled or disabled here"
                    )
                },
                status_code=409,
            )
        registry = hub_api.get_hub_registry(request.app)
        if registry.get_entry(skill_id) is None:
            return JSONResponse(
                {"error": f"skill {skill_id!r} is not in the skill registry"},
                status_code=404,
            )
        try:
            if enable:
                installed = registry.install(skill_id, user_id=user_id, explicit_approval=True)
                return {
                    "ok": True,
                    "skill_id": skill_id,
                    "status": "enabled",
                    "installed": installed,
                }
            record = registry.uninstall(skill_id, user_id=user_id)
            return {"ok": True, "skill_id": skill_id, "status": "disabled", "installed": record}
        except MarketplaceError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/admin/skills/{skill_id}/enable")
    def admin_enable_skill(request: Request, skill_id: str):
        return _toggle_skill(request, skill_id, enable=True)

    @app.post("/api/admin/skills/{skill_id}/disable")
    def admin_disable_skill(request: Request, skill_id: str):
        return _toggle_skill(request, skill_id, enable=False)

    return app
