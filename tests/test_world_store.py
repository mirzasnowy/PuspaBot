"""Postgres persistence tests for `puspa.world.store`.

The serialization round trip runs without a database. The database
tests need a live Postgres (the coordinator installs
postgresql-16); each one skips itself when the server is not
reachable, so this file is safe to run before Postgres is up.
Run the whole file with `pytest tests/test_world_store.py -q`.
"""

import json
import os
import secrets

import psycopg
import pytest

from puspa.world.model import WorldModel
from puspa.world.store import (
    create_schema,
    delete_world_state,
    deserialize_state,
    load_world_model,
    load_world_state,
    save_world_state,
    serialize_state,
)

DSN = os.environ.get("PUSPA_TEST_DSN", "dbname=puspa_test user=root host=/var/run/postgresql")


def _dsn_available():
    try:
        with psycopg.connect(DSN, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


def _task_result(task_kind, success, duration_days, deadline_days, day):
    return {
        "task": task_kind,
        "success": success,
        "result": {
            "duration_days": duration_days,
            "deadline_days": deadline_days,
            "day": day,
        },
    }


def _grounded_model():
    """Model with price, bill, schedule, and capability beliefs."""
    model = WorldModel()
    model.ingest_graph(
        [
            {
                "id": "P1",
                "kind": "product",
                "attributes": {"price": 10000, "store": "S1"},
            },
            {
                "id": "B1",
                "kind": "bill",
                "attributes": {
                    "amount": 75000,
                    "due_day": 20,
                    "status": "not_due",
                },
            },
        ]
    )
    for day in (15, 16, 17):
        model.ingest_task_result(_task_result("pay_bill", True, 2, 3, day))
    model.predict({"type": "check_price", "product_id": "P1", "store": "S1"})
    model.update(
        {
            "action": {"type": "check_price", "product_id": "P1", "store": "S1"},
            "observed": {"price": 12000},
            "task": "watch_price",
            "day": 17,
        }
    )
    return model


def test_serialize_round_trip_is_exact_without_db():
    """Beliefs survive JSON serialization exactly, no database needed."""
    model = _grounded_model()
    payload = json.dumps(serialize_state(model.state))
    restored = WorldModel()
    restored._restore_state(deserialize_state(json.loads(payload)))
    assert restored.state == model.state
    assert (
        restored.predict({"type": "estimate_task", "task_kind": "pay_bill"}).outcome[
            "expected_days"
        ]
        == 2.0
    )
    assert (
        restored.predict({"type": "attempt_task", "task_kind": "pay_bill"}).outcome["success_rate"]
        == 1.0
    )
    assert (
        restored.predict({"type": "check_price", "product_id": "P1", "store": "S1"}).outcome[
            "price"
        ]
        == 12000
    )


def _make_user_agent():
    from puspa.accounts import create_schema as create_account_schema
    from puspa.accounts.access import create_agent_schema
    from puspa.accounts.users import create_user
    from puspa.runtime.agents import ensure_puspa_agent

    create_account_schema(DSN)
    create_agent_schema(DSN)
    create_schema(DSN)
    email = f"world-{secrets.token_hex(4)}@contoh.id"
    user_id = create_user(email, secrets.token_urlsafe(24), dsn=DSN)
    agent_id = ensure_puspa_agent(user_id, dsn=DSN)
    return user_id, agent_id


def test_save_load_round_trip_keeps_beliefs():
    """Save state, load it into a new model: beliefs identical."""
    if not _dsn_available():
        pytest.skip("Postgres is not running")
    user_id, agent_id = _make_user_agent()
    try:
        model = _grounded_model()
        assert load_world_state(user_id, agent_id, dsn=DSN) is None
        assert load_world_model(user_id, agent_id, dsn=DSN) is None
        saved = save_world_state(user_id, agent_id, model, dsn=DSN)
        assert saved["agent_id"] == agent_id
        restored = load_world_model(user_id, agent_id, dsn=DSN)
        assert restored is not None
        assert restored.state == model.state
        assert (
            restored.predict({"type": "estimate_task", "task_kind": "pay_bill"}).outcome[
                "expected_days"
            ]
            == 2.0
        )
        assert (
            restored.predict({"type": "attempt_task", "task_kind": "pay_bill"}).outcome[
                "success_rate"
            ]
            == 1.0
        )
        assert (
            restored.predict({"type": "check_price", "product_id": "P1", "store": "S1"}).outcome[
                "price"
            ]
            == 12000
        )
        # A later save overwrites the row (upsert, one row per agent).
        model.ingest_task_result(_task_result("pay_bill", True, 2, 3, 18))
        save_world_state(user_id, agent_id, model, dsn=DSN)
        again = load_world_model(user_id, agent_id, dsn=DSN)
        assert again.state == model.state
        with psycopg.connect(DSN) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM world_beliefs WHERE user_id = %s AND agent_id = %s",
                (str(user_id), agent_id),
            ).fetchone()[0]
        assert count == 1
    finally:
        delete_world_state(user_id, agent_id, dsn=DSN)
        # Do NOT delete the user row here: DELETE FROM users cascades
        # into agent_meta, and the database guard (tolak_delete_puspa)
        # rightly refuses to delete the required Puspa agent row.
        # The shared conftest fixture removes the user with TRUNCATE,
        # which does not fire the row-level DELETE trigger.
