"""Teams: create/delete, roles, membership, last-owner guard, RLS.

Tests run against the Postgres test database (puspa_test). The team
schema is applied by the autouse fixture from tests.teams_helpers.
"""

import psycopg
import pytest

from puspa.accounts import rls
from puspa.teams.errors import (
    LastOwnerError,
    NotTeamMemberError,
    NotTeamOwnerError,
    TeamNotFoundError,
)
from tests.teams_helpers import _teams_schema, make_team, team_store

# The imported names are fixtures used by pytest injection (not
# by direct call); listing them keeps ruff from flagging the
# imports as unused or shadowed by the test parameters.
__all__ = ["_teams_schema", "make_team", "team_store"]


def test_create_team_lists_owner_membership(team_store, make_team):
    made = make_team("alpha")
    members = team_store.list_members(made["team"].id)
    assert len(members) == 1
    assert members[0].user_id == made["owner"]["user_id"]
    assert members[0].role == "owner"


def test_get_team_returns_none_for_unknown_id(team_store):
    assert team_store.get_team("00000000-0000-0000-0000-000000000000") is None


def test_get_team_unknown_id_raises_in_require(team_store):
    with pytest.raises(TeamNotFoundError):
        team_store.require_team("00000000-0000-0000-0000-000000000000")


def test_create_team_rejects_empty_name(team_store, make_user):
    owner = make_user()
    with pytest.raises(ValueError):
        team_store.create_team("   ", owner["user_id"])


def test_add_member_by_owner(team_store, make_team, make_user):
    made = make_team()
    member = make_user()
    added = team_store.add_member(
        made["team"].id, member["user_id"], added_by=made["owner"]["user_id"]
    )
    assert added.role == "member"
    assert team_store.is_member(made["team"].id, member["user_id"])
    assert not team_store.is_owner(made["team"].id, member["user_id"])


def test_add_member_by_member_is_refused(team_store, make_team, make_user):
    made = make_team()
    first = make_user()
    second = make_user()
    team_store.add_member(made["team"].id, first["user_id"], added_by=made["owner"]["user_id"])
    with pytest.raises(NotTeamOwnerError):
        team_store.add_member(made["team"].id, second["user_id"], added_by=first["user_id"])


def test_add_member_twice_is_idempotent(team_store, make_team, make_user):
    made = make_team()
    member = make_user()
    team_store.add_member(made["team"].id, member["user_id"], added_by=made["owner"]["user_id"])
    again = team_store.add_member(
        made["team"].id, member["user_id"], added_by=made["owner"]["user_id"]
    )
    assert again.role == "member"
    assert len(team_store.list_members(made["team"].id)) == 2


def test_set_role_promote_by_owner(team_store, make_team, make_user):
    made = make_team()
    member = make_user()
    team_store.add_member(made["team"].id, member["user_id"], added_by=made["owner"]["user_id"])
    promoted = team_store.set_role(
        made["team"].id,
        member["user_id"],
        "owner",
        changed_by=made["owner"]["user_id"],
    )
    assert promoted.role == "owner"
    assert team_store.is_owner(made["team"].id, member["user_id"])


def test_set_role_by_member_is_refused(team_store, make_team, make_user):
    made = make_team()
    member = make_user()
    team_store.add_member(made["team"].id, member["user_id"], added_by=made["owner"]["user_id"])
    with pytest.raises(NotTeamOwnerError):
        team_store.set_role(
            made["team"].id, member["user_id"], "owner", changed_by=member["user_id"]
        )


def test_set_role_for_non_member_raises(team_store, make_team, make_user):
    made = make_team()
    stranger = make_user()
    with pytest.raises(NotTeamMemberError):
        team_store.set_role(
            made["team"].id,
            stranger["user_id"],
            "owner",
            changed_by=made["owner"]["user_id"],
        )


def test_set_role_unknown_role_rejected(team_store, make_team, make_user):
    made = make_team()
    member = make_user()
    team_store.add_member(made["team"].id, member["user_id"], added_by=made["owner"]["user_id"])
    with pytest.raises(ValueError):
        team_store.set_role(
            made["team"].id,
            member["user_id"],
            "admin",
            changed_by=made["owner"]["user_id"],
        )


def test_cannot_demote_the_last_owner(team_store, make_team, make_user):
    made = make_team()
    with pytest.raises(LastOwnerError):
        team_store.set_role(
            made["team"].id,
            made["owner"]["user_id"],
            "member",
            changed_by=made["owner"]["user_id"],
        )


def test_can_demote_an_owner_when_another_remains(team_store, make_team, make_user):
    made = make_team()
    second = make_user()
    team_store.add_member(
        made["team"].id,
        second["user_id"],
        added_by=made["owner"]["user_id"],
        role="owner",
    )
    demoted = team_store.set_role(
        made["team"].id,
        second["user_id"],
        "member",
        changed_by=made["owner"]["user_id"],
    )
    assert demoted.role == "member"
    assert team_store.is_owner(made["team"].id, made["owner"]["user_id"])


def test_remove_member_by_owner(team_store, make_team, make_user):
    made = make_team()
    member = make_user()
    team_store.add_member(made["team"].id, member["user_id"], added_by=made["owner"]["user_id"])
    team_store.remove_member(
        made["team"].id, member["user_id"], removed_by=made["owner"]["user_id"]
    )
    assert not team_store.is_member(made["team"].id, member["user_id"])


def test_member_can_remove_themselves(team_store, make_team, make_user):
    made = make_team()
    member = make_user()
    team_store.add_member(made["team"].id, member["user_id"], added_by=made["owner"]["user_id"])
    team_store.remove_member(made["team"].id, member["user_id"], removed_by=member["user_id"])
    assert not team_store.is_member(made["team"].id, member["user_id"])


def test_member_cannot_remove_another_member(team_store, make_team, make_user):
    made = make_team()
    first = make_user()
    second = make_user()
    team_store.add_member(made["team"].id, first["user_id"], added_by=made["owner"]["user_id"])
    team_store.add_member(made["team"].id, second["user_id"], added_by=made["owner"]["user_id"])
    with pytest.raises(NotTeamOwnerError):
        team_store.remove_member(made["team"].id, second["user_id"], removed_by=first["user_id"])


def test_cannot_remove_the_last_owner(team_store, make_team):
    made = make_team()
    with pytest.raises(LastOwnerError):
        team_store.remove_member(
            made["team"].id,
            made["owner"]["user_id"],
            removed_by=made["owner"]["user_id"],
        )


def test_remove_non_member_is_a_noop(team_store, make_team, make_user):
    made = make_team()
    stranger = make_user()
    team_store.remove_member(
        made["team"].id,
        stranger["user_id"],
        removed_by=made["owner"]["user_id"],
    )


def test_delete_team_by_owner(team_store, make_team):
    made = make_team()
    team_store.delete_team(made["team"].id, requested_by=made["owner"]["user_id"])
    assert team_store.get_team(made["team"].id) is None


def test_delete_team_by_member_is_refused(team_store, make_team, make_user):
    made = make_team()
    member = make_user()
    team_store.add_member(made["team"].id, member["user_id"], added_by=made["owner"]["user_id"])
    with pytest.raises(NotTeamOwnerError):
        team_store.delete_team(made["team"].id, requested_by=member["user_id"])
    assert team_store.get_team(made["team"].id) is not None


def test_delete_team_cascades_membership(team_store, make_team, make_user):
    made = make_team()
    member = make_user()
    team_store.add_member(made["team"].id, member["user_id"], added_by=made["owner"]["user_id"])
    team_store.delete_team(made["team"].id, requested_by=made["owner"]["user_id"])
    assert team_store.list_teams_for_user(member["user_id"]) == []


def test_list_teams_for_user(team_store, make_team, make_user):
    first = make_team("one")
    second = make_team("two")
    other = make_user()
    team_store.add_member(
        second["team"].id,
        first["owner"]["user_id"],
        added_by=second["owner"]["user_id"],
    )
    names = [t.name for t in team_store.list_teams_for_user(first["owner"]["user_id"])]
    assert names == ["one", "two"]
    assert team_store.list_teams_for_user(other["user_id"]) == []


def test_role_of_returns_none_for_non_member(team_store, make_team, make_user):
    made = make_team()
    stranger = make_user()
    assert team_store.role_of(made["team"].id, stranger["user_id"]) is None


# --- RLS: the same checks as tests/test_rls.py, for team tables -----------
# All RLS tests run as puspa_app via SET ROLE; the table owner
# (root) bypasses RLS, so setup rows are created as the owner.


@pytest.fixture
def app_conn(test_dsn):
    """Connection as the application role (subject to RLS)."""
    conn = psycopg.connect(test_dsn)
    conn.execute("SET ROLE puspa_app")
    conn.commit()
    yield conn
    conn.close()


def test_rls_non_member_sees_nothing(app_conn, make_team):
    made = make_team()
    team_id = made["team"].id
    stranger_id = "00000000-0000-0000-0000-000000000001"
    with app_conn.transaction(), rls.as_user(app_conn, stranger_id):
        assert app_conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == 0
        assert app_conn.execute("SELECT COUNT(*) FROM team_members").fetchone()[0] == 0
    # The team still exists for its owner (sanity: RLS hides, not deletes).
    with app_conn.transaction(), rls.as_user(app_conn, made["owner"]["user_id"]):
        rows = app_conn.execute("SELECT id FROM teams").fetchall()
        assert [str(r[0]) for r in rows] == [team_id]


def test_rls_member_sees_team_and_own_row_only(app_conn, team_store, make_team, make_user):
    made = make_team()
    member = make_user()
    team_store.add_member(made["team"].id, member["user_id"], added_by=made["owner"]["user_id"])
    with app_conn.transaction(), rls.as_user(app_conn, member["user_id"]):
        assert app_conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == 1
        rows = app_conn.execute("SELECT user_id, role FROM team_members").fetchall()
        assert [(str(r[0]), r[1]) for r in rows] == [(member["user_id"], "member")]


def test_rls_owner_sees_all_member_rows(app_conn, team_store, make_team, make_user):
    made = make_team()
    member = make_user()
    team_store.add_member(made["team"].id, member["user_id"], added_by=made["owner"]["user_id"])
    with app_conn.transaction(), rls.as_user(app_conn, made["owner"]["user_id"]):
        rows = app_conn.execute(
            "SELECT user_id FROM team_members ORDER BY joined_at, user_id"
        ).fetchall()
        assert {str(r[0]) for r in rows} == {made["owner"]["user_id"], member["user_id"]}


def test_rls_non_owner_update_is_refused(app_conn, team_store, make_team, make_user):
    made = make_team()
    member = make_user()
    team_store.add_member(made["team"].id, member["user_id"], added_by=made["owner"]["user_id"])
    # A member's UPDATE on a row they may not update matches nothing:
    # Postgres RLS returns 0 affected rows, it does not raise (same
    # rule as tests/test_rls.py: "baris tidak terlihat -> 0 baris
    # kena"). Only an UPDATE that passes USING but fails WITH CHECK
    # raises InsufficientPrivilege.
    with app_conn.transaction(), rls.as_user(app_conn, member["user_id"]):
        cur = app_conn.execute(
            "UPDATE teams SET name = 'hijacked' WHERE id = %s",
            (made["team"].id,),
        )
        assert cur.rowcount == 0
    # The name is unchanged (read back as the table owner).
    assert team_store.get_team(made["team"].id).name == made["team"].name


def test_rls_no_user_set_is_fail_closed(app_conn, make_team):
    make_team()
    # No app.current_user_id set at all: nothing is visible.
    with app_conn.transaction():
        assert app_conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == 0
        assert app_conn.execute("SELECT COUNT(*) FROM team_members").fetchone()[0] == 0
