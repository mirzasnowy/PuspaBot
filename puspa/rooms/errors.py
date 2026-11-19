"""Errors for shared rooms (B6)."""

from __future__ import annotations

__all__ = [
    "InviteError",
    "LastOwnerError",
    "MoneyApprovalError",
    "NotRoomMemberError",
    "NotRoomOwnerError",
    "RoomError",
    "RoomFullError",
    "RoomNotFoundError",
    "SplitError",
]


class RoomError(Exception):
    """Base error for room operations."""


class RoomNotFoundError(RoomError):
    """The room id does not exist."""


class NotRoomMemberError(RoomError):
    """The user is not a member of the room."""


class NotRoomOwnerError(RoomError):
    """The user is not an owner of the room."""


class LastOwnerError(RoomError):
    """The room would be left without an owner."""


class RoomFullError(RoomError):
    """The room already has the maximum number of members (beta-lite: 5)."""


class InviteError(RoomError):
    """An invite token/code is unknown, expired, revoked, or already used."""


class MoneyApprovalError(RoomError):
    """A money action was attempted without the required approval."""


class SplitError(RoomError):
    """A split is invalid: bad shares, bad amount, or a payment that
    does not fit what a debtor still owes."""
