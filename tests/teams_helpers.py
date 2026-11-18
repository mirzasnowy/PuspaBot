"""Shared helpers for team tests (G13).

The repo conftest does NOT apply the team schema (it only creates
the accounts/ledger/agent/memory/runtime/graphs/workflow schemas),
so each team test file applies it itself through the autouse
fixture below. The fixture runs after conftest's own autouse
fixture (conftest fixtures run first), so the users table is
already truncated when the team tables are emptied.
"""

import pytest

from puspa.teams import create_schema, reset_tables


@pytest.fixture(autouse=True)
def _teams_schema(test_dsn):
    """Create the team schema, then empty the team tables before and
    after each test."""
    create_schema(test_dsn)
    reset_tables(test_dsn)
    yield
    reset_tables(test_dsn)


@pytest.fixture
def team_store(test_dsn):
    """A TeamStore pointed at the test database."""
    from puspa.teams import TeamStore

    return TeamStore(test_dsn)


@pytest.fixture
def make_team(test_dsn, make_user):
    """Factory: create a team owned by a fresh user. Returns a dict
    {"team", "owner"} with the owner user dict from make_user."""

    from puspa.teams import TeamStore

    store = TeamStore(test_dsn)
    counter = {"n": 0}

    def _make(name=None):
        counter["n"] += 1
        owner = make_user()
        team = store.create_team(name or f"team-{counter['n']}", owner["user_id"])
        return {"team": team, "owner": owner}

    return _make
