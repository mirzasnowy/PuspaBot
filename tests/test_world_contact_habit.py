"""Tests for the contact and habit beliefs in the world model.

All in-memory tests are deterministic: no database, no network, no
randomness. Both belief kinds are learned only from observations,
never invented: a contact belief exists only after an observed
interaction with that contact, and a habit exists only after
repeated tasks of the same kind. The database round-trip test
needs a live Postgres and skips itself when the server is not
reachable, following the pattern in `tests/test_world_store.py`.
"""

import json
import os
import secrets

import psycopg
import pytest

from puspa.world import WorldModel
from puspa.world.store import (
    create_schema,
    delete_world_state,
    deserialize_state,
    load_world_model,
    save_world_state,
    serialize_state,
)

DSN = os.environ.get(
    "PUSPA_TEST_DSN", "dbname=puspa_test user=root host=/var/run/postgresql"
)


def _interaction(contact_id, replied, channel="whatsapp", delay=None, day=15):
    """One observed interaction, folded in through a task result."""
    result = {"day": day, "contact_id": contact_id,
              "channel": channel, "replied": replied}
    if delay is not None:
        result["reply_delay_hours"] = delay
    return {"task": "message_contact", "success": True, "result": result}


def _task(task_kind, day, hour=None, weekday=None):
    """One finished task of the user's own history."""
    result = {"day": day}
    if hour is not None:
        result["hour"] = hour
    if weekday is not None:
        result["weekday"] = weekday
    return {"task": task_kind, "success": True, "result": result}


# -- contact beliefs ---------------------------------------------------

def test_contact_belief_absent_before_first_observation():
    model = WorldModel()
    prediction = model.predict({"type": "contact_reply", "contact_id": "sari"})
    assert prediction.outcome == {"contact_id": "sari", "reply_likely": None,
                                  "known": False}
    assert prediction.confidence == 0.0
    assert "sari" not in model._contact_beliefs
    # An unrelated contact's interactions teach nothing about Sari.
    model.ingest_task_result(_interaction("budi", True, day=15))
    assert "sari" not in model._contact_beliefs
    assert "budi" in model._contact_beliefs


def test_contact_belief_forms_from_observed_interactions():
    model = WorldModel()
    model.ingest_task_result(_interaction("sari", True, delay=2, day=15))
    model.ingest_task_result(_interaction("sari", True, delay=3, day=16))
    # Two observations is still trial: verification needs three.
    belief = model._contact_beliefs["sari"]
    assert belief.status == "trial"
    assert belief.reply_rate == 1.0
    model.ingest_task_result(_interaction("sari", True, delay=4, day=17))
    belief = model._contact_beliefs["sari"]
    assert belief.status == "verified"
    assert belief.reply_rate == 1.0
    assert belief.preferred_channel == "whatsapp"
    assert belief.mean_reply_delay_hours == 3.0
    assert belief.version == 1
    prediction = model.predict({"type": "contact_reply", "contact_id": "sari"})
    assert prediction.outcome["reply_likely"] is True
    assert prediction.outcome["reply_rate"] == 1.0
    assert prediction.outcome["preferred_channel"] == "whatsapp"
    assert prediction.outcome["mean_reply_delay_hours"] == 3.0
    assert prediction.confidence > 0.5


def test_contact_belief_counts_silence_and_prefers_usual_channel():
    model = WorldModel()
    model.ingest_task_result(_interaction("sari", True, channel="email", delay=5, day=15))
    model.ingest_task_result(_interaction("sari", False, channel="whatsapp", day=16))
    model.ingest_task_result(_interaction("sari", True, channel="whatsapp", delay=1, day=17))
    belief = model._contact_beliefs["sari"]
    assert belief.reply_rate == 2 / 3
    assert belief.preferred_channel == "whatsapp"
    assert belief.mean_reply_delay_hours == 3.0
    prediction = model.predict({"type": "contact_reply", "contact_id": "sari"})
    assert prediction.outcome["reply_likely"] is True


def test_contact_contradiction_needs_two_consecutive_runs():
    model = WorldModel()
    for day in (15, 16, 17):
        model.ingest_task_result(_interaction("sari", True, delay=2, day=day))
    belief = model._contact_beliefs["sari"]
    assert belief.status == "verified"
    assert belief.reply_rate == 1.0
    # One silence is held as a candidate; the belief does not move.
    model.ingest_task_result(_interaction("sari", False, day=18))
    assert belief.reply_rate == 1.0
    assert belief.replies == 3
    assert len(belief.candidates) == 1
    assert len(model.belief_contradictions) == 1
    assert model.belief_contradictions[0]["kind"] == "contact"
    # A reply in between breaks the streak, so the candidate is dropped.
    model.ingest_task_result(_interaction("sari", True, delay=2, day=19))
    assert belief.consecutive_contradictions == 0
    assert belief.reply_rate == 1.0
    # Two consecutive silences replace the belief as a new version.
    model.ingest_task_result(_interaction("sari", False, day=20))
    model.ingest_task_result(_interaction("sari", False, day=21))
    assert belief.version == 2
    assert belief.reply_rate == 0.0
    assert belief.status == "trial"
    assert len(belief.history) == 1
    assert belief.history[0]["version"] == 1
    assert belief.history[0]["reply_rate"] == 1.0
    assert belief.history[0]["reason"] == "contradiction"
    assert belief.candidates == []


def test_contact_decay_returns_verified_belief_to_trial():
    model = WorldModel()
    for day in (15, 16, 17):
        model.ingest_task_result(_interaction("sari", True, delay=2, day=day))
    belief = model._contact_beliefs["sari"]
    assert belief.status == "verified"
    # 89 days later: nothing decays yet.
    assert model.decay_stale_beliefs(now_day=17 + 89) == []
    # 90 days after the last observation: the contact belief decays.
    decayed = model.decay_stale_beliefs(now_day=17 + 90)
    contact_decayed = [item for item in decayed if item["kind"] == "contact"]
    assert len(contact_decayed) == 1
    assert contact_decayed[0]["contact_id"] == "sari"
    assert belief.status == "trial"
    assert belief.version == 2
    assert belief.confidence == 0.5
    assert belief.history[0]["reason"] == "decay"
    # A decayed belief must earn verification again from new observations.
    prediction = model.predict({"type": "contact_reply", "contact_id": "sari"})
    assert prediction.outcome["known"] is False


def test_contact_reply_scored_by_update():
    model = WorldModel()
    for day in (15, 16, 17):
        model.ingest_task_result(_interaction("sari", True, delay=2, day=day))
    model.predict({"type": "contact_reply", "contact_id": "sari"})
    result = model.update({
        "action": {"type": "contact_reply", "contact_id": "sari"},
        "observed": {"replied": True, "channel": "whatsapp",
                     "reply_delay_hours": 2},
        "task": "message_contact", "day": 18})
    assert result["correct"] is True
    assert result["error"] == 0.0
    # Silence after a reply-likely forecast is a miss, reported onward.
    model.predict({"type": "contact_reply", "contact_id": "sari"})
    result = model.update({
        "action": {"type": "contact_reply", "contact_id": "sari"},
        "observed": {"replied": False, "channel": "whatsapp"},
        "task": "message_contact", "day": 19})
    assert result["correct"] is False
    assert result["error"] == 1.0
    assert len(model.mispredictions) == 1


# -- habit beliefs -----------------------------------------------------

def test_habit_not_formed_from_one_task():
    model = WorldModel()
    model.ingest_task_result(_task("pay_bill", day=15, hour=9, weekday=0))
    assert "pay_bill" in model._habit_beliefs
    belief = model._habit_beliefs["pay_bill"]
    assert belief.observations == 1
    assert belief.status == "trial"
    prediction = model.predict({"type": "habit_pattern", "task_kind": "pay_bill"})
    assert prediction.outcome["pattern_known"] is False
    assert prediction.outcome["peak_hour"] is None
    assert prediction.confidence == 0.0


def test_habit_peak_hour_detected_from_repeated_tasks():
    model = WorldModel()
    model.ingest_task_result(_task("pay_bill", day=15, hour=9, weekday=0))
    model.ingest_task_result(_task("pay_bill", day=16, hour=9, weekday=1))
    # Two tasks at the same hour is a repeat, but verification needs three.
    belief = model._habit_beliefs["pay_bill"]
    assert belief.status == "trial"
    assert belief.peak_hour == 9
    model.ingest_task_result(_task("pay_bill", day=17, hour=9, weekday=2))
    belief = model._habit_beliefs["pay_bill"]
    assert belief.status == "verified"
    assert belief.version == 1
    prediction = model.predict({"type": "habit_pattern", "task_kind": "pay_bill"})
    assert prediction.outcome["pattern_known"] is True
    assert prediction.outcome["peak_hour"] == 9
    assert prediction.outcome["peak_weekday"] is None
    assert prediction.confidence > 0.5


def test_habit_interval_detected_from_regular_tasks():
    model = WorldModel()
    # Same task every 7 days, at scattered hours: no hour pattern,
    # but the interval repeats, so a habit still forms.
    model.ingest_task_result(_task("pay_bill", day=1, hour=8))
    model.ingest_task_result(_task("pay_bill", day=8, hour=14))
    model.ingest_task_result(_task("pay_bill", day=15, hour=20))
    belief = model._habit_beliefs["pay_bill"]
    assert belief.status == "verified"
    assert belief.peak_hour is None
    assert belief.mean_interval_days == 7.0
    prediction = model.predict({"type": "habit_pattern", "task_kind": "pay_bill"})
    assert prediction.outcome["pattern_known"] is True
    assert prediction.outcome["mean_interval_days"] == 7.0


def test_habit_scattered_tasks_form_no_pattern():
    model = WorldModel()
    model.ingest_task_result(_task("pay_bill", day=1, hour=8))
    model.ingest_task_result(_task("pay_bill", day=2, hour=14))
    model.ingest_task_result(_task("pay_bill", day=7, hour=20))
    belief = model._habit_beliefs["pay_bill"]
    assert belief.observations == 3
    assert belief.status == "trial"
    prediction = model.predict({"type": "habit_pattern", "task_kind": "pay_bill"})
    assert prediction.outcome["pattern_known"] is False
    assert prediction.confidence == 0.0


def test_habit_contradiction_promotes_new_version():
    model = WorldModel()
    for day in (15, 16, 17):
        model.ingest_task_result(_task("pay_bill", day=day, hour=9))
    belief = model._habit_beliefs["pay_bill"]
    assert belief.status == "verified"
    assert belief.peak_hour == 9
    # One task at a different hour does not move the belief.
    model.ingest_task_result(_task("pay_bill", day=18, hour=20))
    assert belief.peak_hour == 9
    assert belief.version == 1
    assert len(belief.candidates) == 1
    # A second task at the new hour still does not move it.
    model.ingest_task_result(_task("pay_bill", day=19, hour=20))
    assert belief.peak_hour == 9
    assert belief.version == 1
    # The third confirmation promotes a new version; the old one is kept.
    model.ingest_task_result(_task("pay_bill", day=20, hour=20))
    assert belief.version == 2
    assert belief.peak_hour == 20
    assert belief.status == "verified"
    assert len(belief.history) == 1
    assert belief.history[0]["version"] == 1
    assert belief.history[0]["peak_hour"] == 9
    assert belief.history[0]["reason"] == "contradiction"
    assert belief.candidates == []


def test_habit_decay_returns_verified_belief_to_trial():
    model = WorldModel()
    for day in (15, 16, 17):
        model.ingest_task_result(_task("pay_bill", day=day, hour=9))
    belief = model._habit_beliefs["pay_bill"]
    assert belief.status == "verified"
    assert model.decay_stale_beliefs(now_day=17 + 89) == []
    decayed = model.decay_stale_beliefs(now_day=17 + 90)
    habit_decayed = [item for item in decayed if item["kind"] == "habit"]
    assert len(habit_decayed) == 1
    assert habit_decayed[0]["task_kind"] == "pay_bill"
    assert belief.status == "trial"
    assert belief.version == 2
    assert belief.history[0]["reason"] == "decay"
    prediction = model.predict({"type": "habit_pattern", "task_kind": "pay_bill"})
    assert prediction.outcome["pattern_known"] is False


def test_habit_pattern_scored_by_update():
    model = WorldModel()
    for day in (1, 8, 15):
        model.ingest_task_result(_task("pay_bill", day=day, hour=8))
    model.predict({"type": "habit_pattern", "task_kind": "pay_bill"})
    result = model.update({
        "action": {"type": "habit_pattern", "task_kind": "pay_bill"},
        "observed": {"interval_days": 7, "hour": 8},
        "task": "pay_bill", "day": 22})
    assert result["correct"] is True
    assert result["error"] == 0.0
    # An interval far from the learned 7 days is a miss.
    model.predict({"type": "habit_pattern", "task_kind": "pay_bill"})
    result = model.update({
        "action": {"type": "habit_pattern", "task_kind": "pay_bill"},
        "observed": {"interval_days": 20, "hour": 8},
        "task": "pay_bill", "day": 42})
    assert result["correct"] is False
    assert result["error"] == 13.0


# -- persistence -------------------------------------------------------

def test_contact_and_habit_state_round_trip_without_db():
    """Beliefs survive JSON serialization exactly, no database needed."""
    model = WorldModel()
    for day in (15, 16, 17):
        model.ingest_task_result(_interaction("sari", True, delay=2, day=day))
        model.ingest_task_result(_task("pay_bill", day=day, hour=9))
    payload = json.dumps(serialize_state(model.state))
    restored = WorldModel()
    restored._restore_state(deserialize_state(json.loads(payload)))
    assert restored.state == model.state
    assert (restored.predict({"type": "contact_reply", "contact_id": "sari"})
            .outcome["reply_rate"] == 1.0)
    assert (restored.predict({"type": "habit_pattern", "task_kind": "pay_bill"})
            .outcome["peak_hour"] == 9)
    # An old saved state without the new keys still restores cleanly.
    old_state = {key: value for key, value in model.state.items()
                 if key not in ("contact_beliefs", "habit_beliefs")}
    legacy = WorldModel()
    legacy._restore_state(deserialize_state(serialize_state(old_state)))
    assert legacy._contact_beliefs == {}
    assert legacy._habit_beliefs == {}
    assert (legacy.predict({"type": "contact_reply", "contact_id": "sari"})
            .outcome["known"] is False)


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
    email = f"world-contact-{secrets.token_hex(4)}@contoh.id"
    user_id = create_user(email, secrets.token_urlsafe(24), dsn=DSN)
    agent_id = ensure_puspa_agent(user_id, dsn=DSN)
    return user_id, agent_id


def test_save_load_round_trip_keeps_contact_and_habit_beliefs():
    """Save state, load it into a fresh model: both beliefs intact."""
    if not _dsn_available():
        pytest.skip("Postgres is not running")
    user_id, agent_id = _make_user_agent()
    try:
        model = WorldModel()
        for day in (15, 16, 17):
            model.ingest_task_result(_interaction("sari", True, delay=2, day=day))
            model.ingest_task_result(_task("pay_bill", day=day, hour=9))
        assert load_world_model(user_id, agent_id, dsn=DSN) is None
        save_world_state(user_id, agent_id, model, dsn=DSN)
        restored = load_world_model(user_id, agent_id, dsn=DSN)
        assert restored is not None
        assert restored.state == model.state
        assert (restored.predict({"type": "contact_reply", "contact_id": "sari"})
                .outcome["reply_rate"] == 1.0)
        assert (restored.predict({"type": "contact_reply", "contact_id": "sari"})
                .outcome["preferred_channel"] == "whatsapp")
        assert (restored.predict({"type": "habit_pattern", "task_kind": "pay_bill"})
                .outcome["peak_hour"] == 9)
        assert restored._contact_beliefs["sari"].version == 1
        assert restored._habit_beliefs["pay_bill"].version == 1
    finally:
        delete_world_state(user_id, agent_id, dsn=DSN)
