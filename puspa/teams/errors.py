"""Error types for multi-tenant teams (G13).

Every failure in ``puspa.teams`` raises one of these (never a raw
library exception), so callers can fail closed on a single base type
and tests can assert the exact refusal reason. The pattern follows
``puspa.hosting.errors``: one base type, one type per refusal.
"""

from __future__ import annotations


class TeamError(Exception):
    """Base type for all team failures."""


class TeamNotFoundError(TeamError):
    """A team id does not exist (or the caller may not see it)."""


class NotTeamMemberError(TeamError):
    """The user is not a member of the team."""


class NotTeamOwnerError(TeamError):
    """The action needs a team owner; the user is only a member."""


class LastOwnerError(TeamError):
    """The action would leave the team with no owner."""


class InvitationError(TeamError):
    """An invitation token is unknown, expired, revoked, or used."""


class EntitlementError(TeamError):
    """An entitlement could not be issued (bad tier, bad ids)."""


class MoveError(TeamError):
    """A workspace move is in the wrong state for the operation."""


class MoveFrozenError(MoveError):
    """Bot automation is frozen while a workspace move is active."""


class ThreadNotVisibleError(TeamError):
    """The thread is not visible to this member (visibility gate)."""


class BotAccessError(TeamError):
    """The member has no access to this bot, or the request failed."""


__all__ = [
    "BotAccessError",
    "EntitlementError",
    "InvitationError",
    "LastOwnerError",
    "MoveError",
    "MoveFrozenError",
    "NotTeamMemberError",
    "NotTeamOwnerError",
    "TeamError",
    "TeamNotFoundError",
    "ThreadNotVisibleError",
]
