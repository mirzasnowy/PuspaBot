"""Durable session store (V2-01).

Covers: a Postgres-backed session resumes after a restart (the
acceptance check for this item: save with one store object, load
with a fresh one), the same round trip on the local SQLite file,
sessions never mixing, `temp:` keys never being saved, delete
dropping the saved snapshot, the factory picking a backend, and
the store working through `SessionState` and the hierarchy
runner (a run's state is still there after the process restarts).

The Postgres tests take the `test_dsn` fixture, so they are
classified as integration tests and run against the
`puspa_test_w1` database. The SQLite tests use a temporary file
and are unit tests: no database, no network.
"""

import pytest

from puspa.runtime.hierarchy import (
    AgentNode,
    HierarchicalPlan,
    HierarchicalRunner,
    InMemorySessionStore,
    SessionState,
)
from puspa.runtime.session_store import (
    PostgresSessionStore,
    SqliteSessionStore,
    open_session_store,
)
from puspa.runtime.tasks import Step


def _step(name):
    return Step(
        name=name,
        inputs={"type": "request"},
        expected_output={"type": "result"},
        permission_class="Pembaca",
        completion_condition=name,
    )


# ----------------------------------------------------------------------
# Postgres backend
# ----------------------------------------------------------------------


def test_postgres_round_trip_survives_restart(test_dsn):
    """Save with one store, load with a fresh one: the restart case."""
    first = PostgresSessionStore(test_dsn)
    first.save(
        "sess-restart",
        {"goal": "compare prices", "step": 2, "user:plan": "pro"},
        user_id="user-1",
    )
    second = PostgresSessionStore(test_dsn)
    assert second.load("sess-restart") == {
        "goal": "compare prices",
        "step": 2,
        "user:plan": "pro",
    }


def test_postgres_missing_session_loads_none(test_dsn):
    store = PostgresSessionStore(test_dsn)
    assert store.load("sess-never-saved") is None


def test_postgres_sessions_do_not_mix(test_dsn):
    store = PostgresSessionStore(test_dsn)
    store.save("sess-a", {"k": "a"}, user_id="user-1")
    store.save("sess-b", {"k": "b"}, user_id="user-2")
    assert store.load("sess-a") == {"k": "a"}
    assert store.load("sess-b") == {"k": "b"}


def test_postgres_later_save_wins(test_dsn):
    store = PostgresSessionStore(test_dsn)
    store.save("sess-overwrite", {"step": 1})
    store.save("sess-overwrite", {"step": 2})
    assert store.load("sess-overwrite") == {"step": 2}


def test_postgres_delete_drops_snapshot(test_dsn):
    store = PostgresSessionStore(test_dsn)
    store.save("sess-gone", {"k": "v"})
    store.delete("sess-gone")
    assert store.load("sess-gone") is None
    store.delete("sess-gone")  # deleting twice is a no-op, never an error


def test_session_state_resumes_after_restart(test_dsn):
    """The acceptance check: `SessionState` round trip, fresh store."""
    store = PostgresSessionStore(test_dsn)
    state = SessionState(session_id="sess-state", user_id="user-1", store=store)
    state.set("goal", "compare prices")
    state.set("user:plan", "pro")
    state.set("temp:scratch", "this turn only")
    state.save()

    fresh = SessionState.load("sess-state", user_id="user-1", store=PostgresSessionStore(test_dsn))
    assert fresh.get("goal") == "compare prices"
    assert fresh.get("user:plan") == "pro"
    assert fresh.get("temp:scratch") is None


def test_session_state_delete_clears_store_and_memory(test_dsn):
    store = PostgresSessionStore(test_dsn)
    state = SessionState(session_id="sess-del", user_id="user-1", store=store)
    state.set("k", "v")
    state.save()
    state.delete_saved()
    assert state.get("k") is None
    assert PostgresSessionStore(test_dsn).load("sess-del") is None


def test_runner_state_resumes_after_restart(test_dsn):
    """A run's state is still there when a fresh store loads it."""
    store = PostgresSessionStore(test_dsn)
    state = SessionState(session_id="sess-run", user_id="user-1", store=store)
    root = AgentNode(name="puspa", role="coordinator")
    runner = HierarchicalRunner(root)
    plan = HierarchicalPlan(steps=[_step("greet")])
    runner.run(plan, state=state, task_id="sess-run")

    resumed = SessionState.load("sess-run", user_id="user-1", store=PostgresSessionStore(test_dsn))
    assert resumed.get("greet") == {"type": "request", "value": None}


def test_postgres_owner_recorded_on_row(test_dsn):
    """The saved row carries the user id and app name."""
    from puspa.db import connect as _db_connect

    store = PostgresSessionStore(test_dsn)
    store.save("sess-owner", {"k": "v"}, user_id="user-9")
    with _db_connect(test_dsn) as conn:
        row = conn.execute(
            "SELECT user_id, app_name FROM runtime_session_states WHERE session_id = %s",
            ("sess-owner",),
        ).fetchone()
    assert row == ("user-9", "puspa")


# ----------------------------------------------------------------------
# SQLite backend (unit tests: no database, no network)
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_sqlite_round_trip_survives_restart(tmp_path):
    """Save with one store, load with a fresh one, same file."""
    path = tmp_path / "sessions.db"
    first = SqliteSessionStore(path)
    first.save("sess-local", {"goal": "x", "step": 3}, user_id="user-1")
    first.close()
    second = SqliteSessionStore(path)
    assert second.load("sess-local") == {"goal": "x", "step": 3}
    second.close()


@pytest.mark.unit
def test_sqlite_missing_delete_and_mixing(tmp_path):
    path = tmp_path / "sessions.db"
    store = SqliteSessionStore(path)
    assert store.load("nope") is None
    store.save("a", {"k": "a"})
    store.save("b", {"k": "b"})
    assert store.load("a") == {"k": "a"}
    assert store.load("b") == {"k": "b"}
    store.delete("a")
    assert store.load("a") is None
    assert store.load("b") == {"k": "b"}
    store.delete("a")  # deleting twice is a no-op
    store.close()


@pytest.mark.unit
def test_sqlite_session_state_round_trip(tmp_path):
    path = tmp_path / "sessions.db"
    state = SessionState(session_id="sess-sqlite", user_id="user-1", store=SqliteSessionStore(path))
    state.set("goal", "compare prices")
    state.set("temp:scratch", "this turn only")
    state.save()
    resumed = SessionState.load("sess-sqlite", user_id="user-1", store=SqliteSessionStore(path))
    assert resumed.get("goal") == "compare prices"
    assert resumed.get("temp:scratch") is None


# ----------------------------------------------------------------------
# Factory and interface
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_open_session_store_needs_one_backend(tmp_path):
    with pytest.raises(ValueError):
        open_session_store()
    with pytest.raises(ValueError):
        open_session_store(dsn="dbname=x", sqlite_path=tmp_path / "s.db")
    store = open_session_store(sqlite_path=tmp_path / "s.db")
    assert isinstance(store, SqliteSessionStore)
    store.close()


def test_open_session_store_postgres(test_dsn):
    store = open_session_store(dsn=test_dsn)
    assert isinstance(store, PostgresSessionStore)
    store.save("sess-factory", {"k": "v"})
    assert store.load("sess-factory") == {"k": "v"}


@pytest.mark.unit
def test_in_memory_store_still_works_and_deletes():
    store = InMemorySessionStore()
    store.save("s", {"k": "v"})
    assert store.load("s") == {"k": "v"}
    store.delete("s")
    assert store.load("s") is None
    store.delete("s")  # deleting twice is a no-op


@pytest.mark.unit
def test_delete_one_key_still_works():
    """`SessionState.delete(key)` drops one key, not the whole state."""
    state = SessionState(session_id="sess-key", user_id="user-1")
    state.set("keep", 1)
    state.set("drop", 2)
    state.delete("drop")
    assert state.get("drop") is None
    assert state.get("keep") == 1
