"""HTTP layer for the shared task board (B9).

Plain handler functions over :mod:`puspa.rooms.delegation`, plus
:func:`register_routes`, which mounts them on a FastAPI app. This
module deliberately does not import ``puspa.pwa.app``: the
coordinator wires it into ``create_app`` later with a few lines::

    from puspa.pwa import rooms_api
    rooms_api.register_routes(app, need_user)

``need_user`` is the app's own login resolver (``Request`` ->
user id or None); it is passed in rather than reimplemented here,
so login behaviour stays in exactly one place.

Contract (all responses are JSON; all routes require login):

- ``GET /api/rooms`` -> the logged-in user's rooms
  (``[{id, name, role}]``).
- ``GET /api/rooms/{room_id}/tasks`` -> the shared board:
  every task of the room with owner, deadline, status, its
  delegation (or null when never delegated), and how many steps
  wait for approval or execution. 403 for a non-member, 404
  for an unknown room.
- ``POST /api/rooms/{room_id}/tasks`` (JSON: ``title``,
  optional ``owner_user_id``, ``due_at`` ISO timestamp,
  ``delegate_kind`` + ``assignee_ref``) -> create the task on
  the B6 board and, when delegation fields are given, hand it
  to the agent or a teammate at once. 400 on bad input.
- ``POST /api/rooms/{room_id}/tasks/{task_id}/steps`` (JSON:
  ``description``, ``tool_name``, optional ``irreversible``)
  -> propose a step. An irreversible step without an approval
  policy wired in waits for approval and can never run
  (fail-closed; see ``puspa.rooms.delegation``).
- ``POST /api/rooms/{room_id}/tasks/{task_id}/steps/{step_id}/approve``
  and ``.../reject`` -> the task owner approves or rejects a
  step that waits for approval. 403 when the caller is not the
  task owner.

No approval policy or gate is wired into the HTTP layer in this
iteration: steps proposed here go through the service with no
policy, so irreversible steps stay ``awaiting_approval`` until
the owner decides them through the service with a policy wired
in (or a later ticket wires the policy into the app). That is
the safe default — the API never approves its own steps.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..i18n.server_lines import render as render_server_line
from ..rooms import DelegationService, NotRoomMemberError, RoomNotFoundError
from ..rooms.delegation import BoardRow
from ..rooms.tasks import RoomTask

__all__ = ["board_to_dict", "get_board", "list_rooms", "register_routes"]


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _task_dict(row: BoardRow) -> dict:
    task: RoomTask = row.task
    delegation = row.delegation
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
        "delegation": None
        if delegation is None
        else {
            "delegate_kind": delegation.delegate_kind,
            "assignee_ref": delegation.assignee_ref,
            "status": delegation.status,
            "last_report": delegation.last_report,
            "reported_at": _iso(delegation.reported_at),
            "started_at": _iso(delegation.started_at),
            "blocked_reason": delegation.blocked_reason,
        },
        "pending_steps": row.pending_steps,
    }


def board_to_dict(room_id: str, room_name: str, rows: list[BoardRow]) -> dict:
    """The board payload served by ``GET /api/rooms/{id}/tasks``."""
    return {
        "room_id": room_id,
        "room_name": room_name,
        "tasks": [_task_dict(row) for row in rows],
    }


def list_rooms(user_id: str, *, dsn=None, service: DelegationService | None = None) -> list[dict]:
    """The user's rooms with their role in each."""
    service = service or DelegationService(dsn)
    rooms = service.rooms.list_rooms_for_user(user_id)
    return [
        {"id": room.id, "name": room.name, "role": service.rooms.role_of(room.id, user_id)}
        for room in rooms
    ]


def get_board(
    room_id: str, *, user_id: str, dsn=None, service: DelegationService | None = None
) -> dict:
    """The shared board of one room for one member.

    Raises ``NotRoomMemberError`` for a non-member and
    ``RoomNotFoundError`` for an unknown room.
    """
    service = service or DelegationService(dsn)
    room = service.rooms.require_room(room_id)
    rows = service.board(room.id, viewer_user_id=user_id)
    return board_to_dict(room.id, room.name, rows)


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the room-board routes on ``app`` using its login resolver."""

    def _service(request: Request) -> DelegationService:
        return DelegationService(request.app.state.dsn)

    @app.get("/api/rooms")
    def rooms_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        return {"rooms": list_rooms(user_id, service=_service(request))}

    @app.get("/api/rooms/{room_id}/tasks")
    def board_endpoint(request: Request, room_id: str):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        service = _service(request)
        try:
            return get_board(room_id, user_id=user_id, service=service)
        except NotRoomMemberError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except (ValueError, RoomNotFoundError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.post("/api/rooms/{room_id}/tasks")
    async def create_task_endpoint(request: Request, room_id: str):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        service = _service(request)
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("task body must be a JSON object")
            due_at = None
            if data.get("due_at"):
                due_at = datetime.fromisoformat(str(data["due_at"]))
            task = service.tasks.add_task(
                room_id,
                title=str(data.get("title") or ""),
                created_by=user_id,
                owner_user_id=data.get("owner_user_id"),
                due_at=due_at,
            )
            delegate_kind = data.get("delegate_kind")
            if delegate_kind:
                if delegate_kind == "agent":
                    service.delegate_to_agent(
                        task.id,
                        delegated_by=user_id,
                        subagent_ref=str(data.get("assignee_ref") or ""),
                    )
                elif delegate_kind == "teammate":
                    service.delegate_to_teammate(
                        task.id,
                        delegated_by=user_id,
                        teammate_user_id=str(data.get("assignee_ref") or ""),
                    )
                else:
                    raise ValueError(f"unknown delegate_kind: {delegate_kind!r}")
            rows = service.board(room_id, viewer_user_id=user_id)
            row = next((r for r in rows if r.task.id == task.id), None)
            if row is None:  # pragma: no cover - the task was just created
                raise ValueError("created task is not on the board")
            return _task_dict(row)
        except NotRoomMemberError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/rooms/{room_id}/tasks/{task_id}/steps")
    async def propose_step_endpoint(request: Request, room_id: str, task_id: str):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        service = _service(request)
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("step body must be a JSON object")
            task = service.tasks.list_tasks(room_id, viewer_user_id=user_id)
            if not any(t.id == task_id for t in task):
                return JSONResponse({"error": f"task not found: {task_id}"}, status_code=404)
            step = service.propose_step(
                task_id,
                by=user_id,
                description=str(data.get("description") or ""),
                tool_name=str(data.get("tool_name") or ""),
                irreversible=bool(data.get("irreversible", False)),
                content=data.get("content") if isinstance(data.get("content"), dict) else None,
            )
            return {
                "id": step.id,
                "task_id": step.task_id,
                "description": step.description,
                "tool_name": step.tool_name,
                "irreversible": step.irreversible,
                "status": step.status,
                "approval_request_id": step.approval_request_id,
            }
        except NotRoomMemberError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    def _decide_step(request: Request, room_id: str, task_id: str, step_id: str, approve: bool):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        service = _service(request)
        try:
            service.tasks.list_tasks(room_id, viewer_user_id=user_id)
            step = (
                service.approve_step(step_id, decided_by=user_id)
                if approve
                else service.reject_step(step_id, decided_by=user_id)
            )
            return {"id": step.id, "task_id": step.task_id, "status": step.status}
        except NotRoomMemberError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/rooms/{room_id}/tasks/{task_id}/steps/{step_id}/approve")
    def approve_step_endpoint(request: Request, room_id: str, task_id: str, step_id: str):
        return _decide_step(request, room_id, task_id, step_id, approve=True)

    @app.post("/api/rooms/{room_id}/tasks/{task_id}/steps/{step_id}/reject")
    def reject_step_endpoint(request: Request, room_id: str, task_id: str, step_id: str):
        return _decide_step(request, room_id, task_id, step_id, approve=False)

    return app
