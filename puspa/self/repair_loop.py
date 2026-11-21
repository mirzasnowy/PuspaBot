"""Repair loop (Kosmos v2, Phase 3): detect, diagnose, fix, prove.

Every failure follows one path, from ``docs/kosmos-brain-v2.md``:
detect it, classify the cause, apply the repair for that class,
and add a regression case so the same failure cannot return
silently. At most two automatic repair attempts per failure;
after that the loop escalates with a plain diagnosis of what
failed and what was tried, never a third attempt and never an
apology. Every repair is ledger-recorded as a hypothesis ("this
repair fixes this failure class under these conditions") with
evidence and an expiry date: a repair that is never confirmed
again expires instead of becoming a permanent fact, and two
failed attempts contradict it.

Where the signals come from (all already built, none touched
here):

- K19 prediction surprise (``puspa/world/prediction_surprise.py``):
  a forecast and the observed outcome differ. Feed a scored
  ``SurpriseResult`` to :meth:`RepairLoop.handle_surprise`.
- K8 outcome watcher (``puspa/sentinel/outcome_watcher.py``):
  an outcome marked done changed afterwards. Feed a
  ``CheckResult`` with status ``"late_failure"`` to
  :meth:`RepairLoop.handle_late_failure`.
- Tool errors and postcondition failures arrive as plain
  :class:`FailureSignal` values built by the caller.

Classification is a minimal local taxonomy, not the V2-16
failure taxonomy (that item is built in parallel and its module
must not be awaited or touched): the class is read off the
signal source plus the evidence the signal carries, so a
prediction miss with a fresh re-observation that confirms the
observed value is ``stale_world_state`` (the world moved), a
miss with no such confirmation is ``wrong_prediction`` (the
model's prior was wrong), a late failure is always
``stale_world_state``, a tool error is ``tool_misuse``, and a
postcondition failure is ``postcondition_failed``. Anything
else is ``unknown`` and escalates with no repair attempt.

Repairs are injected callables, never a live model: the loop
owns the orchestration (attempt budget, verification, ledger,
escalation) and the caller owns the world access. The built-in
repairers need at most one small callable each:

- ``reobserve``: read the world fresh (a price, a bill status)
  for a ``stale_world_state`` repair.
- ``repredict``: ask the model for its corrected prediction for
  a ``wrong_prediction`` repair (after K19 already corrected
  the prior, this is the corrected value).
- ``retry``: run the corrected action again for a ``tool_misuse``
  repair.
- ``recheck``: re-read the expected postcondition for a
  ``postcondition_failed`` repair.

A repairer returns a :class:`RepairOutcome`; the loop verifies
it with ``verify`` (default: the repaired outcome agrees with
the observed outcome on every field the observation carries,
scored with the same field-error rule K19 uses). A repairer
that has nothing to do returns ``ok=False`` with a reason
instead of raising: the loop counts that as a failed attempt
and moves on. Repairers must not raise: like the ledger-writer
seam in ``prediction_surprise``, a collaborator failure
propagates to the caller instead of being swallowed here, so
this module adds no blind-except handler.

Hypotheses live in the causal ledger (K4,
``puspa/world/causal.py``): the loop proposes the repair
hypothesis on the first attempt, strengthens it (and refreshes
its expiry) when an attempt verifies, and weakens it on every
failed attempt, so two failures contradict it. The default
time-to-live is 30 days (shorter than the ledger's 90-day
default, because a repair claim should have to re-earn itself
sooner than a general causal claim). A caller may inject its
own ledger; with none, a fresh in-memory ledger is used.

Skill-compiler hook: when a repair succeeds and the signal
carries a compiled skill plus the winning attempt returned
replay runs, the loop offers the repair to the injected
``skill_compiler`` (``SkillCompiler.repair`` from
``puspa/governor/compiler.py``) and stores its result. With no
compiler, no skill, or no runs, the result still carries the
repair as a ``skill_candidate`` dict the caller can feed to
the compiler later. No production path hands the loop a skill
today, so live wiring of a successful repair into the skill
library is follow-up work, not claimed here.

Regression cases live in an in-memory :class:`RegressionSuite`
owned by the loop (or injected). A successful repair adds one
case carrying the failing inputs and the expected outcome; the
suite replays cases through a caller-supplied runner and
reports pass/fail per case. The suite is in-memory on purpose:
persisting it into the eval harness is the follow-up that
wires this loop into the nightly run.

All state is in memory. No database, no network, no live model.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from puspa.world.causal import CausalLedger

__all__ = [
    "DEFAULT_HYPOTHESIS_TTL_DAYS",
    "MAX_ATTEMPTS",
    "Diagnosis",
    "Escalation",
    "FailureSignal",
    "RegressionCase",
    "RegressionSuite",
    "RepairAttempt",
    "RepairLoop",
    "RepairOutcome",
    "RepairResult",
    "classify",
]

#: Automatic repair attempts allowed per failure, from the v2
#: design ("at most two automatic repair attempts per task").
MAX_ATTEMPTS = 2

#: Days before an unconfirmed repair hypothesis expires. Shorter
#: than the causal ledger's 90-day default: a repair claim must
#: re-earn itself sooner than a general causal claim.
DEFAULT_HYPOTHESIS_TTL_DAYS = 30

# -- failure classes (minimal local taxonomy; V2-16 owns the full one) --

#: The world moved under a stale reading: re-observing shows the
#: observed value, so the fix is a fresh read, not a new model.
FAILURE_STALE_WORLD_STATE = "stale_world_state"
#: The model's prior was wrong and the world did not move: the fix
#: is the corrected prediction K19 already stored.
FAILURE_WRONG_PREDICTION = "wrong_prediction"
#: The tool was called wrong (unknown action, exception, bad
#: arguments): the fix is a corrected retry, or a skill patch.
FAILURE_TOOL_MISUSE = "tool_misuse"
#: The live result does not show the intended change: the fix is
#: a refreshed expectation, then a recheck.
FAILURE_POSTCONDITION_FAILED = "postcondition_failed"
#: No repairer knows this failure: escalate without attempting.
FAILURE_UNKNOWN = "unknown"

FAILURE_CLASSES = (
    FAILURE_STALE_WORLD_STATE,
    FAILURE_WRONG_PREDICTION,
    FAILURE_TOOL_MISUSE,
    FAILURE_POSTCONDITION_FAILED,
    FAILURE_UNKNOWN,
)

# -- signal sources -------------------------------------------------------

SOURCE_PREDICTION_SURPRISE = "prediction_surprise"
SOURCE_LATE_FAILURE = "late_failure"
SOURCE_TOOL_ERROR = "tool_error"
SOURCE_POSTCONDITION = "postcondition"

# -- repair strategies ----------------------------------------------------

REPAIR_REFRESH_WORLD = "refresh_world_state"
REPAIR_CORRECT_PRIOR = "correct_prior"
REPAIR_RETRY_ACTION = "retry_corrected_action"
REPAIR_REFRESH_EXPECTATION = "refresh_expectation"

_STRATEGY_FOR_CLASS = {
    FAILURE_STALE_WORLD_STATE: REPAIR_REFRESH_WORLD,
    FAILURE_WRONG_PREDICTION: REPAIR_CORRECT_PRIOR,
    FAILURE_TOOL_MISUSE: REPAIR_RETRY_ACTION,
    FAILURE_POSTCONDITION_FAILED: REPAIR_REFRESH_EXPECTATION,
}

#: Repair outcome of a loop run.
STATUS_REPAIRED = "repaired"
STATUS_ESCALATED = "escalated"


def _as_utc(moment: datetime | None) -> datetime:
    if moment is None:
        return datetime.now(UTC)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _default_ledger():
    """Fresh in-memory causal ledger, imported lazily at use time.

    A top-level import would pull ``puspa.world`` (and its store
    and model) into ``sys.modules`` on any ``puspa.self`` import;
    the lazy import keeps this module importable on its own, the
    same seam ``prediction_surprise`` keeps for the learning loop.
    """
    from puspa.world.causal import CausalLedger

    return CausalLedger()


def _default_verify(signal: FailureSignal, outcome: RepairOutcome) -> bool:
    """Default repair check: the repaired outcome matches reality.

    A repair that reports ``ok=False`` never verifies. A repair
    with no corrected outcome verifies on its own ``ok`` flag.
    Otherwise the corrected outcome must agree with the observed
    outcome on every field the observation carries, scored with
    the same field-error rule K19 uses (a missing corrected
    field counts as a miss, so a partial repair cannot pass).
    With no observed outcome to compare against, the ``ok`` flag
    is the whole check.
    """
    if not outcome.ok:
        return False
    corrected = outcome.corrected
    observed = signal.observed
    if corrected is None or observed is None:
        return outcome.ok
    from puspa.world.prediction_surprise import surprise_of

    shared = {key: corrected[key] for key in observed if key in corrected}
    return surprise_of(shared, observed) == 0.0


@dataclass
class FailureSignal:
    """One detected failure, with the evidence that proves it.

    ``source`` names the detector (``"prediction_surprise"``,
    ``"late_failure"``, ``"tool_error"``, ``"postcondition"``).
    ``predicted`` / ``observed`` are the outcome dicts the
    detector compared (``None`` when the failure carries no
    outcome, e.g. a tool error with no result at all).
    ``action`` is the action that failed, when there is one.
    ``evidence`` is the detector's own proof (prediction id,
    surprise, outcome id). ``fresh_observation`` is a second,
    independent read of the world taken after the failure: when
    it agrees with ``observed`` and disagrees with ``predicted``,
    the world moved and the failure is stale state, not a wrong
    model. ``skill`` is the compiled skill the failure belongs
    to, when the caller knows it (the compiler hook needs it).
    """

    source: str
    task: str
    predicted: dict | None = None
    observed: dict | None = None
    action: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    detected_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    user_id: str = "user-1"
    task_id: str = ""
    fresh_observation: dict | None = None
    skill: Any = None
    signal_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not self.source:
            raise ValueError("source must be a non-empty string")
        if not isinstance(self.task, str) or not self.task:
            raise ValueError("task must be a non-empty string")
        if self.predicted is not None and not isinstance(self.predicted, dict):
            raise ValueError("predicted must be a dict or None")
        if self.observed is not None and not isinstance(self.observed, dict):
            raise ValueError("observed must be a dict or None")
        if not isinstance(self.action, dict):
            raise ValueError("action must be a dict")
        if not isinstance(self.evidence, dict):
            raise ValueError("evidence must be a dict")
        self.detected_at = _as_utc(self.detected_at)


@dataclass
class Diagnosis:
    """What the loop thinks caused a failure, and what fixes it."""

    failure_class: str
    cause_summary: str
    evidence_used: list[str]
    suggested_repair: str


@dataclass
class RepairOutcome:
    """What one repair attempt produced, before verification.

    ``ok`` says the repairer itself ran to completion; it does
    not say the failure is fixed — the loop verifies that
    separately. ``corrected`` is the repaired outcome the
    verifier compares against the observed outcome (``None``
    when the repair has no outcome to compare, e.g. a retry
    whose only signal is success). ``runs`` carries replay runs
    for the skill compiler hook, when the repairer has them.
    """

    ok: bool
    detail: str
    corrected: dict | None = None
    runs: list | None = None


@dataclass
class RepairAttempt:
    """One recorded automatic repair attempt."""

    attempt: int
    strategy: str
    ok: bool
    verified: bool
    detail: str


@dataclass
class Escalation:
    """Plain escalation after the automatic attempts are spent.

    The message names the diagnosis and everything already
    tried, so a human can pick up without re-running the loop.
    It is deliberately plain: no apology, no rewording of the
    failure as a partial success.
    """

    message: str
    diagnosis: Diagnosis
    attempts: list[RepairAttempt]


@dataclass
class RegressionCase:
    """One failure that must never return silently.

    Carries the failing inputs (``action``, ``predicted``) and
    the outcome the repair proved (``observed`` / ``expected``),
    so a replay runner can re-run the same failure and check
    the fixed behaviour holds.
    """

    case_id: str
    task: str
    failure_class: str
    action: dict
    predicted: dict | None
    observed: dict | None
    expected: dict | None
    strategy: str
    created_at: datetime


@dataclass
class RepairResult:
    """The outcome of running the loop on one failure."""

    #: "repaired" | "escalated".
    status: str
    signal: FailureSignal
    diagnosis: Diagnosis
    attempts: list[RepairAttempt]
    #: The repair hypothesis as a dict (ledger ``to_dict`` shape),
    #: or None when no repair was attempted.
    hypothesis: dict | None = None
    #: The regression case added on a repair, else None.
    regression_case: RegressionCase | None = None
    #: The escalation, when the loop escalated, else None.
    escalation: Escalation | None = None
    #: The skill-compiler result, when the hook ran, else None.
    compiler_result: Any = None
    #: What the caller can feed to the skill compiler later: the
    #: task, the winning strategy, and the winning runs, when a
    #: repair succeeded; None when the loop escalated.
    skill_candidate: dict | None = None


class RegressionSuite:
    """In-memory suite of regression cases from repaired failures.

    A case is added by the loop when a repair verifies. ``replay``
    runs every case through a caller-supplied runner
    (``runner(case) -> bool``) and reports pass/fail per case, so
    the suite itself is the proof the same failure stays fixed.
    """

    def __init__(self) -> None:
        self._cases: list[RegressionCase] = []

    def __len__(self) -> int:
        return len(self._cases)

    def add_case(self, case: RegressionCase) -> RegressionCase:
        """Add one case; returns it unchanged."""
        self._cases.append(case)
        return case

    def cases(self) -> list[RegressionCase]:
        """Cases in the order they were added."""
        return list(self._cases)

    def replay(self, runner: Callable[[RegressionCase], bool]) -> list[dict]:
        """Replay every case through ``runner``; one row per case.

        Each row carries the case id, the task, and whether the
        runner says the fixed behaviour still holds. The runner
        must return a bool, not raise: a runner failure is a
        failure of the harness, not of the case, and it
        propagates to the caller.
        """
        return [
            {"case_id": case.case_id, "task": case.task, "passed": bool(runner(case))}
            for case in self._cases
        ]


def classify(signal: FailureSignal) -> Diagnosis:
    """Classify one failure from its source and its evidence.

    The class is read off the detector plus the evidence, never
    guessed from the task text: a late failure is the world
    moving after "done"; a prediction miss is stale state only
    when a fresh re-observation confirms the observed value,
    otherwise the model's prior was wrong; a tool error is tool
    misuse; a postcondition failure is its own class. An
    unrecognised source is ``unknown`` and escalates with no
    repair attempt.
    """
    if signal.source == SOURCE_LATE_FAILURE:
        return Diagnosis(
            failure_class=FAILURE_STALE_WORLD_STATE,
            cause_summary=(
                "the recorded outcome changed after it was marked done: "
                "the world moved under a stale reading"
            ),
            evidence_used=["kind", "outcome_id"],
            suggested_repair=REPAIR_REFRESH_WORLD,
        )
    if signal.source == SOURCE_PREDICTION_SURPRISE:
        fresh = signal.fresh_observation
        if (
            fresh is not None
            and signal.observed is not None
            and signal.predicted is not None
            and fresh == signal.observed
            and fresh != signal.predicted
        ):
            return Diagnosis(
                failure_class=FAILURE_STALE_WORLD_STATE,
                cause_summary=(
                    "a fresh read of the world confirms the observed value, "
                    "so the prediction was stale, not wrong"
                ),
                evidence_used=["prediction_id", "surprise", "fresh_observation"],
                suggested_repair=REPAIR_REFRESH_WORLD,
            )
        return Diagnosis(
            failure_class=FAILURE_WRONG_PREDICTION,
            cause_summary=(
                "the forecast and the observed outcome differ and no fresh "
                "read shows the world moved, so the model's prior was wrong"
            ),
            evidence_used=["prediction_id", "surprise"],
            suggested_repair=REPAIR_CORRECT_PRIOR,
        )
    if signal.source == SOURCE_TOOL_ERROR:
        return Diagnosis(
            failure_class=FAILURE_TOOL_MISUSE,
            cause_summary="the tool call itself failed, so the action was wrong, not the world",
            evidence_used=["error"],
            suggested_repair=REPAIR_RETRY_ACTION,
        )
    if signal.source == SOURCE_POSTCONDITION:
        return Diagnosis(
            failure_class=FAILURE_POSTCONDITION_FAILED,
            cause_summary=(
                "the live result does not show the intended change, "
                "so the expectation (or the action behind it) is stale"
            ),
            evidence_used=["expected", "observed"],
            suggested_repair=REPAIR_REFRESH_EXPECTATION,
        )
    return Diagnosis(
        failure_class=FAILURE_UNKNOWN,
        cause_summary=f"no repairer knows source {signal.source!r}; the cause is unknown",
        evidence_used=[],
        suggested_repair="escalate",
    )


class RepairLoop:
    """Run the detect-diagnose-fix-prove loop on one failure.

    ``ledger`` is the causal ledger repair hypotheses are
    recorded in (a fresh in-memory one by default).
    ``repairers`` overrides the built-in repairer for a failure
    class (``{class: callable(signal, attempt) -> RepairOutcome}``).
    ``verify`` checks a repair outcome (default: the repaired
    outcome matches the observed one). ``reobserve``,
    ``repredict``, ``retry`` and ``recheck`` are the world-access
    callables the built-in repairers use; a missing one makes
    its repairer report a failed attempt instead of raising.
    ``skill_compiler`` is the optional compiler hook (an object
    with ``repair(skill, runs=...)``). ``suite`` is the
    regression suite repaired failures add cases to (a fresh
    in-memory one by default).
    """

    def __init__(
        self,
        *,
        ledger: CausalLedger | None = None,
        repairers: dict[str, Callable[[FailureSignal, int], RepairOutcome]] | None = None,
        verify: Callable[[FailureSignal, RepairOutcome], bool] | None = None,
        reobserve: Callable[[FailureSignal], dict | None] | None = None,
        repredict: Callable[[FailureSignal], dict | None] | None = None,
        retry: Callable[[FailureSignal, int], RepairOutcome] | None = None,
        recheck: Callable[[FailureSignal], dict | None] | None = None,
        skill_compiler: Any = None,
        suite: RegressionSuite | None = None,
        hypothesis_ttl_days: int = DEFAULT_HYPOTHESIS_TTL_DAYS,
    ):
        if hypothesis_ttl_days <= 0:
            raise ValueError("hypothesis_ttl_days must be positive")
        self._ledger = ledger if ledger is not None else _default_ledger()
        self._verify = verify if verify is not None else _default_verify
        self._reobserve = reobserve
        self._repredict = repredict
        self._retry = retry
        self._recheck = recheck
        self._skill_compiler = skill_compiler
        self._suite = suite if suite is not None else RegressionSuite()
        self._ttl_days = hypothesis_ttl_days
        self._repairers = {
            FAILURE_STALE_WORLD_STATE: self._repair_refresh_world,
            FAILURE_WRONG_PREDICTION: self._repair_correct_prior,
            FAILURE_TOOL_MISUSE: self._repair_retry_action,
            FAILURE_POSTCONDITION_FAILED: self._repair_refresh_expectation,
        }
        if repairers:
            self._repairers.update(repairers)
        self.history: list[RepairResult] = []

    @property
    def suite(self) -> RegressionSuite:
        """The regression suite this loop adds cases to."""
        return self._suite

    @property
    def ledger(self):
        """The causal ledger repair hypotheses are recorded in."""
        return self._ledger

    # -- entry points -----------------------------------------------------

    def handle_surprise(self, result, *, fresh_observation=None, now: datetime | None = None):
        """Run the loop on a scored K19 prediction surprise.

        ``result`` is the ``SurpriseResult`` from
        ``PredictionSurprise.observe``. ``fresh_observation`` is
        an independent re-read of the world taken after the miss:
        pass it when the caller re-observed, so classification
        can tell a moved world from a wrong prior.
        """
        moment = _as_utc(now)
        record = result.record
        signal = FailureSignal(
            source=SOURCE_PREDICTION_SURPRISE,
            task=record.task,
            predicted=dict(record.predicted),
            observed=dict(record.observed) if record.observed is not None else None,
            action=dict(record.action),
            evidence={
                "kind": SOURCE_PREDICTION_SURPRISE,
                "prediction_id": record.prediction_id,
                "surprise": result.surprise,
            },
            detected_at=moment,
            user_id=record.user_id,
            task_id=record.task_id,
            fresh_observation=(dict(fresh_observation) if fresh_observation is not None else None),
        )
        return self.handle(signal, now=moment)

    def handle_late_failure(self, check, *, now: datetime | None = None):
        """Run the loop on a K8 late failure.

        ``check`` is the ``CheckResult`` from
        ``OutcomeWatcher.check_due`` with status
        ``"late_failure"``. The recorded outcome is the
        prediction and the fresh recheck value is what the world
        actually shows.
        """
        moment = _as_utc(now)
        watched = check.watched
        signal = FailureSignal(
            source=SOURCE_LATE_FAILURE,
            task=watched.task,
            predicted={"value": watched.observed},
            observed={"value": check.fresh_observed} if check.fresh_observed is not None else None,
            action={},
            evidence={"kind": SOURCE_LATE_FAILURE, "outcome_id": watched.outcome_id},
            detected_at=moment,
            user_id=watched.user_id,
            task_id=watched.task_id,
        )
        return self.handle(signal, now=moment)

    # -- the loop ---------------------------------------------------------

    def handle(self, signal: FailureSignal, *, now: datetime | None = None) -> RepairResult:
        """Diagnose one failure, repair it, or escalate plainly.

        At most ``MAX_ATTEMPTS`` automatic attempts. A verified
        repair records its hypothesis as confirmed, adds a
        regression case, and offers the repair to the skill
        compiler when the hook is wired. Two failed attempts
        contradict the hypothesis and escalate. An unknown
        failure escalates with no attempt at all.
        """
        moment = _as_utc(now)
        diagnosis = classify(signal)
        repairer = self._repairers.get(diagnosis.failure_class)
        if diagnosis.failure_class == FAILURE_UNKNOWN or repairer is None:
            result = RepairResult(
                status=STATUS_ESCALATED,
                signal=signal,
                diagnosis=diagnosis,
                attempts=[],
                escalation=Escalation(
                    message=self._escalation_message(diagnosis, []),
                    diagnosis=diagnosis,
                    attempts=[],
                ),
            )
            self.history.append(result)
            return result
        strategy = _STRATEGY_FOR_CLASS[diagnosis.failure_class]
        cause = f"repair:{diagnosis.failure_class}:{signal.task}"
        condition = _condition_text(signal)
        evidence = [{"kind": "observation", "id": signal.signal_id}]
        hypothesis = self._ledger.propose(
            cause,
            strategy,
            condition,
            evidence=evidence,
            now=moment,
            ttl_days=self._ttl_days,
        )
        attempts: list[RepairAttempt] = []
        winning_runs: list | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            outcome = repairer(signal, attempt)
            verified = self._verify(signal, outcome)
            attempts.append(
                RepairAttempt(
                    attempt=attempt,
                    strategy=strategy,
                    ok=outcome.ok,
                    verified=verified,
                    detail=outcome.detail,
                )
            )
            # Attempts are sequential, so each ledger observation is
            # stamped one second after the previous one. That also
            # keeps the hypothesis valid interval well-formed: the
            # ledger rejects a zero-length interval, which is what a
            # hypothesis contradicted in the very instant it was
            # proposed would have.
            attempt_moment = moment + timedelta(seconds=attempt)
            self._ledger.observe(
                cause,
                strategy,
                condition,
                consistent=verified,
                evidence=evidence,
                now=attempt_moment,
                ttl_days=self._ttl_days,
            )
            if not verified:
                continue
            if outcome.runs is not None:
                winning_runs = list(outcome.runs)
            case = self._add_regression_case(signal, diagnosis, strategy, moment)
            compiler_result = self._offer_to_compiler(signal, winning_runs)
            result = RepairResult(
                status=STATUS_REPAIRED,
                signal=signal,
                diagnosis=diagnosis,
                attempts=attempts,
                hypothesis=self._hypothesis_dict(hypothesis.id),
                regression_case=case,
                compiler_result=compiler_result,
                skill_candidate={
                    "task": signal.task,
                    "strategy": strategy,
                    "failure_class": diagnosis.failure_class,
                    "runs": winning_runs,
                },
            )
            self.history.append(result)
            return result
        stored = self._hypothesis_dict(hypothesis.id)
        result = RepairResult(
            status=STATUS_ESCALATED,
            signal=signal,
            diagnosis=diagnosis,
            attempts=attempts,
            hypothesis=stored,
            escalation=Escalation(
                message=self._escalation_message(diagnosis, attempts),
                diagnosis=diagnosis,
                attempts=list(attempts),
            ),
        )
        self.history.append(result)
        return result

    # -- built-in repairers ----------------------------------------------

    def _repair_refresh_world(self, signal: FailureSignal, attempt: int) -> RepairOutcome:
        """Re-read the world fresh; the fix is the current value."""
        if self._reobserve is None:
            return RepairOutcome(
                ok=False,
                detail="no reobserve callable configured; cannot read the world fresh",
            )
        fresh = self._reobserve(signal)
        if fresh is None:
            return RepairOutcome(
                ok=False,
                detail=f"attempt {attempt}: fresh read observed nothing",
            )
        return RepairOutcome(
            ok=True,
            detail=f"attempt {attempt}: fresh read returned the current world state",
            corrected=dict(fresh),
        )

    def _repair_correct_prior(self, signal: FailureSignal, attempt: int) -> RepairOutcome:
        """Ask the model for its corrected prediction (K19 fixed it)."""
        if self._repredict is None:
            return RepairOutcome(
                ok=False,
                detail="no repredict callable configured; cannot ask for the corrected prediction",
            )
        corrected = self._repredict(signal)
        if corrected is None:
            return RepairOutcome(
                ok=False,
                detail=f"attempt {attempt}: the model has no corrected prediction for this action",
            )
        return RepairOutcome(
            ok=True,
            detail=f"attempt {attempt}: corrected prediction read from the model",
            corrected=dict(corrected),
        )

    def _repair_retry_action(self, signal: FailureSignal, attempt: int) -> RepairOutcome:
        """Run the corrected action again through the retry callable."""
        if self._retry is None:
            return RepairOutcome(
                ok=False,
                detail="no retry callable configured; cannot run the corrected action",
            )
        return self._retry(signal, attempt)

    def _repair_refresh_expectation(self, signal: FailureSignal, attempt: int) -> RepairOutcome:
        """Re-read the expected postcondition fresh."""
        if self._recheck is None:
            return RepairOutcome(
                ok=False,
                detail="no recheck callable configured; cannot re-read the postcondition",
            )
        fresh = self._recheck(signal)
        if fresh is None:
            return RepairOutcome(
                ok=False,
                detail=f"attempt {attempt}: recheck observed nothing",
            )
        return RepairOutcome(
            ok=True,
            detail=f"attempt {attempt}: postcondition re-read fresh",
            corrected=dict(fresh),
        )

    # -- helpers ----------------------------------------------------------

    def _hypothesis_dict(self, hypothesis_id: str) -> dict | None:
        stored = self._ledger.get(hypothesis_id)
        return stored.to_dict() if stored is not None else None

    def _add_regression_case(
        self, signal: FailureSignal, diagnosis: Diagnosis, strategy: str, moment: datetime
    ) -> RegressionCase:
        case = RegressionCase(
            case_id=uuid.uuid4().hex,
            task=signal.task,
            failure_class=diagnosis.failure_class,
            action=dict(signal.action),
            predicted=dict(signal.predicted) if signal.predicted is not None else None,
            observed=dict(signal.observed) if signal.observed is not None else None,
            expected=dict(signal.observed) if signal.observed is not None else None,
            strategy=strategy,
            created_at=moment,
        )
        return self._suite.add_case(case)

    def _offer_to_compiler(self, signal: FailureSignal, runs: list | None):
        """Offer a verified repair to the skill compiler, if wired.

        Runs only when a compiler is injected, the signal carries
        a compiled skill, and the winning attempt returned replay
        runs. A compiler that reports failure returns its result
        (the repair already succeeded); a compiler that raises
        propagates, like any other collaborator failure on this
        module's seams.
        """
        if self._skill_compiler is None or signal.skill is None or not runs:
            return None
        return self._skill_compiler.repair(signal.skill, runs=runs)

    @staticmethod
    def _escalation_message(diagnosis: Diagnosis, attempts: list[RepairAttempt]) -> str:
        if not attempts:
            tried = "no automatic repair was attempted"
        else:
            tried = "; ".join(
                f"attempt {item.attempt} ({item.strategy}): {item.detail}" for item in attempts
            )
        return (
            f"Repair failed: {diagnosis.cause_summary}. "
            f"Tried {len(attempts)} of {MAX_ATTEMPTS} automatic attempts: {tried}. "
            "Needs a human: check the diagnosis and the evidence, then repair by hand."
        )


def _condition_text(signal: FailureSignal) -> str:
    """Short condition for the repair hypothesis (under C)."""
    text = f"predicted={signal.predicted!r} observed={signal.observed!r}"
    return text if len(text) <= 200 else text[:197] + "..."
