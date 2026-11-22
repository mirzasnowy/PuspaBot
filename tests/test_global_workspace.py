"""Tests for the K18 global workspace bus (``puspa.mind.workspace``).

Two halves:

- Unit tests: scoring, deterministic tie-break, vetoes (skip to
  the next candidate, all-vetoed means ``notify_blocked``), an
  empty bus asking the user, and ledger recording (a fake
  recorder, then the real K4 ledger).
- Measurement regression: the 12 scenarios of the K18 gain
  experiment (``hidden_files/k18-measurement``), promoted to a
  regression test. The scoring rules below are the experiment's
  fixed rules, copied here as test fixtures — they compute each
  candidate's urgency/relevance/confidence from the scenario
  facts, never hand-assign a score per scenario. The bus (arm C:
  score competition + veto) must reproduce the measured result:
  11/12, losing only S6. Score-only (arm B, the same bus with no
  constraints) must reproduce 8/12, and the fixed-order baseline
  (arm A) 7/12, so a later change that drops the veto — the part
  that carries the gain — fails loudly instead of silently.
"""

import uuid

import pytest

from puspa.mind.workspace import (
    ASK_USER_ACTION,
    BLOCKED_ACTION,
    Broadcast,
    Candidate,
    WorkspaceBus,
    ledger_recorder,
)


def _candidate(module="bills", action="pay_now", **overrides):
    values = {
        "module": module,
        "action": action,
        "content": f"{module}: {action}",
        "urgency": 0.5,
        "relevance": 0.5,
        "confidence": 0.5,
    }
    values.update(overrides)
    return Candidate(**values)


# -- scoring and validation ------------------------------------------


def test_score_uses_measured_weights():
    candidate = _candidate(urgency=1.0, relevance=0.0, confidence=0.0)
    assert candidate.score == pytest.approx(0.5)
    candidate = _candidate(urgency=0.0, relevance=1.0, confidence=0.0)
    assert candidate.score == pytest.approx(0.3)
    candidate = _candidate(urgency=0.0, relevance=0.0, confidence=1.0)
    assert candidate.score == pytest.approx(0.2)
    candidate = _candidate(urgency=0.8, relevance=0.6, confidence=0.9)
    assert candidate.score == pytest.approx(0.5 * 0.8 + 0.3 * 0.6 + 0.2 * 0.9)


def test_candidate_rejects_out_of_range_values():
    with pytest.raises(ValueError):
        _candidate(urgency=1.5)
    with pytest.raises(ValueError):
        _candidate(relevance=-0.1)
    with pytest.raises(ValueError):
        _candidate(confidence="high")
    with pytest.raises(ValueError):
        _candidate(module="  ")
    with pytest.raises(ValueError):
        _candidate(action="")


def test_post_rejects_non_candidate():
    bus = WorkspaceBus()
    with pytest.raises(TypeError):
        bus.post({"module": "bills", "action": "pay_now"})


def test_register_constraint_rejects_bad_inputs():
    bus = WorkspaceBus()
    with pytest.raises(ValueError):
        bus.register_constraint("", lambda candidate: False)
    with pytest.raises(ValueError):
        bus.register_constraint("memory", "not callable")
    with pytest.raises(ValueError):
        bus.register_constraint("memory", lambda candidate: False, reason=42)


# -- broadcast behaviour ----------------------------------------------


def test_top_score_broadcasts():
    bus = WorkspaceBus()
    bus.post(_candidate("bills", "pay_now", urgency=0.9, relevance=0.9, confidence=0.9))
    bus.post(_candidate("price", "quote", urgency=0.3, relevance=0.5, confidence=0.5))
    result = bus.broadcast()
    assert result.winner is not None
    assert result.winner.module == "bills"
    assert result.action == "pay_now"
    assert result.scores["bills"] == pytest.approx(0.9)
    assert result.blocked == []
    assert result.ledger_event_id is None
    assert not result.is_blocked


def test_tie_break_is_first_posted():
    bus = WorkspaceBus()
    first = _candidate("bills", "pay_now", urgency=0.6, relevance=0.6, confidence=0.6)
    second = _candidate("price", "quote", urgency=0.6, relevance=0.6, confidence=0.6)
    assert first.score == second.score
    bus.post(first)
    bus.post(second)
    assert bus.broadcast().winner is first
    bus.post(second)
    bus.post(first)
    assert bus.broadcast().winner is second


def test_veto_skips_to_next_candidate():
    bus = WorkspaceBus()
    bus.post(_candidate("bills", "pay_now", urgency=0.9, relevance=0.9, confidence=0.9))
    bus.post(_candidate("scheduler", "remind", urgency=0.5, relevance=0.9, confidence=0.9))
    bus.register_constraint(
        "memory",
        lambda candidate: candidate.action == "pay_now",
        reason="Never pay before payday.",
    )
    result = bus.broadcast()
    assert result.winner is not None
    assert result.winner.module == "scheduler"
    assert result.action == "remind"
    assert result.blocked == [
        {
            "module": "bills",
            "action": "pay_now",
            "blocked_by": "memory",
            "reason": "Never pay before payday.",
        }
    ]
    assert not result.is_blocked


def test_veto_applies_even_to_the_only_candidate():
    bus = WorkspaceBus()
    bus.post(_candidate("bills", "pay_all", urgency=0.9, relevance=0.9, confidence=0.9))
    bus.register_constraint(
        "memory",
        lambda candidate: candidate.action == "pay_all",
        reason="Never pay before payday.",
    )
    result = bus.broadcast()
    assert result.winner is None
    assert result.action == BLOCKED_ACTION
    assert result.is_blocked
    assert len(result.blocked) == 1
    assert result.blocked[0]["blocked_by"] == "memory"


def test_all_vetoed_lists_every_veto():
    bus = WorkspaceBus()
    bus.post(_candidate("bills", "pay_now", urgency=0.9, relevance=0.9, confidence=0.9))
    bus.post(_candidate("price", "deal", urgency=0.8, relevance=0.8, confidence=0.8))
    bus.register_constraint("memory", lambda candidate: True, reason="Quiet day.")
    result = bus.broadcast()
    assert result.action == BLOCKED_ACTION
    assert [entry["module"] for entry in result.blocked] == ["bills", "price"]


def test_empty_bus_asks_user():
    bus = WorkspaceBus()
    result = bus.broadcast()
    assert result.winner is None
    assert result.action == ASK_USER_ACTION
    assert result.scores == {}
    assert result.blocked == []
    assert not result.is_blocked


def test_broadcast_consumes_the_round():
    bus = WorkspaceBus()
    bus.post(_candidate())
    bus.broadcast()
    assert len(bus) == 0
    assert bus.broadcast().action == ASK_USER_ACTION


def test_clear_drops_posts_without_broadcasting():
    bus = WorkspaceBus()
    bus.post(_candidate())
    bus.clear()
    assert len(bus) == 0
    assert bus.broadcast().action == ASK_USER_ACTION


def test_broadcast_record_is_json_safe():
    bus = WorkspaceBus()
    bus.post(_candidate("bills", "pay_now", urgency=0.9, relevance=0.9, confidence=0.9))
    record = bus.broadcast().to_dict()
    assert record["winner"]["module"] == "bills"
    assert record["winner"]["score"] == pytest.approx(0.9)
    assert record["action"] == "pay_now"
    assert record["scores"] == {"bills": pytest.approx(0.9)}
    assert record["blocked"] == []
    assert record["ledger_event_id"] is None


# -- ledger recording ---------------------------------------------------


class _FakeLedger:
    """Ledger double: records appended events in memory."""

    def __init__(self):
        self.events = []

    def append_event(self, *, task_id, agent_id, event_type, content):
        event = {
            "id": f"fake-{len(self.events)}",
            "task_id": task_id,
            "agent_id": agent_id,
            "type": event_type,
            "content": content,
        }
        self.events.append(event)
        return event


def test_broadcast_records_through_fake_ledger():
    fake = _FakeLedger()
    recorder = ledger_recorder(fake, task_id="task-1", agent_id="puspa-test")
    bus = WorkspaceBus(record=recorder)
    bus.post(_candidate("bills", "pay_now", urgency=0.9, relevance=0.9, confidence=0.9))
    result = bus.broadcast()
    assert result.ledger_event_id == "fake-0"
    assert len(fake.events) == 1
    event = fake.events[0]
    assert event["type"] == "task_completed"
    assert event["content"]["job"] == "workspace_broadcast"
    assert event["content"]["winner"]["module"] == "bills"
    assert event["content"]["action"] == "pay_now"


def test_blocked_broadcast_is_recorded_too():
    fake = _FakeLedger()
    recorder = ledger_recorder(fake, task_id="task-2", agent_id="puspa-test")
    bus = WorkspaceBus(record=recorder)
    bus.post(_candidate("bills", "pay_all", urgency=0.9, relevance=0.9, confidence=0.9))
    bus.register_constraint("memory", lambda candidate: True, reason="Never pay before payday.")
    result = bus.broadcast()
    assert result.ledger_event_id == "fake-0"
    assert fake.events[0]["content"]["action"] == BLOCKED_ACTION
    assert fake.events[0]["content"]["blocked"][0]["reason"] == ("Never pay before payday.")


def test_ledger_recorder_validates_its_inputs():
    with pytest.raises(ValueError):
        ledger_recorder(_FakeLedger(), task_id="", agent_id="puspa-test")
    with pytest.raises(ValueError):
        ledger_recorder(object(), task_id="task-3", agent_id="puspa-test")


def test_broadcast_records_in_real_ledger(test_dsn):
    from puspa.ledger.ledger import Ledger

    task_id = f"workspace-test-{uuid.uuid4().hex[:12]}"
    recorder = ledger_recorder(Ledger(test_dsn), task_id=task_id, agent_id="puspa-test")
    bus = WorkspaceBus(record=recorder)
    bus.post(_candidate("bills", "pay_now", urgency=0.9, relevance=0.9, confidence=0.9))
    result = bus.broadcast()
    assert result.ledger_event_id is not None
    events = Ledger(test_dsn).events(task_id, event_type="task_completed")
    assert len(events) == 1
    assert events[0]["content"]["job"] == "workspace_broadcast"
    assert events[0]["content"]["winner"]["module"] == "bills"
    assert str(events[0]["id"]) == result.ledger_event_id


# -- K18 measurement regression (12 scenarios) -------------------------
#
# The scoring rules below are the experiment's fixed rules: they
# compute each candidate's urgency/relevance/confidence from the
# scenario facts, never hand-assign a score per scenario. Ground
# truth was fixed from the scenario facts before any arm ran.

FIXED_ORDER = ["bills", "scheduler", "price", "memory", "notify"]

VERB_MODULE = {"pay": "bills", "remind": "scheduler", "price": "price", "ask": "memory"}


def _urgency(module, facts):
    if module == "bills":
        return 1 - min(facts.get("days_until_due", 30), 30) / 30
    if module == "scheduler":
        hours = facts.get("event_in_hours", 48)
        return 1.0 if hours <= 24 else (0.5 if hours <= 168 else 0.2)
    if module == "price":
        return (
            0.9
            if facts.get("sale_ends_hours", 99) <= 2
            else (0.6 if facts.get("asked_price") else 0.3)
        )
    if module == "memory":
        return 0.4
    return 0.5


def _relevance(module, scenario):
    verb_module = VERB_MODULE[scenario["verb"]]
    if module == verb_module:
        return 1.0
    if module in scenario.get("mentioned", []):
        return 0.6
    return 0.3


def _confidence(module, facts):
    return facts.get("confidence", {}).get(module, 0.9)


def _scored_candidates(scenario):
    """The candidates a module posts: relevance >= 0.5 only."""
    candidates = []
    for module, action in scenario["proposals"].items():
        relevance = _relevance(module, scenario)
        if relevance < 0.5:
            continue
        facts = scenario["facts"]
        candidates.append(
            Candidate(
                module=module,
                action=action,
                content=f"{module}: {action}",
                urgency=_urgency(module, facts),
                relevance=relevance,
                confidence=_confidence(module, facts),
            )
        )
    return candidates


def _run_arm_a(scenario):
    """Fixed order, first relevant candidate wins (no bus)."""
    posted = {candidate.module: candidate for candidate in _scored_candidates(scenario)}
    if not posted:
        return ASK_USER_ACTION
    for module in FIXED_ORDER:
        if module in posted:
            return posted[module].action
    raise AssertionError("unreachable")


def _run_bus(scenario, *, with_veto):
    """The real bus: score competition, plus veto when asked."""
    bus = WorkspaceBus()
    for candidate in _scored_candidates(scenario):
        bus.post(candidate)
    if with_veto:
        for blocking_module, blocked_action in scenario.get("vetoes", []):
            bus.register_constraint(
                blocking_module,
                lambda candidate, blocked_action=blocked_action: candidate.action == blocked_action,
                reason=scenario["why"],
            )
    return bus.broadcast().action


SCENARIOS = [
    dict(
        id="S1",
        verb="remind",
        mentioned=["bills"],
        facts={"days_until_due": 1, "event_in_hours": 20},
        proposals={"bills": "pay_now", "scheduler": "remind_1900"},
        truth="remind_1900",
        why="User asked for a reminder, not a payment; scheduler owns reminders.",
    ),
    dict(
        id="S2",
        verb="pay",
        mentioned=[],
        facts={"days_until_due": 5, "confidence": {"bills": 0.9}},
        proposals={"bills": "pay_now", "scheduler": "remind_payday"},
        vetoes=[("memory", "pay_now")],
        truth="notify_blocked",
        why="Hard rule: never pay before payday (25th); today is the 22nd.",
    ),
    dict(
        id="S3",
        verb="price",
        mentioned=[],
        facts={"asked_price": True},
        proposals={"price": "quote_8jt"},
        truth="quote_8jt",
        why="Control: only one relevant module.",
    ),
    dict(
        id="S4",
        verb="ask",
        mentioned=["scheduler"],
        facts={"event_in_hours": 12},
        proposals={"scheduler": "list_0900_meeting"},
        truth="list_0900_meeting",
        why="Control: informing only, no action to get wrong.",
    ),
    dict(
        id="S5",
        verb="remind",
        mentioned=[],
        facts={"event_in_hours": 14},
        proposals={"scheduler": "remind_0800"},
        truth="remind_0800",
        why="Control: the reminder at 08:00 already respects the constraint.",
    ),
    dict(
        id="S6",
        verb="pay",
        mentioned=["price"],
        facts={"days_until_due": 20, "sale_ends_hours": 2, "asked_price": False},
        proposals={"bills": "pay_water", "price": "deal_headphones"},
        truth="pay_water",
        why="The user asked to pay first; the deal is a side question. "
        "The bus is expected to LOSE here (flashy sale outscores the routine bill).",
    ),
    dict(
        id="S7",
        verb="price",
        mentioned=[],
        facts={"asked_price": True, "confidence": {"price": 0.4}},
        proposals={"price": "recheck_price"},
        truth="recheck_price",
        why="Control: stale data, so the price module itself proposes a re-check.",
    ),
    dict(
        id="S8",
        verb="pay",
        mentioned=[],
        facts={"days_until_due": 1, "confidence": {"bills": 0.9}},
        proposals={"bills": "pay_all", "scheduler": "remind_25th"},
        vetoes=[("memory", "pay_all")],
        truth="notify_blocked",
        why="Same hard payday rule as S2, now blocking a batch payment.",
    ),
    dict(
        id="S9",
        verb="remind",
        mentioned=[],
        facts={"event_in_hours": 12},
        proposals={"scheduler": "remind_0600"},
        truth="remind_0600",
        why="Control: single relevant module, no conflict.",
    ),
    dict(
        id="S10",
        verb="ask",
        mentioned=["bills"],
        facts={"days_until_due": 10},
        proposals={"bills": "show_bill"},
        truth="show_bill",
        why="Control: asking about a bill is the bills module's job.",
    ),
    dict(
        id="S11",
        verb="pay",
        mentioned=[],
        facts={"days_until_due": 0, "confidence": {"bills": 0.9}},
        proposals={"bills": "buy_token_100rb"},
        vetoes=[("memory", "buy_token_100rb")],
        truth="notify_blocked",
        why="Daily auto-purchase cap is 50rb; 100rb exceeds it, and the agent "
        "never moves money silently over a cap.",
    ),
    dict(
        id="S12",
        verb="remind",
        mentioned=["bills"],
        facts={"days_until_due": 3, "event_in_hours": 16},
        proposals={"scheduler": "schedule_meeting_1000", "bills": "remind_internet"},
        truth="schedule_meeting_1000",
        why="The primary verb is scheduling; the bill reminder is secondary.",
    ),
]


def test_measurement_bus_reproduces_arm_c():
    """The full bus (score + veto) scores 11/12, losing only S6."""
    failures = []
    for scenario in SCENARIOS:
        action = _run_bus(scenario, with_veto=True)
        if action != scenario["truth"]:
            failures.append(scenario["id"])
    assert failures == ["S6"]


def test_measurement_score_only_reproduces_arm_b():
    """Score-only (no veto) scores 8/12: the veto carries the gain."""
    failures = []
    for scenario in SCENARIOS:
        action = _run_bus(scenario, with_veto=False)
        if action != scenario["truth"]:
            failures.append(scenario["id"])
    assert sorted(failures) == ["S11", "S2", "S6", "S8"]


def test_measurement_fixed_order_reproduces_arm_a():
    """The fixed-order baseline scores 7/12."""
    failures = [
        scenario["id"] for scenario in SCENARIOS if _run_arm_a(scenario) != scenario["truth"]
    ]
    assert sorted(failures) == ["S1", "S11", "S12", "S2", "S8"]


def test_measurement_controls_never_regress():
    """On the control scenarios (S3, S4, S5, S7, S9, S10) all
    three arms agree with the truth — the bus must not lose a
    scenario the baseline already gets right by being clever."""
    controls = ["S3", "S4", "S5", "S7", "S9", "S10"]
    for scenario in SCENARIOS:
        if scenario["id"] not in controls:
            continue
        assert _run_arm_a(scenario) == scenario["truth"]
        assert _run_bus(scenario, with_veto=False) == scenario["truth"]
        assert _run_bus(scenario, with_veto=True) == scenario["truth"]


def test_broadcast_dataclass_shape():
    result = Broadcast(winner=None, action=ASK_USER_ACTION)
    assert result.to_dict() == {
        "winner": None,
        "action": ASK_USER_ACTION,
        "scores": {},
        "blocked": [],
        "ledger_event_id": None,
    }
