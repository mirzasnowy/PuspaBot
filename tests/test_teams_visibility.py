"""Visibility per (thread, member) + per-member bot access.

Tests run against the Postgres test database (puspa_test) for the
access gate; the thread-visibility function is pure and needs no DB.
The team schema is applied by the autouse fixture from
tests.teams_helpers.
"""

import pytest

from puspa.teams.errors import BotAccessError, NotTeamOwnerError, ThreadNotVisibleError
from puspa.teams.visibility import (
    BotAccessGate,
    BotRef,
    ThreadRef,
    can_access_bot,
    can_see_thread,
    require_thread_visible,
)
from tests.teams_helpers import _teams_schema, make_team, team_store

# The imported names are fixtures used by pytest injection (not
# by direct call); listing them keeps ruff from flagging the
# imports as unused or shadowed by the test parameters.
__all__ = ["_teams_schema", "make_team", "team_store"]


@pytest.fixture
def gate(test_dsn):
    return BotAccessGate(test_dsn)


# --- can_see_thread: pure function ------------------------------------------


def test_personal_thread_visible_only_to_owner():
    thread = ThreadRef(thread_id="t1", owner_user_id="u-owner")
    assert can_see_thread(user_id="u-owner", team_ids=[], thread=thread) is True
    assert can_see_thread(user_id="u-other", team_ids=[], thread=thread) is False
    assert can_see_thread(user_id="u-other", team_ids=["team-a"], thread=thread) is False


def test_team_thread_visible_to_team_members_only():
    thread = ThreadRef(thread_id="t2", owner_user_id="u-owner", team_id="team-a")
    assert can_see_thread(user_id="u-owner", team_ids=["team-a"], thread=thread) is True
    assert can_see_thread(user_id="u-member", team_ids=["team-a"], thread=thread) is True
    assert can_see_thread(user_id="u-member", team_ids=["team-b"], thread=thread) is False
    assert can_see_thread(user_id="u-owner", team_ids=[], thread=thread) is False


def test_require_thread_visible_raises_before_content():
    thread = ThreadRef(thread_id="t3", owner_user_id="u-owner", team_id="team-a")
    with pytest.raises(ThreadNotVisibleError):
        require_thread_visible(user_id="u-outsider", team_ids=[], thread=thread)
    # And passes for a member (returns None, no error).
    require_thread_visible(user_id="u-member", team_ids=["team-a"], thread=thread)


# --- can_access_bot: pure function ------------------------------------------


def test_bot_owner_always_has_access():
    bot = BotRef(bot_id="bot-1", owner_user_id="u-owner", team_id="team-a")
    assert can_access_bot(user_id="u-owner", bot=bot, granted_user_ids=[]) is True


def test_team_member_needs_an_explicit_grant():
    bot = BotRef(bot_id="bot-1", owner_user_id="u-owner", team_id="team-a")
    assert can_access_bot(user_id="u-member", bot=bot, granted_user_ids=[]) is False
    assert can_access_bot(user_id="u-member", bot=bot, granted_user_ids=["u-member"]) is True
    assert can_access_bot(user_id="u-other", bot=bot, granted_user_ids=["u-member"]) is False


def test_personal_bot_never_grants_to_others():
    bot = BotRef(bot_id="bot-1", owner_user_id="u-owner", team_id=None)
    assert can_access_bot(user_id="u-other", bot=bot, granted_user_ids=["u-other"]) is False


# --- BotAccessGate: grants ---------------------------------------------------


def test_grant_and_revoke_by_owner(gate, make_team, make_user):
    made = make_team()
    member = make_user()
    gate.grant_access(
        "bot-1",
        member["user_id"],
        team_id=made["team"].id,
        granted_by=made["owner"]["user_id"],
    )
    assert gate.has_access(member["user_id"], "bot-1") is True
    assert gate.list_granted_user_ids("bot-1") == [member["user_id"]]
    gate.revoke_access(
        "bot-1",
        member["user_id"],
        team_id=made["team"].id,
        revoked_by=made["owner"]["user_id"],
    )
    assert gate.has_access(member["user_id"], "bot-1") is False


def test_grant_by_member_is_refused(gate, make_team, make_user):
    made = make_team()
    first = make_user()
    second = make_user()
    with pytest.raises(NotTeamOwnerError):
        gate.grant_access(
            "bot-1",
            second["user_id"],
            team_id=made["team"].id,
            granted_by=first["user_id"],
        )


def test_grant_twice_is_idempotent(gate, make_team, make_user):
    made = make_team()
    member = make_user()
    for _ in range(2):
        gate.grant_access(
            "bot-1",
            member["user_id"],
            team_id=made["team"].id,
            granted_by=made["owner"]["user_id"],
        )
    assert gate.list_granted_user_ids("bot-1") == [member["user_id"]]


def test_revoke_without_grant_is_a_noop(gate, make_team, make_user):
    made = make_team()
    member = make_user()
    gate.revoke_access(
        "bot-1",
        member["user_id"],
        team_id=made["team"].id,
        revoked_by=made["owner"]["user_id"],
    )


# --- BotAccessGate: requests -------------------------------------------------


def test_request_approve_grants_access(gate, team_store, make_team, make_user):
    made = make_team()
    member = make_user()
    team_store.add_member(made["team"].id, member["user_id"], added_by=made["owner"]["user_id"])
    request = gate.request_access(
        "bot-1", team_id=made["team"].id, requester_user_id=member["user_id"]
    )
    assert request.status == "pending"
    assert request.decided_by is None
    approved = gate.approve(request.id, approver_user_id=made["owner"]["user_id"])
    assert approved.status == "approved"
    assert approved.decided_by == made["owner"]["user_id"]
    assert gate.has_access(member["user_id"], "bot-1") is True


def test_request_by_non_member_is_refused(gate, make_team, make_user):
    made = make_team()
    stranger = make_user()
    with pytest.raises(BotAccessError):
        gate.request_access("bot-1", team_id=made["team"].id, requester_user_id=stranger["user_id"])


def test_request_when_access_already_granted_is_refused(gate, team_store, make_team, make_user):
    made = make_team()
    member = make_user()
    team_store.add_member(made["team"].id, member["user_id"], added_by=made["owner"]["user_id"])
    gate.grant_access(
        "bot-1",
        member["user_id"],
        team_id=made["team"].id,
        granted_by=made["owner"]["user_id"],
    )
    with pytest.raises(BotAccessError):
        gate.request_access("bot-1", team_id=made["team"].id, requester_user_id=member["user_id"])


def test_second_pending_request_returns_the_existing_one(gate, team_store, make_team, make_user):
    made = make_team()
    member = make_user()
    team_store.add_member(made["team"].id, member["user_id"], added_by=made["owner"]["user_id"])
    first = gate.request_access(
        "bot-1", team_id=made["team"].id, requester_user_id=member["user_id"]
    )
    second = gate.request_access(
        "bot-1", team_id=made["team"].id, requester_user_id=member["user_id"]
    )
    assert first.id == second.id
    assert len(gate.list_requests(made["team"].id)) == 1


def test_deny_is_final(gate, team_store, make_team, make_user):
    made = make_team()
    member = make_user()
    team_store.add_member(made["team"].id, member["user_id"], added_by=made["owner"]["user_id"])
    request = gate.request_access(
        "bot-1", team_id=made["team"].id, requester_user_id=member["user_id"]
    )
    denied = gate.deny(request.id, approver_user_id=made["owner"]["user_id"])
    assert denied.status == "denied"
    assert gate.has_access(member["user_id"], "bot-1") is False
    with pytest.raises(BotAccessError):
        gate.deny(request.id, approver_user_id=made["owner"]["user_id"])
    with pytest.raises(BotAccessError):
        gate.approve(request.id, approver_user_id=made["owner"]["user_id"])


def test_decision_by_member_is_refused(gate, team_store, make_team, make_user):
    made = make_team()
    first = make_user()
    second = make_user()
    team_store.add_member(made["team"].id, first["user_id"], added_by=made["owner"]["user_id"])
    team_store.add_member(made["team"].id, second["user_id"], added_by=made["owner"]["user_id"])
    request = gate.request_access(
        "bot-1", team_id=made["team"].id, requester_user_id=first["user_id"]
    )
    with pytest.raises(NotTeamOwnerError):
        gate.approve(request.id, approver_user_id=second["user_id"])
    with pytest.raises(NotTeamOwnerError):
        gate.deny(request.id, approver_user_id=second["user_id"])


def test_approve_twice_is_idempotent(gate, team_store, make_team, make_user):
    made = make_team()
    member = make_user()
    team_store.add_member(made["team"].id, member["user_id"], added_by=made["owner"]["user_id"])
    request = gate.request_access(
        "bot-1", team_id=made["team"].id, requester_user_id=member["user_id"]
    )
    first = gate.approve(request.id, approver_user_id=made["owner"]["user_id"])
    second = gate.approve(request.id, approver_user_id=made["owner"]["user_id"])
    assert first.id == second.id == request.id
    assert gate.list_granted_user_ids("bot-1") == [member["user_id"]]


def test_bot_owner_can_decide_when_lookup_is_wired(test_dsn, team_store, make_team, make_user):
    made = make_team()
    member = make_user()
    bot_owner = make_user()
    team_store.add_member(made["team"].id, member["user_id"], added_by=made["owner"]["user_id"])
    wired = BotAccessGate(test_dsn, bot_owner_lookup=lambda bot_id: bot_owner["user_id"])
    request = wired.request_access(
        "bot-9", team_id=made["team"].id, requester_user_id=member["user_id"]
    )
    approved = wired.approve(request.id, approver_user_id=bot_owner["user_id"])
    assert approved.status == "approved"


def test_decide_unknown_request_raises(gate, make_team):
    made = make_team()
    with pytest.raises(BotAccessError):
        gate.approve(
            "00000000-0000-0000-0000-000000000000",
            approver_user_id=made["owner"]["user_id"],
        )


def test_list_requests_filters_by_status(gate, team_store, make_team, make_user):
    made = make_team()
    first = make_user()
    second = make_user()
    for user in (first, second):
        team_store.add_member(made["team"].id, user["user_id"], added_by=made["owner"]["user_id"])
    pending = gate.request_access(
        "bot-1", team_id=made["team"].id, requester_user_id=first["user_id"]
    )
    decided = gate.request_access(
        "bot-1", team_id=made["team"].id, requester_user_id=second["user_id"]
    )
    gate.deny(decided.id, approver_user_id=made["owner"]["user_id"])
    assert [r.id for r in gate.list_requests(made["team"].id, status="pending")] == [pending.id]
    assert [r.id for r in gate.list_requests(made["team"].id, status="denied")] == [decided.id]
    assert len(gate.list_requests(made["team"].id)) == 2
