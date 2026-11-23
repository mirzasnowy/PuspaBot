"""HTTP layer for shared rooms on the local PWA (broken-rooms fix).

The React PWA lists Rooms as a screen and fetches ``/api/rooms``
plus the per-room detail, board, memory, invite, and job routes —
but no local route existed for any of them, so ``/rooms`` answered
raw ``{"detail": "Not Found"}`` and the Tasks screen's shared board
failed with a 404. This module mounts those routes on the local
app, backed by the public ``puspa.rooms`` stores (rooms, task
board, room memory, display names). Nothing here calls a hosted
service: a self-hosted user gets real rooms from their own
database, and an empty room list is an honest ``{"rooms": []}``.

Deliberately NOT here: delegation (a board task's ``delegation``
is always null locally) and money splits — those live in the
hosted service. Invites and the per-room job queue are the small
local versions of the same tables (``room_invites``,
``room_job_queue``): a link invite (7 days) or a one-time 6-digit
code (24 hours), hash-only secrets, single use; a job is either a
deterministic chat summary or a recorded note — no model call.

Contract (all routes require login; ``need_user`` is the app's
own login resolver, passed in like every other PWA API module):

- ``GET /api/rooms`` -> ``{"rooms": [...]}``: id, name, and the
  caller's role per room, oldest first.
- ``POST /api/rooms`` (JSON ``{"name"}``) -> the created room,
  caller as owner.
- ``POST /api/rooms/join`` (JSON ``{"token"}`` or ``{"code"}``)
  -> ``{"room_id", "invite_id", "role", "already_member"}``.
- ``GET /api/rooms/{room_id}`` -> the room with its members
  (display names resolved) plus ``names`` / ``bot_names`` maps.
  403 for a non-member, 404 for an unknown room.
- ``DELETE /api/rooms/{room_id}`` -> owner only.
- ``GET /api/rooms/{room_id}/tasks`` -> the shared board:
  ``{"room_id", "room_name", "tasks": [...]}``, each task with
  ``delegation`` null and ``pending_steps`` 0 (no hosted
  delegation locally).
- ``POST /api/rooms/{room_id}/invites`` (JSON ``kind`` link/code,
  ``role`` member/guest) -> the invite; the token/code is
  returned exactly once here, only its hash is stored.
- ``GET /api/rooms/{room_id}/invites`` -> pending invites, no
  secrets in the list.
- ``POST /api/rooms/{room_id}/invites/{invite_id}/revoke``.
- ``POST /api/rooms/{room_id}/leave`` -> the caller leaves;
  their private room memories leave with them.
- ``POST /api/rooms/{room_id}/members/{member_id}/role`` and
  ``.../remove`` -> owner only.
- ``GET /api/rooms/{room_id}/memory`` / ``POST .../memory`` /
  ``POST .../memory/bring`` / ``DELETE .../memory/{memory_id}``.
- ``GET /api/rooms/{room_id}/jobs`` / ``POST .../jobs`` /
  ``POST .../jobs/{job_id}/run`` -> the serial per-room queue.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from puspa.db import connect as _db_connect
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from puspa.db import dict_row

from puspa.accounts.ports import resolve_dsn
from puspa.i18n.server_lines import render as render_server_line
from puspa.pwa.memory_api import MemoryReadService
from puspa.pwa.rooms_jobs import enqueue_job, get_job, job_to_dict, list_jobs, run_job
from puspa.rooms import (
    RoomMemoryStore,
    RoomStore,
    RoomTaskStore,
    resolve_memory_authors,
    resolve_room_names,
)
from puspa.rooms.errors import (
    GuestReadOnlyError,
    InviteError,
    LastOwnerError,
    NotRoomMemberError,
    NotRoomOwnerError,
    RoomFullError,
    RoomNotFoundError,
)
from puspa.rooms.rooms import MAX_ROOM_SIZE, ROLE_GUEST, ROLE_MEMBER, _validate_uuid

__all__ = [
    "board_task_to_dict",
    "invite_to_dict",
    "member_to_dict",
    "memory_to_dict",
    "register_routes",
    "room_to_dict",
]

#: Link invites live 7 days, one-time codes 24 hours (same TTLs
#: as the hosted rooms service, so a link issued locally behaves
#: like one issued there).
INVITE_LINK_TTL_DAYS = 7
CODE_TTL_HOURS = 24
JOIN_LINK_BASE = "https://puspa.app/join"
INVITE_ROLES = (ROLE_MEMBER, ROLE_GUEST)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _new_code() -> str:
    """A 6-digit one-time code (zero-padded, may start with 0)."""
    return f"{secrets.randbelow(1_000_000):06d}"


def room_to_dict(room, *, role: str | None = None) -> dict:
    """One room, with the caller's role when known."""
    return {
        "id": room.id,
        "name": room.name,
        "created_by": room.created_by,
        "created_at": _iso(room.created_at),
        "role": role,
    }


def member_to_dict(member, *, display_name: str | None = None) -> dict:
    row = {
        "user_id": member.user_id,
        "role": member.role,
        "joined_at": _iso(member.joined_at),
    }
    if display_name is not None:
        row["display_name"] = display_name
    return row


def board_task_to_dict(task) -> dict:
    """One board task. Delegation is hosted-only, so a local task
    always carries ``delegation`` null and zero pending steps."""
    return {
        "id": task.id,
        "room_id": task.room_id,
        "title": task.title,
        "owner_user_id": task.owner_user_id,
        "due_at": _iso(task.due_at),
        "status": task.status,
        "created_by": task.created_by,
        "created_at": _iso(task.created_at),
        "completed_at": _iso(task.completed_at),
        "delegation": None,
        "pending_steps": 0,
    }


def memory_to_dict(
    memory, *, author_display_name: str | None = None, rendered_content: str | None = None
) -> dict:
    row = {
        "id": memory.id,
        "room_id": memory.room_id,
        "author_user_id": memory.author_user_id,
        "kind": memory.kind,
        "content": memory.content,
        "visibility": memory.visibility,
        "created_at": _iso(memory.created_at),
    }
    if author_display_name is not None:
        row["author_display_name"] = author_display_name
    if rendered_content is not None:
        row["rendered_content"] = rendered_content
    return row


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


@dataclass(frozen=True)
class RoomInvite:
    """One room invitation: only the secret's hash is stored."""

    id: str
    room_id: str
    inviter_user_id: str
    kind: str
    role: str
    status: str
    created_at: datetime
    expires_at: datetime


def _invite_row(row) -> RoomInvite:
    return RoomInvite(
        id=str(row["id"]),
        room_id=str(row["room_id"]),
        inviter_user_id=str(row["inviter_user_id"]),
        kind=row["kind"],
        role=row["role"],
        status=row["status"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
    )


def _connect(dsn=None):
    return _db_connect(resolve_dsn(dsn), row_factory=dict_row)


def _emit_activity(request: Request, room_id: str, kind: str, actor: str, summary: str) -> None:
    """Append a room-activity event for the SSE stream. Best
    effort: a missing event list must never fail the request."""
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


# -- Invites (hash-only secrets, one table, two kinds) --------------
#
# The invite rules: a link token lives 7 days, a one-time code 24
# hours; accepting consumes the invite in the same transaction
# that inserts the membership, so one invite can never mint two
# memberships; accepting twice (or while already a member) still
# yields exactly one membership row and reports already_member.


def _issue_invite(dsn, room_id: str, *, inviter_user_id: str, kind: str, role: str) -> dict:
    rooms = RoomStore(dsn)
    room_id = _validate_uuid(room_id, "room_id")
    inviter = _validate_uuid(inviter_user_id, "inviter_user_id")
    if role not in INVITE_ROLES:
        raise ValueError(f"invite role must be one of {INVITE_ROLES}: {role!r}")
    if not rooms.can_invite(room_id, inviter):
        raise NotRoomMemberError(f"user {inviter} may not invite into room {room_id}")
    if kind == "link":
        secret = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + timedelta(days=INVITE_LINK_TTL_DAYS)
    elif kind == "code":
        secret = _new_code()
        expires_at = datetime.now(UTC) + timedelta(hours=CODE_TTL_HOURS)
    else:
        raise ValueError(f"unknown invite kind: {kind!r}")
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO room_invites "
            "(room_id, inviter_user_id, kind, role, token_hash, expires_at) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (room_id, inviter, kind, role, _sha256_hex(secret), expires_at),
        )
        invite_id = str(cur.fetchone()["id"])
        conn.commit()
    issued = {"invite_id": invite_id, "expires_at": expires_at}
    if kind == "link":
        issued["token"] = secret
        issued["join_link"] = f"{JOIN_LINK_BASE.rstrip('/')}/{secret}"
    else:
        issued["code"] = secret
    return issued


def _list_invites(dsn, room_id: str) -> list[RoomInvite]:
    room_id = _validate_uuid(room_id, "room_id")
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM room_invites WHERE room_id = %s ORDER BY created_at DESC, id",
            (room_id,),
        )
        return [_invite_row(row) for row in cur.fetchall()]


def _revoke_invite(dsn, invite_id: str, *, revoked_by: str) -> RoomInvite:
    rooms = RoomStore(dsn)
    invite_id = _validate_uuid(invite_id, "invite_id")
    revoked_by = _validate_uuid(revoked_by, "revoked_by")
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM room_invites WHERE id = %s", (invite_id,))
        row = cur.fetchone()
        if row is None:
            raise InviteError(f"invite not found: {invite_id}")
        invite = _invite_row(row)
        if not rooms.can_invite(invite.room_id, revoked_by):
            raise NotRoomMemberError(
                f"user {revoked_by} may not revoke invites of room {invite.room_id}"
            )
        if invite.status == "pending":
            cur.execute(
                "UPDATE room_invites SET status = %s WHERE id = %s RETURNING *",
                ("revoked", invite_id),
            )
            invite = _invite_row(cur.fetchone())
        conn.commit()
    return invite


def _accept_invite(dsn, secret: str, *, user_id: str, kind: str) -> dict:
    rooms = RoomStore(dsn)
    user_id = _validate_uuid(user_id, "user_id")
    if not isinstance(secret, str) or not secret:
        raise InviteError("invite unknown, expired, or revoked")
    now = datetime.now(UTC)
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM room_invites WHERE token_hash = %s AND kind = %s",
            (_sha256_hex(secret), kind),
        )
        row = cur.fetchone()
        if row is None:
            raise InviteError("invite unknown, expired, or revoked")
        invite = _invite_row(row)
        if invite.status == "revoked":
            raise InviteError("invite was revoked")
        if invite.status == "accepted":
            # Idempotent for the user who already accepted (a
            # double-click or a retried request). A different user
            # presenting a spent invite is refused.
            if rooms.is_member(invite.room_id, user_id):
                return {
                    "room_id": invite.room_id,
                    "invite_id": invite.id,
                    "role": invite.role,
                    "already_member": True,
                }
            raise InviteError("invite was already accepted")
        if invite.status == "expired" or invite.expires_at <= now:
            cur.execute(
                "UPDATE room_invites SET status = %s WHERE id = %s",
                ("expired", invite.id),
            )
            conn.commit()
            raise InviteError("invite expired")
        already_member = rooms.is_member(invite.room_id, user_id)
        if not already_member:
            cur.execute(
                "SELECT COUNT(*) FROM room_members WHERE room_id = %s",
                (invite.room_id,),
            )
            if int(cur.fetchone()["count"]) >= MAX_ROOM_SIZE:
                conn.commit()
                raise RoomFullError(f"room {invite.room_id} already has {MAX_ROOM_SIZE} members")
            cur.execute(
                "INSERT INTO room_members (room_id, user_id, role) VALUES (%s, %s, %s) "
                "ON CONFLICT (room_id, user_id) DO NOTHING",
                (invite.room_id, user_id, invite.role),
            )
        cur.execute(
            "UPDATE room_invites SET status = %s WHERE id = %s",
            ("accepted", invite.id),
        )
        conn.commit()
    return {
        "room_id": invite.room_id,
        "invite_id": invite.id,
        "role": invite.role,
        "already_member": already_member,
    }


# -- Routes ----------------------------------------------------------


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the local room routes on ``app`` using its login resolver."""

    def _unauthorized():
        return JSONResponse({"error": render_server_line("error.not_logged_in")}, status_code=401)

    def _rooms(request: Request) -> RoomStore:
        return RoomStore(request.app.state.dsn)

    def _tasks(request: Request) -> RoomTaskStore:
        return RoomTaskStore(request.app.state.dsn)

    def _memory(request: Request) -> RoomMemoryStore:
        return RoomMemoryStore(request.app.state.dsn)

    def _require_member(store: RoomStore, room_id: str, user_id: str):
        """404 for an unknown room, 403 for a non-member."""
        store.require_room(room_id)
        if not store.is_member(room_id, user_id):
            raise NotRoomMemberError(f"user {user_id} is not a member of room {room_id}")

    @app.get("/api/rooms")
    def list_rooms_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        store = _rooms(request)
        try:
            rooms = store.list_rooms_for_user(user_id)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {
            "rooms": [
                {"id": room.id, "name": room.name, "role": store.role_of(room.id, user_id)}
                for room in rooms
            ]
        }

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
            dsn = request.app.state.dsn
            if data.get("token"):
                accepted = _accept_invite(dsn, str(data["token"]), user_id=user_id, kind="link")
            elif data.get("code"):
                accepted = _accept_invite(dsn, str(data["code"]), user_id=user_id, kind="code")
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
            names = resolve_room_names(room_id, request.app.state.dsn, room_store=store)
        except RoomNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except NotRoomMemberError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {
            **room_to_dict(room, role=store.role_of(room_id, user_id)),
            "members": [
                member_to_dict(m, display_name=names.names.get(m.user_id)) for m in names.members
            ],
            "names": names.names,
            "bot_names": names.bot_names,
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

    @app.get("/api/rooms/{room_id}/tasks")
    def board_endpoint(request: Request, room_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            room = _rooms(request).require_room(room_id)
            tasks = _tasks(request).list_tasks(room_id, viewer_user_id=user_id)
        except RoomNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except NotRoomMemberError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {
            "room_id": room.id,
            "room_name": room.name,
            "tasks": [board_task_to_dict(task) for task in tasks],
        }

    @app.post("/api/rooms/{room_id}/invites")
    async def issue_invite_endpoint(request: Request, room_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("invite body must be a JSON object")
            _rooms(request).require_room(room_id)
            kind = str(data.get("kind") or "link")
            role = str(data.get("role") or "member")
            issued = _issue_invite(
                request.app.state.dsn,
                room_id,
                inviter_user_id=user_id,
                kind=kind,
                role=role,
            )
        except NotRoomMemberError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except RoomNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
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
            invites = _list_invites(request.app.state.dsn, room_id)
        except RoomNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except NotRoomMemberError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
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
            invite = _revoke_invite(request.app.state.dsn, invite_id, revoked_by=user_id)
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
            named = resolve_memory_authors(rows, request.app.state.dsn)
        except RoomNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except NotRoomMemberError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {
            "room_id": room_id,
            "memories": [
                memory_to_dict(
                    row,
                    author_display_name=named.author_display_name,
                    rendered_content=named.content,
                )
                for row, named in zip(rows, named)
            ],
        }

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
        room-visible note with a provenance line. The personal fact
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
            jobs = list_jobs(request.app.state.dsn, room_id, viewer_user_id=user_id)
        except RoomNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except NotRoomMemberError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
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
            job = enqueue_job(
                request.app.state.dsn,
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
        try:
            job = get_job(request.app.state.dsn, job_id)
            if job is None or job.room_id != room_id:
                return JSONResponse({"error": f"job not found: {job_id}"}, status_code=404)
            finished = run_job(request.app.state.dsn, job_id, actor_user_id=user_id)
        except RoomNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except (NotRoomMemberError, GuestReadOnlyError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        _emit_activity(request, room_id, "job_done", user_id, f"room job {finished.kind} finished")
        return job_to_dict(finished)

    return app
