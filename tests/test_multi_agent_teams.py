"""Multi-agent teams: peer agents in one shared conversation.

Covers the team store (create/add/remove/delete, owner-only
changes, name and size rules), the shared conversation log
(numbered, attributed turns), and the runner: round-robin turns,
``@name`` handoff, ``done`` completion, the round budget, and the
end-to-end researcher + writer run where the writer's draft quotes
the researcher's findings — proof the second role read the first
role's turn in the same conversation.
"""

from __future__ import annotations

import pytest

from puspa.teams import (
    Contribution,
    TeamConversationStore,
    TeamFullError,
    TeamRunner,
    TeamStore,
    TeamTooSmallError,
    create_schema,
    reset_tables,
)
from puspa.teams.errors import NotTeamOwnerError, TeamNotFoundError


@pytest.fixture(autouse=True)
def _team_schema(test_dsn):
    """Team tables (0044)."""
    create_schema(test_dsn)
    reset_tables(test_dsn)
    yield
    reset_tables(test_dsn)


def _make_team(store, owner_id, name="Research desk"):
    return store.create_team(name, owner_id)


def _add_researcher(store, team_id, owner_id):
    return store.add_member(
        team_id,
        name="researcher",
        role="researcher",
        model="openai/gpt-4o",
        skills=["web_search", "read_page"],
        knowledge=["market-notes"],
        workspace="/work/researcher",
        instruction="Find facts and cite sources.",
        added_by=owner_id,
    )


def _add_writer(store, team_id, owner_id):
    return store.add_member(
        team_id,
        name="writer",
        role="writer",
        model="anthropic/claude-sonnet-4",
        skills=["draft", "summarize"],
        knowledge=["style-guide"],
        workspace="/work/writer",
        instruction="Write clearly from the researcher's findings.",
        added_by=owner_id,
    )


def _two_member_team(store, owner_id):
    team = _make_team(store, owner_id)
    _add_researcher(store, team.id, owner_id)
    _add_writer(store, team.id, owner_id)
    return team


# ----------------------------------------------------------------------
# Team store
# ----------------------------------------------------------------------


def test_create_team_and_add_two_peer_members(make_user, test_dsn):
    owner_id = make_user()["user_id"]
    store = TeamStore(test_dsn)
    team = _make_team(store, owner_id)
    assert team.name == "Research desk"
    assert team.owner_user_id == owner_id
    researcher = _add_researcher(store, team.id, owner_id)
    writer = _add_writer(store, team.id, owner_id)
    assert researcher.role == "researcher"
    assert researcher.model == "openai/gpt-4o"
    assert researcher.skills == ["web_search", "read_page"]
    assert researcher.knowledge == ["market-notes"]
    assert researcher.workspace == "/work/researcher"
    assert writer.role == "writer"
    assert writer.model == "anthropic/claude-sonnet-4"
    assert writer.position > researcher.position
    # Persisted: a fresh store reads the same team and members.
    fresh = TeamStore(test_dsn)
    assert fresh.get_team(team.id) == team
    members = fresh.list_members(team.id)
    assert [m.name for m in members] == ["researcher", "writer"]
    assert {m.role for m in members} == {"researcher", "writer"}
    assert fresh.member_count(team.id) == 2
    assert [t.id for t in fresh.list_teams_for_user(owner_id)] == [team.id]


def test_add_member_validation(make_user, test_dsn):
    owner_id = make_user()["user_id"]
    store = TeamStore(test_dsn)
    team = _make_team(store, owner_id)
    with pytest.raises(ValueError):
        store.create_team("  ", owner_id)
    with pytest.raises(ValueError):
        store.create_team("Team", "not-a-uuid")
    with pytest.raises(ValueError):
        store.add_member(team.id, name="two words", role="r", model="m", added_by=owner_id)
    with pytest.raises(ValueError):
        store.add_member(team.id, name="has@mention", role="r", model="m", added_by=owner_id)
    with pytest.raises(ValueError):
        store.add_member(team.id, name="solo", role=" ", model="m", added_by=owner_id)
    with pytest.raises(ValueError):
        store.add_member(team.id, name="solo", role="r", model=" ", added_by=owner_id)
    with pytest.raises(ValueError):
        store.add_member(
            team.id,
            name="solo",
            role="r",
            model="m",
            skills=["ok", " "],
            added_by=owner_id,
        )
    with pytest.raises(ValueError):
        store.add_member(
            team.id,
            name="solo",
            role="r",
            model="m",
            knowledge="not-a-list",
            added_by=owner_id,
        )
    _add_researcher(store, team.id, owner_id)
    with pytest.raises(ValueError, match="already has a member"):
        _add_researcher(store, team.id, owner_id)
    assert store.member_count(team.id) == 1


def test_team_changes_are_owner_only(make_user, test_dsn):
    owner_id = make_user()["user_id"]
    stranger_id = make_user()["user_id"]
    store = TeamStore(test_dsn)
    team = _make_team(store, owner_id)
    with pytest.raises(NotTeamOwnerError):
        _add_researcher(store, team.id, stranger_id)
    _add_researcher(store, team.id, owner_id)
    with pytest.raises(NotTeamOwnerError):
        store.remove_member(team.id, "researcher", removed_by=stranger_id)
    with pytest.raises(NotTeamOwnerError):
        store.delete_team(team.id, requested_by=stranger_id)
    assert store.member_count(team.id) == 1


def test_team_size_cap(make_user, test_dsn):
    owner_id = make_user()["user_id"]
    store = TeamStore(test_dsn)
    team = _make_team(store, owner_id)
    for index in range(8):
        store.add_member(
            team.id,
            name=f"agent{index}",
            role=f"role{index}",
            model="model",
            added_by=owner_id,
        )
    assert store.member_count(team.id) == 8
    with pytest.raises(TeamFullError):
        store.add_member(team.id, name="ninth", role="role9", model="model", added_by=owner_id)


def test_remove_member_and_delete_team_cascade(make_user, test_dsn):
    owner_id = make_user()["user_id"]
    store = TeamStore(test_dsn)
    conv_store = TeamConversationStore(test_dsn)
    team = _two_member_team(store, owner_id)
    conversation = conv_store.start_conversation(team.id, "Do the task.")
    conv_store.append_message(conversation.id, author_agent_name="researcher", content="Found it.")
    store.remove_member(team.id, "writer", removed_by=owner_id)
    assert [m.name for m in store.list_members(team.id)] == ["researcher"]
    store.remove_member(team.id, "writer", removed_by=owner_id)  # idempotent
    store.delete_team(team.id, requested_by=owner_id)
    assert store.get_team(team.id) is None
    with pytest.raises(TeamNotFoundError):
        store.list_members(team.id)
    # Conversations and messages went with the team.
    assert conv_store.get_conversation(conversation.id) is None
    assert conv_store.list_conversations(team.id) == []


# ----------------------------------------------------------------------
# Shared conversation log
# ----------------------------------------------------------------------


def test_conversation_log_is_numbered_and_attributed(make_user, test_dsn):
    owner_id = make_user()["user_id"]
    store = TeamStore(test_dsn)
    conv_store = TeamConversationStore(test_dsn)
    team = _two_member_team(store, owner_id)
    conversation = conv_store.start_conversation(team.id, "Summarize the market.")
    assert conversation.status == "running"
    first = conv_store.append_message(
        conversation.id, author_agent_name="researcher", content="Fact one."
    )
    second = conv_store.append_message(
        conversation.id, author_agent_name="writer", content="Draft one."
    )
    assert (first.seq, second.seq) == (0, 1)
    assert first.author_agent_name == "researcher"
    assert second.author_agent_name == "writer"
    # A fresh store reads the same log back.
    fresh = TeamConversationStore(test_dsn)
    messages = fresh.list_messages(conversation.id)
    assert [(m.seq, m.author_agent_name, m.content) for m in messages] == [
        (0, "researcher", "Fact one."),
        (1, "writer", "Draft one."),
    ]
    assert [c.id for c in fresh.list_conversations(team.id)] == [conversation.id]
    with pytest.raises(ValueError):
        conv_store.append_message(conversation.id, author_agent_name="writer", content="   ")


# ----------------------------------------------------------------------
# Runner
# ----------------------------------------------------------------------


def _researcher_writer_contributor(member, history, task):
    """Scripted turns: the researcher finds facts, the writer drafts
    from them and finishes; the draft quotes a finding, proving the
    writer read the researcher's turn in the shared log."""
    if member.name == "researcher":
        return Contribution(
            text="Findings: coffee prices rose 12% in Karawang this month.",
            done=True,
        )
    findings = next(
        (m.content for m in history if m.author_agent_name == "researcher"),
        "",
    )
    assert "12%" in findings, "writer must see the researcher's findings"
    return Contribution(
        text="Draft: Coffee in Karawang costs 12% more this month.",
        done=True,
    )


def test_two_agent_run_researcher_and_writer(make_user, test_dsn):
    owner_id = make_user()["user_id"]
    store = TeamStore(test_dsn)
    team = _two_member_team(store, owner_id)
    runner = TeamRunner(store, TeamConversationStore(test_dsn))
    result = runner.run(
        team.id, "Write a one-line market update.", contributor=_researcher_writer_contributor
    )
    assert result.status == "completed"
    assert result.rounds_used == 1
    assert [m.author_agent_name for m in result.messages] == ["researcher", "writer"]
    assert "12%" in result.messages[0].content
    assert "12%" in result.messages[1].content
    # Persisted: the finished conversation reads back from a fresh store.
    fresh = TeamConversationStore(test_dsn)
    saved = fresh.get_conversation(result.conversation_id)
    assert saved is not None
    assert saved.status == "completed"
    assert saved.rounds_used == 1
    assert saved.finished_at is not None
    assert [m.author_agent_name for m in fresh.list_messages(result.conversation_id)] == [
        "researcher",
        "writer",
    ]


def test_run_requires_two_members_with_distinct_roles(make_user, test_dsn):
    owner_id = make_user()["user_id"]
    store = TeamStore(test_dsn)
    runner = TeamRunner(store, TeamConversationStore(test_dsn))
    team = _make_team(store, owner_id)
    with pytest.raises(TeamTooSmallError):
        runner.run(team.id, "Task.", contributor=_researcher_writer_contributor)
    _add_researcher(store, team.id, owner_id)
    with pytest.raises(TeamTooSmallError):
        runner.run(team.id, "Task.", contributor=_researcher_writer_contributor)
    store.add_member(team.id, name="researcher2", role="researcher", model="m", added_by=owner_id)
    with pytest.raises(TeamTooSmallError, match="distinct roles"):
        runner.run(team.id, "Task.", contributor=_researcher_writer_contributor)
    with pytest.raises(ValueError):
        runner.run(team.id, "   ", contributor=_researcher_writer_contributor)


def test_mention_handoff_jumps_the_queue(make_user, test_dsn):
    owner_id = make_user()["user_id"]
    store = TeamStore(test_dsn)
    team = _make_team(store, owner_id)
    _add_researcher(store, team.id, owner_id)
    _add_writer(store, team.id, owner_id)
    store.add_member(team.id, name="reviewer", role="reviewer", model="m", added_by=owner_id)

    def contributor(member, history, task):
        if member.name == "researcher":
            return Contribution(text="@reviewer please check this first.", done=True)
        if member.name == "reviewer":
            return Contribution(text="Checked: looks right.", done=True)
        return Contribution(text="Draft done.", done=True)

    runner = TeamRunner(store, TeamConversationStore(test_dsn))
    result = runner.run(team.id, "Task.", contributor=contributor)
    assert result.status == "completed"
    # Round-robin order would be researcher, writer, reviewer; the
    # @reviewer mention makes the reviewer speak second instead.
    assert [m.author_agent_name for m in result.messages] == [
        "researcher",
        "reviewer",
        "writer",
    ]


def test_max_rounds_when_nobody_finishes(make_user, test_dsn):
    owner_id = make_user()["user_id"]
    store = TeamStore(test_dsn)
    team = _two_member_team(store, owner_id)

    def contributor(member, history, task):
        return Contribution(text=f"{member.name} still working.", done=False)

    runner = TeamRunner(store, TeamConversationStore(test_dsn), max_rounds=2)
    result = runner.run(team.id, "Task.", contributor=contributor)
    assert result.status == "max_rounds"
    assert result.rounds_used == 2
    assert [m.author_agent_name for m in result.messages] == [
        "researcher",
        "writer",
        "researcher",
        "writer",
    ]
    saved = TeamConversationStore(test_dsn).get_conversation(result.conversation_id)
    assert saved is not None and saved.status == "max_rounds"


def test_empty_contribution_is_a_pass(make_user, test_dsn):
    owner_id = make_user()["user_id"]
    store = TeamStore(test_dsn)
    team = _two_member_team(store, owner_id)
    seen = {"researcher": 0}

    def contributor(member, history, task):
        if member.name == "researcher":
            seen["researcher"] += 1
            if seen["researcher"] == 1:
                return Contribution(text="   ", done=False)
            return Contribution(text="Findings ready.", done=True)
        return Contribution(text="Draft ready.", done=True)

    runner = TeamRunner(store, TeamConversationStore(test_dsn), max_rounds=3)
    result = runner.run(team.id, "Task.", contributor=contributor)
    assert result.status == "completed"
    assert result.rounds_used == 2
    # The empty first turn wrote no message, and the writer (done in
    # round 1) is not asked again in round 2.
    assert [m.author_agent_name for m in result.messages] == [
        "writer",
        "researcher",
    ]


def test_runner_rejects_bad_max_rounds():
    with pytest.raises(ValueError):
        TeamRunner(max_rounds=0)
    with pytest.raises(ValueError):
        TeamRunner(max_rounds="3")
