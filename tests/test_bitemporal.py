"""Bitemporal state tests (Kosmos K3).

Pure in-memory tests run without a database. The database tests
need a live Postgres (the coordinator installs postgresql-16);
each one skips itself when the server is not reachable, and each
one cleans up its own rows, so this file is safe to run before
Postgres is up. Run with:
`pytest tests/test_bitemporal.py -q` (SOLO, no -n: the shared
puspa_test database belongs to the coordinator's central suite).

What is proven here:

- a correction appends a new version and never deletes history
  (the old value stays readable through the known-time query);
- the as-of known-time query returns the old belief;
- the as-of valid-time query returns the fact that was true then;
- the ledger hash inputs are unchanged (a pinned hash vector);
- the ledger chain still verifies after adding bitemporal
  metadata, and old entries backfill honestly;
- the migration is idempotent.
"""

import os
from datetime import UTC, datetime

import psycopg
import pytest

from puspa.ledger import EVENT_TYPES, Ledger, compute_event_hash
from puspa.ledger.errors import LedgerError
from puspa.world.bitemporal import (
    BeliefVersion,
    BitemporalBeliefStore,
    belief_key,
)
from puspa.world.store import (
    apply_bitemporal_migration,
    beliefs_known_at,
    beliefs_valid_at,
    delete_belief_history,
    load_belief_history,
    save_belief_versions,
)
from puspa.world.store import (
    create_schema as create_world_schema,
)

DSN = os.environ.get("PUSPA_TEST_DSN", "dbname=puspa_test user=root host=/var/run/postgresql")
AGENT = "agen-tes"

T0 = datetime(2026, 11, 10, 0, 0, tzinfo=UTC)
T1 = datetime(2026, 11, 12, 0, 0, tzinfo=UTC)
T2 = datetime(2026, 11, 14, 0, 0, tzinfo=UTC)
BETWEEN_T0_T1 = datetime(2026, 11, 11, 0, 0, tzinfo=UTC)
BETWEEN_T1_T2 = datetime(2026, 11, 13, 0, 0, tzinfo=UTC)

#: Hash of one fixed event, computed before the K3 change with
#: the same inputs. If the hash inputs ever change, this test
#: fails loudly instead of silently redefining history.
HASH_PIN = "98b22fbe30409a77db7fdd9efcc949494f511f536164b21234f8ef1936162113"


def _dsn_available():
    try:
        with psycopg.connect(DSN, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


# -- pure: recording ---------------------------------------------------
def test_record_defaults_valid_time_to_known_time():
    """A new belief's valid time defaults to its known time."""
    store = BitemporalBeliefStore()
    version = store.record(belief_key("price", "P1@S1"), {"price": 10000}, known_at=T0)
    assert version.version == 1
    assert version.valid_from == T0
    assert version.valid_to is None
    assert version.known_from == T0
    assert version.known_to is None
    assert version.supersedes is None
    assert version.is_current is True
    assert store.current(belief_key("price", "P1@S1")) is version


def test_record_twice_raises_use_correct():
    store = BitemporalBeliefStore()
    store.record("price:P1@S1", {"price": 10000}, known_at=T0)
    with pytest.raises(ValueError):
        store.record("price:P1@S1", {"price": 12000}, known_at=T1)


def test_naive_datetimes_are_rejected():
    store = BitemporalBeliefStore()
    naive = datetime(2026, 11, 10, 0, 0)
    with pytest.raises(ValueError):
        store.record("price:P1@S1", {"price": 10000}, known_at=naive)
    store.record("price:P1@S1", {"price": 10000}, known_at=T0)
    with pytest.raises(ValueError):
        store.correct("price:P1@S1", {"price": 12000}, known_at=naive)
    with pytest.raises(ValueError):
        store.known_as_of("price:P1@S1", naive)


def test_inverted_intervals_are_rejected():
    store = BitemporalBeliefStore()
    with pytest.raises(ValueError):
        store.record(
            "price:P1@S1",
            {"price": 10000},
            known_at=T0,
            valid_from=T1,
            valid_to=T0,
        )
    store.record("price:P1@S1", {"price": 10000}, known_at=T0)
    with pytest.raises(ValueError):
        # Closing the old valid interval before it even started.
        store.correct(
            "price:P1@S1",
            {"price": 12000},
            known_at=T1,
            end_previous_valid_at=datetime(2026, 11, 9, tzinfo=UTC),
        )


def test_correct_unknown_key_raises():
    store = BitemporalBeliefStore()
    with pytest.raises(ValueError):
        store.correct("price:P1@S1", {"price": 12000}, known_at=T1)


def test_correct_out_of_known_order_raises():
    store = BitemporalBeliefStore()
    store.record("price:P1@S1", {"price": 10000}, known_at=T1)
    with pytest.raises(ValueError):
        store.correct("price:P1@S1", {"price": 12000}, known_at=T0)


# -- pure: correction semantics ----------------------------------------
def test_correction_appends_without_deleting_history():
    """Correcting appends a version; the old row is closed, not changed."""
    store = BitemporalBeliefStore()
    key = belief_key("price", "P1@S1")
    first = store.record(key, {"price": 10000}, known_at=T0)
    second = store.correct(
        key, {"price": 12000}, known_at=T1, valid_from=T1, end_previous_valid_at=T1
    )
    assert second.version == 2
    assert second.supersedes == 1
    assert second.value == {"price": 12000}
    assert second.known_from == T1
    # The first version object itself is frozen and untouched;
    # the store holds a closed copy of it.
    assert first.known_to is None
    assert first.valid_to is None
    history = store.history(key)
    assert len(history) == 2
    assert history[0].value == {"price": 10000}
    assert history[0].known_to == T1
    assert history[0].valid_to == T1
    assert history[0].is_current is False
    assert history[1].is_current is True
    assert store.current(key) is history[1]


def test_late_correction_keeps_old_valid_interval():
    """We were wrong about the past: old valid time stays as believed."""
    store = BitemporalBeliefStore()
    key = belief_key("price", "P1@S1")
    store.record(key, {"price": 10000}, known_at=T0, valid_from=T0)
    # On the 14th we learn the price on the 10th was really 9000.
    store.correct(key, {"price": 9000}, known_at=T2, valid_from=T0)
    old, new = store.history(key)
    assert old.known_to == T2
    assert old.valid_to is None  # NOT closed: the claim stands as believed
    assert old.valid_from == T0
    assert new.valid_from == T0
    assert new.value == {"price": 9000}


# -- pure: as-of queries ------------------------------------------------
def test_known_as_of_returns_old_belief():
    """Known-time query: yesterday's belief survives today's correction."""
    store = BitemporalBeliefStore()
    key = belief_key("price", "P1@S1")
    store.record(key, {"price": 10000}, known_at=T0)
    store.correct(key, {"price": 12000}, known_at=T1, valid_from=T1, end_previous_valid_at=T1)
    assert store.known_as_of(key, BETWEEN_T0_T1).value == {"price": 10000}
    assert store.known_as_of(key, BETWEEN_T1_T2).value == {"price": 12000}
    # At the exact instant the correction was learned, the new
    # version already counts as believed (half-open intervals).
    assert store.known_as_of(key, T1).value == {"price": 12000}
    assert store.known_as_of(key, T0).value == {"price": 10000}
    assert store.known_as_of("price:unknown", T1) is None


def test_valid_as_of_returns_fact_true_then():
    """Valid-time query: the fact that was true then, as known now."""
    store = BitemporalBeliefStore()
    key = belief_key("price", "P1@S1")
    store.record(key, {"price": 10000}, known_at=T0, valid_from=T0)
    store.correct(key, {"price": 12000}, known_at=T1, valid_from=T1, end_previous_valid_at=T1)
    assert store.valid_as_of(key, BETWEEN_T0_T1).value == {"price": 10000}
    assert store.valid_as_of(key, BETWEEN_T1_T2).value == {"price": 12000}
    assert store.valid_as_of(key, T1).value == {"price": 12000}
    # Before any valid interval: no fact was true then.
    assert store.valid_as_of(key, datetime(2026, 11, 9, tzinfo=UTC)) is None


def test_valid_as_of_uses_corrected_past():
    """After a late correction, the past reads corrected (current knowledge)."""
    store = BitemporalBeliefStore()
    key = belief_key("price", "P1@S1")
    store.record(key, {"price": 10000}, known_at=T0, valid_from=T0)
    store.correct(key, {"price": 9000}, known_at=T2, valid_from=T0)
    assert store.valid_as_of(key, BETWEEN_T0_T1).value == {"price": 9000}
    # ...while the known-time query still shows what was believed then.
    assert store.known_as_of(key, BETWEEN_T0_T1).value == {"price": 10000}


def test_as_of_combines_both_times():
    """One version must be both valid then and known then."""
    store = BitemporalBeliefStore()
    key = belief_key("price", "P1@S1")
    store.record(key, {"price": 10000}, known_at=T0, valid_from=T0)
    store.correct(key, {"price": 12000}, known_at=T1, valid_from=T1, end_previous_valid_at=T1)
    both = store.as_of(key, valid_at=BETWEEN_T0_T1, known_at=BETWEEN_T0_T1)
    assert both is not None and both.value == {"price": 10000}
    # Valid then, but known only later: no version satisfies both.
    assert store.as_of(key, valid_at=BETWEEN_T1_T2, known_at=BETWEEN_T0_T1) is None
    # Either argument alone behaves like its single query.
    assert store.as_of(key, known_at=BETWEEN_T0_T1).value == {"price": 10000}
    assert store.as_of(key, valid_at=BETWEEN_T1_T2).value == {"price": 12000}
    assert store.as_of(key).value == {"price": 12000}


# -- pure: serialization -------------------------------------------------
def test_to_dict_from_dict_round_trip_is_exact():
    store = BitemporalBeliefStore()
    key = belief_key("price", "P1@S1")
    store.record(key, {"price": 10000}, known_at=T0, valid_from=T0)
    store.correct(key, {"price": 12000}, known_at=T1, valid_from=T1, end_previous_valid_at=T1)
    store.record(belief_key("bill", "PLN-1"), {"amount": 75000}, known_at=T0)
    restored = BitemporalBeliefStore.from_dict(store.to_dict())
    assert restored.to_dict() == store.to_dict()
    assert restored.known_as_of(key, BETWEEN_T0_T1).value == {"price": 10000}
    assert restored.current(key).value == {"price": 12000}


def test_from_dict_rejects_broken_history():
    gap = BeliefVersion(
        key="price:P1@S1",
        value={"price": 12000},
        version=3,  # version 2 is missing: a gap
        valid_from=T1,
        valid_to=None,
        known_from=T1,
        known_to=None,
        supersedes=2,
    )
    first = BeliefVersion(
        key="price:P1@S1",
        value={"price": 10000},
        version=1,
        valid_from=T0,
        valid_to=None,
        known_from=T0,
        known_to=T1,
    )
    with pytest.raises(ValueError):
        BitemporalBeliefStore.from_versions([first, gap])


# -- pure: ledger hash is untouched --------------------------------------
def test_hash_inputs_unchanged_pinned_vector():
    """compute_event_hash must not gain valid_at as an input."""
    hashed = compute_event_hash(
        task_id="t1",
        agent_id="a1",
        subagent_id=None,
        event_type="heartbeat",
        content={"n": 1},
        parent_id=None,
        previous_hash="GENESIS",
        created_at=datetime(2026, 11, 19, 6, 0, 0, tzinfo=UTC),
    )
    assert hashed == HASH_PIN


def test_event_types_still_closed_at_31():
    assert len(EVENT_TYPES) == 31


# -- database: ledger ----------------------------------------------------
@pytest.fixture
def ledger():
    led = Ledger(DSN)
    led.create_schema()
    return led


@pytest.fixture
def task_id(ledger):
    import uuid

    tid = f"bitemporal-{uuid.uuid4().hex[:12]}"
    yield tid
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DELETE FROM events WHERE task_id = %s", (tid,))


def _append(ledger, task_id, event_type="heartbeat", content=None, **kwargs):
    return ledger.append_event(
        task_id=task_id,
        agent_id=AGENT,
        event_type=event_type,
        content=content if content is not None else {"n": 1},
        **kwargs,
    )


def test_ledger_valid_at_defaults_to_created_at(ledger, task_id):
    if not _dsn_available():
        pytest.skip("Postgres is not running")
    ev = _append(ledger, task_id, event_type="task_created", content={"a": 1})
    assert ev["valid_at"] is not None
    assert ev["valid_at"] == ev["created_at"]
    ok, broken = ledger.verify_chain(task_id)
    assert ok is True and broken == []


def test_ledger_late_recorded_event_orders_by_valid_time(ledger, task_id):
    if not _dsn_available():
        pytest.skip("Postgres is not running")
    monday = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
    tuesday = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
    # Recorded Tuesday-first, Monday-second: insertion order is
    # Tuesday then Monday, but valid order is Monday then Tuesday.
    first_recorded = _append(
        ledger, task_id, event_type="tool_completed", content={"day": "tue"}, valid_at=tuesday
    )
    second_recorded = _append(
        ledger, task_id, event_type="tool_completed", content={"day": "mon"}, valid_at=monday
    )
    assert [e["id"] for e in ledger.events(task_id)] == [
        first_recorded["id"],
        second_recorded["id"],
    ]
    assert [e["id"] for e in ledger.events_by_valid_time(task_id)] == [
        second_recorded["id"],
        first_recorded["id"],
    ]
    window = ledger.events_by_valid_time(task_id, valid_from=monday, valid_to=tuesday)
    assert [e["id"] for e in window] == [second_recorded["id"]]
    ok, broken = ledger.verify_chain(task_id)
    assert ok is True and broken == []


def test_ledger_naive_valid_at_rejected(ledger, task_id):
    if not _dsn_available():
        pytest.skip("Postgres is not running")
    with pytest.raises(LedgerError):
        _append(ledger, task_id, valid_at=datetime(2026, 11, 16, 9, 0))
    assert ledger.events(task_id) == []


# -- database: migration ---------------------------------------------------
def test_migration_is_idempotent_and_backfills_honestly(ledger, task_id):
    if not _dsn_available():
        pytest.skip("Postgres is not running")
    # Two entries written before the migration runs.
    first = _append(ledger, task_id, event_type="task_created", content={"a": 1})
    second = _append(ledger, task_id, event_type="heartbeat", content={"b": 2})
    # Simulate a pre-K3 database: drop the column and the table, and
    # forget that the migration ran. (Numbered migrations trust the
    # schema_migrations table: re-applying a recorded migration is a
    # no-op by design, so dropping the objects alone is not enough
    # to simulate a database the migration never touched.)
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("ALTER TABLE events DROP COLUMN IF EXISTS valid_at")
        conn.execute("DROP TABLE IF EXISTS belief_versions")
        conn.execute("DELETE FROM schema_migrations WHERE version = '0006'")
    # Run the migration twice: the second run must change nothing.
    apply_bitemporal_migration(DSN)
    apply_bitemporal_migration(DSN)
    with psycopg.connect(DSN) as conn:
        rows = conn.execute(
            "SELECT id, created_at, valid_at FROM events WHERE task_id = %s",
            (task_id,),
        ).fetchall()
    assert len(rows) == 2
    for _id, created_at, valid_at in rows:
        # Honest backfill: the past has no separately recorded
        # valid time, so valid_at == created_at.
        assert valid_at == created_at
    assert first["id"] in {str(row[0]) for row in rows}
    assert second["id"] in {str(row[0]) for row in rows}
    # The chain still verifies after the migration.
    ok, broken = ledger.verify_chain(task_id)
    assert ok is True and broken == []
    # New appends after the migration store a real valid_at.
    ev = _append(
        ledger,
        task_id,
        event_type="tool_completed",
        content={"c": 3},
        valid_at=datetime(2026, 11, 16, 9, 0, tzinfo=UTC),
    )
    assert ev["valid_at"] == datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
    ok, broken = ledger.verify_chain(task_id)
    assert ok is True and broken == []


# -- database: beliefs -----------------------------------------------------
def _make_user_agent():
    import secrets

    from puspa.accounts import create_schema as create_account_schema
    from puspa.accounts.access import create_agent_schema
    from puspa.accounts.users import create_user
    from puspa.runtime.agents import ensure_puspa_agent

    create_account_schema(DSN)
    create_agent_schema(DSN)
    create_world_schema(DSN)
    email = f"bitemporal-{secrets.token_hex(4)}@contoh.id"
    user_id = create_user(email, secrets.token_urlsafe(24), dsn=DSN)
    agent_id = ensure_puspa_agent(user_id, dsn=DSN)
    return user_id, agent_id


def test_belief_versions_round_trip_and_as_of_sql():
    if not _dsn_available():
        pytest.skip("Postgres is not running")
    user_id, agent_id = _make_user_agent()
    key = belief_key("price", "P1@S1")
    try:
        store = BitemporalBeliefStore()
        store.record(key, {"price": 10000}, known_at=T0, valid_from=T0)
        store.correct(key, {"price": 12000}, known_at=T1, valid_from=T1, end_previous_valid_at=T1)
        assert load_belief_history(user_id, agent_id, dsn=DSN) is None
        assert save_belief_versions(user_id, agent_id, store, dsn=DSN) == 2
        # Re-saving the same history inserts nothing (immutable versions).
        assert save_belief_versions(user_id, agent_id, store, dsn=DSN) == 0
        restored = load_belief_history(user_id, agent_id, dsn=DSN)
        assert restored is not None
        assert restored.to_dict() == store.to_dict()
        # SQL known-time query: before the correction, the old belief.
        known = beliefs_known_at(user_id, agent_id, BETWEEN_T0_T1, dsn=DSN)
        assert len(known) == 1
        assert known[0]["belief_key"] == key
        assert known[0]["value"] == {"price": 10000}
        assert known[0]["version"] == 1
        known_now = beliefs_known_at(user_id, agent_id, BETWEEN_T1_T2, dsn=DSN)
        assert known_now[0]["value"] == {"price": 12000}
        # SQL valid-time query: the fact true then.
        valid_then = beliefs_valid_at(user_id, agent_id, BETWEEN_T0_T1, dsn=DSN)
        assert len(valid_then) == 1
        assert valid_then[0]["value"] == {"price": 10000}
        valid_now = beliefs_valid_at(user_id, agent_id, BETWEEN_T1_T2, dsn=DSN)
        assert valid_now[0]["value"] == {"price": 12000}
        # One-key filter.
        assert (
            beliefs_known_at(user_id, agent_id, BETWEEN_T0_T1, belief_key="price:nope", dsn=DSN)
            == []
        )
    finally:
        delete_belief_history(user_id, agent_id, dsn=DSN)


def test_belief_late_correction_readable_in_sql():
    if not _dsn_available():
        pytest.skip("Postgres is not running")
    user_id, agent_id = _make_user_agent()
    key = belief_key("price", "P1@S1")
    try:
        store = BitemporalBeliefStore()
        store.record(key, {"price": 10000}, known_at=T0, valid_from=T0)
        # Learned on the 14th: the price on the 10th was really 9000.
        store.correct(key, {"price": 9000}, known_at=T2, valid_from=T0)
        assert save_belief_versions(user_id, agent_id, store, dsn=DSN) == 2
        # Current knowledge says the past price was 9000...
        valid = beliefs_valid_at(user_id, agent_id, BETWEEN_T0_T1, dsn=DSN)
        assert valid[0]["value"] == {"price": 9000}
        # ...while the known-time query still shows what was
        # believed on the 11th.
        known = beliefs_known_at(user_id, agent_id, BETWEEN_T0_T1, dsn=DSN)
        assert known[0]["value"] == {"price": 10000}
    finally:
        delete_belief_history(user_id, agent_id, dsn=DSN)
