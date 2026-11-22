"""Missions API (``puspa/pwa/missions_api.py``, V2-42 issue #219).

Routes (all require login, mounted by ``puspa.pwa.app``),
except the webhook wake, whose token in the URL is the
credential:

- ``POST /api/missions`` — ``{"goal": ..., "steps": [...],
  "title": ...}`` creates a mission; a step is a title string
  or ``{"title", "question", "options"}``.
- ``GET  /api/missions`` — the caller's missions, newest first.
- ``GET  /api/missions/{mission_id}`` — one mission with its
  checklist (step titles, statuses, results, open question).
- ``POST /api/missions/{mission_id}/run`` — queue the next
  step now: an immediate one-shot scheduler trigger, so the
  serve loop runs it in the background. The PWA never runs a
  turn inline.
- ``POST /api/missions/{mission_id}/answer`` — ``{"answer",
  "go"}`` answers the open step-ask; ``go: false`` declines
  and stops the mission.
- ``POST /api/missions/{mission_id}/cancel`` — stop a mission.
- ``POST /api/missions/{mission_id}/schedule`` — ``{"schedule"}``
  registers the mission on the scheduler (a schedule dict or
  a short string like ``"every 2h"``).
- ``POST /api/missions/{mission_id}/webhook/rotate`` — a fresh
  webhook token; the old one stops working at once.
- ``POST /api/missions/webhook/{token}`` — no login: wake the
  mission with an outside payload (recorded in the ledger)
  and queue its next step.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..i18n.server_lines import render as render_server_line
from ..missions.missions import (
    MissionError,
    cancel_mission,
    create_mission,
    create_schema,
    get_mission,
    list_missions,
)
from ..missions.runner import answer_step_ask
from ..missions.webhooks import rotate_webhook_token, wake_mission_by_token

__all__ = ["register_routes"]


def _dsn(request: Request):
    if not getattr(request.app.state, "missions_schema_ready", False):
        with contextlib.suppress(Exception):
            create_schema(request.app.state.dsn)
        request.app.state.missions_schema_ready = True
    return request.app.state.dsn


def _unauthorized():
    return JSONResponse({"error": render_server_line("error.not_logged_in")}, status_code=401)


async def _json_body(request: Request):
    try:
        body = await request.json()
    except Exception:
        return None
    return body if isinstance(body, dict) else None


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the missions routes on ``app`` using its login resolver."""

    @app.post("/api/missions/webhook/{token}")
    async def wake_mission(request: Request, token: str):
        body = await _json_body(request)
        payload = body if body is not None else {}
        try:
            outcome = wake_mission_by_token(token, payload, dsn=_dsn(request))
        except MissionError:
            return JSONResponse({"error": "unknown webhook token"}, status_code=404)
        return {"ok": True, **outcome}

    @app.post("/api/missions")
    async def create(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        body = await _json_body(request)
        if body is None:
            return JSONResponse({"error": "body must be JSON"}, status_code=400)
        try:
            mission = create_mission(
                user_id,
                body.get("goal", ""),
                body.get("steps", []),
                title=body.get("title"),
                dsn=_dsn(request),
            )
        except MissionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return mission.to_dict()

    @app.get("/api/missions")
    def list_all(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        return {"missions": [m.to_dict() for m in list_missions(user_id, dsn=_dsn(request))]}

    @app.get("/api/missions/{mission_id}")
    def get_one(request: Request, mission_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            return get_mission(mission_id, user_id, dsn=_dsn(request)).to_dict()
        except MissionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.post("/api/missions/{mission_id}/run")
    async def run_now(request: Request, mission_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            mission = get_mission(mission_id, user_id, dsn=_dsn(request))
        except MissionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        if mission.status == "waiting":
            return JSONResponse(
                {"error": "mission is waiting for an answer; answer its question first"},
                status_code=400,
            )
        if mission.status in ("done", "failed", "stopped"):
            return JSONResponse({"error": f"mission is already {mission.status}"}, status_code=400)
        from puspa.scheduler.mission_triggers import schedule_mission
        from puspa.scheduler.scheduler import SchedulerError

        try:
            trigger = schedule_mission(
                user_id,
                mission_id,
                {
                    "type": "once",
                    "at": (datetime.now(UTC) + timedelta(seconds=10)).isoformat(),
                },
                dsn=_dsn(request),
            )
        except SchedulerError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"queued": True, "mission_id": mission_id, "trigger_id": trigger.id}

    @app.post("/api/missions/{mission_id}/answer")
    async def answer(request: Request, mission_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        body = await _json_body(request)
        if body is None:
            return JSONResponse({"error": "body must be JSON"}, status_code=400)
        go = body.get("go", True)
        if not isinstance(go, bool):
            return JSONResponse({"error": "go must be true or false"}, status_code=400)
        try:
            get_mission(mission_id, user_id, dsn=_dsn(request))
        except MissionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        try:
            mission = answer_step_ask(
                mission_id, user_id, answer=body.get("answer", ""), go=go, dsn=_dsn(request)
            )
        except MissionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return mission.to_dict()

    @app.post("/api/missions/{mission_id}/cancel")
    def cancel(request: Request, mission_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            get_mission(mission_id, user_id, dsn=_dsn(request))
        except MissionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        try:
            return cancel_mission(mission_id, user_id, dsn=_dsn(request)).to_dict()
        except MissionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/missions/{mission_id}/schedule")
    async def schedule(request: Request, mission_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        body = await _json_body(request)
        if body is None or "schedule" not in body:
            return JSONResponse({"error": "schedule is required"}, status_code=400)
        try:
            get_mission(mission_id, user_id, dsn=_dsn(request))
        except MissionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        from puspa.scheduler.mission_triggers import schedule_mission
        from puspa.scheduler.scheduler import SchedulerError

        try:
            trigger = schedule_mission(user_id, mission_id, body.get("schedule"), dsn=_dsn(request))
        except SchedulerError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {
            "scheduled": True,
            "mission_id": mission_id,
            "trigger_id": trigger.id,
            "next_trigger_at": trigger.next_trigger_at.isoformat(),
        }

    @app.post("/api/missions/{mission_id}/webhook/rotate")
    def rotate(request: Request, mission_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            token = rotate_webhook_token(mission_id, user_id, dsn=_dsn(request))
        except MissionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        return {"webhook_token": token}

    return app
