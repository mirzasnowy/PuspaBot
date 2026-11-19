"""Money actions in a room: two people, or the owner (B6).

Beta-lite scope: there are no shared funds — no balance, no
wallet, nothing is ever paid from a room. This module is the gate
in front of any future money action (payment preparation lives in
B8): before the agent prepares a payment for the group, the room
must approve it, and the rule is:

- The room owner alone may approve a money action.
- Any other member needs a second person: the requester approves,
  and one other owner/member approves the same action. Two
  distinct people, both owner/member. A guest's approval never
  counts, and the same person approving twice still counts once.

An action is identified by a caller-supplied ``action_id`` (for
example the id of the payment preparation being approved). The
gate is deliberately dumb about what the action is: it only
answers "may this action go ahead for this room, asked by this
person". The caller checks the gate, then does the action; the
approvals stay recorded in ``room_money_approvals`` so the group
can audit who approved what.

Rules, all enforced here:

- Only an owner or member may approve; a guest or non-member
  gets ``NotRoomMemberError``.
- Approving twice is idempotent: one row per (action, room,
  approver), so the second approval is a no-op.
- ``is_money_approved`` is a pure read: True when the requester
  is an owner, or when the requester plus at least one other
  owner/member have both approved the action.
"""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from puspa.accounts.users import resolve_dsn

from .errors import NotRoomMemberError
from .rooms import APPROVER_ROLES, RoomStore, _validate_uuid

__all__ = [
    "MoneyGate",
    "approve_money_action",
    "is_money_approved",
    "list_money_approvals",
]

#: A money action needs this many distinct approvers when the
#: requester is not the room owner (the requester + one other).
REQUIRED_APPROVERS = 2


def _connect(dsn=None):
    return psycopg.connect(resolve_dsn(dsn), row_factory=dict_row)


def _validate_action_id(action_id: str) -> str:
    if not isinstance(action_id, str) or not action_id.strip():
        raise ValueError("action_id must be a non-empty string")
    return action_id.strip()


class MoneyGate:
    """The two-person-or-owner gate for room money actions."""

    def __init__(self, dsn=None, room_store: RoomStore | None = None):
        self.dsn = resolve_dsn(dsn)
        self.rooms = room_store or RoomStore(dsn)

    def approve_money_action(self, room_id: str, action_id: str, *, approver_user_id: str) -> int:
        """Record one person's approval of a money action.

        Returns the number of distinct owner/member approvers for
        the action after this approval. Idempotent: approving twice
        still counts once. Raises ``NotRoomMemberError`` when the
        approver is a guest or not a member at all.
        """
        room_id = _validate_uuid(room_id, "room_id")
        action_id = _validate_action_id(action_id)
        approver = _validate_uuid(approver_user_id, "approver_user_id")
        if self.rooms.role_of(room_id, approver) not in APPROVER_ROLES:
            raise NotRoomMemberError(
                f"user {approver} may not approve money actions in room {room_id}"
            )
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO room_money_approvals (action_id, room_id, approver_user_id) "
                "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (action_id, room_id, approver),
            )
            conn.commit()
            return self._approver_count_in(conn, room_id, action_id)

    def list_money_approvals(self, room_id: str, action_id: str) -> list[str]:
        """User ids of the distinct approvers of one action, in
        approval order."""
        room_id = _validate_uuid(room_id, "room_id")
        action_id = _validate_action_id(action_id)
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT approver_user_id FROM room_money_approvals "
                "WHERE room_id = %s AND action_id = %s ORDER BY created_at, approver_user_id",
                (room_id, action_id),
            )
            return [str(row["approver_user_id"]) for row in cur.fetchall()]

    def is_money_approved(self, room_id: str, action_id: str, *, requester_user_id: str) -> bool:
        """True when the money action may go ahead.

        The owner alone approves. Any other member needs a second
        person: the requester must have approved, and at least one
        other owner/member must have approved the same action.
        A guest or non-member is never approved.
        """
        room_id = _validate_uuid(room_id, "room_id")
        action_id = _validate_action_id(action_id)
        requester = _validate_uuid(requester_user_id, "requester_user_id")
        if self.rooms.is_owner(room_id, requester):
            return True
        if self.rooms.role_of(room_id, requester) not in APPROVER_ROLES:
            return False
        approvers = self.list_money_approvals(room_id, action_id)
        return requester in approvers and len(approvers) >= REQUIRED_APPROVERS

    def _approver_count_in(self, conn, room_id: str, action_id: str) -> int:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM room_money_approvals WHERE room_id = %s AND action_id = %s",
                (room_id, action_id),
            )
            return int(cur.fetchone()["count"])


def _gate(dsn=None) -> MoneyGate:
    return MoneyGate(dsn)


def approve_money_action(room_id: str, action_id: str, *, approver_user_id: str, dsn=None) -> int:
    return _gate(dsn).approve_money_action(room_id, action_id, approver_user_id=approver_user_id)


def list_money_approvals(room_id: str, action_id: str, dsn=None) -> list[str]:
    return _gate(dsn).list_money_approvals(room_id, action_id)


def is_money_approved(room_id: str, action_id: str, *, requester_user_id: str, dsn=None) -> bool:
    return _gate(dsn).is_money_approved(room_id, action_id, requester_user_id=requester_user_id)
