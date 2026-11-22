"""Multi-agent teams: peer agents in one shared conversation.

A team is a small group of peer agents — each with its own role,
model, skills, knowledge, and workspace — that collaborate inside
ONE shared conversation to finish a task together. No member is
the boss of another, and no member is a helper of a single main
agent (helpers live in ``puspa.subagents``; people sharing one
agent live in ``puspa.rooms``).

Public surface:

- :class:`TeamStore` — teams and peer-agent memberships (owner-only
  changes, at most 8 members, one-token names for ``@name``
  handoffs).
- :class:`TeamConversationStore` — one shared log per task:
  every turn is numbered and attributed to the member who wrote
  it, so the log shows whose turn it was.
- :class:`TeamRunner` — the turn-taking loop: round-robin in join
  order, ``@name`` handoff to a named member, ``done`` to finish,
  a round budget that ends the run as ``max_rounds`` when nobody
  finishes in time. The runner calls no model: how a member
  produces its turn is an injected ``contributor`` callable.
"""

from __future__ import annotations

from .conversations import (
    CONVERSATION_STATUSES,
    STATUS_COMPLETED,
    STATUS_MAX_ROUNDS,
    STATUS_RUNNING,
    TeamConversation,
    TeamConversationStore,
    TeamMessage,
    append_message,
    finish_conversation,
    get_conversation,
    list_conversations,
    list_messages,
    start_conversation,
)
from .errors import (
    NotTeamOwnerError,
    TeamConversationNotFoundError,
    TeamError,
    TeamFullError,
    TeamMemberNotFoundError,
    TeamNotFoundError,
    TeamTooSmallError,
)
from .runner import Contribution, TeamRunner, TeamRunResult
from .schema import TEAM_TABLES, create_schema, reset_tables, schema_path
from .teams import (
    MAX_TEAM_SIZE,
    MIN_TEAM_SIZE,
    Team,
    TeamMember,
    TeamStore,
    add_member,
    create_team,
    delete_team,
    get_team,
    list_members,
    list_teams_for_user,
    member_count,
    remove_member,
)

__all__ = [
    "CONVERSATION_STATUSES",
    "MAX_TEAM_SIZE",
    "MIN_TEAM_SIZE",
    "STATUS_COMPLETED",
    "STATUS_MAX_ROUNDS",
    "STATUS_RUNNING",
    "TEAM_TABLES",
    "Contribution",
    "NotTeamOwnerError",
    "Team",
    "TeamConversation",
    "TeamConversationNotFoundError",
    "TeamConversationStore",
    "TeamError",
    "TeamFullError",
    "TeamMember",
    "TeamMemberNotFoundError",
    "TeamMessage",
    "TeamNotFoundError",
    "TeamRunResult",
    "TeamRunner",
    "TeamStore",
    "TeamTooSmallError",
    "add_member",
    "append_message",
    "create_schema",
    "create_team",
    "delete_team",
    "finish_conversation",
    "get_conversation",
    "get_team",
    "list_conversations",
    "list_members",
    "list_messages",
    "list_teams_for_user",
    "member_count",
    "remove_member",
    "reset_tables",
    "schema_path",
    "start_conversation",
]
