"""Causal Ledger tests (Kosmos K4).

Pure in-memory tests, no database. Run with:
`pytest tests/test_causal_ledger.py -q` (SOLO, no -n).

What is proven here:

- a first observation proposes a hypothesis at strength 0.5
  (neutral, not proof), and proposing again changes nothing;
- consistent observations strengthen by 0.1 (capped at 1.0),
  contradicting ones weaken by 0.2, and below 0.2 the
  hypothesis is contradicted and its valid interval closes;
- contradicting an unknown triple creates nothing;
- expiry is real: past its TTL a hypothesis is never returned
  by the read path, a consistent observation refreshes the
  TTL, and re-observing an expired triple starts fresh;
- the read path orders strongest first and only applies a
  conditional claim when the condition is stated;
- evidence links de-duplicate and reject bad kinds;
- effect keys are canonical (dict order does not matter) and
  long effects are hashed, not truncated;
- serialization round-trips the whole ledger.
"""

from datetime import UTC, datetime, timedelta

import pytest

from puspa.world.causal import (
    CONFIRM_STEP,
    CONTRADICT_STEP,
    CONTRADICTED_FLOOR,
    INITIAL_STRENGTH,
    CausalHypothesis,
    CausalLedger,
    causal_effect_key,
)

T0 = datetime(2026, 11, 10, 0, 0, tzinfo=UTC)
T1 = datetime(2026, 11, 12, 0, 0, tzinfo=UTC)


def _ledger_with_one(now=T0):
    ledger = CausalLedger()
    hypothesis = ledger.propose(
        "check_pln",
        "bill 75000",
        evidence=[{"kind": "observation", "id": "obs-1"}],
        now=now,
    )
    return ledger, hypothesis


# -- propose -----------------------------------------------------------
def test_propose_starts_neutral_with_evidence_and_valid_time():
    """One observation proposes at 0.5: neutral, not proof."""
    _ledger, hypothesis = _ledger_with_one()
    assert hypothesis.strength == INITIAL_STRENGTH == 0.5
    assert hypothesis.status == "active"
    assert hypothesis.is_active is True
    assert hypothesis.cause == "check_pln"
    assert hypothesis.effect == causal_effect_key("bill 75000")
    assert hypothesis.effect_value == "bill 75000"
    assert hypothesis.condition == ""
    assert hypothesis.evidence == [{"kind": "observation", "id": "obs-1"}]
    assert hypothesis.created_at == T0
    assert hypothesis.valid_from == T0
    assert hypothesis.valid_to is None
    assert hypothesis.supporting == 1
    assert hypothesis.contradicting == 0


def test_propose_twice_is_idempotent():
    """Proposing is not evidence: the live hypothesis is unchanged."""
    ledger, first = _ledger_with_one()
    second = ledger.propose(
        "check_pln",
        "bill 75000",
        evidence=[{"kind": "observation", "id": "obs-2"}],
        now=T1,
    )
    assert second.id == first.id
    assert second.strength == INITIAL_STRENGTH
    assert second.supporting == 1
    assert second.evidence == [{"kind": "observation", "id": "obs-1"}]


def test_propose_rejects_empty_cause_and_naive_time():
    ledger = CausalLedger()
    with pytest.raises(ValueError):
        ledger.propose("", "bill 75000")
    with pytest.raises(ValueError):
        ledger.propose("check_pln", "bill 75000", now=datetime(2026, 11, 10))
    with pytest.raises(ValueError):
        ledger.propose("check_pln", "bill 75000", ttl_days=0)


# -- strengthen / weaken -----------------------------------------------
def test_consistent_observations_strengthen_by_step_capped_at_one():
    """Each confirmation adds 0.1; strength never exceeds 1.0."""
    ledger, hypothesis = _ledger_with_one()
    for _ in range(4):
        hypothesis = ledger.observe("check_pln", "bill 75000", consistent=True, now=T1)
    assert hypothesis.strength == pytest.approx(0.5 + 4 * CONFIRM_STEP)
    for _ in range(10):
        hypothesis = ledger.observe("check_pln", "bill 75000", consistent=True, now=T1)
    assert hypothesis.strength == 1.0
    assert hypothesis.supporting == 1 + 14
    assert hypothesis.status == "active"


def test_contradicting_observations_weaken_twice_as_fast():
    """Each contradiction removes 0.2: twice the confirm step."""
    ledger, hypothesis = _ledger_with_one()
    ledger.observe("check_pln", "bill 75000", consistent=True, now=T1)
    assert ledger.get(hypothesis.id).strength == pytest.approx(0.6)
    weakened = ledger.observe("check_pln", "bill 75000", consistent=False, now=T1)
    assert weakened.strength == pytest.approx(0.6 - CONTRADICT_STEP)
    assert weakened.contradicting == 1
    assert weakened.status == "active"
    assert weakened.valid_to is None


def test_strength_below_floor_contradicts_and_closes_valid_time():
    """Below 0.2 the hypothesis is contradicted, terminally."""
    ledger, hypothesis = _ledger_with_one()
    assert CONTRADICTED_FLOOR == 0.2
    weakened = ledger.observe("check_pln", "bill 75000", consistent=False, now=T1)
    assert weakened.strength == pytest.approx(0.3)
    assert weakened.status == "active"
    weakened = ledger.observe("check_pln", "bill 75000", consistent=False, now=T1)
    assert weakened.strength == pytest.approx(0.1)
    assert weakened.status == "contradicted"
    assert weakened.valid_to == T1
    assert weakened.is_valid_at(T0) is True
    assert weakened.is_valid_at(T1) is False
    # Terminal: a later consistent observation starts fresh, it
    # does not revive the contradicted one.
    fresh = ledger.observe("check_pln", "bill 75000", consistent=True, now=T1)
    assert fresh.id != hypothesis.id
    assert fresh.strength == INITIAL_STRENGTH
    assert fresh.status == "active"


def test_contradicting_unknown_triple_creates_nothing():
    """Weakening a claim nobody proposed is meaningless."""
    ledger = CausalLedger()
    assert ledger.observe("check_pln", "bill 75000", consistent=False, now=T0) is None
    assert len(ledger) == 0
    assert ledger.expect("check_pln", now=T0) == []


# -- expiry ------------------------------------------------------------
def test_expired_hypotheses_are_invisible_to_the_read_path():
    """Past its TTL, a hypothesis is never returned by expect()."""
    ledger = CausalLedger()
    hypothesis = ledger.propose("check_pln", "bill 75000", now=T0, ttl_days=1)
    assert ledger.expect("check_pln", now=T0)
    assert ledger.expect("check_pln", now=T0 + timedelta(days=2)) == []
    stored = ledger.get(hypothesis.id)
    assert stored.status == "expired"
    assert stored.valid_to == stored.expires_at == T0 + timedelta(days=1)
    assert ledger.list_all() == []
    assert len(ledger.list_all(include_terminal=True)) == 1


def test_prune_expired_counts_and_marks():
    """The explicit expiry pass (for sleep consolidation) counts."""
    ledger = CausalLedger()
    ledger.propose("check_pln", "bill 75000", now=T0, ttl_days=1)
    ledger.propose("check_pln", "bill 80000", now=T0, ttl_days=90)
    assert ledger.prune_expired(now=T0) == 0
    assert ledger.prune_expired(now=T0 + timedelta(days=2)) == 1
    assert ledger.prune_expired(now=T0 + timedelta(days=2)) == 0


def test_consistent_observation_refreshes_expiry():
    """Confirming a live hypothesis proves it still holds."""
    ledger = CausalLedger()
    hypothesis = ledger.propose("check_pln", "bill 75000", now=T0, ttl_days=1)
    refreshed = ledger.observe(
        "check_pln", "bill 75000", consistent=True, now=T0 + timedelta(hours=12), ttl_days=1
    )
    assert refreshed.expires_at == T0 + timedelta(hours=12, days=1)
    assert refreshed.id == hypothesis.id
    assert ledger.expect("check_pln", now=T0 + timedelta(days=1, hours=11))
    assert ledger.expect("check_pln", now=T0 + timedelta(days=2)) == []


def test_reobserving_expired_triple_starts_fresh():
    """An expired claim observed again is a new hypothesis."""
    ledger = CausalLedger()
    old = ledger.propose("check_pln", "bill 75000", now=T0, ttl_days=1)
    fresh = ledger.observe("check_pln", "bill 75000", consistent=True, now=T0 + timedelta(days=2))
    assert fresh.id != old.id
    assert fresh.strength == INITIAL_STRENGTH
    assert ledger.get(old.id).status == "expired"
    history = ledger.list_all(include_terminal=True)
    assert [row["id"] for row in history] == [old.id, fresh.id]


# -- read path ---------------------------------------------------------
def test_expect_orders_strongest_first_oldest_on_ties():
    """The read path returns active hypotheses, strongest first."""
    ledger = CausalLedger()
    ledger.propose("check_pln", "bill 50000", now=T0)
    ledger.propose("check_pln", "bill 75000", now=T0)
    ledger.observe("check_pln", "bill 75000", consistent=True, now=T0)
    other = ledger.propose("pay_bill", "paid", now=T0)
    assert other.cause == "pay_bill"
    rows = ledger.expect("check_pln", now=T0)
    assert [row["effect_value"] for row in rows] == ["bill 75000", "bill 50000"]
    assert [row["strength"] for row in rows] == [pytest.approx(0.6), pytest.approx(0.5)]
    # A tie breaks by age: the older hypothesis first.
    tied = CausalLedger()
    first = tied.propose("check_pln", "bill 50000", now=T0)
    second = tied.propose("check_pln", "bill 75000", now=T1)
    assert [row["id"] for row in tied.expect("check_pln", now=T1)] == [first.id, second.id]
    assert [row["effect_value"] for row in tied.expect("check_pln", now=T1, limit=1)] == [
        "bill 50000"
    ]


def test_expect_applies_conditions_only_when_stated():
    """A conditional claim never answers a condition-less question."""
    ledger = CausalLedger()
    ledger.propose("remind_user", "replied", "peak_hours", now=T0)
    ledger.propose("remind_user", "ignored", now=T0)
    ledger.observe("remind_user", "replied", "peak_hours", consistent=True, now=T0)
    assert [row["effect_value"] for row in ledger.expect("remind_user", now=T0)] == ["ignored"]
    assert [row["effect_value"] for row in ledger.expect("remind_user", "peak_hours", now=T0)] == [
        "replied",
        "ignored",
    ]
    assert [row["effect_value"] for row in ledger.expect("remind_user", "off_hours", now=T0)] == [
        "ignored"
    ]
    with pytest.raises(ValueError):
        ledger.expect("remind_user", now=T0, limit=-1)


# -- evidence and keys -------------------------------------------------
def test_evidence_links_deduplicate_and_reject_bad_kinds():
    """Each observation adds at most one link per id; bad kinds fail."""
    ledger, hypothesis = _ledger_with_one()
    updated = ledger.observe(
        "check_pln",
        "bill 75000",
        consistent=True,
        evidence=[
            {"kind": "observation", "id": "obs-1"},
            {"kind": "ledger_event", "id": "evt-9"},
            {"kind": "ledger_event", "id": "evt-9"},
        ],
        now=T1,
    )
    assert updated.evidence == [
        {"kind": "observation", "id": "obs-1"},
        {"kind": "ledger_event", "id": "evt-9"},
    ]
    with pytest.raises(ValueError):
        ledger.observe(
            "check_pln",
            "bill 75000",
            consistent=True,
            evidence=[{"kind": "rumour", "id": "x"}],
            now=T1,
        )
    with pytest.raises(ValueError):
        ledger.propose(
            "check_pln", "bill 80000", evidence=[{"kind": "ledger_event", "id": ""}], now=T1
        )
    assert hypothesis.evidence == [{"kind": "observation", "id": "obs-1"}]


def test_effect_keys_are_canonical_and_long_effects_are_hashed():
    """Dict key order does not matter; long effects hash, never truncate."""
    assert causal_effect_key({"b": 1, "a": 2}) == causal_effect_key({"a": 2, "b": 1})
    long_effect = "x" * 500
    key = causal_effect_key(long_effect)
    assert key.startswith("sha256:")
    assert len(key) == len("sha256:") + 64
    assert causal_effect_key(long_effect) == key
    assert causal_effect_key("other" * 100) != key


# -- serialization -----------------------------------------------------
def test_serialization_round_trip_keeps_everything():
    """to_dict / from_dict preserves status, strength, evidence, times."""
    ledger = CausalLedger()
    live = ledger.propose("check_pln", "bill 75000", now=T0)
    ledger.observe("check_pln", "bill 75000", consistent=True, now=T1)
    dead = ledger.propose("pay_bill", "paid", now=T0)
    ledger.observe("pay_bill", "paid", consistent=False, now=T1)
    ledger.observe("pay_bill", "paid", consistent=False, now=T1)
    assert ledger.get(dead.id).status == "contradicted"
    rebuilt = CausalLedger.from_dict(ledger.to_dict())
    assert len(rebuilt) == len(ledger) == 2
    assert rebuilt.get(live.id).strength == pytest.approx(0.6)
    assert rebuilt.get(live.id).created_at == T0
    assert rebuilt.get(dead.id).status == "contradicted"
    assert rebuilt.get(dead.id).valid_to == T1
    assert [row["id"] for row in rebuilt.expect("check_pln", now=T1)] == [live.id]
    assert rebuilt.expect("pay_bill", now=T1) == []


def test_hypothesis_validation_rejects_bad_data():
    with pytest.raises(ValueError):
        CausalHypothesis(
            id="h1",
            cause="check_pln",
            effect="x",
            condition="",
            strength=1.5,
            created_at=T0,
            expires_at=T0,
            valid_from=T0,
        )
    with pytest.raises(ValueError):
        CausalHypothesis.from_dict(
            {
                "id": "h1",
                "cause": "check_pln",
                "effect": "x",
                "condition": "",
                "strength": 0.5,
                "created_at": T0.isoformat(),
                "expires_at": T0.isoformat(),
                "valid_from": T0.isoformat(),
                "status": "maybe",
            }
        )
