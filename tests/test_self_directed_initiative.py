"""Self-directed initiative (Kosmos K14, BDI).

No live model, no network, no database in the in-memory tests:
the engine, the governor, the dial and the budget are all
in-memory, and the executor works on a plain dict standing in
for the world. The two database tests at the end use the
repo-wide ``test_dsn`` fixture (Postgres): the digest job
reading a real durable-memory snapshot, and the scheduler
branch firing the digest trigger.
"""

from __future__ import annotations

import pytest

from puspa.autonomy.dial import AutonomyDial
from puspa.mind.metacognition import Metacognition
from puspa.proactivity.budget import Initiative, InterruptionBudget
from puspa.self.initiative import (
    ActionResult,
    DailyDigest,
    InitiativeEngine,
    OpenLoop,
    StandingGoal,
    derive_subgoals,
    open_loops_from_snapshot,
    run_daily_digest_job,
    standing_goals_from_snapshot,
)

NOW_DAY = "2026-11-22"


def _goal(key="standing_goal:pay-pln-on-time", text="pay the PLN bill on time every month"):
    """A standing goal about the electricity bill."""
    return StandingGoal(key=key, text=text)


def _stale_number_loop(**overrides):
    """An open loop: the stored PLN number is out of date.

    Reversible (the old number is kept), low stakes, high
    confidence: the shape of work that may run silently.
    """
    values = {
        "loop_id": "loop-pln-number",
        "kind": "stale_fact",
        "title": "stored PLN customer number is out of date",
        "detail": "last bill shows 512345678901",
        "area": "system",
        "reversible": True,
        "stakes": 0.2,
        "confidence": 0.9,
        "benefit": 0.8,
    }
    values.update(overrides)
    return OpenLoop(**values)


def _executor_over(world, *, ok=True, with_undo=True):
    """An executor that writes the new PLN number into a fake world."""

    def execute(subgoal):
        previous = world["pln_number"]
        world["pln_number"] = "512345678901"

        def undo():
            world["pln_number"] = previous
            return True

        return ActionResult(
            ok=ok,
            detail="updated the stored PLN number to 512345678901",
            undo=undo if with_undo else None,
            result={"pln_number": "512345678901"},
        )

    return execute


# -- the done criterion -----------------------------------------------------


def test_done_criterion_agent_closes_unasked_loop_and_reports_it_with_working_undo():
    """Done criterion: an open loop the agent was never asked about.

    The seed is one standing goal plus one open loop, and no
    user command anywhere: the engine's own tick derives the
    sub-goal, runs the reversible close silently, and reports
    it with an undo. Undoing restores the world exactly, and
    a second undo does nothing.
    """
    world = {"pln_number": "000000000000"}
    engine = InitiativeEngine(executor=_executor_over(world))
    result = engine.tick([_goal()], [_stale_number_loop()], day=NOW_DAY)
    assert len(result.done) == 1
    assert result.deferred == []
    report = result.done[0]
    assert report.status == "done"
    assert report.undo_available
    assert world["pln_number"] == "512345678901"
    assert "PLN customer number" in report.subgoal.title
    assert report.subgoal.goal_key == "standing_goal:pay-pln-on-time"
    assert "undo available" in result.digest.render()
    assert result.delivered is False  # no budget attached: rendered, not sent
    assert report.undo() is True
    assert report.undone
    assert world["pln_number"] == "000000000000"
    assert report.undo() is False  # undo runs exactly once
    assert world["pln_number"] == "000000000000"


# -- derivation -------------------------------------------------------------


def test_derivation_attaches_the_matching_standing_goal():
    """A loop about the bill attaches to the bill goal (shared word)."""
    derived = derive_subgoals([_goal()], [_stale_number_loop()])
    assert len(derived) == 1
    assert derived[0].goal_key == "standing_goal:pay-pln-on-time"
    assert derived[0].title.startswith("Close open loop: ")
    assert derived[0].score == pytest.approx(0.8 * 0.9 - 0.2)


def test_derivation_without_a_matching_goal_still_proposes():
    """A loop no goal covers still derives a candidate, unattached."""
    loop = _stale_number_loop(
        title="water meter reading is stale", detail="last reading was in June"
    )
    derived = derive_subgoals([_goal()], [loop])
    assert len(derived) == 1
    assert derived[0].goal_key == ""


def test_score_orders_best_first():
    """Candidates come back best score first, not in input order."""
    low = _stale_number_loop(loop_id="loop-low", benefit=0.3, confidence=0.5)
    high = _stale_number_loop(loop_id="loop-high", benefit=0.9, confidence=0.9)
    derived = derive_subgoals([_goal()], [low, high])
    assert [item.loop_id for item in derived] == ["loop-high", "loop-low"]


def test_snapshot_readers_use_the_durable_sections():
    """Goals come from the goals section, loops from commitments."""
    snapshot = {
        "goals": [
            {"key": "standing_goal:tidy", "text": "keep the flat tidy", "source": "user"},
        ],
        "commitments": [
            {"key": "commitment:call-mum", "text": "call mum on Sunday", "source": "user"},
        ],
    }
    goals = standing_goals_from_snapshot(snapshot)
    assert [goal.key for goal in goals] == ["standing_goal:tidy"]
    loops = open_loops_from_snapshot(snapshot)
    assert len(loops) == 1
    assert loops[0].kind == "pending_commitment"
    assert loops[0].title == "call mum on Sunday"
    assert loops[0].reversible is False  # unknown close: fails closed
    assert standing_goals_from_snapshot({}) == []
    assert open_loops_from_snapshot({}) == []


def test_open_loop_rejects_unknown_kind_and_bad_numbers():
    """An open loop validates its kind and its 0..1 numbers."""
    with pytest.raises(ValueError, match="unknown open-loop kind"):
        OpenLoop(loop_id="loop-x", kind="telepathy", title="read minds")
    with pytest.raises(ValueError, match="stakes"):
        _stale_number_loop(stakes=1.5)


# -- the silent gate --------------------------------------------------------


def test_irreversible_loop_never_runs_silently():
    """Irreversible work goes to the digest, however valuable."""
    world = {"pln_number": "000000000000"}
    engine = InitiativeEngine(executor=_executor_over(world))
    loop = _stale_number_loop(reversible=False, benefit=0.95, confidence=0.95)
    result = engine.tick([_goal()], [loop], day=NOW_DAY)
    assert result.done == []
    assert len(result.deferred) == 1
    assert "not reversible" in result.deferred[0].gate_reason
    assert world["pln_number"] == "000000000000"
    assert "Held back for you" in result.digest.render()


def test_low_calibrated_confidence_defers():
    """K20: an area that kept being wrong calibrates below the bar."""
    mind = Metacognition()
    for _ in range(4):
        mind.record_decision("system", 0.9, False)
    world = {"pln_number": "000000000000"}
    engine = InitiativeEngine(metacognition=mind, executor=_executor_over(world))
    result = engine.tick([_goal()], [_stale_number_loop()], day=NOW_DAY)
    assert result.done == []
    assert len(result.deferred) == 1
    assert "calibrated confidence" in result.deferred[0].gate_reason
    assert world["pln_number"] == "000000000000"


def test_fresh_dial_blocks_silent_work():
    """K11: a fresh dial starts at propose, so nothing runs alone."""
    dial = AutonomyDial()
    assert dial.stage("system") == "propose"
    world = {"pln_number": "000000000000"}
    engine = InitiativeEngine(dial=dial, executor=_executor_over(world))
    result = engine.tick([_goal()], [_stale_number_loop()], day=NOW_DAY)
    assert result.done == []
    assert "autonomy dial" in result.deferred[0].gate_reason
    assert world["pln_number"] == "000000000000"


def test_earned_dial_allows_silent_work():
    """K11: five correct outcomes earn do-and-show, and work runs."""
    dial = AutonomyDial()
    for _ in range(5):
        dial.record_outcome("system", True)
    assert dial.stage("system") == "do_and_show"
    world = {"pln_number": "000000000000"}
    engine = InitiativeEngine(dial=dial, executor=_executor_over(world))
    result = engine.tick([_goal()], [_stale_number_loop()], day=NOW_DAY)
    assert len(result.done) == 1
    assert world["pln_number"] == "512345678901"


def test_money_area_never_runs_silently():
    """K11/P-02: money is capped at propose, so it always asks first."""
    dial = AutonomyDial()
    for _ in range(40):
        dial.record_outcome("money", True)
    assert dial.stage("money") == "propose"
    world = {"pln_number": "000000000000"}
    engine = InitiativeEngine(dial=dial, executor=_executor_over(world))
    loop = _stale_number_loop(area="money")
    result = engine.tick([_goal()], [loop], day=NOW_DAY)
    assert result.done == []
    assert "autonomy dial" in result.deferred[0].gate_reason


def test_failed_repair_loop_goes_to_digest():
    """A loop the repair loop already gave up on never runs silently."""
    world = {"pln_number": "000000000000"}
    engine = InitiativeEngine(executor=_executor_over(world))
    loop = _stale_number_loop(kind="failed_repair", loop_id="loop-repair")
    result = engine.tick([_goal()], [loop], day=NOW_DAY)
    assert result.done == []
    assert "failed repair" in result.deferred[0].gate_reason
    assert world["pln_number"] == "000000000000"


def test_failed_silent_run_is_never_silently_retried():
    """Governor: a failed run's retry goes deep, so it defers."""
    attempts = {"count": 0}

    def flaky_executor(subgoal):
        attempts["count"] += 1
        return ActionResult(ok=False, detail="the write failed")

    engine = InitiativeEngine(executor=flaky_executor)
    first = engine.tick([_goal()], [_stale_number_loop()], day=NOW_DAY)
    assert len(first.done) == 1
    assert first.done[0].status == "failed"
    assert first.done[0].undo() is False  # a failed action has nothing to undo
    second = engine.tick([_goal()], [_stale_number_loop()], day=NOW_DAY)
    assert second.done == []
    assert len(second.deferred) == 1
    assert "deep" in second.deferred[0].gate_reason
    assert attempts["count"] == 1  # the retry never ran


def test_simulator_failure_defers():
    """K7: a dry-run that fails its postcondition defers the work."""
    world = {"pln_number": "000000000000"}
    engine = InitiativeEngine(executor=_executor_over(world), simulator=lambda subgoal: False)
    result = engine.tick([_goal()], [_stale_number_loop()], day=NOW_DAY)
    assert result.done == []
    assert "dry-run" in result.deferred[0].gate_reason
    assert world["pln_number"] == "000000000000"


def test_no_executor_defers_everything():
    """An engine with no executor can only propose, never act."""
    engine = InitiativeEngine()
    result = engine.tick([_goal()], [_stale_number_loop()], day=NOW_DAY)
    assert result.done == []
    assert len(result.deferred) == 1
    assert "no executor" in result.deferred[0].gate_reason


# -- undo and reporting -----------------------------------------------------


def test_missing_undo_is_flagged_in_report_and_digest():
    """A silent action with no undo is reported as such, not hidden."""
    world = {"pln_number": "000000000000"}
    engine = InitiativeEngine(executor=_executor_over(world, with_undo=False))
    result = engine.tick([_goal()], [_stale_number_loop()], day=NOW_DAY)
    assert len(result.done) == 1
    report = result.done[0]
    assert report.status == "done"
    assert report.undo_available is False
    assert report.undo() is False
    assert "WARNING: no undo available" in result.digest.render()
    assert world["pln_number"] == "512345678901"


def test_silent_outcome_feeds_metacognition_and_dial():
    """A silent success is scored back into K20 and the K11 dial."""
    mind = Metacognition()
    dial = AutonomyDial(initial_score=0.7)
    world = {"pln_number": "000000000000"}
    engine = InitiativeEngine(metacognition=mind, dial=dial, executor=_executor_over(world))
    engine.tick([_goal()], [_stale_number_loop()], day=NOW_DAY)
    assert len(mind) == 1
    assert mind.decisions("system")[0].outcome is True
    assert dial.score("system") == pytest.approx(0.7 + 0.3 * 0.05)


def test_tick_does_not_queue_the_same_loop_twice():
    """A second tick with the same loops does not duplicate the queue."""
    world = {"pln_number": "000000000000"}
    engine = InitiativeEngine(executor=_executor_over(world))
    engine.tick([_goal()], [_stale_number_loop()], day=NOW_DAY)
    assert len(engine.queue) == 1
    second = engine.tick([_goal()], [_stale_number_loop()], day=NOW_DAY)
    assert len(engine.queue) == 1
    assert second.done == []  # the loop is already done
    assert second.deferred == []


# -- the digest -------------------------------------------------------------


def test_digest_renders_done_and_held_back():
    """The digest reads as outcomes first, then what was held back."""
    world = {"pln_number": "000000000000"}
    engine = InitiativeEngine(executor=_executor_over(world))
    loops = [
        _stale_number_loop(),
        _stale_number_loop(loop_id="loop-irr", reversible=False),
    ]
    result = engine.tick([_goal()], loops, day=NOW_DAY)
    rendered = result.digest.render()
    assert "Done on my own:" in rendered
    assert "Held back for you:" in rendered
    assert "not reversible" in rendered
    assert len(result.digest) == 2
    assert result.digest.to_dict()["day"] == NOW_DAY


def test_digest_delivery_pays_from_the_interruption_budget():
    """K10: the digest ping costs one unit; an empty budget silences it."""
    budget = InterruptionBudget()
    digest = DailyDigest(day=NOW_DAY, deferred=derive_subgoals([_goal()], [_stale_number_loop()]))
    decision = digest.deliver(budget, day=NOW_DAY)
    assert decision.allowed
    assert budget.remaining(NOW_DAY) == 4
    for _ in range(4):
        budget.try_spend(Initiative(kind="bill_due", value=0.9), day=NOW_DAY)
    assert budget.remaining(NOW_DAY) == 0
    denied = digest.deliver(budget, day=NOW_DAY)
    assert denied.allowed is False
    assert denied.reason == "budget_empty"


def test_empty_digest_never_pings():
    """An empty digest has nothing to say, so delivery refuses."""
    budget = InterruptionBudget()
    with pytest.raises(ValueError, match="nothing to deliver"):
        DailyDigest(day=NOW_DAY).deliver(budget, day=NOW_DAY)


def test_tick_delivers_the_digest_when_a_budget_is_attached():
    """A tick with a budget marks the digest delivered."""
    budget = InterruptionBudget()
    engine = InitiativeEngine(budget=budget)
    result = engine.tick([_goal()], [_stale_number_loop()], day=NOW_DAY)
    assert result.delivered is True
    assert result.delivery is not None
    assert result.delivery.allowed is True
    assert budget.remaining(NOW_DAY) == 4


def test_daily_digest_job_with_injected_snapshot_proposes_only():
    """The scheduled job without an executor only ever proposes."""
    snapshot = {
        "goals": [{"key": "standing_goal:tidy", "text": "keep the flat tidy"}],
        "commitments": [{"key": "commitment:call-mum", "text": "call mum on Sunday"}],
    }
    result = run_daily_digest_job("user-1", snapshot=snapshot)
    assert result["user_id"] == "user-1"
    assert result["done"] == []
    assert len(result["deferred"]) == 1
    assert "call mum on Sunday" in result["deferred"][0]["title"]
    assert "call mum on Sunday" in result["rendered"]


# -- database: snapshot read and scheduler wiring ---------------------------


def test_db_digest_job_reads_the_real_snapshot(test_dsn, make_user):
    """The digest job reads standing goals + commitments from Postgres."""
    from puspa.memory.facts import PostgresFactStore, record_fact

    user = make_user()
    user_id = user["user_id"]
    store = PostgresFactStore(dsn=test_dsn)
    assert record_fact(
        user_id,
        "standing_goal:pay-pln-on-time",
        "pay the PLN bill on time every month",
        source="user_stated",
        store=store,
    )["stored"]
    assert record_fact(
        user_id,
        "commitment:pay-pln",
        "pay the PLN bill before the 20th",
        source="user_stated",
        store=store,
    )["stored"]
    result = run_daily_digest_job(user_id, dsn=test_dsn)
    assert result["done"] == []
    assert len(result["deferred"]) == 1
    assert "pay the PLN bill before the 20th" in result["deferred"][0]["title"]
    assert result["deferred"][0]["goal_key"] == "standing_goal:pay-pln-on-time"
    assert "pay the PLN bill before the 20th" in result["rendered"]


def test_db_scheduler_branch_runs_the_initiative_digest(test_dsn, make_user):
    """The scheduler fires an initiative_digest trigger into the job."""
    from puspa.scheduler import scheduler

    user = make_user()
    user_id = user["user_id"]
    trigger = scheduler.schedule_initiative_digest(user_id, dsn=test_dsn)
    assert trigger.event_type == "initiative_digest"
    run = scheduler.run_trigger(trigger, dsn=test_dsn, now=trigger.next_trigger_at)
    assert run is not None
    result = scheduler.run_scheduled_job(run, dsn=test_dsn)
    assert result["job"] == "initiative_digest"
    assert result["result"]["user_id"] == user_id
    assert "rendered" in result["result"]
    assert "Nothing to report" in result["result"]["rendered"]
