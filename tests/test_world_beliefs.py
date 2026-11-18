"""Tests for the new world-model beliefs: schedule and capability.

All tests are in-memory and deterministic: no database, no network,
no randomness. Beliefs are learned only from observations (task
results and action outcomes), never invented.

Note: the repo-wide conftest runs a Postgres reset fixture for
every test file, but nothing in this file touches the database.
Run with `--noconftest` on a host without Postgres.
"""

from puspa.world import WorldModel


def _task_result(task, success, duration_days=None, deadline_days=None, day=15):
    result = {"day": day}
    if duration_days is not None:
        result["duration_days"] = duration_days
    if deadline_days is not None:
        result["deadline_days"] = deadline_days
    return {"task": task, "success": success, "result": result}


def test_schedule_belief_forms_from_task_results():
    model = WorldModel()
    assert model.predict({"type": "estimate_task", "task_kind": "pay_bill"}).outcome == {
        "task_kind": "pay_bill",
        "expected_days": None,
        "known": False,
    }
    model.ingest_task_result(_task_result("pay_bill", True, 2, 3, day=15))
    model.ingest_task_result(_task_result("pay_bill", True, 2, 3, day=16))
    prediction = model.predict({"type": "estimate_task", "task_kind": "pay_bill"})
    assert prediction.outcome["expected_days"] == 2.0
    assert prediction.outcome["deadline_reliable"] is True
    # Two observations is still trial: verification needs three.
    belief = model._schedule_beliefs["pay_bill"]
    assert belief.status == "trial"
    model.ingest_task_result(_task_result("pay_bill", True, 3, 3, day=17))
    belief = model._schedule_beliefs["pay_bill"]
    assert belief.status == "verified"
    assert belief.mean_duration == 7 / 3
    assert belief.on_time_rate == 1.0
    assert belief.version == 1
    prediction = model.predict({"type": "estimate_task", "task_kind": "pay_bill"})
    assert prediction.outcome["expected_days"] == 7 / 3
    assert prediction.confidence > 0.5


def test_schedule_belief_deadline_reliability_tracks_misses():
    model = WorldModel()
    model.ingest_task_result(_task_result("compare_prices", True, 5, 3, day=15))
    model.ingest_task_result(_task_result("compare_prices", True, 5, 3, day=16))
    model.ingest_task_result(_task_result("compare_prices", True, 4, 3, day=17))
    belief = model._schedule_beliefs["compare_prices"]
    assert belief.on_time_rate == 0.0
    prediction = model.predict({"type": "estimate_task", "task_kind": "compare_prices"})
    assert prediction.outcome["deadline_reliable"] is False


def test_capability_belief_forms_from_success_and_failure():
    model = WorldModel()
    assert model.predict({"type": "attempt_task", "task_kind": "pay_bill"}).outcome == {
        "task_kind": "pay_bill",
        "success": None,
        "known": False,
    }
    model.ingest_task_result(_task_result("pay_bill", True, day=15))
    model.ingest_task_result(_task_result("pay_bill", True, day=16))
    model.ingest_task_result(_task_result("pay_bill", False, day=17))
    belief = model._capability_beliefs["pay_bill"]
    assert belief.successes == 2
    assert belief.failures == 1
    assert belief.success_rate == 2 / 3
    assert belief.status == "verified"
    prediction = model.predict({"type": "attempt_task", "task_kind": "pay_bill"})
    assert prediction.outcome["success"] is True
    assert prediction.outcome["success_rate"] == 2 / 3


def test_failed_task_teaches_capability_but_not_price():
    model = WorldModel()
    model.ingest_task_result(
        {
            "task": "buy_from_store",
            "success": False,
            "result": {"product_id": "phone-x1", "store": "Shopee", "price": 5100000, "day": 15},
        }
    )
    belief = model._capability_beliefs["buy_from_store"]
    assert belief.failures == 1
    assert belief.successes == 0
    # The failed result must not seed a price belief.
    assert (
        model.predict({"type": "check_price", "product_id": "phone-x1", "store": "Shopee"}).outcome[
            "known"
        ]
        is False
    )


def test_capability_belief_changes_pay_forecast():
    model = WorldModel()
    model.ingest_graph(
        [
            {
                "id": "bill-pln-001",
                "kind": "bill",
                "attributes": {
                    "amount": 150000,
                    "due_day": 20,
                    "status": "not_due",
                    "amount_due": 150000,
                },
            }
        ]
    )
    # One success, three failures: the agent mostly cannot pay.
    model.ingest_task_result(_task_result("pay_bill", True, day=15))
    model.ingest_task_result(_task_result("pay_bill", False, day=16))
    model.ingest_task_result(_task_result("pay_bill", False, day=17))
    model.ingest_task_result(_task_result("pay_bill", False, day=18))
    prediction = model.predict({"type": "pay_bill", "bill_id": "bill-pln-001"})
    assert prediction.outcome == {"success": False, "reason": "low_capability"}


def test_schedule_contradiction_held_aside_until_confirmed():
    model = WorldModel()
    for day in (15, 16, 17):
        model.ingest_task_result(_task_result("pay_bill", True, 2, 3, day=day))
    belief = model._schedule_beliefs["pay_bill"]
    assert belief.status == "verified"
    assert belief.mean_duration == 2.0
    # One wild duration does not move the belief at all.
    model.ingest_task_result(_task_result("pay_bill", True, 10, 3, day=18))
    assert belief.mean_duration == 2.0
    assert belief.version == 1
    assert len(belief.candidates) == 1
    assert len(model.belief_contradictions) == 1
    # A second wild duration still does not move it.
    model.ingest_task_result(_task_result("pay_bill", True, 10, 3, day=19))
    assert belief.mean_duration == 2.0
    assert belief.version == 1
    # The third confirmation promotes a new version; the old one is kept.
    model.ingest_task_result(_task_result("pay_bill", True, 11, 3, day=20))
    assert belief.version == 2
    assert belief.mean_duration == 31 / 3
    assert len(belief.history) == 1
    assert belief.history[0]["version"] == 1
    assert belief.history[0]["mean_duration"] == 2.0
    assert belief.history[0]["reason"] == "contradiction"
    assert belief.candidates == []


def test_capability_contradiction_needs_two_consecutive_runs():
    model = WorldModel()
    for day in (15, 16, 17):
        model.ingest_task_result(_task_result("pay_bill", True, day=day))
    belief = model._capability_beliefs["pay_bill"]
    assert belief.status == "verified"
    assert belief.success_rate == 1.0
    # One failure is held as a candidate; the belief does not move.
    model.ingest_task_result(_task_result("pay_bill", False, day=18))
    assert belief.success_rate == 1.0
    assert belief.failures == 0
    assert len(belief.candidates) == 1
    # A success in between breaks the streak, so the candidate is dropped.
    model.ingest_task_result(_task_result("pay_bill", True, day=19))
    assert belief.success_rate == 1.0
    assert belief.consecutive_contradictions == 0
    # Two consecutive failures replace the belief as a new version.
    model.ingest_task_result(_task_result("pay_bill", False, day=20))
    model.ingest_task_result(_task_result("pay_bill", False, day=21))
    assert belief.version == 2
    assert belief.success_rate == 0.0
    assert belief.status == "trial"
    assert len(belief.history) == 1
    assert belief.history[0]["version"] == 1
    assert belief.history[0]["success_rate"] == 1.0
    assert belief.history[0]["reason"] == "contradiction"


def test_decay_returns_verified_beliefs_to_trial():
    model = WorldModel()
    for day in (15, 16, 17):
        model.ingest_task_result(_task_result("pay_bill", True, 2, 3, day=day))
    schedule = model._schedule_beliefs["pay_bill"]
    capability = model._capability_beliefs["pay_bill"]
    assert schedule.status == "verified"
    assert capability.status == "verified"
    # 89 days later: nothing decays yet.
    assert model.decay_stale_beliefs(now_day=17 + 89) == []
    # 90 days after the last observation: both decay, old versions kept.
    # The same three tasks also formed a verified habit belief (the
    # 1-day gaps repeat), so it decays in the same sweep.
    decayed = model.decay_stale_beliefs(now_day=17 + 90)
    assert {item["kind"] for item in decayed} == {"schedule", "capability", "habit"}
    assert schedule.status == "trial"
    assert schedule.version == 2
    assert schedule.history[0]["reason"] == "decay"
    assert schedule.history[0]["version"] == 1
    assert capability.status == "trial"
    assert capability.version == 2
    assert capability.history[0]["reason"] == "decay"
    # Decayed beliefs must earn verification again from new observations.
    model.ingest_task_result(_task_result("pay_bill", True, 2, 3, day=17 + 90))
    assert schedule.status == "trial"
    model.ingest_task_result(_task_result("pay_bill", True, 2, 3, day=17 + 91))
    model.ingest_task_result(_task_result("pay_bill", True, 2, 3, day=17 + 92))
    assert schedule.status == "verified"


def test_decay_leaves_trial_beliefs_untouched():
    model = WorldModel()
    model.ingest_task_result(_task_result("pay_bill", True, 2, 3, day=15))
    assert model._schedule_beliefs["pay_bill"].status == "trial"
    assert model.decay_stale_beliefs(now_day=15 + 200) == []
    assert model._schedule_beliefs["pay_bill"].status == "trial"
    assert model._schedule_beliefs["pay_bill"].version == 1


def test_state_restore_round_trip_keeps_new_beliefs():
    model = WorldModel()
    for day in (15, 16, 17):
        model.ingest_task_result(_task_result("pay_bill", True, 2, 3, day=day))
    restored = WorldModel()
    restored._restore_state(model.state)
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
    # The planner copies state too: planning must not touch the beliefs.
    before = model.state
    from puspa.world import plan

    plan(
        model,
        {
            "goal": "sequence",
            "steps": [
                {"type": "estimate_task", "task_kind": "pay_bill"},
                {"type": "attempt_task", "task_kind": "pay_bill"},
            ],
        },
    )
    assert model.state == before


def test_estimate_and_attempt_scored_by_update():
    model = WorldModel()
    model.ingest_task_result(_task_result("pay_bill", True, 2, 3, day=15))
    model.ingest_task_result(_task_result("pay_bill", True, 2, 3, day=16))
    model.ingest_task_result(_task_result("pay_bill", True, 2, 3, day=17))
    prediction = model.predict({"type": "estimate_task", "task_kind": "pay_bill"})
    assert prediction.outcome["expected_days"] == 2.0
    result = model.update(
        {
            "action": {"type": "estimate_task", "task_kind": "pay_bill"},
            "observed": {"duration_days": 2, "deadline_days": 3},
            "task": "pay_bill",
            "day": 18,
        }
    )
    assert result["correct"] is True
    assert result["error"] == 0.0
    model.predict({"type": "attempt_task", "task_kind": "pay_bill"})
    result = model.update(
        {
            "action": {"type": "attempt_task", "task_kind": "pay_bill"},
            "observed": {"success": True},
            "task": "pay_bill",
            "day": 18,
        }
    )
    assert result["correct"] is True
    # A duration far outside the tolerance is a miss.
    model.predict({"type": "estimate_task", "task_kind": "pay_bill"})
    result = model.update(
        {
            "action": {"type": "estimate_task", "task_kind": "pay_bill"},
            "observed": {"duration_days": 9, "deadline_days": 3},
            "task": "pay_bill",
            "day": 19,
        }
    )
    assert result["correct"] is False
    assert result["error"] == 7.0
