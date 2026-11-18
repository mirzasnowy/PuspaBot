"""HTTP layer for onboarding + hints (G28).

Plain handler functions wrapping :mod:`puspa.onboarding.onboarding`
(the 4-step first-run flow) and :mod:`puspa.onboarding.hints`
(contextual one-time hints), plus :func:`register_routes`, which
mounts them on a FastAPI app. This module deliberately does not
import ``puspa.pwa.app``: the coordinator wires it into
``create_app`` later with a few lines::

    from puspa.pwa import onboarding_api
    onboarding_api.register_routes(app, need_user)

``need_user`` is the app's own login resolver (``Request`` ->
user id or None); it is passed in rather than reimplemented here,
so login behaviour stays in exactly one place. The existing
``POST /api/onboarding/waktu`` timing stub in ``app.py`` is
untouched; these routes are the real onboarding flow.

Contract (all responses are JSON; all routes require login):

- ``GET /api/onboarding`` -> the user's onboarding status, or
  ``{"started": false, "current_step": 0}`` when never started.
- ``POST /api/onboarding/start`` -> starts onboarding, returns the
  status (``{"started": true, ...}``). Idempotent: starting twice
  returns the existing status.
- ``POST /api/onboarding/step1`` ``{"name", "style",
  "example_message"?}`` -> step 1 (name + speaking style; the
  style must be one of ``santai`` / ``rapi`` / ``campur``).
- ``POST /api/onboarding/step2`` -> step 2 (notification test).
  Honest limit, inherited from the flow: Web Push is not built
  yet, so the test is recorded as NOT sent
  (``notification_sent`` is false) -- never faked as delivered.
- ``POST /api/onboarding/step3`` ``{"connector"?}`` -> step 3
  (connector choice; ``"nanti"`` by default).
- ``POST /api/onboarding/first-task`` ``{"event_type", "target"}``
  -> the guided first task. Honest limit, inherited from the
  flow: the price-compare / bill wedges are not built yet, so the
  task is a measured stub (``verified`` is false, with a reason);
  no price or bill result is ever invented.
- ``GET /api/onboarding/hints`` -> hints due right now:
  ``{"hints": [...]}``. The client reports the branch it just
  reached as query params -- ``screen``, ``first_visit``,
  ``approval_requested``, ``memory_used``, ``task_cost_idr``,
  ``billing_rows`` -- and the server returns only the unseen
  hints that branch makes due. Only branch facts are accepted;
  no other client data is stored.
- ``POST /api/onboarding/hints/{flag}/seen`` -> marks one hint
  seen (dismissed or shown); unknown flags are a 400.

Validation failures are 400 with ``{"error": ...}``; a step
called before onboarding started is also a 400. ``401
{"error": "belum masuk"}`` when not logged in.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..i18n.server_lines import render as render_server_line
from ..onboarding import hints as hints_engine
from ..onboarding import onboarding as onboarding_core

__all__ = [
    "dismiss_hint",
    "due_hints",
    "first_task",
    "get_status",
    "parse_hint_context",
    "register_routes",
    "start",
    "step1",
    "step2",
    "step3",
]


def _status_dict(status: dict | None) -> dict:
    if status is None:
        return {"started": False, "current_step": 0}
    return {"started": True, **status}


def get_status(user_id, dsn=None) -> dict:
    """Onboarding status, or ``{"started": false, ...}`` if never started."""
    return _status_dict(onboarding_core.get_status(user_id, dsn=dsn))


def start(user_id, dsn=None) -> dict:
    """Start onboarding (idempotent) and return the status."""
    return _status_dict(onboarding_core.start_onboarding(user_id, dsn=dsn))


def step1(user_id, name, style, example_message=None, dsn=None) -> dict:
    """Complete step 1: name + speaking style (+ optional examples)."""
    return _status_dict(
        onboarding_core.complete_step1(
            user_id, name, style, example_message=example_message, dsn=dsn
        )
    )


def step2(user_id, dsn=None) -> dict:
    """Complete step 2: notification test (honestly reported, see module docstring)."""
    return _status_dict(onboarding_core.complete_step2(user_id, dsn=dsn))


def step3(user_id, connector="nanti", dsn=None) -> dict:
    """Complete step 3: connector choice (``"nanti"`` by default)."""
    return _status_dict(onboarding_core.complete_step3(user_id, connector=connector, dsn=dsn))


def first_task(user_id, event_type, target, dsn=None) -> dict:
    """Run the guided first task (measured stub, see module docstring)."""
    return onboarding_core.run_first_task(user_id, event_type, target, dsn=dsn)


def due_hints(user_id, context=None, dsn=None) -> dict:
    """Hints due right now, as ``{"hints": [...]}``."""
    return {"hints": hints_engine.due_hints(user_id, context=context, dsn=dsn)}


def dismiss_hint(user_id, flag, dsn=None) -> dict:
    """Mark one hint seen. Raises ValueError for an unknown flag."""
    return hints_engine.mark_seen(user_id, flag, dsn=dsn)


def parse_hint_context(params: dict) -> dict:
    """Parse hint query params into a typed branch context.

    Accepts the flat string values a query string carries:
    ``"true"`` / ``"1"`` / ``"yes"`` (case-insensitive) for flags,
    integers for ``task_cost_idr`` / ``billing_rows``. Unknown keys
    are ignored; unparseable integers raise ValueError.
    """
    context: dict = {}
    if params.get("screen"):
        context["screen"] = str(params["screen"])
    for key in ("first_visit", "approval_requested", "memory_used"):
        if key in params:
            context[key] = str(params[key]).strip().lower() in ("true", "1", "yes")
    for key in ("task_cost_idr", "billing_rows"):
        if key in params and params[key] != "":
            try:
                context[key] = int(params[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} must be an integer, got {params[key]!r}") from exc
    return context


def _json_body(request_body) -> dict:
    if not isinstance(request_body, dict):
        raise ValueError("request body must be a JSON object")
    return request_body


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the onboarding + hints routes on ``app``."""

    def _require_user(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return None, JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        return user_id, None

    def _bad_request(exc: Exception):
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.get("/api/onboarding")
    def onboarding_status_endpoint(request: Request):
        user_id, error = _require_user(request)
        if error is not None:
            return error
        return get_status(user_id, dsn=app.state.dsn)

    @app.post("/api/onboarding/start")
    def onboarding_start_endpoint(request: Request):
        user_id, error = _require_user(request)
        if error is not None:
            return error
        try:
            return start(user_id, dsn=app.state.dsn)
        except Exception as exc:
            return _bad_request(exc)

    @app.post("/api/onboarding/step1")
    async def onboarding_step1_endpoint(request: Request):
        user_id, error = _require_user(request)
        if error is not None:
            return error
        try:
            data = _json_body(await request.json())
            return step1(
                user_id,
                data.get("name"),
                data.get("style"),
                example_message=data.get("example_message"),
                dsn=app.state.dsn,
            )
        except (ValueError, KeyError) as exc:
            return _bad_request(exc)

    @app.post("/api/onboarding/step2")
    def onboarding_step2_endpoint(request: Request):
        user_id, error = _require_user(request)
        if error is not None:
            return error
        try:
            return step2(user_id, dsn=app.state.dsn)
        except (ValueError, KeyError) as exc:
            return _bad_request(exc)

    @app.post("/api/onboarding/step3")
    async def onboarding_step3_endpoint(request: Request):
        user_id, error = _require_user(request)
        if error is not None:
            return error
        try:
            data = _json_body(await request.json())
            return step3(user_id, connector=data.get("connector", "nanti"), dsn=app.state.dsn)
        except (ValueError, KeyError) as exc:
            return _bad_request(exc)

    @app.post("/api/onboarding/first-task")
    async def onboarding_first_task_endpoint(request: Request):
        user_id, error = _require_user(request)
        if error is not None:
            return error
        try:
            data = _json_body(await request.json())
            return first_task(
                user_id, data.get("event_type"), data.get("target"), dsn=app.state.dsn
            )
        except (ValueError, KeyError) as exc:
            return _bad_request(exc)

    @app.get("/api/onboarding/hints")
    def onboarding_hints_endpoint(request: Request):
        user_id, error = _require_user(request)
        if error is not None:
            return error
        try:
            context = parse_hint_context(dict(request.query_params))
        except ValueError as exc:
            return _bad_request(exc)
        return due_hints(user_id, context=context, dsn=app.state.dsn)

    @app.post("/api/onboarding/hints/{flag}/seen")
    def onboarding_hint_seen_endpoint(request: Request, flag: str):
        user_id, error = _require_user(request)
        if error is not None:
            return error
        try:
            return dismiss_hint(user_id, flag, dsn=app.state.dsn)
        except ValueError as exc:
            return _bad_request(exc)

    return app
