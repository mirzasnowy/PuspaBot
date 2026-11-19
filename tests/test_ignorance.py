"""Tests for the ignorance map (Kosmos K6).

All tests are in-memory and deterministic: no database, no network,
no randomness. The repo-wide conftest still runs its Postgres reset
fixture for every test file; nothing in this file touches the
database.
"""

import pytest

from puspa.world import IgnoranceArea, WorldModel, plan
from puspa.world.ignorance import (
    UNKNOWN_CONFIDENCE,
    area_key,
    parse_area_key,
    probe_action_for,
)
from puspa.world.model import HABIT_MIN_OBSERVATIONS as MODEL_HABIT_MIN_OBSERVATIONS
from puspa.world.simulator import PRODUCT_ID, STORES


def test_habit_min_observations_matches_model():
    """The repeated constant in ignorance.py must track model.py.

    The value is duplicated on purpose (importing the model
    would be circular); this test is the guard that catches
    drift the day one side changes.
    """
    from puspa.world.ignorance import HABIT_MIN_OBSERVATIONS

    assert HABIT_MIN_OBSERVATIONS == MODEL_HABIT_MIN_OBSERVATIONS == 3


def test_empty_model_has_empty_map():
    model = WorldModel()
    assert model.ignorance_map() == []
    assert model.is_unknown("price", "kopi@tokopedia") is False


def test_registered_price_area_is_no_evidence():
    model = WorldModel()
    area = model.register_unknown("price", "kopi@tokopedia")
    assert area is not None
    assert area.key == "price:kopi@tokopedia"
    assert area.domain == "price"
    assert area.topic == "kopi@tokopedia"
    assert area.reason == "no_evidence"
    assert area.confidence is None
    assert area.observations == 0
    assert area.first_seen_day == 0
    assert area.last_seen_day == 0
    assert model.ignorance_map() == [area]
    assert model.is_unknown("price", "kopi@tokopedia") is True
    assert area.to_dict() == {
        "key": "price:kopi@tokopedia",
        "domain": "price",
        "topic": "kopi@tokopedia",
        "reason": "no_evidence",
        "confidence": None,
        "observations": 0,
        "first_seen_day": 0,
        "last_seen_day": 0,
    }


def test_area_disappears_when_evidence_arrives():
    model = WorldModel()
    model.register_unknown("price", "kopi@tokopedia")
    assert model.is_unknown("price", "kopi@tokopedia") is True
    model.ingest_task_result(
        {
            "task": "watch_price",
            "success": True,
            "result": {"product_id": "kopi", "store": "tokopedia", "price": 15000},
        }
    )
    assert model.ignorance_map() == []
    assert model.is_unknown("price", "kopi@tokopedia") is False
    assert model.probe_for("price", "kopi@tokopedia") is None


def test_register_known_topic_is_noop():
    model = WorldModel()
    model.ingest_task_result(
        {
            "task": "watch_price",
            "success": True,
            "result": {"product_id": "kopi", "store": "tokopedia", "price": 15000},
        }
    )
    assert model.register_unknown("price", "kopi@tokopedia") is None
    assert model.ignorance_map() == []


def test_reregister_refreshes_last_seen_day_only():
    model = WorldModel()
    first = model.register_unknown("price", "kopi@tokopedia")
    assert first is not None and first.first_seen_day == 0
    model.day = 5
    second = model.register_unknown("price", "kopi@tokopedia")
    assert second is not None
    assert second.first_seen_day == 0
    assert second.last_seen_day == 5
    assert len(model.ignorance_map()) == 1


def test_low_confidence_area_after_missed_prediction_then_heals():
    model = WorldModel()
    model.day = 15
    action = {"type": "check_price", "product_id": PRODUCT_ID, "store": "Shopee"}
    model.update({"action": action, "observed": {"price": 5100000}, "task": "grounding", "day": 15})
    assert model.ignorance_map() == []
    prediction = model.predict(action)
    assert prediction.outcome["price"] == 5100000
    result = model.update({"action": action, "observed": {"price": 9999999}, "day": 15})
    assert result["correct"] is False
    areas = model.ignorance_map()
    assert len(areas) == 1
    assert areas[0].key == f"price:{PRODUCT_ID}@Shopee"
    assert areas[0].reason == "low_confidence"
    assert areas[0].confidence == pytest.approx(UNKNOWN_CONFIDENCE - 0.2)
    assert areas[0].observations == 2
    # Four correct predictions raise confidence back to the floor
    # in exact arithmetic; float accumulation lands a hair under
    # it (0.5 - 0.2 + 4 x 0.05 == 0.49999999999999994), so one
    # more correct prediction is needed to clear the floor.
    for _ in range(5):
        model.predict(action)
        model.update({"action": action, "observed": {"price": 9999999}, "day": 15})
    assert model.ignorance_map() == []


def test_planner_returns_probe_for_unknown_product():
    model = WorldModel()
    before = model.state
    result = plan(model, {"goal": "find_cheapest_price", "product_id": PRODUCT_ID})
    assert result["feasible"] is False
    assert result["reason"] == "needs_probe"
    assert result["probe"] == {
        "action": {"type": "check_price", "product_id": PRODUCT_ID, "store": STORES[0]},
        "area": f"price:{PRODUCT_ID}@{STORES[0]}",
    }
    assert result["unknown_areas"] == [f"price:{PRODUCT_ID}@{store}" for store in STORES]
    assert len(result["steps"]) == 1
    step = result["steps"][0]
    assert step["action"] == {"type": "check_price", "product_id": PRODUCT_ID, "store": STORES[0]}
    assert step["predicted_outcome"] == {"price": None, "known": False}
    assert step["confidence"] == 0.0
    assert step["probe"] is True
    # The probe plan records the gap but never touches beliefs or state.
    assert model.state == before
    assert [area.key for area in model.ignorance_map()] == sorted(result["unknown_areas"])


def test_probe_then_evidence_makes_plan_feasible():
    model = WorldModel()
    first = plan(model, {"goal": "find_cheapest_price", "product_id": PRODUCT_ID})
    assert first["feasible"] is False
    probe_store = first["probe"]["action"]["store"]
    model.ingest_task_result(
        {
            "task": "watch_price",
            "success": True,
            "result": {"product_id": PRODUCT_ID, "store": probe_store, "price": 5000000},
        }
    )
    remaining = [area.key for area in model.ignorance_map()]
    assert f"price:{PRODUCT_ID}@{probe_store}" not in remaining
    assert len(remaining) == len(STORES) - 1
    second = plan(model, {"goal": "find_cheapest_price", "product_id": PRODUCT_ID})
    assert second["feasible"] is True
    assert second["predicted_outcome"]["cheapest_store"] == probe_store
    assert second["predicted_outcome"]["cheapest_price"] == 5000000


def test_free_topic_area_needs_explicit_resolve():
    model = WorldModel()
    area = model.register_unknown("topic", "whether ana likes oat milk")
    assert area is not None and area.reason == "no_evidence"
    assert model.is_unknown("topic", "whether ana likes oat milk") is True
    assert model.probe_for("topic", "whether ana likes oat milk") is None
    assert model.resolve_unknown("topic", "whether ana likes oat milk") is True
    assert model.ignorance_map() == []
    assert model.resolve_unknown("topic", "whether ana likes oat milk") is False


def test_resolve_unknown_refuses_belief_domains():
    model = WorldModel()
    model.register_unknown("price", "kopi@tokopedia")
    assert model.resolve_unknown("price", "kopi@tokopedia") is False
    assert model.is_unknown("price", "kopi@tokopedia") is True


def test_probe_for_each_domain():
    model = WorldModel()
    cases = {
        ("price", "kopi@tokopedia"): "check_price",
        ("bill", "bill-pln-001"): "check_bill",
        ("schedule", "email"): "estimate_task",
        ("capability", "email"): "attempt_task",
        ("contact", "ana"): "contact_reply",
        ("habit", "email"): "habit_pattern",
    }
    for (domain, topic), action_type in cases.items():
        model.register_unknown(domain, topic)
        probe = model.probe_for(domain, topic)
        assert probe is not None
        assert probe["action"]["type"] == action_type
        assert probe["area"] == area_key(domain, topic)
    assert probe_action_for("topic", "anything") is None


def test_all_domains_register_as_no_evidence_sorted():
    model = WorldModel()
    for domain, topic in [
        ("price", "kopi@tokopedia"),
        ("bill", "bill-pln-001"),
        ("schedule", "email"),
        ("capability", "email"),
        ("contact", "ana"),
        ("habit", "email"),
        ("topic", "whether ana likes oat milk"),
    ]:
        model.register_unknown(domain, topic)
    keys = [area.key for area in model.ignorance_map()]
    assert keys == sorted(keys)
    assert keys == [
        "bill:bill-pln-001",
        "capability:email",
        "contact:ana",
        "habit:email",
        "price:kopi@tokopedia",
        "schedule:email",
        "topic:whether ana likes oat milk",
    ]
    assert all(area.reason == "no_evidence" for area in model.ignorance_map())
    assert [area.key for area in model.ignorance_map(domain="price")] == ["price:kopi@tokopedia"]


def test_area_key_validation_and_roundtrip():
    assert area_key("price", "kopi@tokopedia") == "price:kopi@tokopedia"
    assert parse_area_key("price:kopi@tokopedia") == ("price", "kopi@tokopedia")
    assert parse_area_key("topic:why is the sky blue") == ("topic", "why is the sky blue")
    with pytest.raises(ValueError):
        area_key("weather", "rain")
    with pytest.raises(ValueError):
        area_key("price", "")
    with pytest.raises(ValueError):
        area_key("price", "kopi")
    with pytest.raises(ValueError):
        parse_area_key("no-colon-here")
    with pytest.raises(ValueError):
        parse_area_key("weather:rain")
    with pytest.raises(ValueError):
        probe_action_for("weather", "rain")
    with pytest.raises(ValueError):
        probe_action_for("price", "kopi")
    with pytest.raises(ValueError):
        IgnoranceArea(
            key="price:kopi@tokopedia",
            domain="price",
            topic="teh@tokopedia",
            reason="no_evidence",
        )
    model = WorldModel()
    with pytest.raises(ValueError):
        model.register_unknown("weather", "rain")
    with pytest.raises(ValueError):
        model.ignorance_map(domain="weather")


def test_schedule_area_clears_with_one_observation():
    model = WorldModel()
    model.register_unknown("schedule", "email")
    assert model.is_unknown("schedule", "email") is True
    model.ingest_task_result({"task": "email", "success": True, "result": {"duration_days": 2.0}})
    assert model.is_unknown("schedule", "email") is False


def test_habit_area_needs_repeated_pattern():
    model = WorldModel()
    model.register_unknown("habit", "email")
    model.ingest_task_result(
        {"task": "email", "success": True, "result": {"hour": 9, "weekday": 1}}
    )
    areas = model.ignorance_map()
    assert len(areas) == 1
    assert areas[0].key == "habit:email"
    assert areas[0].reason == "no_evidence"
    assert areas[0].observations == 1
    for _ in range(2):
        model.ingest_task_result(
            {"task": "email", "success": True, "result": {"hour": 9, "weekday": 1}}
        )
    assert model.is_unknown("habit", "email") is False
