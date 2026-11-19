"""Errors for co-drafting (B13)."""

from __future__ import annotations


class DraftValidationError(ValueError):
    """Bad input to a drafting call (unknown kind, empty brief)."""


class DraftNotFoundError(KeyError):
    """The draft id does not exist for this user."""


class DraftNotApprovedError(PermissionError):
    """Send refused: no usable approval for this draft.

    Raised when there is no recorded approval, when the approval
    belongs to another user or draft, when it expired, when it
    was already consumed, or when the draft text changed after
    approval (the approval binds to the exact text hash).
    """


class NoDeliveryChannelError(RuntimeError):
    """Send approved but no delivery channel is wired in.

    The approval still stands (it is not consumed by this
    failure); the caller wires a sender and retries.
    """
