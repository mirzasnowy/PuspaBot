"""Team runner: peer agents taking turns in one shared conversation.

The runner is the turn-taking loop, nothing more. It calls no model
and no network: how a member produces its turn is an injected
``contributor`` callable, exactly like the injected ``executor`` in
``puspa.runtime.hierarchy``. Production wiring injects a contributor
that calls the member's own model with its role, instruction,
skills, knowledge, and workspace; tests inject a scripted one.

Turn order:

- Round-robin in join order: every unfinished member speaks once
  per round, up to ``max_rounds`` rounds.
- Handoff: when a member's turn mentions ``@name`` of another
  unfinished member, that member speaks next instead of the
  round-robin order. A mention of a finished member, of the
  speaker itself, or of nobody in the team changes nothing.
- A turn is a :class:`Contribution` with text and a ``done`` flag.
  Empty text is a pass: no message is written, and the member is
  asked again next round. A member that signals ``done`` is never
  asked again.
- The run ends ``completed`` when every member signalled ``done``,
  or ``max_rounds`` when the round budget ran out first. Every
  turn is written to the shared log the moment it is produced, so
  a run that crashes mid-way still keeps the turns it made.

A run needs at least two members with at least two distinct
roles — a team of one, or two members doing the same job, is not
a team run (``TeamTooSmallError``).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from .conversations import (
    STATUS_COMPLETED,
    STATUS_MAX_ROUNDS,
    TeamConversationStore,
    TeamMessage,
)
from .errors import TeamTooSmallError
from .teams import MIN_TEAM_SIZE, TeamMember, TeamStore

__all__ = [
    "Contribution",
    "Contributor",
    "TeamRunResult",
    "TeamRunner",
]

#: ``@name`` mentions in a turn, for handoffs.
_MENTION_RE = re.compile(r"@([A-Za-z0-9_-]+)")


@dataclass(frozen=True)
class Contribution:
    """One member's turn: what it said, and whether it is finished.

    ``done`` means the member has nothing more to add to this task
    and will not be asked again. Text may be empty (a pass) with
    ``done`` False; empty text with ``done`` True still finishes
    the member.
    """

    text: str
    done: bool = False


#: How a member produces a turn: (member, shared log so far, task).
Contributor = Callable[[TeamMember, list[TeamMessage], str], Contribution]


@dataclass(frozen=True)
class TeamRunResult:
    """The outcome of one team run, with the full shared log."""

    conversation_id: str
    team_id: str
    task: str
    status: str
    rounds_used: int
    messages: list[TeamMessage]


def _mentioned_member(
    text: str, members: list[TeamMember], finished: set[str], *, exclude: str
) -> str | None:
    """First ``@name`` in the text that names an unfinished member.

    Returns the member name, or None when no mention names one.
    Mentions of the speaker, of finished members, and of names
    outside the team are ignored.
    """
    unfinished = {member.name for member in members if member.name not in finished}
    for name in _MENTION_RE.findall(text or ""):
        if name != exclude and name in unfinished:
            return name
    return None


class TeamRunner:
    """Run a team at a task, turn by turn, in the shared conversation."""

    def __init__(
        self,
        store: TeamStore | None = None,
        conversations: TeamConversationStore | None = None,
        *,
        max_rounds: int = 3,
    ) -> None:
        if not isinstance(max_rounds, int) or max_rounds < 1:
            raise ValueError("max_rounds must be a positive integer")
        self.store = store if store is not None else TeamStore()
        self.conversations = conversations if conversations is not None else TeamConversationStore()
        self.max_rounds = max_rounds

    def run(self, team_id: str, task: str, *, contributor: Contributor) -> TeamRunResult:
        """Run the team at one task and return the result.

        Raises ``TeamTooSmallError`` when the team has fewer than
        two members or fewer than two distinct roles, and
        ``ValueError`` for an empty task. A contributor that raises
        aborts the run: the conversation stays ``running`` and the
        turns already written stay in the log.
        """
        if not isinstance(task, str) or not task.strip():
            raise ValueError("task must be a non-empty string")
        team = self.store.require_team(team_id)
        members = self.store.list_members(team.id)
        roles = {member.role for member in members}
        if len(members) < MIN_TEAM_SIZE or len(roles) < MIN_TEAM_SIZE:
            raise TeamTooSmallError(
                f"a team run needs at least {MIN_TEAM_SIZE} members with "
                f"{MIN_TEAM_SIZE} distinct roles, team {team.id} has "
                f"{len(members)} member(s) with role(s) {sorted(roles)}"
            )
        conversation = self.conversations.start_conversation(team.id, task.strip())
        finished: set[str] = set()
        rounds_used = 0
        next_name: str | None = None
        while rounds_used < self.max_rounds:
            pending = [member for member in members if member.name not in finished]
            if not pending:
                break
            rounds_used += 1
            while pending:
                if next_name is not None:
                    speaker = next((m for m in pending if m.name == next_name), pending[0])
                    next_name = None
                else:
                    speaker = pending[0]
                pending.remove(speaker)
                history = self.conversations.list_messages(conversation.id)
                contribution = contributor(speaker, history, task.strip())
                text = contribution.text.strip() if isinstance(contribution.text, str) else ""
                if text:
                    self.conversations.append_message(
                        conversation.id,
                        author_agent_name=speaker.name,
                        content=text,
                    )
                if contribution.done:
                    finished.add(speaker.name)
                if len(finished) == len(members):
                    pending.clear()
                    break
                target = _mentioned_member(text, members, finished, exclude=speaker.name)
                if target is not None and any(member.name == target for member in pending):
                    next_name = target
        status = STATUS_COMPLETED if len(finished) == len(members) else STATUS_MAX_ROUNDS
        self.conversations.finish_conversation(
            conversation.id, status=status, rounds_used=rounds_used
        )
        return TeamRunResult(
            conversation_id=conversation.id,
            team_id=team.id,
            task=task.strip(),
            status=status,
            rounds_used=rounds_used,
            messages=self.conversations.list_messages(conversation.id),
        )
