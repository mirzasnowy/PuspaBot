"""HTTP layer for shared rooms lite (P-05, plan section 12.1):
rooms, membership, invites, room memory, and the per-room job
queue.

Plain handler functions over :mod:`puspa.rooms` services, plus
:func:`register_routes`, which mounts them on a FastAPI app. This
module deliberately does not import ``puspa.pwa.app``: the
coordinator wires it into ``create_app`` with a few lines::

    from puspa.pwa import rooms_membership_api
    rooms_membership_api.register_routes(app, need_user)

``need_user`` is the app's own login resolver (``Request`` ->
user id or None); it is passed in rather than reimplemented here,
so login behaviour stays in exactly one place. The task-board and
split routes live in :mod:`puspa.pwa.rooms_api` (B9/B11); this
module owns the room itself.

Contract (all responses are JSON; all routes require login):

- ``POST /api/rooms`` (JSON: ``name``) -> the room the caller
  just created, with their role (``owner``).
- ``GET /api/rooms/{room_id}`` -> the room with its members and
  the caller's role. 403 for a non-member, 404 for an unknown room.
- ``DELETE /api/rooms/{room_id}`` -> owner only.
- ``POST /api/rooms/{room_id}/invites`` (JSON: ``kind`` ``link``
  or ``code``, ``role`` ``member`` or ``guest``) -> the invite;
  the token/code is returned exactly once here, only its hash is
  stored. Owner/member only; guests may not invite.
- ``GET /api/rooms/{room_id}/invites`` -> pending invites
  (no secrets in the list). Owner/member only.
- ``POST /api/rooms/{room_id}/invites/{invite_id}/revoke`` ->
  the revoked invite. Owner/member only.
- ``POST /api/rooms/join`` (JSON: ``token`` or ``code``) -> the
  room joined, with the role granted. The token is scoped to its
  room: it can only ever join that one room. 400 for an unknown,
  expired, revoked, or already-spent invite, or a full room.
- ``POST /api/rooms/{room_id}/leave`` -> the caller leaves; their
  private room memories leave with them.
- ``POST /api/rooms/{room_id}/members/{user_id}/role`` (JSON:
  ``role``) -> owner only; demoting the last owner is refused.
- ``POST /api/rooms/{room_id}/members/{user_id}/remove`` ->
  owner only; the removed member loses access at once (their
  membership row is gone in the same transaction as their
  private data), so every later read is a 403.
- ``GET /api/rooms/{room_id}/memory`` -> room memory visible to
  the caller: every room-visible row plus their own private rows.
- ``POST /api/rooms/{room_id}/memory`` (JSON: ``kind``,
  ``content``, ``visibility`` ``room`` or ``private``) -> the
  recorded row. Guests are read-only and get 403.
- ``POST /api/rooms/{room_id}/memory/bring`` (JSON:
  ``fact_id``) -> "bring to room": the caller's own personal
  memory fact, copied into the room as a room-visible note, with
  a provenance line. Explicit per item — personal memory is never
  copied wholesale, and the fact must belong to the caller.
- ``DELETE /api/rooms/{room_id}/memory/{memory_id}`` -> author,
  or owner for a room-visible row.
- ``GET /api/rooms/{room_id}/jobs`` -> the room's job queue,
  oldest first. Any member may read.
- ``POST /api/rooms/{room_id}/jobs`` (JSON: ``kind``,
  ``payload``) -> the queued job. Owner/member only.
- ``POST /api/rooms/{room_id}/jobs/{job_id}/run`` -> run one
  queued job now, serialised by the room lock, and return the
  finished job with its result.

Room activity: mutations append a ``room_activity`` event to the
app's in-memory event list (``app.state.event``), which the
existing ``/sse`` stream serves to the Rooms screen — the same
stream the Office screen already uses. An event carries no
secret: no invite token or code is ever written into one.
"""

from __future__ import annotations

import time
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..i18n.server_lines import render as render_server_line
from ..rooms import (
    RoomInviteStore,
    RoomJobQueue,
    RoomMemoryStore,
    RoomStore,
)
from ..rooms.errors import (
    GuestReadOnlyError,
    InviteError,
    LastOwnerError,
    NotRoomMemberError,
    NotRoomOwnerError,
    RoomFullError,
    RoomNotFoundError,
)
from .memory_api import MemoryReadService

__all__ = [
    "invite_to_dict",
    "job_to_dict",
    "member_to_dict",
    "memory_to_dict",
    "register_routes",
    "room_to_dict",
]


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def room_to_dict(room, *, role: str | None = None) -> dict:
    """One room, with the caller's role when known."""
    return {
        "id": room.id,
        "name": room.name,
        "created_by": room.created_by,
        "created_at": _iso(room.created_at),
        "role": role,
    }


def member_to_dict(member) -> dict:
    return {
        "user_id": member.user_id,
        "role": member.role,
        "joined_at": _iso(member.joined_at),
    }


def invite_to_dict(invite) -> dict:
    """One invite as listed: never carries the token or code."""
    return {
        "id": invite.id,
        "room_id": invite.room_id,
        "inviter_user_id": invite.inviter_user_id,
        "kind": invite.kind,
        "role": invite.role,
        "status": invite.status,
        "created_at": _iso(invite.created_at),
        "expires_at": _iso(invite.expires_at),
    }


def memory_to_dict(memory) -> dict:
    return {
        "id": memory.id,
        "room_id": memory.room_id,
        "author_user_id": memory.author_user_id,
        "kind": memory.kind,
        "content": memory.content,
        "visibility": memory.visibility,
        "created_at": _iso(memory.created_at),
    }


def job_to_dict(job) -> dict:
    return {
        "id": job.id,
        "room_id": job.room_id,
        "kind": job.kind,
        "payload": job.payload,
        "status": job.status,
        "requested_by": job.requested_by,
        "result": job.result,
        "error": job.error,
        "created_at": _iso(job.created_at),
        "started_at": _iso(job.started_at),
        "finished_at": _iso(job.finished_at),
    }


def _emit_activity(request: Request, room_id: str, kind: str, actor: str, summary: str) -> None:
    """Append a room-activity event for the SSE stream. Best
    effort: a missing event list (an app built without one) must
    never fail the request that caused the activity."""
    events = getattr(request.app.state, "event", None)
    if not isinstance(events, list):
        return
    events.append(
        {
            "type": "room_activity",
            "room_id": room_id,
            "kind": kind,
            "actor": actor,
            "summary": summary,
            "created_at": time.time(),
        }
    )


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the room-membership routes on ``app`` using its login resolver."""

    def _unauthorized():
        return JSONResponse({"error": render_server_line("error.not_logged_in")}, status_code=401)

    def _rooms(request: Request) -> RoomStore:
        return RoomStore(request.app.state.dsn)

    def _invites(request: Request) -> RoomInviteStore:
        return RoomInviteStore(request.app.state.dsn)

    def _memory(request: Request) -> RoomMemoryStore:
        return RoomMemoryStore(request.app.state.dsn)

    def _jobs(request: Request) -> RoomJobQueue:
        return RoomJobQueue(request.app.state.dsn)

    def _require_member(store: RoomStore, room_id: str, user_id: str):
        """404 for an unknown room, 403 for a non-member."""
        store.require_room(room_id)
        if not store.is_member(room_id, user_id):
            raise NotRoomMemberError(f"user {user_id} is not a member of room {room_id}")

    @app.post("/api/rooms")
    async def create_room_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("room body must be a JSON object")
            room = _rooms(request).create_room(str(data.get("name") or ""), user_id)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        _emit_activity(request, room.id, "room_created", user_id, f"room {room.name} created")
        return room_to_dict(room, role="owner")

    # Join is registered before the /api/rooms/{room_id} routes so
    # the literal "join" is never captured as a room id.
    @app.post("/api/rooms/join")
    async def join_room_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("join body must be a JSON object")
            store = _invites(request)
            if data.get("token"):
                accepted = store.accept_invite(str(data["token"]), user_id=user_id)
            elif data.get("code"):
                accepted = store.accept_invite_code(str(data["code"]), user_id=user_id)
            else:
                raise ValueError("join needs a token or a code")
        except (InviteError, RoomFullError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        _emit_activity(
            request, accepted["room_id"], "member_joined", user_id, "a member joined the room"
        )
        return accepted

    @app.get("/api/rooms/{room_id}")
    def room_detail_endpoint(request: Request, room_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        store = _rooms(request)
        try:
            _require_member(store, room_id, user_id)
            room = store.require_room(room_id)
        except RoomNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except NotRoomMemberError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        return {
            **room_to_dict(room, role=store.role_of(room_id, user_id)),
            "members": [member_to_dict(m) for m in store.list_members(room_id)],
        }

    @app.delete("/api/rooms/{room_id}")
    def delete_room_endpoint(request: Request, room_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            _rooms(request).delete_room(room_id, requested_by=user_id)
        except RoomNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except NotRoomOwnerError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"deleted": True, "room_id": room_id}

    @app.post("/api/rooms/{room_id}/invites")
    async def issue_invite_endpoint(request: Request, room_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        store = _invites(request)
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("invite body must be a JSON object")
            _rooms(request).require_room(room_id)
            kind = str(data.get("kind") or "link")
            role = str(data.get("role") or "member")
            if kind == "link":
                issued = store.issue_invite_link(room_id, inviter_user_id=user_id, role=role)
            elif kind == "code":
                issued = store.issue_invite_code(room_id, inviter_user_id=user_id, role=role)
            else:
                raise ValueError(f"unknown invite kind: {kind!r}")
        except NotRoomMemberError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except (RoomNotFoundError, ValueError) as exc:
            status = 404 if isinstance(exc, RoomNotFoundError) else 400
            return JSONResponse({"error": str(exc)}, status_code=status)
        _emit_activity(request, room_id, "invite_issued", user_id, "an invite was issued")
        return {**issued, "kind": kind, "role": role}

    @app.get("/api/rooms/{room_id}/invites")
    def list_invites_endpoint(request: Request, room_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        store = _rooms(request)
        try:
            _require_member(store, room_id, user_id)
            if store.role_of(room_id, user_id) == "guest":
                raise NotRoomMemberError("guests may not list invites")
        except RoomNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except NotRoomMemberError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        invites = _invites(request).list_invites(room_id)
        return {"room_id": room_id, "invites": [invite_to_dict(i) for i in invites]}

    @app.post("/api/rooms/{room_id}/invites/{invite_id}/revoke")
    def revoke_invite_endpoint(request: Request, room_id: str, invite_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        store = _rooms(request)
        try:
            _require_member(store, room_id, user_id)
            if store.role_of(room_id, user_id) == "guest":
                raise NotRoomMemberError("guests may not revoke invites")
            invite = _invites(request).revoke_invite(invite_id, revoked_by=user_id)
            if invite.room_id != room_id:
                return JSONResponse({"error": f"invite not found: {invite_id}"}, status_code=404)
        except RoomNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except NotRoomMemberError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except InviteError as exc:
            # Here InviteError only means an unknown invite id.
            return JSONResponse({"error": str(exc)}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return invite_to_dict(invite)

    @app.post("/api/rooms/{room_id}/leave")
    def leave_room_endpoint(request: Request, room_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            _rooms(request).leave_room(room_id, user_id=user_id)
        except RoomNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except LastOwnerError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        _emit_activity(request, room_id, "member_left", user_id, "a member left the room")
        return {"left": True, "room_id": room_id}

    @app.post("/api/rooms/{room_id}/members/{member_id}/role")
    async def set_role_endpoint(request: Request, room_id: str, member_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("role body must be a JSON object")
            member = _rooms(request).set_role(
                room_id, member_id, str(data.get("role") or ""), changed_by=user_id
            )
        except RoomNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except (NotRoomMemberError, NotRoomOwnerError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except (LastOwnerError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        _emit_activity(
            request, room_id, "role_changed", user_id, f"member role changed to {member.role}"
        )
        return member_to_dict(member)

    @app.post("/api/rooms/{room_id}/members/{member_id}/remove")
    def remove_member_endpoint(request: Request, room_id: str, member_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            _rooms(request).remove_member(room_id, member_id, removed_by=user_id)
        except RoomNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except (NotRoomMemberError, NotRoomOwnerError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except (LastOwnerError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        _emit_activity(request, room_id, "member_removed", user_id, "a member was removed")
        return {"removed": True, "room_id": room_id, "user_id": member_id}

    @app.get("/api/rooms/{room_id}/memory")
    def list_memory_endpoint(request: Request, room_id: str, kind: str | None = None):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            _rooms(request).require_room(room_id)
            rows = _memory(request).list_room_memory(room_id, viewer_user_id=user_id, kind=kind)
        except RoomNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except NotRoomMemberError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"room_id": room_id, "memories": [memory_to_dict(m) for m in rows]}

    @app.post("/api/rooms/{room_id}/memory")
    async def add_memory_endpoint(request: Request, room_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("memory body must be a JSON object")
            _rooms(request).require_room(room_id)
            row = _memory(request).record_memory(
                room_id,
                author_user_id=user_id,
                content=str(data.get("content") or ""),
                kind=str(data.get("kind") or "note"),
                visibility=str(data.get("visibility") or "room"),
            )
        except RoomNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except (NotRoomMemberError, GuestReadOnlyError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        _emit_activity(request, room_id, "memory_added", user_id, "a room note was added")
        return memory_to_dict(row)

    @app.post("/api/rooms/{room_id}/memory/bring")
    async def bring_memory_endpoint(request: Request, room_id: str):
        """Bring one personal memory item into the room, explicitly.

        The fact must belong to the caller (``get_fact`` only ever
        returns the caller's own facts), and it lands as a
        room-visible note with a provenance line, so the room can
        see it was brought in — never silently. The personal fact
        itself is untouched.
        """
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("bring body must be a JSON object")
            fact_id = str(data.get("fact_id") or "")
            if not fact_id:
                raise ValueError("fact_id is required")
            _rooms(request).require_room(room_id)
            fact = MemoryReadService(request.app.state.dsn).get_fact(user_id, fact_id)
            content = str((fact["fact"] or {}).get("content") or "")
            if not content.strip():
                raise ValueError("that memory item has no content to bring")
            row = _memory(request).record_memory(
                room_id,
                author_user_id=user_id,
                content=content.strip() + "\n\n(Dibawa dari memori pribadi saya.)",
                kind="note",
                visibility="room",
            )
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except RoomNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except (NotRoomMemberError, GuestReadOnlyError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        _emit_activity(request, room_id, "memory_brought", user_id, "a memory was brought in")
        return {**memory_to_dict(row), "brought_from_fact_id": fact_id}

    @app.delete("/api/rooms/{room_id}/memory/{memory_id}")
    def delete_memory_endpoint(request: Request, room_id: str, memory_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            # Scope the delete to this room: the row must be visible
            # to the caller inside this room, so a memory id from
            # another room can never be deleted through this path.
            _rooms(request).require_room(room_id)
            visible = _memory(request).list_room_memory(room_id, viewer_user_id=user_id)
            if memory_id not in {row.id for row in visible}:
                return JSONResponse({"error": f"memory not found: {memory_id}"}, status_code=404)
            deleted = _memory(request).delete_memory(memory_id, deleted_by=user_id)
        except RoomNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except NotRoomMemberError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"deleted": deleted, "room_id": room_id, "memory_id": memory_id}

    @app.get("/api/rooms/{room_id}/jobs")
    def list_jobs_endpoint(request: Request, room_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            jobs = _jobs(request).list_jobs(room_id, viewer_user_id=user_id)
        except RoomNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except NotRoomMemberError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        return {"room_id": room_id, "jobs": [job_to_dict(j) for j in jobs]}

    @app.post("/api/rooms/{room_id}/jobs")
    async def enqueue_job_endpoint(request: Request, room_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("job body must be a JSON object")
            payload = data.get("payload")
            if payload is None:
                payload = {}
            job = _jobs(request).enqueue(
                room_id,
                str(data.get("kind") or ""),
                payload,
                requested_by=user_id,
            )
        except RoomNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except (NotRoomMemberError, GuestReadOnlyError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return job_to_dict(job)

    @app.post("/api/rooms/{room_id}/jobs/{job_id}/run")
    def run_job_endpoint(request: Request, room_id: str, job_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        queue = _jobs(request)
        try:
            job = queue.get_job(job_id)
            if job is None or job.room_id != room_id:
                return JSONResponse({"error": f"job not found: {job_id}"}, status_code=404)
            finished = queue.run_job(job_id, actor_user_id=user_id)
        except RoomNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except (NotRoomMemberError, GuestReadOnlyError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        _emit_activity(request, room_id, "job_done", user_id, f"room job {finished.kind} finished")
        return job_to_dict(finished)

    return app
