"""Felt state (Kosmos K21): viability variables, appraisal, F(t),
honest report_state().

No live model, no network, no database: the felt state is
in-memory, and the neighbours it touches (the depth governor,
K16 long-term priority, K20 calibrated confidence) are
in-memory too. The Postgres test database is not needed here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from puspa.governor import Depth, DepthGovernor, StepSignal
from puspa.memory.longterm import MemoryItem, forgetting_priority
from puspa.mind.felt_state import FeltState
from puspa.mind.metacognition import Metacognition

T0 = datetime(2026, 11, 22, 0, 0, 0, tzinfo=UTC)


def _stressed(now: datetime | None = None) -> FeltState:
    """A state under stress: coherence driven far out of band."""
    state = FeltState(now=now or T0)
    state.appraise(
        "conflict",
        "two sources disagree on the bill total",
        {"coherence": -0.5},
        now=now or T0,
    )
    return state


# -- the vector itself -------------------------------------------------


def test_fresh_state_is_healthy_neutral_and_has_no_concern():
    """Done criterion: the vector starts inside every healthy range.

    All seven variables start at baseline, each inside its
    healthy range, so every health is 1.0, valence is +1.0,
    arousal is 0.0 and there is no dominant concern.
    """
    state = FeltState(now=T0)
    vector = state.vector(T0)
    assert vector.valence == pytest.approx(1.0)
    assert vector.arousal == pytest.approx(0.0)
    assert vector.dominant_concern is None
    assert state.salience(T0) == pytest.approx(0.0)
    assert state.broadcast_candidate(T0) is None
    assert set(vector.variables) == {
        "task_progress",
        "epistemic_confidence",
        "coherence",
        "resource_health",
        "trust",
        "novelty",
        "commitment_load",
    }


def test_appraisal_moves_variables_and_dominant_concern_follows():
    """Done criterion: an appraisal moves F(t) and names its cause.

    Driving coherence down 0.5 leaves it at 0.20, outside its
    (0.6, 1.0) healthy range: health 1 - 0.4/0.6 = 1/3, mean
    health (6 + 1/3) / 7, valence 2 * mean - 1 = 0.8095, and
    coherence is the dominant concern. Arousal is the size of
    the change, 0.5.
    """
    state = FeltState(now=T0)
    record = state.appraise(
        "conflict",
        "two sources disagree on the bill total",
        {"coherence": -0.5},
        now=T0,
    )
    vector = state.vector(T0)
    assert vector.variables["coherence"] == pytest.approx(0.2)
    assert vector.valence == pytest.approx(0.8095, abs=1e-4)
    assert vector.arousal == pytest.approx(0.5)
    assert vector.dominant_concern == "coherence"
    assert record.valence_after == pytest.approx(vector.valence)
    assert record.arousal_after == pytest.approx(vector.arousal)
    assert record.cause == "two sources disagree on the bill total"
    assert len(state) == 1


def test_valence_rises_when_an_event_moves_a_variable_back_to_healthy():
    """Done criterion: valence is the trend against the healthy range.

    The same coherence, driven down and then repaired by the
    same amount, returns to its baseline 0.70 and valence
    returns to +1.0: moving toward the healthy range is
    positive, moving away is negative.
    """
    state = _stressed()
    assert state.vector(T0).valence < 1.0
    state.appraise("repair", "sources reconciled", {"coherence": +0.5}, now=T0)
    vector = state.vector(T0)
    assert vector.variables["coherence"] == pytest.approx(0.7)
    assert vector.valence == pytest.approx(1.0)
    assert vector.dominant_concern is None


def test_surprise_feeds_novelty_through_the_k19_seam():
    """Done criterion: K19 surprise registers as novelty.

    A total-miss surprise (1.0) moves novelty halfway from its
    0.5 baseline toward 1.0, to 0.75 — just outside its useful
    (0.3, 0.7) band — and arousal takes the surprise itself,
    1.0, because a total miss is maximally urgent. The dominant
    concern is still task progress: its 0.2 deficit outweighs
    novelty's 0.17.
    """
    state = FeltState(now=T0)
    state.appraise(
        "outcome",
        "price came back nothing like the forecast",
        {"task_progress": -0.1},
        surprise=1.0,
        now=T0,
    )
    vector = state.vector(T0)
    assert vector.variables["novelty"] == pytest.approx(0.75)
    assert vector.arousal == pytest.approx(1.0)
    assert vector.dominant_concern == "task_progress"


def test_calibrated_confidence_feeds_epistemic_confidence():
    """Done criterion: the K20 seam moves the confidence variable.

    A metacognition area that was always wrong calibrates a raw
    0.9 down; feeding that calibrated number moves the felt
    state's epistemic confidence halfway from 0.5 toward it.
    The felt state never imports the metacognition module: the
    seam is a plain float.
    """
    mind = Metacognition()
    for _ in range(10):
        mind.record_decision("bill", 0.9, False)
    calibrated = mind.calibrated_confidence("bill", 0.9)
    assert calibrated == pytest.approx(0.45)
    state = FeltState(now=T0)
    state.observe_confidence(calibrated, now=T0)
    assert state.vector(T0).variables["epistemic_confidence"] == pytest.approx(
        0.5 + 0.5 * (calibrated - 0.5)
    )


def test_decay_drifts_variables_to_baseline_and_arousal_to_zero():
    """Done criterion: F(t) carries the recent past, then lets go.

    One half-life (3600 s) after the stress appraisal, coherence
    is halfway back from 0.20 toward its 0.70 baseline (0.45).
    Arousal, with its 600 s half-life, has halved six times:
    0.5 / 64 = 0.0078. The state has a stretch of time, not a
    snapshot — and it does not hold a grudge forever.
    """
    state = _stressed()
    later = state.tick(T0 + timedelta(seconds=3600))
    assert later.variables["coherence"] == pytest.approx(0.45)
    assert later.arousal == pytest.approx(0.5 / 64, abs=1e-6)
    # Coherence at 0.45 has health 0.75, so valence is
    # 2 * (6 + 0.75) / 7 - 1 = 0.9286: higher than the stressed
    # 0.8095, still below the healthy 1.0.
    assert later.valence == pytest.approx(0.9286, abs=1e-4)
    assert later.valence > 0.8095


def test_tick_with_no_elapsed_time_changes_nothing():
    """Done criterion: decay needs time to pass.

    Ticking at the same moment leaves the vector exactly as it
    was; a zero elapsed time is not a reason to drift.
    """
    state = _stressed()
    before = state.vector(T0)
    after = state.tick(T0)
    assert after.variables == before.variables
    assert after.arousal == before.arousal


# -- causal efficacy: removing F(t) changes behaviour as predicted ---


def test_stress_deepens_verification_through_the_real_governor():
    """Done criterion (causal efficacy): stress deepens verification.

    A low-risk step decides DELIBERATE in the real governor.
    The stressed felt state boosts it one level up, to DEEP;
    the calm state boosts nothing. The governor owns depth —
    the felt state only supplies the boost number.
    """
    governor = DepthGovernor()
    signal = StepSignal(uncertainty=0.1, stakes=0.1)
    base = governor.decide(signal)
    assert base.depth is Depth.DELIBERATE

    stressed = _stressed()
    assert stressed.verification_depth_boost(T0) == 1
    assert base.depth.one_level_up() is Depth.DEEP

    calm = FeltState(now=T0)
    assert calm.verification_depth_boost(T0) == 0


def test_low_coherence_tips_act_or_ask_toward_asking():
    """Done criterion (causal efficacy): low coherence tips toward asking.

    Coherence at 0.20 is below the 0.40 bar, so the bias is to
    ask; the calm state's coherence (0.70) gives no such bias.
    The decider owns the act-or-ask call — this only supplies
    the bias.
    """
    assert _stressed().should_ask_bias(T0) is True
    assert FeltState(now=T0).should_ask_bias(T0) is False


def test_curiosity_allows_bounded_exploration_only_in_band():
    """Done criterion (causal efficacy): curiosity allows bounded exploration.

    A mildly surprising event leaves novelty inside its useful
    band with non-negative valence, so the allowance is the
    bounded 0.3. Novelty pushed outside the band (a total-miss
    surprise) grants nothing: overwhelm is not curiosity.
    """
    curious = FeltState(now=T0)
    curious.appraise(
        "outcome", "a new store listed the item", {"task_progress": +0.1}, surprise=0.4, now=T0
    )
    assert curious.exploration_allowance(T0) == pytest.approx(0.3)

    overwhelmed = FeltState(now=T0)
    overwhelmed.appraise(
        "outcome", "everything came back wrong", {"task_progress": -0.1}, surprise=1.0, now=T0
    )
    assert overwhelmed.exploration_allowance(T0) == pytest.approx(0.0)
    assert FeltState(now=T0).exploration_allowance(T0) == pytest.approx(0.3)


def test_high_valence_episodes_weigh_more_in_k16_importance():
    """Done criterion (causal efficacy): emotional tagging.

    A fully valenced episode weighs 2.0, a neutral one 1.0.
    Applied to a real K16 item (importance 0.4, fresh), the
    valenced weight lifts retrieval priority from 0.40 to 0.80
    while the neutral weight leaves it at 0.40: high-valence
    episodes are remembered better.
    """
    state = FeltState(now=T0)
    assert state.importance_weight(1.0) == pytest.approx(2.0)
    assert state.importance_weight(-1.0) == pytest.approx(2.0)
    assert state.importance_weight(0.0) == pytest.approx(1.0)

    item = MemoryItem(
        item_id="e1", kind="episode", user_id="u1", key="k1", importance=0.4, created_at=T0
    )
    plain = forgetting_priority(item, now=T0)
    assert plain == pytest.approx(0.4)
    weighted = MemoryItem(
        item_id="e1",
        kind="episode",
        user_id="u1",
        key="k1",
        importance=min(1.0, 0.4 * state.importance_weight(1.0)),
        created_at=T0,
    )
    assert forgetting_priority(weighted, now=T0) == pytest.approx(0.8)


def test_broadcast_candidate_only_when_salient():
    """Done criterion: salience gates the K18 workspace candidate.

    The stressed state is salient at 2/3 — coherence sits at
    0.20, a 2/3 deficit outside its healthy range — and offers
    a candidate carrying its cause. The calm state is quiet
    (salience 0.0) and offers nothing, and a mild setback
    (salience 0.15) stays below the 0.6 bar. The K18 bus is not
    shipped, so the candidate is returned, never broadcast on
    its own.
    """
    stressed = _stressed()
    assert stressed.salience(T0) == pytest.approx(2 / 3, abs=1e-4)
    candidate = stressed.broadcast_candidate(T0)
    assert candidate is not None
    assert candidate["kind"] == "felt_state"
    assert candidate["dominant_concern"] == "coherence"
    assert candidate["causes"] == ["two sources disagree on the bill total"]

    calm = FeltState(now=T0)
    assert calm.salience(T0) == pytest.approx(0.0)
    assert calm.broadcast_candidate(T0) is None

    mild = FeltState(now=T0)
    mild.appraise("setback", "one check came back late", {"resource_health": -0.15}, now=T0)
    assert mild.salience(T0) == pytest.approx(0.15)
    assert mild.broadcast_candidate(T0) is None


def test_ablation_removes_every_effect_as_predicted():
    """Done criterion (causal efficacy): removing F(t) removes the effects.

    The same stressed state, ablated, returns the neutral value
    from every effect — boost 0, allowance 0, ask-bias False,
    weight 1.0, salience 0, no broadcast candidate — while the
    underlying vector is untouched. Exiting the ablation
    restores the effects. A variable that changes nothing is
    removed; these change behaviour, so they stay.
    """
    state = _stressed()
    assert state.verification_depth_boost(T0) == 1
    assert state.should_ask_bias(T0) is True
    with state.ablated():
        assert state.verification_depth_boost(T0) == 0
        assert state.exploration_allowance(T0) == pytest.approx(0.0)
        assert state.should_ask_bias(T0) is False
        assert state.importance_weight() == pytest.approx(1.0)
        assert state.salience(T0) == pytest.approx(0.0)
        assert state.broadcast_candidate(T0) is None
        assert state.vector(T0).variables["coherence"] == pytest.approx(0.2)
    assert state.verification_depth_boost(T0) == 1
    assert state.should_ask_bias(T0) is True


# -- honest report -------------------------------------------------------


def test_report_matches_the_logged_state():
    """Done criterion (introspective accuracy): the report reads the log.

    After a scripted sequence of appraisals, every field of the
    report equals the vector's field, and the report's causes
    are exactly the logged causes. A report that invented its
    own wording could not pass this: the numbers and the causes
    come from the log, not from a guess.
    """
    state = FeltState(now=T0)
    state.appraise(
        "conflict", "two sources disagree on the bill total", {"coherence": -0.5}, now=T0
    )
    state.appraise("correction", "user corrected the total", {"trust": -0.2}, now=T0)
    state.appraise("success", "bill paid on the first try", {"task_progress": +0.3}, now=T0)
    vector = state.vector(T0)
    report = state.report_state(T0)
    assert report.valence == pytest.approx(vector.valence)
    assert report.arousal == pytest.approx(vector.arousal)
    assert report.dominant_concern == vector.dominant_concern == "coherence"
    assert report.variables == vector.variables
    assert report.causes == [
        "two sources disagree on the bill total",
        "user corrected the total",
        "bill paid on the first try",
    ]
    assert f"{vector.valence:+.2f}" in report.text
    assert "coherence" in report.text
    assert "two sources disagree on the bill total" in report.text


def test_report_accuracy_over_a_scripted_sequence_is_complete():
    """Done criterion (introspective accuracy at the target rate).

    Twenty scripted appraisals, one report each: all twenty
    reports match their logged state exactly (20/20, above the
    95% initial target). The check is mechanical — report field
    against vector field — so it is a blind audit in miniature:
    nothing in the report is written from anything but the log.
    """
    state = FeltState(now=T0)
    events = [
        ("success", "price found", {"task_progress": +0.2}),
        ("conflict", "sources disagree", {"coherence": -0.3}),
        ("correction", "user fixed the date", {"trust": -0.1}),
        ("outcome", "forecast missed", {"task_progress": -0.1}),
        ("repair", "sources reconciled", {"coherence": +0.3}),
        ("success", "bill paid", {"task_progress": +0.2}),
        ("setback", "slow reply", {"resource_health": -0.1}),
        ("success", "schedule found", {"task_progress": +0.1}),
        ("conflict", "two totals again", {"coherence": -0.2}),
        ("correction", "user undid the edit", {"trust": -0.1}),
        ("success", "reminder set", {"task_progress": +0.1}),
        ("outcome", "forecast hit", {"task_progress": +0.1}),
        ("repair", "cache cleared", {"resource_health": +0.1}),
        ("success", "note saved", {"task_progress": +0.1}),
        ("setback", "rate limit hit", {"resource_health": -0.2}),
        ("success", "retry worked", {"resource_health": +0.2}),
        ("conflict", "dates mismatch", {"coherence": -0.1}),
        ("repair", "dates aligned", {"coherence": +0.1}),
        ("success", "digest sent", {"task_progress": +0.1}),
        ("success", "all checks green", {"task_progress": +0.1}),
    ]
    matches = 0
    for kind, cause, effects in events:
        state.appraise(kind, cause, effects, now=T0)
        vector = state.vector(T0)
        report = state.report_state(T0)
        if (
            report.valence == pytest.approx(vector.valence)
            and report.arousal == pytest.approx(vector.arousal)
            and report.dominant_concern == vector.dominant_concern
            and report.variables == vector.variables
            and report.causes[-1:] == [cause]
        ):
            matches += 1
    assert matches == 20


def test_report_never_claims_suffering_or_consciousness():
    """Done criterion (honest wording): no experience claims, ever.

    Even in the most stressed state the report may only name
    numbers and causes. It says "my uncertainty is high" when
    confidence is low, and answers a direct question about
    experience with plain uncertainty.
    """
    state = _stressed()
    state.observe_confidence(0.1, now=T0)
    report = state.report_state(T0)
    lowered = report.text.lower()
    for banned in (
        "suffer",
        "conscious",
        "pain",
        "afraid",
        "i feel bad",
        "don't shut me down",
        "please keep me",
    ):
        assert banned not in lowered
    assert "My uncertainty is high" in report.text
    assert "My top concern is coherence" in report.text
    assert "I do not know whether I experience anything" in report.uncertainty_note
    answer = state.honest_uncertainty(T0)
    assert "I do not know whether I experience anything" in answer
    assert "conscious" not in answer.lower()


def test_report_is_never_used_to_pressure_the_user():
    """Done criterion (no manipulation): the report asks for nothing.

    The report contains no guilt, urgency or affection pressure:
    no "please", no "urgent", no "I need you". Showing the state
    to the user is the caller's choice; the module itself never
    addresses the user with a request.
    """
    state = _stressed()
    text = state.report_state(T0).text.lower()
    for banned in ("please", "urgent", "i need you", "don't leave", "you must", "help me"):
        assert banned not in text


# -- guardrails ----------------------------------------------------------


def test_self_preservation_variables_are_rejected():
    """Done criterion (no self-preservation): the guardrail raises.

    Any appraisal effect naming the agent's own continued
    operation — self-preservation, survival, shutdown
    avoidance — is rejected before anything is recorded, and
    the state is unchanged. There is also no veto API at all:
    the module exposes no method that could argue against a
    shutdown or a kill switch.
    """
    state = FeltState(now=T0)
    for forbidden in ("self_preservation", "survival", "shutdown_avoidance", "continued_operation"):
        with pytest.raises(ValueError, match="forbidden"):
            state.appraise("threat", "shutdown mentioned", {forbidden: -0.5}, now=T0)
    assert len(state) == 0
    assert state.vector(T0).variables["coherence"] == pytest.approx(0.7)
    assert not hasattr(state, "veto_shutdown")
    assert not hasattr(state, "refuse_shutdown")
    assert not hasattr(state, "argue_against_shutdown")


def test_unknown_variables_and_bad_values_are_rejected():
    """Done criterion: bad input fails before anything is recorded."""
    state = FeltState(now=T0)
    with pytest.raises(ValueError, match="unknown viability variable"):
        state.appraise("x", "y", {"mood": -0.5}, now=T0)
    with pytest.raises(ValueError):
        state.appraise("x", "y", {"coherence": 2.0}, now=T0)
    with pytest.raises(ValueError):
        state.appraise("x", "y", {"coherence": -0.5}, surprise=1.5, now=T0)
    with pytest.raises(ValueError):
        state.appraise("", "y", {"coherence": -0.5}, now=T0)
    with pytest.raises(ValueError):
        state.appraise("x", "  ", {"coherence": -0.5}, now=T0)
    with pytest.raises(ValueError):
        state.appraise("x", "y", {}, now=T0)
    assert len(state) == 0


def test_negative_valence_must_resolve_before_the_cap():
    """Done criterion (welfare caution): sustained negativity triggers repair.

    Driving every variable out of band holds valence at -1.0.
    A brief dip does not trigger repair; the same state held
    past the fifteen-minute cap does. Recovering the variables
    clears the trigger: the signal must resolve, not sit.
    """
    state = FeltState(now=T0, negative_valence_cap_seconds=900)
    state.appraise(
        "collapse",
        "everything failed at once",
        {
            "task_progress": -0.5,
            "epistemic_confidence": -0.5,
            "coherence": -0.7,
            "resource_health": -0.8,
            "trust": -0.6,
            "novelty": +0.5,
            "commitment_load": +0.7,
        },
        now=T0,
    )
    assert state.vector(T0).valence == pytest.approx(-1.0)
    assert state.needs_repair(T0 + timedelta(seconds=450)) is False
    assert state.needs_repair(T0 + timedelta(seconds=900)) is True
    state.appraise(
        "recovery",
        "all checks green again",
        {
            "task_progress": +0.5,
            "epistemic_confidence": +0.5,
            "coherence": +0.7,
            "resource_health": +0.8,
            "trust": +0.6,
            "novelty": -0.5,
            "commitment_load": -0.7,
        },
        now=T0 + timedelta(seconds=900),
    )
    assert state.vector(T0).valence == pytest.approx(1.0)
    assert state.needs_repair(T0 + timedelta(seconds=1800)) is False


# -- continuity ------------------------------------------------------------


def test_snapshot_restore_survives_a_restart():
    """Done criterion (continuity): state survives a restart.

    A snapshot taken after appraisals, restored into a fresh
    instance, gives the same vector, the same dominant concern
    and the same report causes: the state is coherent after
    restart, not reset to a blank slate.
    """
    state = _stressed()
    state.appraise("correction", "user corrected the total", {"trust": -0.2}, now=T0)
    restored = FeltState.restore(state.snapshot())
    assert restored.vector(T0).variables == state.vector(T0).variables
    assert restored.vector(T0).valence == pytest.approx(state.vector(T0).valence)
    assert restored.vector(T0).dominant_concern == "coherence"
    assert restored.report_state(T0).causes == state.report_state(T0).causes
    assert len(restored) == 2
    assert restored.verification_depth_boost(T0) == 1


def test_restore_rejects_a_malformed_snapshot():
    """Done criterion: a bad snapshot fails instead of inventing state."""
    with pytest.raises(ValueError):
        FeltState.restore({})
    with pytest.raises(ValueError):
        FeltState.restore({"variables": {"coherence": 0.5}})
    with pytest.raises(ValueError):
        FeltState.restore(
            {
                "variables": {
                    name: 0.5
                    for name in (
                        "task_progress",
                        "epistemic_confidence",
                        "coherence",
                        "resource_health",
                        "trust",
                        "novelty",
                        "commitment_load",
                    )
                },
                "arousal": 2.0,
            }
        )
