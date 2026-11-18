"""Postgres persistence tests for the learning loop corrections.

The round trip goes through real Postgres, not a mock store: save
a loop's corrections, build a fresh LearningLoop from zero, load
it, and the corrections must still be there AND the loop must
behave the same (same predictions, same next proposed task).

DO NOT run this file while other workers run tests: it shares the
`puspa_test` database with the full suite. The coordinator runs it
alone before push. Each test skips itself when Postgres is not
reachable, so the file is safe to collect before Postgres is up.
"""

import os
import secrets

import psycopg
import pytest

from puspa.chassis import agent_session
from puspa.memory.learning_loop import LearningLoop
from puspa.memory.loop_store import (
    create_schema,
    delete_loop_state,
    load_learning_loop,
    load_loop_state,
    save_loop_state,
)

DSN = os.environ.get("PUSPA_TEST_DSN", "dbname=puspa_test user=root host=/var/run/postgresql")


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
    create_schema(DSN)
    email = f"loop-{secrets.token_hex(4)}@contoh.id"
    user_id = create_user(email, secrets.token_urlsafe(24), dsn=DSN)
    agent_id = ensure_puspa_agent(user_id, dsn=DSN)
    return user_id, agent_id


def _grounded_loop():
    """Loop with two mispredictions and one feedback correction."""
    loop = LearningLoop()
    loop.record_misprediction(
        {
            "task": "watch_price",
            "predicted": 10000,
            "observed": 12000,
            "error": "price moved",
            "context": {"store": "S1"},
        }
    )
    loop.record_misprediction(
        {
            "task": "watch_price",
            "predicted": 12000,
            "observed": 11500,
            "error": "price moved again",
            "context": {"store": "S1"},
        }
    )
    loop.record_feedback("pay_bill", "wrong", correction="paid 75000")
    return loop


def test_save_load_round_trip_keeps_corrections_and_behaviour():
    """Save, load into a fresh loop from zero: same predictions."""
    if not _dsn_available():
        pytest.skip("Postgres is not running")
    user_id, agent_id = _make_user_agent()
    try:
        loop = _grounded_loop()
        assert load_loop_state(user_id, agent_id, dsn=DSN) is None
        assert load_learning_loop(user_id, agent_id, dsn=DSN) is None
        saved = save_loop_state(user_id, agent_id, loop, dsn=DSN)
        assert saved["corrections"] == 2
        assert saved["experiences"] == 3
        restored = load_learning_loop(user_id, agent_id, dsn=DSN)
        assert restored is not None
        assert restored is not loop
        assert restored.predict("watch_price") == 11500
        assert restored.predict("pay_bill") == "paid 75000"
        assert restored.predict("unknown_task") is None
        assert restored.correction_for("watch_price") == loop.correction_for("watch_price")
        assert restored.correction_for("pay_bill") == loop.correction_for("pay_bill")
        assert restored.propose_next_task() == loop.propose_next_task()
        # One row per correction, plus one row per carrying experience.
        with psycopg.connect(DSN) as conn:
            corrections = conn.execute(
                "SELECT COUNT(*) FROM loop_corrections WHERE user_id = %s AND agent_id = %s",
                (str(user_id), agent_id),
            ).fetchone()[0]
            experiences = conn.execute(
                "SELECT COUNT(*) FROM loop_experiences WHERE user_id = %s AND agent_id = %s",
                (str(user_id), agent_id),
            ).fetchone()[0]
        assert corrections == 2
        assert experiences == 3
        # A later save upserts (one row per task), it never duplicates.
        loop.record_misprediction(
            {
                "task": "watch_price",
                "predicted": 11500,
                "observed": 11000,
                "error": "price moved",
                "context": {"store": "S1"},
            }
        )
        save_loop_state(user_id, agent_id, loop, dsn=DSN)
        again = load_learning_loop(user_id, agent_id, dsn=DSN)
        assert again.predict("watch_price") == 11000
        assert again.correction_for("watch_price")["count"] == 3
        with psycopg.connect(DSN) as conn:
            corrections = conn.execute(
                "SELECT COUNT(*) FROM loop_corrections WHERE user_id = %s AND agent_id = %s",
                (str(user_id), agent_id),
            ).fetchone()[0]
        assert corrections == 2
    finally:
        delete_loop_state(user_id, agent_id, dsn=DSN)
        # Do NOT delete the user row here: DELETE FROM users cascades
        # into agent_meta, and the database guard (tolak_delete_puspa)
        # rightly refuses to delete the required Puspa agent row.
        # The shared conftest fixture removes the user with TRUNCATE,
        # which does not fire the row-level DELETE trigger.


def test_delete_loop_state():
    """Delete removes the saved rows; a second delete reports False."""
    if not _dsn_available():
        pytest.skip("Postgres is not running")
    user_id, agent_id = _make_user_agent()
    try:
        save_loop_state(user_id, agent_id, _grounded_loop(), dsn=DSN)
        assert delete_loop_state(user_id, agent_id, dsn=DSN) is True
        assert load_loop_state(user_id, agent_id, dsn=DSN) is None
        assert delete_loop_state(user_id, agent_id, dsn=DSN) is False
    finally:
        delete_loop_state(user_id, agent_id, dsn=DSN)


def test_session_brain_loads_saved_corrections(monkeypatch):
    """bind_turn with a DSN loads the saved corrections into the brain.

    This is the real startup path: a fresh process (reset brains)
    starts a turn, and the session loop already knows the corrected
    values instead of starting empty.
    """
    if not _dsn_available():
        pytest.skip("Postgres is not running")
    user_id, agent_id = _make_user_agent()
    try:
        save_loop_state(user_id, agent_id, _grounded_loop(), dsn=DSN)
        agent_session.reset_session_brains()
        monkeypatch.setattr(agent_session, "validate_session", lambda token, dsn=None: user_id)
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )

        keys = Ed25519PrivateKey.generate()
        agent_session.bind_turn(
            session_token="tok",
            signing_key=keys,
            public_key=keys.public_key(),
            agent_id=agent_id,
            tools=["read"],
            dsn=DSN,
        )
        brain = agent_session.get_session_brain(user_id, agent_id)
        assert brain.learning_loop.predict("watch_price") == 11500
        assert brain.learning_loop.predict("pay_bill") == "paid 75000"
        assert brain.world_model.learning_loop is brain.learning_loop
        # A second turn reuses the same brain (no reload, no reset).
        agent_session.bind_turn(
            session_token="tok",
            signing_key=keys,
            public_key=keys.public_key(),
            agent_id=agent_id,
            tools=["read"],
            dsn=DSN,
        )
        assert agent_session.get_session_brain(user_id, agent_id) is brain
        agent_session.reset_session_brains()
    finally:
        delete_loop_state(user_id, agent_id, dsn=DSN)
