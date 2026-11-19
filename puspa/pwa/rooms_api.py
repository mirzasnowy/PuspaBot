"""HTTP layer for the shared task board (B9) and shared splits (B11).

Plain handler functions over :mod:`puspa.rooms.delegation` and
:mod:`puspa.rooms.splitting`, plus :func:`register_routes`, which
mounts them on a FastAPI app. This module deliberately does not
import ``puspa.pwa.app``: the coordinator wires it into
``create_app`` later with a few lines::

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
- ``GET /api/rooms/{room_id}/splits`` -> the room's splits with
  every participant's share, paid, and remaining amounts. 403
  for a non-member, 404 for an unknown room.
- ``POST /api/rooms/{room_id}/splits`` (JSON: ``title``,
  ``total_cents`` (integer sen), ``payer_user_id``,
  ``participant_user_ids``, optional ``kind``, ``shares``,
  ``weights``, ``due_at`` ISO timestamp) -> create a split.
  400 on bad input.
- ``POST /api/rooms/{room_id}/splits/{split_id}/payments``
  (JSON: ``debtor_user_id``, ``amount_cents``) -> record a
  (partial) payment. 400 on overpayment or a bad amount.
- ``POST /api/rooms/{room_id}/splits/{split_id}/settlements/{debtor_user_id}/prepare``
  -> prepare (never execute) the debtor's settlement through
  the B6 money gate. 403 when the gate has not approved it
  (the room owner passes alone; anyone else needs their own
  approval plus one other owner/member — see
  ``puspa.rooms.splitting``); the prepared settlement is what
  the user pays in their own bank/e-wallet app.

No approval policy or gate is wired into the HTTP layer in this
iteration: steps proposed here go through the service with no
policy, so irreversible steps stay ``awaiting_approval`` until
the owner decides them through the service with a policy wired
in (or a later ticket wires the policy into the app). That is
the safe default — the API never approves its own steps. The
same fail-closed shape holds for settlements: the API never
approves a settlement itself, it only prepares one the money
gate already approved.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..i18n.server_lines import render as render_server_line
from ..rooms import DelegationService, NotRoomMemberError, RoomNotFoundError, SplitService
from ..rooms.delegation import BoardRow
from ..rooms.errors import MoneyApprovalError, SplitError
from ..rooms.splitting import Share, Split
from ..rooms.tasks import RoomTask

__all__ = [
    "board_to_dict",
    "get_board",
    "get_split",
    "list_rooms",
    "list_splits",
    "register_routes",
    "split_to_dict",
]


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


def _share_dict(share: Share) -> dict:
    return {
        "user_id": share.user_id,
        "share_cents": share.share_cents,
        "paid_cents": share.paid_cents,
        "remaining_cents": share.remaining_cents,
        "is_paid": share.is_paid,
    }


def split_to_dict(split: Split, shares: list[Share]) -> dict:
    """One split with its shares, as served by the splits routes."""
    return {
        "id": split.id,
        "room_id": split.room_id,
        "title": split.title,
        "kind": split.kind,
        "total_cents": split.total_cents,
        "payer_user_id": split.payer_user_id,
        "created_by": split.created_by,
        "due_at": _iso(split.due_at),
        "status": split.status,
        "created_at": _iso(split.created_at),
        "settled_at": _iso(split.settled_at),
        "shares": [_share_dict(share) for share in shares],
    }


def list_splits(
    room_id: str, *, user_id: str, dsn=None, service: SplitService | None = None
) -> list[dict]:
    """The room's splits with shares, for one member."""
    service = service or SplitService(dsn)
    splits = service.list_splits(room_id, viewer_user_id=user_id)
    return [
        split_to_dict(split, service.list_shares(split.id, viewer_user_id=user_id))
        for split in splits
    ]


def get_split(
    split_id: str, *, user_id: str, dsn=None, service: SplitService | None = None
) -> dict:
    """One split with its shares, for one member of its room."""
    service = service or SplitService(dsn)
    split = service.require_split(split_id)
    shares = service.list_shares(split.id, viewer_user_id=user_id)
    return split_to_dict(split, shares)


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

    def _split_service(request: Request) -> SplitService:
        return SplitService(request.app.state.dsn)

    @app.get("/api/rooms/{room_id}/splits")
    def splits_endpoint(request: Request, room_id: str):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        service = _split_service(request)
        try:
            return {"splits": list_splits(room_id, user_id=user_id, service=service)}
        except NotRoomMemberError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except (ValueError, RoomNotFoundError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.post("/api/rooms/{room_id}/splits")
    async def create_split_endpoint(request: Request, room_id: str):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        service = _split_service(request)
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("split body must be a JSON object")
            participants = data.get("participant_user_ids")
            if not isinstance(participants, list) or not all(
                isinstance(item, str) for item in participants
            ):
                raise ValueError("participant_user_ids must be a list of user ids")
            shares = data.get("shares")
            if shares is not None and not isinstance(shares, dict):
                raise ValueError("shares must be an object of user id to sen")
            weights = data.get("weights")
            if weights is not None and not isinstance(weights, list):
                raise ValueError("weights must be a list of integers")
            due_at = None
            if data.get("due_at"):
                due_at = datetime.fromisoformat(str(data["due_at"]))
            total_cents = data.get("total_cents")
            if isinstance(total_cents, bool) or not isinstance(total_cents, int):
                raise ValueError("total_cents must be an integer number of sen")
            split = service.create_split(
                room_id,
                title=str(data.get("title") or ""),
                total_cents=total_cents,
                payer_user_id=str(data.get("payer_user_id") or ""),
                participant_user_ids=participants,
                created_by=user_id,
                kind=str(data.get("kind") or "other"),
                shares=shares,
                weights=weights,
                due_at=due_at,
            )
            return get_split(split.id, user_id=user_id, service=service)
        except NotRoomMemberError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except (SplitError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/rooms/{room_id}/splits/{split_id}/payments")
    async def record_payment_endpoint(request: Request, room_id: str, split_id: str):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        service = _split_service(request)
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("payment body must be a JSON object")
            service.rooms.require_room(room_id)
            split = service.require_split(split_id)
            if split.room_id != room_id:
                return JSONResponse({"error": f"split not found: {split_id}"}, status_code=404)
            amount_cents = data.get("amount_cents")
            if isinstance(amount_cents, bool) or not isinstance(amount_cents, int):
                raise ValueError("amount_cents must be an integer number of sen")
            payment = service.record_payment(
                split_id,
                debtor_user_id=str(data.get("debtor_user_id") or ""),
                amount_cents=amount_cents,
                recorded_by=user_id,
            )
            return {
                "id": payment.id,
                "split_id": payment.split_id,
                "debtor_user_id": payment.debtor_user_id,
                "amount_cents": payment.amount_cents,
                "recorded_by": payment.recorded_by,
                "created_at": _iso(payment.created_at),
                "split": get_split(split_id, user_id=user_id, service=service),
            }
        except NotRoomMemberError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except (SplitError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/rooms/{room_id}/splits/{split_id}/settlements/{debtor_user_id}/prepare")
    def prepare_settlement_endpoint(
        request: Request, room_id: str, split_id: str, debtor_user_id: str
    ):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        service = _split_service(request)
        try:
            service.rooms.require_room(room_id)
            split = service.require_split(split_id)
            if split.room_id != room_id:
                return JSONResponse({"error": f"split not found: {split_id}"}, status_code=404)
            settlement = service.prepare_settlement(split_id, debtor_user_id, requested_by=user_id)
            return {
                "id": settlement.id,
                "split_id": settlement.split_id,
                "debtor_user_id": settlement.debtor_user_id,
                "recipient_user_id": settlement.recipient_user_id,
                "amount_cents": settlement.amount_cents,
                "action_id": settlement.action_id,
                "status": settlement.status,
                "prepared_by": settlement.prepared_by,
                "prepared_at": _iso(settlement.prepared_at),
                "executed": False,
            }
        except MoneyApprovalError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except NotRoomMemberError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except (SplitError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    return app
