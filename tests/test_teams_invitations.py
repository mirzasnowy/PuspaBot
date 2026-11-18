"""Invitations: issue/accept/revoke/expiry, hash-only tokens.

Tests run against the Postgres test database (puspa_test). The team
schema is applied by the autouse fixture from tests.teams_helpers.
"""

import hashlib

import pytest

from puspa.teams import InvitationStore
from puspa.teams.errors import InvitationError, NotTeamOwnerError
from tests.teams_helpers import _teams_schema, make_team, team_store

# The imported names are fixtures used by pytest injection (not
# by direct call); listing them keeps ruff from flagging the
# imports as unused or shadowed by the test parameters.
__all__ = ["_teams_schema", "make_team", "team_store"]


@pytest.fixture
def invitation_store(test_dsn):
    return InvitationStore(test_dsn)


def test_issue_returns_token_once_and_stores_only_hash(test_dsn, invitation_store, make_team):
    made = make_team()
    issued = invitation_store.issue_invitation(
        made["team"].id,
        inviter_user_id=made["owner"]["user_id"],
        invited_email="New@Example.ID",
    )
    assert issued["token"]
    assert issued["invitation_id"]
    assert issued["expires_at"] is not None
    invitation = invitation_store.get_invitation(issued["invitation_id"])
    assert invitation.status == "pending"
    assert invitation.invited_email == "new@example.id"  # normalised
    # Only the hash is in the database — the raw token is nowhere.
    import psycopg

    with psycopg.connect(test_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT token_hash FROM invitations WHERE id = %s",
            (issued["invitation_id"],),
        )
        stored_hash = cur.fetchone()[0]
    assert stored_hash == hashlib.sha256(issued["token"].encode()).hexdigest()
    assert stored_hash != issued["token"]


def test_issue_by_member_is_refused(invitation_store, make_team, make_user):
    made = make_team()
    member = make_user()
    with pytest.raises(NotTeamOwnerError):
        invitation_store.issue_invitation(
            made["team"].id,
            inviter_user_id=member["user_id"],
            invited_email="x@example.id",
        )


def test_issue_rejects_empty_email(invitation_store, make_team):
    made = make_team()
    with pytest.raises(ValueError):
        invitation_store.issue_invitation(
            made["team"].id,
            inviter_user_id=made["owner"]["user_id"],
            invited_email="   ",
        )


def test_accept_makes_the_invitee_a_member(invitation_store, team_store, make_team, make_user):
    made = make_team()
    invitee = make_user()
    issued = invitation_store.issue_invitation(
        made["team"].id,
        inviter_user_id=made["owner"]["user_id"],
        invited_email=invitee["email"],
    )
    result = invitation_store.accept_invitation(issued["token"], user_id=invitee["user_id"])
    assert result["team_id"] == made["team"].id
    assert result["already_member"] is False
    assert team_store.is_member(made["team"].id, invitee["user_id"])
    assert invitation_store.get_invitation(issued["invitation_id"]).status == "accepted"


def test_accept_twice_is_idempotent_one_membership(
    invitation_store, team_store, make_team, make_user
):
    made = make_team()
    invitee = make_user()
    issued = invitation_store.issue_invitation(
        made["team"].id,
        inviter_user_id=made["owner"]["user_id"],
        invited_email=invitee["email"],
    )
    first = invitation_store.accept_invitation(issued["token"], user_id=invitee["user_id"])
    second = invitation_store.accept_invitation(issued["token"], user_id=invitee["user_id"])
    assert first["already_member"] is False
    assert second["already_member"] is True
    assert second["team_id"] == made["team"].id
    members = team_store.list_members(made["team"].id)
    assert [m.user_id for m in members].count(invitee["user_id"]) == 1


def test_accept_by_a_different_user_after_consumption_is_refused(
    invitation_store, make_team, make_user
):
    made = make_team()
    first = make_user()
    second = make_user()
    issued = invitation_store.issue_invitation(
        made["team"].id,
        inviter_user_id=made["owner"]["user_id"],
        invited_email=first["email"],
    )
    invitation_store.accept_invitation(issued["token"], user_id=first["user_id"])
    with pytest.raises(InvitationError):
        invitation_store.accept_invitation(issued["token"], user_id=second["user_id"])


def test_accept_unknown_token_is_refused(invitation_store, make_user):
    user = make_user()
    with pytest.raises(InvitationError):
        invitation_store.accept_invitation("no-such-token", user_id=user["user_id"])


def test_accept_already_member_is_idempotent(invitation_store, team_store, make_team, make_user):
    made = make_team()
    member = make_user()
    team_store.add_member(made["team"].id, member["user_id"], added_by=made["owner"]["user_id"])
    issued = invitation_store.issue_invitation(
        made["team"].id,
        inviter_user_id=made["owner"]["user_id"],
        invited_email=member["email"],
    )
    result = invitation_store.accept_invitation(issued["token"], user_id=member["user_id"])
    assert result["already_member"] is True
    assert len(team_store.list_members(made["team"].id)) == 2


def test_revoke_by_owner_kills_the_token(invitation_store, make_team, make_user):
    made = make_team()
    invitee = make_user()
    issued = invitation_store.issue_invitation(
        made["team"].id,
        inviter_user_id=made["owner"]["user_id"],
        invited_email=invitee["email"],
    )
    revoked = invitation_store.revoke_invitation(
        issued["invitation_id"], revoked_by=made["owner"]["user_id"]
    )
    assert revoked.status == "revoked"
    with pytest.raises(InvitationError):
        invitation_store.accept_invitation(issued["token"], user_id=invitee["user_id"])


def test_revoke_by_member_is_refused(invitation_store, make_team, make_user):
    made = make_team()
    member = make_user()
    issued = invitation_store.issue_invitation(
        made["team"].id,
        inviter_user_id=made["owner"]["user_id"],
        invited_email="x@example.id",
    )
    with pytest.raises(NotTeamOwnerError):
        invitation_store.revoke_invitation(issued["invitation_id"], revoked_by=member["user_id"])


def test_revoke_unknown_invitation_raises(invitation_store, make_user):
    user = make_user()
    with pytest.raises(InvitationError):
        invitation_store.revoke_invitation(
            "00000000-0000-0000-0000-000000000000", revoked_by=user["user_id"]
        )


def test_expired_invitation_is_refused_and_marked(test_dsn, invitation_store, make_team, make_user):
    made = make_team()
    invitee = make_user()
    issued = invitation_store.issue_invitation(
        made["team"].id,
        inviter_user_id=made["owner"]["user_id"],
        invited_email=invitee["email"],
    )
    # Force the expiry into the past (test-only direct update).
    import psycopg

    with psycopg.connect(test_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE invitations SET expires_at = now() - interval '1 day' WHERE id = %s",
            (issued["invitation_id"],),
        )
        conn.commit()
    with pytest.raises(InvitationError, match="expired"):
        invitation_store.accept_invitation(issued["token"], user_id=invitee["user_id"])
    assert invitation_store.get_invitation(issued["invitation_id"]).status == "expired"


def test_purge_expired_marks_overdue_invitations(test_dsn, invitation_store, make_team):
    made = make_team()
    first = invitation_store.issue_invitation(
        made["team"].id,
        inviter_user_id=made["owner"]["user_id"],
        invited_email="a@example.id",
    )
    second = invitation_store.issue_invitation(
        made["team"].id,
        inviter_user_id=made["owner"]["user_id"],
        invited_email="b@example.id",
    )
    import psycopg

    with psycopg.connect(test_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE invitations SET expires_at = now() - interval '1 day' WHERE id = %s",
            (first["invitation_id"],),
        )
        conn.commit()
    assert invitation_store.purge_expired() == 1
    assert invitation_store.get_invitation(first["invitation_id"]).status == "expired"
    assert invitation_store.get_invitation(second["invitation_id"]).status == "pending"
    assert invitation_store.purge_expired() == 0


def test_list_invitations_newest_first(invitation_store, make_team):
    made = make_team()
    first = invitation_store.issue_invitation(
        made["team"].id,
        inviter_user_id=made["owner"]["user_id"],
        invited_email="a@example.id",
    )
    second = invitation_store.issue_invitation(
        made["team"].id,
        inviter_user_id=made["owner"]["user_id"],
        invited_email="b@example.id",
    )
    listed = invitation_store.list_invitations(made["team"].id)
    assert [inv.id for inv in listed] == [second["invitation_id"], first["invitation_id"]]
