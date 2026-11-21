"""Metacognition (Kosmos K20): calibrated confidence per area.

No live model, no network, no database: the calibrator is
in-memory, and the governor it hands numbers to is the real
DepthGovernor (in-memory too).
"""

from __future__ import annotations

import pytest

from puspa.governor import DEEP_RISK, Depth, DepthGovernor
from puspa.mind.metacognition import Metacognition


def _seed_unreliable(mind, area="bill", count=4, confidence=0.9):
    """An area that claimed high confidence and was always wrong."""
    for _ in range(count):
        mind.record_decision(area, confidence, False)
    return mind


def _seed_reliable(mind, area="price", count=4, confidence=0.9):
    """An area that claimed high confidence and was always right."""
    for _ in range(count):
        mind.record_decision(area, confidence, True)
    return mind


def test_brier_score_is_computed_per_area_not_globally():
    """Done criterion: calibration is computed per area.

    Two areas with the same stated confidence (0.9) but opposite
    accuracy must get different Brier scores: the always-right
    area scores (0.9 - 1)^2 = 0.01, the always-wrong area scores
    (0.9 - 0)^2 = 0.81. A single global number could never show
    that split, which is why the module reports per area only.
    """
    mind = Metacognition()
    _seed_reliable(mind)
    _seed_unreliable(mind)
    assert mind.brier_score("price") == pytest.approx(0.01)
    assert mind.brier_score("bill") == pytest.approx(0.81)
    assert mind.brier_score("price") != mind.brier_score("bill")


def test_calibrated_confidence_differs_per_area_for_same_raw_confidence():
    """Done criterion: the same raw confidence calibrates differently.

    Both areas stated 0.9 four times. The reliable area was right
    every time (bias -0.1), so its calibrated confidence rises a
    little; the unreliable area was wrong every time (bias +0.9),
    so its calibrated confidence falls a lot. Weight is 4/14.
    """
    mind = Metacognition()
    _seed_reliable(mind)
    _seed_unreliable(mind)
    weight = 4 / (4 + 10)
    assert mind.calibrated_confidence("price", 0.9) == pytest.approx(0.9 + weight * 0.1)
    assert mind.calibrated_confidence("bill", 0.9) == pytest.approx(0.9 - weight * 0.9)
    assert mind.calibrated_confidence("bill", 0.9) < mind.calibrated_confidence("price", 0.9)


def test_escalation_only_when_uncertainty_and_stakes_are_both_high():
    """Done criterion: escalate only on high uncertainty AND high stakes.

    All four combinations: high/high escalates; high uncertainty
    with low stakes does not (act carefully, log the doubt); low
    uncertainty with high stakes does not (the agent is sure, so
    it acts); low/low does not.
    """
    mind = Metacognition()
    assert mind.should_escalate(uncertainty=0.8, stakes=0.8) is True
    assert mind.should_escalate(uncertainty=0.8, stakes=0.2) is False
    assert mind.should_escalate(uncertainty=0.2, stakes=0.8) is False
    assert mind.should_escalate(uncertainty=0.2, stakes=0.2) is False


def test_assess_escalates_end_to_end_from_area_history():
    """The AND rule, driven by real per-area calibration.

    After 20 confident misses the bill area calibrates a 0.9
    claim down to 0.3, so uncertainty is 0.7: a high-stakes bill
    decision escalates, the same decision at low stakes does
    not. After 20 confident hits the price area calibrates 0.9
    up to about 0.97, so uncertainty is about 0.03: even a
    high-stakes price decision does not escalate.
    """
    mind = Metacognition()
    _seed_unreliable(mind, count=20)
    _seed_reliable(mind, count=20)
    bill_high = mind.assess("bill", 0.9, 0.9)
    assert bill_high.calibrated_confidence == pytest.approx(0.3)
    assert bill_high.uncertainty == pytest.approx(0.7)
    assert bill_high.escalate is True
    assert "escalate to the user" in bill_high.reason
    bill_low = mind.assess("bill", 0.9, 0.2)
    assert bill_low.escalate is False
    assert "do not escalate" in bill_low.reason
    price_high = mind.assess("price", 0.9, 0.9)
    assert price_high.calibrated_confidence == pytest.approx(0.9 + (20 / 30) * 0.1)
    assert price_high.escalate is False


def test_recorded_decision_stores_calibrated_confidence():
    """Every decision stores a calibrated confidence.

    The first decision in an unknown area has nothing to correct
    it with, so calibrated equals raw. Later decisions in an
    area with four confident misses store the corrected number
    (0.9 minus the 4/14-weighted bias of 0.9), computed from the
    history before that decision — a decision never calibrates
    itself with its own outcome.
    """
    mind = Metacognition()
    first = mind.record_decision("schedule", 0.8, True)
    assert first.calibrated_confidence == pytest.approx(0.8)
    _seed_unreliable(mind)
    fifth = mind.record_decision("bill", 0.9, False)
    assert fifth.calibrated_confidence == pytest.approx(0.9 - (4 / 14) * 0.9)
    assert fifth.confidence == pytest.approx(0.9)


def test_observe_prediction_feeds_calibration_from_surprise():
    """The K19 seam: surprise is the calibration signal.

    A zero-surprise prediction counts as a right decision, a
    full-surprise one as wrong, and the threshold decides the
    middle: surprise 0.2 is a miss at the default threshold
    (0.0) and a hit at 0.3. One hit and one miss at confidence
    0.8 give Brier ((0.8 - 1)^2 + (0.8 - 0)^2) / 2 = 0.34.
    """
    mind = Metacognition()
    hit = mind.observe_prediction("price", 0.8, 0.0)
    assert hit.outcome is True
    miss = mind.observe_prediction("price", 0.8, 1.0)
    assert miss.outcome is False
    assert mind.brier_score("price") == pytest.approx(0.34)
    assert mind.observe_prediction("schedule", 0.8, 0.2).outcome is False
    assert mind.observe_prediction("schedule", 0.8, 0.2, surprise_threshold=0.3).outcome is True


def test_step_signal_hands_calibrated_uncertainty_to_the_governor():
    """K20 supplies the number; the governor owns depth and retry.

    The unreliable area's signal carries uncertainty 0.7 at
    stakes 0.9, so its risk (0.63) clears the governor's deep
    bar and the real governor decides DEEP — no second escalate
    path is written here. The reliable area's signal carries
    uncertainty about 0.03 at low stakes, so the same governor
    decides DELIBERATE.
    """
    mind = Metacognition()
    _seed_unreliable(mind, count=20)
    _seed_reliable(mind, count=20)
    governor = DepthGovernor()
    bad_signal = mind.to_step_signal("bill", 0.9, 0.9)
    assert bad_signal.uncertainty == pytest.approx(0.7)
    assert bad_signal.risk == pytest.approx(0.63)
    assert bad_signal.risk >= DEEP_RISK
    assert governor.decide(bad_signal).depth is Depth.DEEP
    good_signal = mind.to_step_signal("price", 0.9, 0.2)
    assert good_signal.uncertainty == pytest.approx(1.0 - (0.9 + (20 / 30) * 0.1))
    assert governor.decide(good_signal).depth is Depth.DELIBERATE


def test_unknown_area_has_no_calibration_and_trusts_raw_confidence():
    """An area with no history has no Brier score and no correction."""
    mind = Metacognition()
    assert mind.brier_score("price") is None
    assert mind.calibration("price") is None
    assert mind.calibrated_confidence("price", 0.75) == pytest.approx(0.75)
    assert mind.uncertainty("price", 0.75) == pytest.approx(0.25)
    assert mind.summary() == {}


def test_summary_reports_per_area_brier_scores():
    """The weekly-report shape: one entry per area, no global score."""
    mind = Metacognition()
    _seed_reliable(mind)
    _seed_unreliable(mind)
    summary = mind.summary()
    assert sorted(summary) == ["bill", "price"]
    assert summary["price"] == {
        "area": "price",
        "decisions": 4,
        "brier": pytest.approx(0.01),
        "mean_forecast": pytest.approx(0.9),
        "accuracy": pytest.approx(1.0),
        "bias": pytest.approx(-0.1),
    }
    assert summary["bill"]["brier"] == pytest.approx(0.81)
    assert summary["bill"]["accuracy"] == pytest.approx(0.0)
    assert summary["bill"]["bias"] == pytest.approx(0.9)
    assert len(mind) == 8
    assert mind.areas() == ["price", "bill"]


def test_validation_rejects_bad_inputs():
    """Bad areas, confidences, stakes, outcomes and surprises fail loudly."""
    mind = Metacognition()
    with pytest.raises(ValueError):
        mind.record_decision("", 0.5, True)
    with pytest.raises(ValueError):
        mind.record_decision("   ", 0.5, True)
    with pytest.raises(ValueError):
        mind.record_decision("price", 1.5, True)
    with pytest.raises(ValueError):
        mind.record_decision("price", -0.1, True)
    with pytest.raises(ValueError):
        mind.record_decision("price", True, True)
    with pytest.raises(ValueError):
        mind.record_decision("price", 0.5, "yes")
    with pytest.raises(ValueError):
        mind.record_decision("price", 0.5, 1)
    with pytest.raises(ValueError):
        mind.record_decision("price", 0.5, True, stakes=2.0)
    with pytest.raises(ValueError):
        mind.observe_prediction("price", 0.5, 1.5)
    with pytest.raises(ValueError):
        mind.should_escalate(uncertainty=0.5, stakes=-0.1)
    with pytest.raises(ValueError):
        Metacognition(uncertainty_high=1.5)
    with pytest.raises(ValueError):
        Metacognition(prior_weight=0)


def test_assess_decision_shape_is_ledger_ready():
    """The escalation decision serialises with its reason attached."""
    mind = Metacognition()
    decision = mind.assess("schedule", 0.4, 0.9)
    assert decision.uncertainty == pytest.approx(0.6)
    assert decision.escalate is True
    assert decision.to_dict()["reason"] == decision.reason
    assert decision.to_dict()["area"] == "schedule"
