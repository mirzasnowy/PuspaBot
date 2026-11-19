"""Postgres persistence tests for other-agent goal models (Kosmos K5).

The round trip goes through real Postgres, not a mock store:
build goal models in memory, save them, build a fresh store from
zero, load it, and the models must still be there AND the read
path must behave the same (same strengths, same ordering, same
scored predictions, dropped goals still readable as history).

DO NOT run this file while other workers run tests: it shares
the `puspa_test` database with the full suite. The coordinator
runs it alone before push. Each test skips itself when
Postgres is not reachable, so the file is safe to collect
before Postgres is up. Each test cleans up its own rows.

What is proven here:

- the migration is idempotent (run twice, plus the fresh
  schema, with no error);
- save / load round-trips goals, strengths, statuses, scored
  predictions, and accuracy counters, and re-saving after
  updates upserts;
- the world snapshot round trip keeps the agent models too
  (same `save_world_state` / `load_world_model` path);
- a goal model can cite a real ledger event as evidence, and
  the event is really in the ledger — with the closed set of
  31 event types untouched (no 32nd type was added).
"""

import os
import secrets

import psycopg
import pytest

from puspa.ledger import EVENT_TYPES, Ledger
from puspa.world import WorldModel
from puspa.world.agents import AgentGoalStore
from puspa.world.store import (
    apply_agent_migration,
    delete_agent_models,
    delete_world_state,
    load_agent_models,
    load_world_model,
    save_agent_models,
    save_world_state,
)
from puspa.world.store import (
    create_schema as create_world_schema,
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
    create_world_schema(DSN)
    email = f"agent-k5-{secrets.token_hex(4)}@contoh.id"
    user_id = create_user(email, secrets.token_urlsafe(24), dsn=DSN)
    agent_id = ensure_puspa_agent(user_id, dsn=DSN)
    return user_id, agent_id


def _grounded_store():
    """Sari (reliable reviewer) and Budi (never replies)."""
    store = AgentGoalStore()
    for day in range(3):
        store.observe(
            {
                "contact_id": "sari",
                "day": day,
                "channel": "whatsapp",
                "replied": True,
                "reply_delay_hours": 2.0,
                "asked_for": ["review_work"],
                "agreed_to": ["review_work"],
            }
        )
    for day in range(3):
        store.observe({"contact_id": "budi", "day": day, "channel": "email", "replied": False})
    # One dropped goal for Sari: asked once, refused twice. A
    # refusal is itself a reply, so these count as replies too.
    store.observe({"contact_id": "sari", "day": 3, "replied": True, "asked_for": ["pay_bill"]})
    store.observe({"contact_id": "sari", "day": 4, "replied": True, "declined": ["pay_bill"]})
    store.observe({"contact_id": "sari", "day": 5, "replied": True, "declined": ["pay_bill"]})
    # One scored prediction: Sari agreed to the review.
    predictions = store.predict("sari", {"goal": "review_work"}, day=5)
    agreement = next(item for item in predictions if item.kind == "agreement")
    store.score_prediction("sari", agreement.id, True)
    return store


# -- migration ---------------------------------------------------------


def test_migration_is_idempotent():
    """Run the migration twice (plus the fresh schema): no error."""
    if not _dsn_available():
        pytest.skip("Postgres is not running")
    apply_agent_migration(DSN)
    apply_agent_migration(DSN)
    create_world_schema(DSN)
    apply_agent_migration(DSN)


# -- save / load -------------------------------------------------------


def test_save_load_round_trip_keeps_models_and_read_path():
    """Save, load into a fresh store: same strengths and behaviour."""
    if not _dsn_available():
        pytest.skip("Postgres is not running")
    user_id, agent_id = _make_user_agent()
    try:
        apply_agent_migration(DSN)
        assert load_agent_models(user_id, agent_id, dsn=DSN) is None
        store = _grounded_store()
        assert save_agent_models(user_id, agent_id, store, dsn=DSN) == 2
        loaded = load_agent_models(user_id, agent_id, dsn=DSN)
        assert loaded is not None
        assert len(loaded) == 2
        sari = loaded.get("sari")
        assert sari.goals["review_work"].strength == pytest.approx(1.0)
        assert sari.goals["pay_bill"].status == "dropped"
        assert sari.accuracy == pytest.approx(1.0)
        assert sari.reply_rate == pytest.approx(1.0)
        budi = loaded.get("budi")
        assert budi.reply_rate == pytest.approx(0.0)
        # The read path behaves the same after the round trip.
        predictions = loaded.predict("sari", {"goal": "review_work"}, day=5)
        kinds = {item.kind: item for item in predictions}
        assert kinds["reply"].label == "will_reply"
        assert kinds["next_action"].label == "review_work"
        # Budi has no goals at all: reply + channel only, no
        # next-action or agreement prediction.
        assert {item.kind for item in loaded.predict("budi", day=5)} == {"reply", "channel"}
        # Re-saving after more observations upserts, not duplicates.
        loaded.observe({"contact_id": "sari", "day": 6, "asked_for": ["review_work"]})
        assert save_agent_models(user_id, agent_id, loaded, dsn=DSN) == 2
        reloaded = load_agent_models(user_id, agent_id, dsn=DSN)
        assert len(reloaded) == 2
        assert reloaded.get("sari").observations == 7
    finally:
        delete_agent_models(user_id, agent_id, dsn=DSN)
        assert load_agent_models(user_id, agent_id, dsn=DSN) is None


def test_save_world_model_keeps_agent_models():
    """The world snapshot round trip keeps the goal models too."""
    if not _dsn_available():
        pytest.skip("Postgres is not running")
    user_id, agent_id = _make_user_agent()
    try:
        apply_agent_migration(DSN)
        model = WorldModel()
        model.ingest_task_result(
            {
                "task": "message_contact",
                "success": True,
                "result": {
                    "day": 0,
                    "contact_id": "sari",
                    "channel": "whatsapp",
                    "replied": True,
                    "asked_for": ["review_work"],
                },
            }
        )
        save_world_state(user_id, agent_id, model, dsn=DSN)
        restored = load_world_model(user_id, agent_id, dsn=DSN)
        assert restored is not None
        assert restored.agent_model("sari") is not None
        assert restored.agent_model("sari").goals["review_work"].strength == pytest.approx(0.5)
        assert restored.predict_agent("sari")["known"] is True
    finally:
        delete_world_state(user_id, agent_id, dsn=DSN)
        delete_agent_models(user_id, agent_id, dsn=DSN)


def test_save_rejects_a_non_store():
    if not _dsn_available():
        pytest.skip("Postgres is not running")
    user_id, agent_id = _make_user_agent()
    try:
        with pytest.raises(ValueError):
            save_agent_models(user_id, agent_id, {"not": "a store"}, dsn=DSN)
    finally:
        delete_agent_models(user_id, agent_id, dsn=DSN)


# -- evidence linkage to real ledger events -----------------------------


def test_goal_can_cite_a_real_ledger_event():
    """Evidence links point at events that are really in the ledger.

    No new event type is added for this: the observation is
    recorded with the existing `evidence_recorded` type, and the
    goal cites the event by id. The closed set stays 31.
    """
    if not _dsn_available():
        pytest.skip("Postgres is not running")
    assert len(EVENT_TYPES) == 31
    user_id, agent_id = _make_user_agent()
    try:
        apply_agent_migration(DSN)
        ledger = Ledger(dsn=DSN)
        ledger.create_schema()
        event = ledger.append_event(
            task_id="task-agent-k5-evidence",
            agent_id=agent_id,
            event_type="evidence_recorded",
            content={"task": "message_contact", "observed": "sari agreed to review_work"},
        )
        store = AgentGoalStore()
        store.observe(
            {
                "contact_id": "sari",
                "day": 0,
                "replied": True,
                "agreed_to": ["review_work"],
                "evidence": [{"kind": "ledger_event", "id": str(event["id"])}],
            }
        )
        save_agent_models(user_id, agent_id, store, dsn=DSN)
        loaded = load_agent_models(user_id, agent_id, dsn=DSN)
        goal = loaded.get("sari").goals["review_work"]
        assert {"kind": "ledger_event", "id": str(event["id"])} in goal.evidence
        # The event is really in the ledger.
        stored = ledger.events("task-agent-k5-evidence")
        assert [item["id"] for item in stored] == [event["id"]]
        assert stored[0]["type"] == "evidence_recorded"
    finally:
        delete_agent_models(user_id, agent_id, dsn=DSN)
