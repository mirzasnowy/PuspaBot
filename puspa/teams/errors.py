"""Team errors: one base class, one error per broken rule."""

from __future__ import annotations

__all__ = [
    "NotTeamOwnerError",
    "TeamConversationNotFoundError",
    "TeamError",
    "TeamFullError",
    "TeamMemberNotFoundError",
    "TeamNotFoundError",
    "TeamTooSmallError",
]


class TeamError(Exception):
    """Any multi-agent team rule violation."""


class TeamNotFoundError(TeamError):
    """The team id does not exist."""


class TeamMemberNotFoundError(TeamError):
    """The named agent is not a member of the team."""


class NotTeamOwnerError(TeamError):
    """Only the team owner may change the team."""


class TeamTooSmallError(TeamError):
    """A run needs at least two members with at least two distinct roles."""


class TeamFullError(TeamError):
    """The team already has the maximum number of members."""


class TeamConversationNotFoundError(TeamError):
    """The conversation id does not exist."""
