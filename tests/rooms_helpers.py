"""Shared helpers for room tests (B6).

The repo conftest does NOT apply the room schema (it only creates
the accounts/ledger/agent/memory/runtime/graphs/workflow schemas),
so each room test file applies it itself through the autouse
fixture below. The fixture runs after conftest's own autouse
fixture (conftest fixtures run first), so the users table is
already truncated when the room tables are emptied.

The DSN always comes from the ``test_dsn`` fixture (env
``PUSPA_TEST_DSN``), never a hardcoded database name — a worker
running against ``puspa_test_w2`` must not touch ``puspa_test``.
"""

import pytest

from puspa.rooms import create_schema, reset_tables


@pytest.fixture(autouse=True)
def _rooms_schema(test_dsn):
    """Create the room schema, then empty the room tables before and
    after each test."""
    create_schema(test_dsn)
    reset_tables(test_dsn)
    yield
    reset_tables(test_dsn)


@pytest.fixture
def room_store(test_dsn):
    """A RoomStore pointed at the test database."""
    from puspa.rooms import RoomStore

    return RoomStore(test_dsn)


@pytest.fixture
def make_room(test_dsn, make_user):
    """Factory: create a room owned by a fresh user. Returns a dict
    {"room", "owner"} with the owner user dict from make_user."""

    from puspa.rooms import RoomStore

    store = RoomStore(test_dsn)
    counter = {"n": 0}

    def _make(name=None):
        counter["n"] += 1
        owner = make_user()
        room = store.create_room(name or f"room-{counter['n']}", owner["user_id"])
        return {"room": room, "owner": owner}

    return _make


@pytest.fixture
def add_room_member(test_dsn, make_user):
    """Factory: add a fresh user to a room as a member. Returns the
    new member's user dict."""

    from puspa.rooms import RoomStore

    store = RoomStore(test_dsn)

    def _add(room_id, added_by, role="member"):
        member = make_user()
        store.add_member(room_id, member["user_id"], added_by=added_by, role=role)
        return member

    return _add
