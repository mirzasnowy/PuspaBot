"""HTTP layer for the morning summary + notes-to-actions (P-07,
plan section 12.8).

Contract (all routes require login):

- ``GET /api/morning/status`` -> per-source availability:
  calendar + gmail (from the stored Google token and its granted
  scopes — Calendar is not connected yet, so the summary says so
  instead of pretending), the voice transcriber (still the stub;
  see ``GET /api/voice/status``), and the two public sources
  (prayer times, holidays) with their names.
- ``GET /api/morning/summary?date=YYYY-MM-DD&city=Jakarta`` ->
  the day's schedule, bill candidates, open tasks, holidays, and
  prayer times. Each section carries its own status; a source
  that fails degrades its own section, never the whole summary.
  Imsak appears only during Ramadan.
- ``POST /api/morning/notes/text`` (JSON ``text``, ``source``,
  ``title``) -> extract actions, store the note, create one
  tracked task per action. 400 on empty text / unknown source.
- ``POST /api/morning/notes/voice`` (multipart ``audio``,
  optional ``source``/``title``) -> transcribe through the P-03
  voice pipeline, then the same as the text route. The audio is
  deleted right after transcribing, like ``/api/voice/upload``.
  400 on a bad recording.
- ``GET /api/morning/notes`` -> the user's notes, newest first.
- ``GET /api/morning/notes/{note_id}`` -> one note with its full
  text. 404 for an unknown or foreign id.

Test seams (same pattern as the transcriber in
:mod:`puspa.pwa.voice_api`): ``app.state.morning_connector_factory``
(a ``user_id -> GoogleConnector`` callable) and
``app.state.morning_http_client`` (an ``httpx.Client`` for the
public sources) are read when present, so tests inject fakes
instead of touching Google or the network.
"""

from __future__ import annotations

import datetime
from typing import Annotated

import httpx
import psycopg
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

from ..accounts.vault import VaultError
from ..auth.google_oauth import load_tokens
from ..connectors.google import SCOPES, ConnectorError, GoogleConnector
from ..i18n.server_lines import render as render_server_line
from ..morning.holidays import HolidaySourceError, fetch_public_holidays
from ..morning.notes import NOTE_SOURCES, get_note, list_notes, save_note
from ..morning.prayer import JAKARTA_TZ, PrayerSourceError, fetch_prayer_times
from ..morning.summary import (
    BILL_QUERY,
    MAX_BILL_READS,
    build_morning_summary,
    find_bill_candidates,
    open_tasks_for_user,
)
from ..voice.pipeline import AudioValidationError, VoicePipeline

__all__ = ["register_routes"]

#: Default city for prayer times (the user is in Indonesia; the
#: caller may pass another city with ``?city=``).
DEFAULT_CITY = "Jakarta"


def _not_logged_in() -> JSONResponse:
    return JSONResponse({"error": render_server_line("error.not_logged_in")}, status_code=401)


def _focus_recorder(dsn, user_id: str, day: datetime.date):
    """Best-effort ledger recorder for the morning focus broadcast.

    Returns a K18 bus recorder (see
    :func:`puspa.mind.workspace.ledger_recorder`) that appends
    one ``task_completed`` event per briefing under
    ``morning-focus-<user>-<date>``. A database that cannot take
    the event (down, or the ledger tables missing) records
    nothing and returns None — the briefing is a read, so a
    failed audit write must never fail the summary itself.
    """
    from ..ledger import Ledger
    from ..ledger.errors import LedgerError

    def record(broadcast_record: dict) -> str | None:
        try:
            event = Ledger(dsn).append_event(
                task_id=f"morning-focus-{user_id}-{day.isoformat()}",
                agent_id="puspa",
                event_type="task_completed",
                content={
                    "job": "workspace_broadcast",
                    "user_id": user_id,
                    **broadcast_record,
                },
            )
        except (psycopg.Error, LedgerError):
            return None
        return str(event["id"])

    return record


def _google_source_status(user_id: str, dsn) -> dict:
    """Calendar + Gmail availability from the stored Google token.

    The token dict (vault ``google_oauth``) carries the granted
    scopes as a space-separated ``scope`` string when Google
    returned one. A service whose scope is absent is
    ``not_connected``; a token with no recorded scopes is reported
    as ``unknown`` rather than guessed either way.
    """
    try:
        tokens = load_tokens(user_id, dsn=dsn)
    except (psycopg.Error, VaultError):
        return {
            "calendar": {
                "status": "unavailable",
                "detail": "Could not check the Google connection (vault or database error).",
            },
            "gmail": {
                "status": "unavailable",
                "detail": "Could not check the Google connection (vault or database error).",
            },
        }
    if not tokens:
        detail = "Google is not connected yet. Connect Google to read calendar events and email."
        return {
            "calendar": {"status": "not_connected", "detail": detail},
            "gmail": {"status": "not_connected", "detail": detail},
        }
    scope_text = str(tokens.get("scope") or "")
    if not scope_text:
        detail = (
            "Google is connected, but the granted scopes were not "
            "recorded, so it is unknown whether Calendar and Gmail "
            "are available. Reconnect Google to record them."
        )
        return {
            "calendar": {"status": "unknown", "detail": detail},
            "gmail": {"status": "unknown", "detail": detail},
        }
    granted = set(scope_text.split())
    out = {}
    for service in ("calendar", "gmail"):
        wanted = set(SCOPES[service])
        if wanted & granted:
            out[service] = {
                "status": "connected",
                "detail": f"Google {service} scope granted.",
            }
        else:
            out[service] = {
                "status": "not_connected",
                "detail": (
                    f"Google is connected, but the {service} scope was "
                    "not granted. Reconnect Google with that scope."
                ),
            }
    return out


def _connector_for(request: Request, user_id: str) -> GoogleConnector:
    factory = getattr(request.app.state, "morning_connector_factory", None)
    if factory is not None:
        return factory(user_id)
    return GoogleConnector(user_id, "puspa", dsn=request.app.state.dsn)


def _http_client(request: Request) -> httpx.Client | None:
    return getattr(request.app.state, "morning_http_client", None)


def _day_bounds(day: datetime.date) -> tuple[str, str]:
    """One day in Jakarta time, as RFC 3339 bounds for Calendar."""
    start = datetime.datetime.combine(day, datetime.time.min, tzinfo=JAKARTA_TZ)
    end = datetime.datetime.combine(day, datetime.time.max, tzinfo=JAKARTA_TZ)
    return start.isoformat(), end.isoformat()


def _event_dict(event: dict) -> dict:
    start = event.get("start") or {}
    end = event.get("end") or {}
    return {
        "summary": str(event.get("summary") or "(no title)"),
        "start": str(start.get("dateTime") or start.get("date") or ""),
        "end": str(end.get("dateTime") or end.get("date") or ""),
        "location": str(event.get("location") or ""),
    }


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the morning routes on ``app`` using its login resolver."""
    transcriber = getattr(app.state, "transcriber", None)
    if transcriber is None:
        from ..voice.transcriber import resolve_transcriber

        transcriber = resolve_transcriber("stub")
    pipeline = VoicePipeline(transcriber)

    @app.get("/api/morning/status")
    async def morning_status(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        google = _google_source_status(user_id, request.app.state.dsn)
        return {
            "calendar": google["calendar"],
            "gmail": google["gmail"],
            "transcriber": {
                "engine": transcriber.name,
                "engine_selected": False,
                "note": (
                    "No speech-to-text engine is chosen yet; voice notes "
                    "are transcribed by the test-double transcriber until "
                    "the 50-recording word error rate measurement exists."
                ),
            },
            "prayer": {
                "status": "public",
                "detail": "Prayer times come from the Aladhan API (method 20, Kementerian Agama RI); checked on each summary, no account needed.",
            },
            "holidays": {
                "status": "public",
                "detail": "National holidays come from the Nager.Date API; checked on each summary, no account needed.",
            },
        }

    @app.get("/api/morning/summary")
    async def morning_summary(request: Request, date: str = "", city: str = DEFAULT_CITY):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        if date:
            try:
                day = datetime.date.fromisoformat(date)
            except ValueError:
                return JSONResponse({"error": "date must be YYYY-MM-DD"}, status_code=400)
        else:
            day = datetime.datetime.now(JAKARTA_TZ).date()
        city = (city or DEFAULT_CITY).strip() or DEFAULT_CITY
        dsn = request.app.state.dsn
        http_client = _http_client(request)
        google = _google_source_status(user_id, dsn)
        source_status = dict(google)

        # -- schedule (Calendar) -------------------------------------
        events: list[dict] | None = None
        if google["calendar"]["status"] == "connected":
            try:
                time_min, time_max = _day_bounds(day)
                raw = _connector_for(request, user_id).calendar_read(
                    time_min=time_min, time_max=time_max
                )
                events = [_event_dict(event) for event in raw]
                source_status["calendar"] = {
                    "status": "ok",
                    "detail": f"{len(events)} event(s) today.",
                }
            except (ConnectorError, httpx.HTTPError) as exc:
                events = None
                source_status["calendar"] = {
                    "status": "unavailable",
                    "detail": f"Calendar could not be read: {exc}",
                }
        else:
            events = None

        # -- bills due (Gmail candidates) ------------------------------
        bill_candidates: list[dict] | None = None
        if google["gmail"]["status"] == "connected":
            try:
                connector = _connector_for(request, user_id)
                found = connector.gmail_search(BILL_QUERY, max_results=10)
                messages = []
                for item in found[:MAX_BILL_READS]:
                    messages.append(connector.gmail_read(str(item.get("id", ""))))
                bill_candidates = find_bill_candidates(messages)
                source_status["gmail"] = {
                    "status": "ok",
                    "detail": f"{len(bill_candidates)} bill candidate(s) from email.",
                }
            except (ConnectorError, httpx.HTTPError) as exc:
                bill_candidates = None
                source_status["gmail"] = {
                    "status": "unavailable",
                    "detail": f"Gmail could not be read: {exc}",
                }
        else:
            bill_candidates = None

        # -- open tasks (task store) ------------------------------------
        open_tasks: list[dict] | None = None
        try:
            open_tasks = open_tasks_for_user(user_id, dsn=dsn)
            source_status["tasks"] = {
                "status": "ok",
                "detail": f"{len(open_tasks)} open task(s).",
            }
        except psycopg.Error:
            open_tasks = None
            source_status["tasks"] = {
                "status": "unavailable",
                "detail": "Tasks could not be read (database error).",
            }

        # -- national holidays (public source) --------------------------
        holidays: list[dict] | None = None
        try:
            holidays = fetch_public_holidays(day.year, http_client=http_client)
            source_status["holidays"] = {"status": "ok", "detail": "Holidays loaded."}
        except HolidaySourceError as exc:
            holidays = None
            source_status["holidays"] = {"status": "unavailable", "detail": str(exc)}

        # -- prayer times (public source) -------------------------------
        prayer: dict | None = None
        try:
            prayer = fetch_prayer_times(city=city, day=day, http_client=http_client)
            source_status["prayer"] = {"status": "ok", "detail": "Prayer times loaded."}
        except PrayerSourceError as exc:
            prayer = None
            source_status["prayer"] = {"status": "unavailable", "detail": str(exc)}

        return build_morning_summary(
            day=day,
            events=events,
            bill_candidates=bill_candidates,
            open_tasks=open_tasks,
            holidays=holidays,
            prayer=prayer,
            source_status=source_status,
            focus_record=_focus_recorder(dsn, user_id, day),
        )

    @app.post("/api/morning/notes/text")
    async def create_note_from_text(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            data = await request.json()
        except ValueError:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        text = str(data.get("text", "") or "")
        source = str(data.get("source", "") or "text")
        title = str(data.get("title", "") or "")
        try:
            saved = save_note(
                user_id,
                source=source,
                title=title,
                raw_text=text,
                dsn=request.app.state.dsn,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"ok": True, **saved}

    @app.post("/api/morning/notes/voice")
    async def create_note_from_voice(
        request: Request,
        audio: Annotated[UploadFile, File()],
        source: Annotated[str, Form()] = "voice",
        title: Annotated[str, Form()] = "",
    ):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        if source not in NOTE_SOURCES:
            return JSONResponse({"error": f"unknown note source {source!r}"}, status_code=400)
        content = await audio.read()
        try:
            result = pipeline.transcribe_upload(content, audio.content_type or "")
        except AudioValidationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        try:
            saved = save_note(
                user_id,
                source=source,
                title=title,
                raw_text=result.transcript,
                dsn=request.app.state.dsn,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {
            "ok": True,
            "transcript": result.transcript,
            "language": result.language,
            "engine": result.engine,
            "audio_deleted": result.audio_deleted,
            **saved,
        }

    @app.get("/api/morning/notes")
    async def list_user_notes(request: Request, limit: int = 20):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            notes = list_notes(user_id, dsn=request.app.state.dsn, limit=limit)
        except psycopg.Error:
            return JSONResponse(
                {"error": "notes could not be read (database error)"}, status_code=500
            )
        return {"notes": notes}

    @app.get("/api/morning/notes/{note_id}")
    async def get_user_note(request: Request, note_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            note = get_note(user_id, note_id, dsn=request.app.state.dsn)
        except psycopg.Error:
            return JSONResponse(
                {"error": "note could not be read (database error)"}, status_code=500
            )
        if note is None:
            return JSONResponse({"error": "unknown note"}, status_code=404)
        return note

    return app
