"""Postgres persistence tests for the Causal Ledger (Kosmos K4).

The round trip goes through real Postgres, not a mock store:
propose and update hypotheses in memory, save them, build a
fresh ledger from zero, load it, and the hypotheses must still
be there AND the read path must behave the same (same
strengths, same ordering, expired ones invisible).

DO NOT run this file while other workers run tests: it shares
the `puspa_test` database with the full suite. The coordinator
runs it alone before push. Each test skips itself when
Postgres is not reachable, so the file is safe to collect
before Postgres is up. Each test cleans up its own rows.

What is proven here:

- the migration is idempotent (run twice, plus the fresh
  schema, with no error);
- save / load round-trips strengths, statuses, evidence, and
  the valid interval, and re-saving after updates upserts;
- the valid-time query answers which claims held at a moment;
- a hypothesis can cite a real ledger event as evidence, and
  the event is really in the ledger — with the closed set of
  33 event types untouched (no 34th type was added).
"""

import os
import secrets
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from puspa.ledger import EVENT_TYPES, Ledger
from puspa.memory.learning_loop import LearningLoop
from puspa.world.causal import CausalLedger
from puspa.world.store import (
    apply_causal_migration,
    causal_hypotheses_valid_at,
    delete_causal_hypotheses,
    load_causal_ledger,
    save_causal_hypotheses,
)
from puspa.world.store import (
    create_schema as create_world_schema,
)

DSN = os.environ.get("PUSPA_TEST_DSN", "dbname=puspa_test user=root host=/var/run/postgresql")

T0 = datetime(2026, 11, 10, 0, 0, tzinfo=UTC)
T1 = datetime(2026, 11, 12, 0, 0, tzinfo=UTC)
BETWEEN = datetime(2026, 11, 11, 0, 0, tzinfo=UTC)


def _dsn_available():
    try:
        with psycopg.connect(DSN, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


def _make_user_agent():
    from puspa.accounts import create_schema as create_account_schema
    from puspa.accounts.access import create_agent_schema
    from puspa.accounts.users import create_user
    from puspa.runtime.agents import ensure_puspa_agent

    create_account_schema(DSN)
    create_agent_schema(DSN)
    create_world_schema(DSN)
    email = f"causal-{secrets.token_hex(4)}@contoh.id"
    user_id = create_user(email, secrets.token_urlsafe(24), dsn=DSN)
    agent_id = ensure_puspa_agent(user_id, dsn=DSN)
    return user_id, agent_id


def _grounded_ledger():
    """Ledger with one live and one contradicted hypothesis."""
    ledger = CausalLedger()
    live = ledger.propose("check_pln", "bill 75000", now=T0)
    ledger.observe("check_pln", "bill 75000", consistent=True, now=T1)
    dead = ledger.propose("pay_bill", "paid", now=T0)
    ledger.observe("pay_bill", "paid", consistent=False, now=T1)
    ledger.observe("pay_bill", "paid", consistent=False, now=T1)
    assert ledger.get(dead.id).status == "contradicted"
    assert ledger.get(dead.id).valid_to == T1
    return ledger, live, dead


# -- migration ---------------------------------------------------------
def test_migration_is_idempotent():
    """Run the migration twice (plus the fresh schema): no error."""
    if not _dsn_available():
        pytest.skip("Postgres is not running")
    apply_causal_migration(DSN)
    apply_causal_migration(DSN)
    create_world_schema(DSN)
    apply_causal_migration(DSN)


# -- save / load -------------------------------------------------------
def test_save_load_round_trip_keeps_hypotheses_and_read_path():
    """Save, load into a fresh ledger: same strengths and order."""
    if not _dsn_available():
        pytest.skip("Postgres is not running")
    user_id, agent_id = _make_user_agent()
    try:
        apply_causal_migration(DSN)
        assert load_causal_ledger(user_id, agent_id, dsn=DSN) is None
        ledger, live, dead = _grounded_ledger()
        assert save_causal_hypotheses(user_id, agent_id, ledger, dsn=DSN) == 2
        loaded = load_causal_ledger(user_id, agent_id, dsn=DSN)
        assert loaded is not None
        assert loaded.get(live.id).strength == pytest.approx(0.6)
        assert loaded.get(dead.id).status == "contradicted"
        assert loaded.get(dead.id).valid_to == T1
        assert [row["id"] for row in loaded.expect("check_pln")] == [live.id]
        assert loaded.expect("pay_bill") == []
        # Re-saving after more observations upserts, not duplicates.
        loaded.observe("check_pln", "bill 75000", consistent=True, now=T1)
        assert save_causal_hypotheses(user_id, agent_id, loaded, dsn=DSN) == 2
        reloaded = load_causal_ledger(user_id, agent_id, dsn=DSN)
        assert len(reloaded.list_all(include_terminal=True)) == 2
        assert reloaded.get(live.id).strength == pytest.approx(0.7)
    finally:
        delete_causal_hypotheses(user_id, agent_id, dsn=DSN)
        assert load_causal_ledger(user_id, agent_id, dsn=DSN) is None


def test_save_rejects_a_non_ledger():
    if not _dsn_available():
        pytest.skip("Postgres is not running")
    user_id, agent_id = _make_user_agent()
    try:
        with pytest.raises(ValueError):
            save_causal_hypotheses(user_id, agent_id, {"not": "a ledger"}, dsn=DSN)
    finally:
        delete_causal_hypotheses(user_id, agent_id, dsn=DSN)


# -- valid time --------------------------------------------------------
def test_valid_time_query_answers_which_claims_held_then():
    """A contradicted claim still reads true before its valid_to."""
    if not _dsn_available():
        pytest.skip("Postgres is not running")
    user_id, agent_id = _make_user_agent()
    try:
        apply_causal_migration(DSN)
        ledger, _live, _dead = _grounded_ledger()
        save_causal_hypotheses(user_id, agent_id, ledger, dsn=DSN)
        held_then = causal_hypotheses_valid_at(user_id, agent_id, BETWEEN, dsn=DSN)
        assert {row["id"] for row in held_then} == {
            row["id"] for row in ledger.list_all(include_terminal=True)
        }
        held_after = causal_hypotheses_valid_at(
            user_id, agent_id, T1 + timedelta(days=1), cause="pay_bill", dsn=DSN
        )
        assert held_after == []
        with pytest.raises(ValueError):
            causal_hypotheses_valid_at(user_id, agent_id, datetime(2026, 11, 11), dsn=DSN)
    finally:
        delete_causal_hypotheses(user_id, agent_id, dsn=DSN)


# -- evidence linkage to real ledger events -----------------------------
def test_hypothesis_can_cite_a_real_ledger_event():
    """Evidence links point at events that are really in the ledger.

    No new event type is added for this: the outcome is recorded
    with the existing `evidence_recorded` type, and the
    hypothesis cites the event by id. The closed set stays 33.
    """
    if not _dsn_available():
        pytest.skip("Postgres is not running")
    assert len(EVENT_TYPES) == 33
    user_id, agent_id = _make_user_agent()
    try:
        apply_causal_migration(DSN)
        ledger = Ledger(dsn=DSN)
        ledger.create_schema()
        event = ledger.append_event(
            task_id="task-causal-evidence",
            agent_id=agent_id,
            event_type="evidence_recorded",
            content={"task": "check_pln", "observed": "bill 75000"},
        )
        loop = LearningLoop(causal_ledger=CausalLedger())
        loop.record_misprediction(
            {
                "task": "check_pln",
                "predicted": 50000,
                "observed": 75000,
                "error": "price moved",
                "context": {},
            },
            evidence_event_id=event["id"],
        )
        save_causal_hypotheses(user_id, agent_id, loop.causal_ledger, dsn=DSN)
        loaded = load_causal_ledger(user_id, agent_id, dsn=DSN)
        rows = loaded.expect("check_pln")
        assert len(rows) == 1
        assert {"kind": "ledger_event", "id": event["id"]} in rows[0]["evidence"]
        stored = ledger.events("task-causal-evidence")
        assert [item["id"] for item in stored] == [event["id"]]
        assert stored[0]["type"] == "evidence_recorded"
        assert ledger.verify_chain("task-causal-evidence")[0] is True
    finally:
        delete_causal_hypotheses(user_id, agent_id, dsn=DSN)
