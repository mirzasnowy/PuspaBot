"""Workspace moves with move-freeze.

Tests run against the Postgres test database (puspa_test). The team
schema is applied by the autouse fixture from tests.teams_helpers.
"""

import pytest

from puspa.teams.errors import MoveError, MoveFrozenError
from puspa.teams.moves import MoveCoordinator
from tests.teams_helpers import _teams_schema, make_team

# The imported names are fixtures used by pytest injection (not
# by direct call); listing them keeps ruff from flagging the
# imports as unused or shadowed by the test parameters.
__all__ = ["_teams_schema", "make_team"]


@pytest.fixture
def moves(test_dsn):
    return MoveCoordinator(test_dsn)


@pytest.fixture
def team_and_owner(make_team):
    made = make_team()
    return made["team"], made["owner"]


def _start(moves, team, owner, bot_id="bot-1"):
    return moves.start_move(
        bot_id,
        team_id=team.id,
        source_workspace="home-a",
        target_workspace="home-b",
        requested_by=owner["user_id"],
    )


def test_start_move_is_pending_and_frozen(moves, team_and_owner):
    team, owner = team_and_owner
    move = _start(moves, team, owner)
    assert move.status == "pending"
    assert move.current_step == "created"
    assert move.bot_id == "bot-1"
    assert moves.is_frozen("bot-1") is True


def test_full_lifecycle_releases_the_freeze(moves, team_and_owner):
    team, owner = team_and_owner
    move = _start(moves, team, owner)
    assert moves.is_frozen("bot-1") is True
    move = moves.begin_move(move.id)
    assert move.status == "in_progress"
    assert moves.is_frozen("bot-1") is True
    move = moves.advance_move(move.id, "copying threads", state={"threads_copied": 3})
    assert move.current_step == "copying threads"
    assert move.state == {"threads_copied": 3}
    move = moves.complete_move(move.id)
    assert move.status == "done"
    assert moves.is_frozen("bot-1") is False


def test_require_not_frozen_raises_while_active(moves, team_and_owner):
    team, owner = team_and_owner
    _start(moves, team, owner)
    with pytest.raises(MoveFrozenError):
        moves.require_not_frozen("bot-1")
    moves.require_not_frozen("bot-2")  # another bot is unaffected


def test_second_active_move_for_same_bot_is_refused(moves, team_and_owner):
    team, owner = team_and_owner
    _start(moves, team, owner)
    with pytest.raises(MoveError):
        _start(moves, team, owner)


def test_failed_move_stays_frozen_and_resumable(moves, team_and_owner):
    team, owner = team_and_owner
    move = _start(moves, team, owner)
    moves.begin_move(move.id)
    moves.advance_move(move.id, "copying threads", state={"threads_copied": 3})
    failed = moves.fail_move(move.id, "target workspace unreachable")
    assert failed.status == "failed"
    assert failed.error == "target workspace unreachable"
    assert failed.state == {"threads_copied": 3}
    # Still frozen: the bot must not run half-moved.
    assert moves.is_frozen("bot-1") is True
    with pytest.raises(MoveFrozenError):
        moves.require_not_frozen("bot-1")
    # Resume from the recorded step, then finish.
    resumed = moves.resume_move(move.id)
    assert resumed.status == "in_progress"
    assert moves.is_frozen("bot-1") is True
    moves.complete_move(move.id)
    assert moves.is_frozen("bot-1") is False


def test_failed_move_can_be_cancelled(moves, team_and_owner):
    team, owner = team_and_owner
    move = _start(moves, team, owner)
    moves.begin_move(move.id)
    moves.fail_move(move.id, "boom")
    cancelled = moves.cancel_move(move.id)
    assert cancelled.status == "cancelled"
    assert moves.is_frozen("bot-1") is False


def test_cancel_pending_move_releases_freeze(moves, team_and_owner):
    team, owner = team_and_owner
    move = _start(moves, team, owner)
    assert moves.cancel_move(move.id).status == "cancelled"
    assert moves.is_frozen("bot-1") is False
    # A new move can start afterwards.
    assert _start(moves, team, owner).status == "pending"


def test_illegal_transitions_are_refused(moves, team_and_owner):
    team, owner = team_and_owner
    move = _start(moves, team, owner)
    # pending -> done is not a transition (must begin first).
    with pytest.raises(MoveError):
        moves.complete_move(move.id)
    # pending -> resume is not a transition (only failed resumes).
    with pytest.raises(MoveError):
        moves.resume_move(move.id)
    moves.begin_move(move.id)
    moves.complete_move(move.id)
    # done is terminal.
    with pytest.raises(MoveError):
        moves.begin_move(move.id)
    with pytest.raises(MoveError):
        moves.fail_move(move.id, "too late")
    with pytest.raises(MoveError):
        moves.cancel_move(move.id)
    with pytest.raises(MoveError):
        moves.advance_move(move.id, "nope")


def test_unknown_move_raises(moves):
    with pytest.raises(MoveError):
        moves.begin_move("00000000-0000-0000-0000-000000000000")
    assert moves.get_move("00000000-0000-0000-0000-000000000000") is None


def test_start_move_validates_inputs(moves, team_and_owner):
    team, owner = team_and_owner
    with pytest.raises(ValueError):
        moves.start_move(
            "  ",
            team_id=team.id,
            source_workspace="a",
            target_workspace="b",
            requested_by=owner["user_id"],
        )
    with pytest.raises(ValueError):
        moves.start_move(
            "bot-1",
            team_id=team.id,
            source_workspace="same",
            target_workspace="same",
            requested_by=owner["user_id"],
        )
    with pytest.raises(ValueError):
        moves.fail_move(_start(moves, team, owner, bot_id="bot-9").id, "   ")


def test_advance_rejects_non_json_state(moves, team_and_owner):
    team, owner = team_and_owner
    move = _start(moves, team, owner)
    with pytest.raises(TypeError):
        moves.advance_move(move.id, "step", state={"bad": object()})


def test_list_moves_filters(moves, team_and_owner):
    team, owner = team_and_owner
    first = _start(moves, team, owner, bot_id="bot-1")
    second = _start(moves, team, owner, bot_id="bot-2")
    moves.begin_move(second.id)
    moves.complete_move(second.id)
    assert [m.id for m in moves.list_moves(bot_id="bot-1")] == [first.id]
    assert [m.id for m in moves.list_moves(status="done")] == [second.id]
    assert [m.id for m in moves.list_moves(status="pending")] == [first.id]
    assert len(moves.list_moves()) == 2
    with pytest.raises(ValueError):
        moves.list_moves(status="nope")
