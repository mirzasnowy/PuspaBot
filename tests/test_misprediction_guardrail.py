"""Misprediction guardrail end to end: every path feeds the loop.

The learning loop's guardrail used to be stubbed out of the
outcome-watcher tests because its lazy import of the evidence
renderer was believed broken on the scrubbed main (the deleted
private sandbox chain). The chain resolves now through the
public ``puspa.policy`` allowlist, so these tests run the real
guardrail on every path a misprediction can take into the
loop: the watcher with an injected loop, the watcher with the
shared default loop, the world model on a missed prediction,
and the module-level function itself. No stub stands in for
the classifier anywhere here.

The guardrail itself is still enforced, not bypassed: a
record carrying bare figures with no evidence behind it
(orphan numbers) or one marked unverified is quarantined and
never teaches, on every path.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from puspa.evidence.renderer import find_orphan_numbers as renderer_find_orphans
from puspa.memory.curriculum_guardrails import (
    classify_experience,
    find_orphan_numbers,
)
from puspa.memory.learning_loop import (
    LearningLoop,
    get_learning_loop,
    record_misprediction,
    reset_learning_loop,
)
from puspa.sentinel.outcome_watcher import OutcomeWatcher
from puspa.world.model import WorldModel

NOW = datetime(2026, 11, 21, 12, 0, 0, tzinfo=UTC)


def _outcome(**over):
    outcome = {
        "outcome_id": "evt-outcome-1",
        "task": "pay the electricity bill",
        "predicted": "bill unpaid",
        "observed": "paid",
        "user_id": "user-1",
        "task_id": "task-1",
    }
    outcome.update(over)
    return outcome


class _FakeWriter:
    """A ledger writer that only records what it was asked to write."""

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(dict(kwargs))
        record = {"outcome_id": f"evt-late-{len(self.calls)}"}
        record.update(kwargs)
        return record


# ---------------------------------------------------------------------------
# Watcher path: injected loop
# ---------------------------------------------------------------------------


def test_watcher_late_failure_teaches_a_real_injected_loop():
    """A plain-text late failure reaches the loop and teaches it."""
    loop = LearningLoop()
    watcher = OutcomeWatcher(record_outcome=_FakeWriter(), learning_loop=loop)
    watcher.watch(_outcome(), recheck=lambda: "unpaid", now=NOW)
    (result,) = watcher.check_due(NOW + timedelta(days=1))
    assert result.status == "late_failure"
    assert result.misprediction is not None
    assert result.misprediction.get("quarantined") is not True
    assert loop.predict("pay the electricity bill") == "unpaid"


def test_watcher_late_failure_with_bare_figures_quarantines():
    """A late failure with bare figures is quarantined, never taught.

    The fresh value is a price with no evidence behind it, so the
    real guardrail quarantines the record (orphan numbers) and the
    loop keeps no correction for the task.
    """
    loop = LearningLoop()
    watcher = OutcomeWatcher(record_outcome=_FakeWriter(), learning_loop=loop)
    watcher.watch(
        _outcome(outcome_id="evt-price", observed="Rp100.000"),
        recheck=lambda: "Rp120.000",
        now=NOW,
    )
    (result,) = watcher.check_due(NOW + timedelta(days=1))
    assert result.status == "late_failure"
    assert result.misprediction is not None
    assert result.misprediction.get("quarantined") is True
    assert "orphan_numbers" in result.misprediction.get("quarantine_reasons", [])
    assert loop.predict("pay the electricity bill") is None


# ---------------------------------------------------------------------------
# Watcher path: shared default loop
# ---------------------------------------------------------------------------


def test_watcher_feeds_the_shared_default_loop():
    """The default loop learns from a watcher late failure."""
    reset_learning_loop()
    try:
        watcher = OutcomeWatcher(record_outcome=_FakeWriter())
        task = "guardrail default loop task 7d2a41"
        watcher.watch(_outcome(task=task), recheck=lambda: "unpaid", now=NOW)
        (result,) = watcher.check_due(NOW + timedelta(days=1))
        assert result.status == "late_failure"
        assert result.misprediction is not None
        assert result.misprediction.get("quarantined") is not True
        assert get_learning_loop().predict(task) == "unpaid"
    finally:
        reset_learning_loop()


# ---------------------------------------------------------------------------
# World-model path: a missed prediction reaches the default loop
# ---------------------------------------------------------------------------


def test_world_model_miss_reaches_the_default_loop():
    """A world-model miss lands in the loop as a correction.

    The model predicts the price stays flat, the world shows a
    higher price, and the default loop then predicts the
    observed price for the same task.
    """
    reset_learning_loop()
    try:
        model = WorldModel()
        model.ingest_graph(
            [{"id": "P1", "kind": "product", "attributes": {"price": 10000, "store": "S1"}}]
        )
        prediction = model.predict({"type": "check_price", "product_id": "P1", "store": "S1"})
        assert prediction.outcome["price"] == 10000
        result = model.update(
            {
                "action": {"type": "check_price", "product_id": "P1", "store": "S1"},
                "observed": {"price": 12000},
                "task": "check_price:P1@S1",
                "day": 0,
            }
        )
        assert result["correct"] is False
        assert len(model.mispredictions) == 1
        assert get_learning_loop().predict("check_price:P1@S1") == {"price": 12000}
    finally:
        reset_learning_loop()


# ---------------------------------------------------------------------------
# Module-level path and the guardrail itself
# ---------------------------------------------------------------------------


def test_module_level_record_misprediction_teaches():
    """The module-level seam teaches the default loop."""
    reset_learning_loop()
    try:
        record = {
            "task": "guardrail module task 3c9e08",
            "predicted": "sunny",
            "observed": "rainy",
            "error": "forecast was wrong",
            "context": {},
        }
        stored = record_misprediction(record)
        assert stored.get("quarantined") is not True
        assert get_learning_loop().predict("guardrail module task 3c9e08") == "rainy"
    finally:
        reset_learning_loop()


def test_unverified_record_still_quarantines():
    """The guardrail is enforced, not bypassed: unverified never teaches."""
    loop = LearningLoop()
    stored = loop.record_misprediction(
        {
            "task": "guardrail unverified task 5b71d0",
            "predicted": "paid",
            "observed": "unpaid",
            "error": "late change",
            "context": {},
        },
        verified=False,
    )
    assert stored.get("quarantined") is True
    assert "unverified" in stored.get("quarantine_reasons", [])
    assert loop.predict("guardrail unverified task 5b71d0") is None


def test_guardrail_and_renderer_agree_on_orphan_numbers():
    """The guardrail shares the renderer's one orphan-number definition.

    A bare price is an orphan number in both places, a price
    carried by an evidence marker is not, and plain text has
    none, so the guardrail cannot drift away from the renderer.
    """
    bare = "the rice costs Rp100.000 today"
    marked = "the rice costs {harga:bukti-41} today"
    plain = "the rice is cheaper today"
    assert find_orphan_numbers(bare) == renderer_find_orphans(bare)
    assert find_orphan_numbers(bare) == ["Rp100.000"]
    assert find_orphan_numbers(marked) == renderer_find_orphans(marked) == []
    assert find_orphan_numbers(plain) == renderer_find_orphans(plain) == []
    assert classify_experience(predicted="Rp100.000", observed="Rp120.000")[
        "quarantine_reasons"
    ] == ["orphan_numbers"]
    assert classify_experience(predicted="paid", observed="unpaid")["quarantined"] is False
