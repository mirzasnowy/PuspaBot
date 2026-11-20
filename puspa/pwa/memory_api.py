"""Memory screen read API (P-01, plan section 12.6).

The "What Puspa knows about me" screen: read endpoints for stored
memory plus the actions that make data-subject rights visible —
access (list + export), correction (edit a fact, confirm or reject
an inferred proposal), deletion ("forget this" per fact), and
consent withdrawal — plus guest mode (a turn that saves no memory)
and room memory kept separate from personal memory.

What already existed, and what this module adds:

- The fact store already existed (``puspa.memory.facts``:
  versioned profile facts with supersede / expire / restore, and
  ``puspa.memory.profile``: add / confirm / reject / soft-delete).
  This module does NOT re-store facts: it reads through those
  stores and exposes them over HTTP with a source label and a
  confidence per fact.
- The graph view already existed (B18, ``puspa.pwa.graph_api``):
  nodes/edges endpoints and graph edits. This module does NOT
  touch the graph; it covers the facts list the graph view never
  showed.
- The full user export already existed
  (``puspa.ledger.export.build_user_export``). This module wires
  it to ``GET /api/memory/export`` as a download instead of
  rebuilding it.
- Room memory already existed as a service
  (``puspa.rooms.memory``: membership-gated rows in their own
  table, never mixed with personal memory). This module adds the
  HTTP routes for it — there were none — and the screen shows
  room memory in its own section.
- Nothing existed for confidence on a stored fact, for a
  human-readable source ("from the chat on 3 November 2026"),
  for hard-deleting one fact, for consent withdrawal, or for
  guest mode. Those are what P-01 adds (migration 0023,
  ``facts.forget_fact`` / ``record_fact`` confidence, the service
  below, and the guest-mode flags on the memory provider and the
  turn-loop intercepts).

Confidence rule (stated openly, not hidden): a stored confidence
wins when one was recorded. Otherwise a fact the user stated or
confirmed is certain by definition (1.0), and an inferred fact
with no stored score is an unverified guess (0.5). The response
marks derived values with ``confidence_stored: false`` so the
screen can show them differently from measured scores.

Ledger: corrections, forgets, and consent withdrawals are
recorded as ``evidence_recorded`` events (the closed set of 31
revision-7 types stays closed) on one task per user,
``memory-rights-<user_id>``, with content ``{"kind":
"memory_right", "operation": ..., "fact_id": ...}``. No fact
content is ever written to a rights event: a "forget this" that
kept a copy of the fact in the audit trail would not be a
forget. This differs deliberately from B18 graph corrections,
which record before/after detail — graph edits are corrections,
not deletions.

Ordering limit: a fact correction commits to Postgres before its
ledger record is written, so a ledger failure leaves the
correction without its audit entry (the fact itself is never
lost). A forget deletes first for the same reason: the deletion
is the point, the audit entry is secondary.

Guest mode is process-local (an in-memory set on this service,
like the B18 graph cache): restarting the server ends every
guest turn. The flag itself only gates the service's own
reporting; the writes it protects are refused at the two real
write funnels — ``DefaultPgVectorProvider.store`` and
``InterceptedTools`` — which the PWA chat never reaches through
this module alone.
"""

from __future__ import annotations

import json
from dataclasses import asdict

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row

from ..i18n.server_lines import render as render_server_line
from ..ledger import Ledger
from ..memory import facts as facts_module
from ..memory import profile as profile_module
from ..memory.facts import PostgresFactStore
from ..memory.schema import resolve_dsn
from ..rooms import memory as room_memory_module
from ..rooms.errors import NotRoomMemberError

__all__ = [
    "MemoryReadService",
    "display_confidence",
    "memory_rights_task_id",
    "register_routes",
    "source_label",
]

#: Page-size cap for the facts list (same bound as the B18 graph read).
MAX_LIMIT = 100
DEFAULT_LIMIT = 50

#: Closed ledger event type for rights actions (no new type added).
RIGHTS_EVENT_TYPE = "evidence_recorded"

#: Statuses the facts list treats as history rather than live memory.
HISTORY_STATUSES = ("superseded", "expired", "replaced", "rejected")

#: Months spelled out (no locale dependence in the source label).
_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

_SOURCE_VERBS = {
    "user_stated": "you told Puspa",
    "user_confirmed": "you confirmed",
    "inferred": "Puspa inferred",
}


def memory_rights_task_id(user_id: str) -> str:
    """Ledger task holding one user's memory-rights events."""
    return f"memory-rights-{user_id}"


def _format_date(value) -> str | None:
    """Format a datetime as "3 November 2026" (English, no locale)."""
    if value is None:
        return None
    if isinstance(value, str):
        return value[:10]
    try:
        return f"{value.day} {_MONTHS[value.month - 1]} {value.year}"
    except (AttributeError, IndexError, TypeError):
        return None


def source_label(fact: dict) -> str:
    """Human-readable provenance for one fact.

    A stored ``source_detail`` wins (written at record time, for
    example "from the chat on 3 November 2026"). Otherwise the
    label is built from the fact's source and creation date, for
    example "you told Puspa on 3 November 2026". An unconfirmed
    inference says so plainly.
    """
    detail = fact.get("source_detail")
    if detail:
        return str(detail)
    verb = _SOURCE_VERBS.get(fact.get("source"), "recorded")
    date = _format_date(fact.get("created_at"))
    label = f"{verb} on {date}" if date else verb
    if fact.get("source") == "inferred" and fact.get("status") == "proposed":
        label += ", waiting for your confirmation"
    return label


def display_confidence(fact: dict) -> float:
    """Confidence to show for one fact (see the module docstring).

    A stored confidence wins. A user-stated or user-confirmed fact
    is certain by definition (1.0). An inferred fact with no
    stored score is an unverified guess (0.5).
    """
    stored = fact.get("confidence")
    if stored is not None:
        return float(stored)
    if fact.get("source") in ("user_stated", "user_confirmed"):
        return 1.0
    return 0.5


def _fact_view(fact: dict) -> dict:
    """One fact as the screen shows it: content, source, confidence."""
    return {
        "id": str(fact["id"]),
        "key": fact["key"],
        "content": fact.get("content") or fact.get("value"),
        "status": fact["status"],
        "source": fact["source"],
        "source_label": source_label(fact),
        "confidence": display_confidence(fact),
        "confidence_stored": fact.get("confidence") is not None,
        "valid_from": fact.get("valid_from"),
        "valid_until": fact.get("valid_until"),
        "event_id": str(fact["event_id"]) if fact.get("event_id") else None,
        "agent_id": fact.get("agent_id"),
        "created_at": fact.get("created_at"),
        "updated_at": fact.get("updated_at") or fact.get("created_at"),
    }


def _parse_int(raw: str | None, name: str, default: int) -> int:
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _parse_body(data) -> dict:
    if not isinstance(data, dict):
        raise ValueError("request body must be a JSON object")
    return data


class MemoryReadService:
    """Read endpoints and rights actions over the real memory stores."""

    def __init__(self, dsn=None, ledger: Ledger | None = None, fact_store=None):
        self.dsn = resolve_dsn(dsn)
        self._ledger_override = ledger
        self._fact_store = fact_store
        # Guest-mode users, process-local: a restart ends every
        # guest turn (same lifetime as the B18 graph cache).
        self._guest_users: set[str] = set()

    def _ledger(self) -> Ledger:
        if self._ledger_override is None:
            self._ledger_override = Ledger(self.dsn)
        return self._ledger_override

    def _store(self):
        if self._fact_store is not None:
            return self._fact_store
        return PostgresFactStore(self.dsn)

    # -- guest mode ----------------------------------------------------

    def is_guest(self, user_id: str) -> bool:
        """Whether this user is currently in a guest turn."""
        return str(user_id) in self._guest_users

    def set_guest(self, user_id: str, guest: bool) -> bool:
        """Start or end a guest turn. Returns the new guest state."""
        if guest:
            self._guest_users.add(str(user_id))
        else:
            self._guest_users.discard(str(user_id))
        return bool(guest)

    # -- facts: access -------------------------------------------------

    def list_facts(
        self,
        user_id: str,
        *,
        status: str | None = None,
        include_history: bool = False,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> dict:
        """One page of the user's facts, with source + confidence.

        Default view is live memory (active + proposed). ``status``
        takes one exact status, or ``"history"`` for every past
        version. ``limit`` is clamped to 100, ``offset`` pages
        through the list.
        """
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if offset < 0:
            raise ValueError("offset must be at least 0")
        limit = min(limit, MAX_LIMIT)
        if status == "history":
            statuses = HISTORY_STATUSES
        elif status is not None:
            if status not in facts_module.STATUSES:
                raise ValueError(f"unknown status: {status!r}")
            statuses = (status,)
        elif include_history:
            statuses = None
        else:
            statuses = ("active", "proposed")
        store = self._store()
        rows = store.list_user(str(user_id), statuses=statuses)
        total = len(rows)
        page = rows[offset : offset + limit]
        return {
            "facts": [_fact_view(row) for row in page],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def get_fact(self, user_id: str, fact_id: str) -> dict:
        """One fact plus every past version of the same key."""
        store = self._store()
        fact = store.get(str(fact_id), str(user_id))
        if fact is None:
            raise KeyError(f"fact not found: {fact_id}")
        history = store.list_for_key(str(user_id), fact["key"], agent_id=fact.get("agent_id"))
        return {
            "fact": _fact_view(fact),
            "history": [_fact_view(row) for row in history],
        }

    # -- facts: correction and deletion -------------------------------

    def _record_rights_event(
        self, user_id: str, operation: str, *, fact_id: str | None = None
    ) -> dict:
        """Audit one rights action. Content carries ids only, never
        fact content (see the module docstring)."""
        content: dict = {"kind": "memory_right", "operation": operation}
        if fact_id is not None:
            content["fact_id"] = str(fact_id)
        return self._ledger().append_event(
            task_id=memory_rights_task_id(str(user_id)),
            agent_id="puspa",
            event_type=RIGHTS_EVENT_TYPE,
            content=content,
        )

    def correct_fact(self, user_id: str, fact_id: str, content: str) -> dict:
        """Correct a fact: the old version is superseded, the new
        one is recorded as user-confirmed. Raises KeyError for an
        unknown fact and ValueError for empty content."""
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content must be a non-empty string")
        result = facts_module.supersede_fact(
            str(user_id),
            str(fact_id),
            replacement_content=content.strip(),
            source="user_confirmed",
            store=self._store(),
        )
        self._record_rights_event(user_id, "correct_fact", fact_id=fact_id)
        return {"fact": _fact_view(result["new"]), "previous_id": str(result["old"]["id"])}

    def forget_fact(self, user_id: str, fact_id: str) -> dict:
        """Forget one fact ("forget this"): hard-delete the fact and
        all its past versions. Raises KeyError for an unknown fact."""
        result = facts_module.forget_fact(str(user_id), str(fact_id), store=self._store())
        self._record_rights_event(user_id, "forget_fact", fact_id=fact_id)
        return {
            "forgotten_ids": result["forgotten_ids"],
            "key": result["key"],
            "forgotten": len(result["forgotten_ids"]),
        }

    def confirm_proposal(self, user_id: str, fact_id: str) -> dict:
        """Confirm an inferred proposal: it enters the profile."""
        fact = profile_module.confirm_proposal(str(fact_id), str(user_id), dsn=self.dsn)
        self._record_rights_event(user_id, "confirm_proposal", fact_id=fact_id)
        return {"fact": _fact_view(fact)}

    def reject_proposal(self, user_id: str, fact_id: str) -> dict:
        """Reject an inferred proposal: it stays on record as rejected."""
        fact = profile_module.reject_proposal(str(fact_id), str(user_id), dsn=self.dsn)
        self._record_rights_event(user_id, "reject_proposal", fact_id=fact_id)
        return {"fact": _fact_view(fact)}

    # -- consent -------------------------------------------------------

    def consent_status(self, user_id: str) -> dict:
        """The user's data-consent state: granted, withdrawn, or none."""
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT text_version, approved_at, withdrawn_at FROM data_consents "
                "WHERE user_id = %s ORDER BY approved_at DESC LIMIT 1",
                (str(user_id),),
            )
            row = cur.fetchone()
        if row is None:
            return {"status": "none", "text_version": None, "approved_at": None}
        if row["withdrawn_at"] is not None:
            return {
                "status": "withdrawn",
                "text_version": row["text_version"],
                "approved_at": row["approved_at"],
                "withdrawn_at": row["withdrawn_at"],
            }
        return {
            "status": "granted",
            "text_version": row["text_version"],
            "approved_at": row["approved_at"],
            "withdrawn_at": None,
        }

    def withdraw_consent(self, user_id: str) -> dict:
        """Withdraw data consent: marks the latest consent row
        withdrawn and audits the withdrawal. Withdrawing stops
        future storage; it does not delete stored data (that is
        the forget / account-delete path). Raises KeyError when
        the user never gave consent. Idempotent: withdrawing twice
        reports the same withdrawn state."""
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE data_consents SET withdrawn_at = now() "
                    "WHERE id = (SELECT id FROM data_consents WHERE user_id = %s "
                    "ORDER BY approved_at DESC LIMIT 1) "
                    "AND withdrawn_at IS NULL RETURNING text_version, approved_at, withdrawn_at",
                    (str(user_id),),
                )
                row = cur.fetchone()
            conn.commit()
        if row is None:
            status = self.consent_status(user_id)
            if status["status"] == "none":
                raise KeyError(f"no consent recorded for user {user_id}")
            return status
        self._record_rights_event(user_id, "withdraw_consent")
        return {
            "status": "withdrawn",
            "text_version": row["text_version"],
            "approved_at": row["approved_at"],
            "withdrawn_at": row["withdrawn_at"],
        }

    # -- export --------------------------------------------------------

    def export_memory(self, user_id: str) -> dict:
        """The user's full export document (wired, not rebuilt:
        ``puspa.ledger.export.build_user_export``). Raises
        ValueError for an unknown user."""
        from puspa.ledger.export import build_user_export

        return build_user_export(str(user_id), dsn=self.dsn)

    # -- room memory: separate from personal memory ------------------

    def list_room_memory(self, room_id: str, *, viewer_user_id: str) -> dict:
        """Room memory visible to one member: every room-visible row
        plus the viewer's own private rows. Personal memory is
        never read here and never appears in the result."""
        rows = room_memory_module.list_room_memory(
            room_id, viewer_user_id=viewer_user_id, dsn=self.dsn
        )
        return {
            "room_id": str(room_id),
            "memories": [asdict(row) for row in rows],
            "total": len(rows),
        }

    def record_room_memory(
        self, room_id: str, *, author_user_id: str, content: str, kind: str, visibility: str
    ) -> dict:
        """Record one room-memory row (member-only, attributed to
        the author)."""
        row = room_memory_module.record_memory(
            room_id,
            author_user_id=author_user_id,
            content=content,
            kind=kind,
            visibility=visibility,
            dsn=self.dsn,
        )
        return {"memory": asdict(row)}

    def delete_room_memory(self, memory_id: str, *, deleted_by: str) -> dict:
        """Delete one room-memory row (author, or owner for a
        room-visible row). Returns whether a row was deleted."""
        deleted = room_memory_module.delete_memory(memory_id, deleted_by=deleted_by, dsn=self.dsn)
        return {"deleted": deleted}

    # -- screen summary ------------------------------------------------

    def screen(self, user_id: str, **kwargs) -> dict:
        """Everything the Memory screen shows in one payload:
        facts, proposals, consent state, and guest mode."""
        page = self.list_facts(user_id, **kwargs)
        proposals = self.list_facts(user_id, status="proposed", limit=MAX_LIMIT)
        return {
            "facts": page["facts"],
            "total_facts": page["total"],
            "limit": page["limit"],
            "offset": page["offset"],
            "proposals": proposals["facts"],
            "consent": self.consent_status(user_id),
            "guest_mode": self.is_guest(user_id),
        }


def _service(request: Request) -> MemoryReadService:
    service = getattr(request.app.state, "memory_service", None)
    if service is None:
        return MemoryReadService(dsn=request.app.state.dsn)
    return service


def _unauthorized():
    return JSONResponse({"error": render_server_line("error.not_logged_in")}, status_code=401)


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the memory-screen routes on ``app`` using its login resolver."""

    @app.get("/api/memory")
    def memory_endpoint(
        request: Request,
        limit: str | None = None,
        offset: str | None = None,
        status: str | None = None,
        include_history: str | None = None,
    ):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            return _service(request).screen(
                user_id,
                limit=_parse_int(limit, "limit", DEFAULT_LIMIT),
                offset=_parse_int(offset, "offset", 0),
                status=status,
                include_history=include_history == "true",
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.get("/api/memory/facts/{fact_id}")
    def fact_endpoint(request: Request, fact_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            return _service(request).get_fact(user_id, fact_id)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.patch("/api/memory/facts/{fact_id}")
    async def correct_fact_endpoint(request: Request, fact_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            data = _parse_body(await request.json())
            return _service(request).correct_fact(user_id, fact_id, data.get("content", ""))
        except (ValueError, KeyError) as exc:
            status_code = 404 if isinstance(exc, KeyError) else 400
            return JSONResponse({"error": str(exc)}, status_code=status_code)

    @app.delete("/api/memory/facts/{fact_id}")
    def delete_fact_endpoint(request: Request, fact_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            return _service(request).forget_fact(user_id, fact_id)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.post("/api/memory/facts/{fact_id}/forget")
    def forget_fact_endpoint(request: Request, fact_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            return _service(request).forget_fact(user_id, fact_id)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.post("/api/memory/facts/{fact_id}/confirm")
    def confirm_proposal_endpoint(request: Request, fact_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            return _service(request).confirm_proposal(user_id, fact_id)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.post("/api/memory/facts/{fact_id}/reject")
    def reject_proposal_endpoint(request: Request, fact_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            return _service(request).reject_proposal(user_id, fact_id)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.get("/api/memory/export")
    def export_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            document = _service(request).export_memory(user_id)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        return JSONResponse(
            content=json.loads(json.dumps(document, default=str)),
            headers={"Content-Disposition": 'attachment; filename="puspa-memory-export.json"'},
        )

    @app.get("/api/memory/consent")
    def consent_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        return _service(request).consent_status(user_id)

    @app.post("/api/memory/consent/withdraw")
    def withdraw_consent_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            return _service(request).withdraw_consent(user_id)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.post("/api/memory/guest")
    async def guest_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            data = _parse_body(await request.json())
            guest = data.get("guest")
            if not isinstance(guest, bool):
                raise ValueError("guest must be true or false")
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"guest_mode": _service(request).set_guest(user_id, guest)}

    @app.get("/api/memory/rooms/{room_id}")
    def room_memory_endpoint(request: Request, room_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            return _service(request).list_room_memory(room_id, viewer_user_id=user_id)
        except NotRoomMemberError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/memory/rooms/{room_id}/memories")
    async def record_room_memory_endpoint(request: Request, room_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            data = _parse_body(await request.json())
            return _service(request).record_room_memory(
                room_id,
                author_user_id=user_id,
                content=data.get("content", ""),
                kind=data.get("kind", "note"),
                visibility=data.get("visibility", "room"),
            )
        except NotRoomMemberError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.delete("/api/memory/rooms/{room_id}/memories/{memory_id}")
    def delete_room_memory_endpoint(request: Request, room_id: str, memory_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            return _service(request).delete_room_memory(memory_id, deleted_by=user_id)
        except NotRoomMemberError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    return app
