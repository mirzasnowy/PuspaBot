"""Self-directed initiative (Kosmos K14, BDI).

The agent derives its own sub-goals from the user's standing
goals and open loops, acts on them without being told, and
reports what it did — with a working undo for every silent
action. Desires are the standing goals (the ``"goals"``
section of durable memory, ``puspa.memory.durable``); open
loops are pending commitments, unanswered messages, things
about to expire, and failures the repair loop already gave up
on. Intentions are the committed plans in the engine's queue.

The rules, from ``docs/kosmos-brain-v2.md`` (K14) and the
safety invariants that never loosen:

1. Silent work is reversible work only. Anything irreversible
   never runs silently: it goes into the daily digest (or asks
   the user first). Unknown reversibility counts as
   irreversible — the gate fails closed.
2. Silent work needs enough calibrated confidence (K20): the
   raw confidence is corrected by the area's measured bias
   through :class:`Metacognition <puspa.mind.metacognition.Metacognition>`
   when one is attached; without one the raw number stands,
   and the bar is the same.
3. Silent work respects the depth governor (V2-12): the
   governor decides the depth from the calibrated uncertainty
   and the stakes, and a step the governor sends deep never
   runs silently.
4. Silent work respects the autonomy dial (K11): the area's
   stage must allow running (``do_and_show`` or
   ``do_silently``). A fresh dial starts at ``propose``, so
   trust is earned before anything runs on its own. An area
   the dial does not know is blocked, not assumed.
5. Silent work is scored by expected benefit against its
   interruption cost (K10): the score is benefit times
   calibrated confidence, minus the cost of telling the user
   about it. The digest is one batched ping, so its cost is
   one K10 unit out of five.
6. A failed repair never runs silently: an open loop the
   repair loop already failed on goes to the digest, however
   reversible it looks.
7. Every silent action reports an outcome, not process: what
   was done, the result, and an undo handle. The undo is
   exercised by the tests (do, report, undo, verify the state
   is back) — an undo that does not work is reported as a
   missing undo, not hidden.
8. Before acting, a candidate may be dry-run in the K7
   simulator when one is attached: a simulated postcondition
   failure defers the candidate to the digest.

Where the inputs come from (nothing here invents them):

- Standing goals and pending commitments are read from a
  durable-memory snapshot (``collect_snapshot`` in
  ``puspa.memory.durable``): the ``"goals"`` section gives
  the desires, the ``"commitments"`` section gives the open
  loops. No second goal store is built.
- Other open loops (unanswered messages, expiring items,
  failed repairs) arrive as :class:`OpenLoop` values from the
  caller — the channel gateway, the scheduler, or the repair
  loop — because no store for them exists yet.
- The executor is injected: the engine owns the gating and
  the reporting, the caller owns the world access. An engine
  with no executor defers everything to the digest, so a
  scheduled job can never silently act without a wired
  executor.

The daily digest is a scheduled job on the existing path:
``run_daily_digest_job`` is what the scheduler calls for a
trigger of type ``"initiative_digest"`` (see
:mod:`puspa.scheduler.scheduler`), and it renders the
digest from the snapshot — standing goals plus pending
commitments — with no executor, so the job itself only ever
proposes. Delivery of the digest ping goes through the K10
interruption budget (kind ``"digest"``); the actual send to
a channel is the caller's job, not this module's.

All state is in memory: the intention queue, the reports,
and the digest. A restart loses the queue, which can only
make the agent less proactive, never bolder — the same
in-memory choice as the K6 ignorance map, the K11 dial, and
the K10 budget. Persisting the queue (a Postgres table +
migration, the K3/K4/K5 pattern) is the follow-up when
intentions need to survive restarts. No database, no
network, no live model call in this module.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from puspa.governor.governor import Depth, DepthGovernor, StepSignal
from puspa.proactivity.budget import Initiative, InterruptionBudget

__all__ = [
    "DIGEST_INTERRUPTION_COST",
    "LOOP_EXPIRING",
    "LOOP_FAILED_REPAIR",
    "LOOP_PENDING_COMMITMENT",
    "LOOP_STALE_FACT",
    "LOOP_UNANSWERED_MESSAGE",
    "OPEN_LOOP_KINDS",
    "SILENT_MAX_STAKES",
    "SILENT_MIN_CONFIDENCE",
    "ActionResult",
    "DailyDigest",
    "InitiativeEngine",
    "InitiativeReport",
    "OpenLoop",
    "StandingGoal",
    "SubGoal",
    "TickResult",
    "derive_subgoals",
    "open_loops_from_snapshot",
    "run_daily_digest_job",
    "standing_goals_from_snapshot",
]

#: Stakes at or below this may run silently (the governor's
#: reflex bar: reflex needs stakes at or below 0.3).
SILENT_MAX_STAKES = 0.3
#: Calibrated confidence at or above this may run silently
#: (uncertainty at or below 0.3, the governor's reflex bar).
SILENT_MIN_CONFIDENCE = 0.7
#: The interruption cost of one digest ping, as a 0..1 score:
#: one K10 unit out of a daily budget of five.
DIGEST_INTERRUPTION_COST = 0.2

#: A commitment the user made that is still open.
LOOP_PENDING_COMMITMENT = "pending_commitment"
#: A message the user has not answered yet.
LOOP_UNANSWERED_MESSAGE = "unanswered_message"
#: Something about to expire (a trial, a deadline, a price).
LOOP_EXPIRING = "expiring"
#: A failure the repair loop already gave up on.
LOOP_FAILED_REPAIR = "failed_repair"
#: A stored fact that has gone stale.
LOOP_STALE_FACT = "stale_fact"

OPEN_LOOP_KINDS = (
    LOOP_PENDING_COMMITMENT,
    LOOP_UNANSWERED_MESSAGE,
    LOOP_EXPIRING,
    LOOP_FAILED_REPAIR,
    LOOP_STALE_FACT,
)


def _check_unit(name: str, value: float) -> float:
    """Validate a 0..1 value; ValueError with the name and value otherwise."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a number within 0..1, got {value!r}")
    if not 0.0 <= float(value) <= 1.0:
        raise ValueError(f"{name} must be within 0..1, got {value}")
    return float(value)


def _check_text(name: str, value: str) -> str:
    """Validate a non-blank string; ValueError otherwise."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string, got {value!r}")
    return value.strip()


def _as_utc(moment: datetime | None) -> datetime:
    if moment is None:
        return datetime.now(UTC)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


# -- inputs: standing goals and open loops -------------------------------


@dataclass(frozen=True)
class StandingGoal:
    """One standing goal: a desire from durable memory.

    ``key`` is the fact key (``"standing_goal:..."``); ``text``
    is the goal in the user's own words; ``source`` names where
    it was read from (``"memory"`` for the durable snapshot).
    """

    key: str
    text: str
    source: str = "memory"

    def to_dict(self) -> dict:
        """JSON-safe dict of this goal (digest/snapshot shape)."""
        return {"key": self.key, "text": self.text, "source": self.source}


def standing_goals_from_snapshot(snapshot: dict) -> list[StandingGoal]:
    """The standing goals in a durable-memory snapshot.

    Reads the snapshot's ``"goals"`` section — the same items
    ``pinned_items`` loads first in recall — and nothing else.
    A snapshot without a goals section yields no goals, never
    an error: a user with no recorded goals simply has no
    desires for initiative to derive from.
    """
    goals: list[StandingGoal] = []
    for item in snapshot.get("goals") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        text = str(item.get("text") or "").strip()
        if not key or not text:
            continue
        goals.append(StandingGoal(key=key, text=text, source=str(item.get("source") or "memory")))
    return goals


@dataclass(frozen=True)
class OpenLoop:
    """One open loop: something unfinished the agent noticed.

    ``kind`` is one of :data:`OPEN_LOOP_KINDS`. ``title`` is a
    plain-language description of what is open; ``detail``
    carries the evidence (who owes what, what expires when).
    ``area`` is the K11 dial area the closing action belongs
    to (``"money"``, ``"messaging"``, ``"files"``,
    ``"scheduling"``, ``"system"``, ``"accounts"``,
    ``"shopping"``). ``reversible`` says whether closing the
    loop can be fully undone — False when unknown, so the gate
    fails closed. ``stakes`` is what a wrong close costs;
    ``confidence`` is how sure the agent is about the close;
    ``benefit`` is how much closing it is worth to the user
    (all 0..1). ``due`` is when the loop stops mattering, when
    known. ``source`` names who reported the loop.
    """

    loop_id: str
    kind: str
    title: str
    detail: str = ""
    area: str = "system"
    reversible: bool = False
    stakes: float = 0.5
    confidence: float = 0.5
    benefit: float = 0.5
    due: datetime | None = None
    source: str = "memory"

    def __post_init__(self) -> None:
        if not self.loop_id or not isinstance(self.loop_id, str):
            raise ValueError(f"loop_id must be a non-empty string, got {self.loop_id!r}")
        if self.kind not in OPEN_LOOP_KINDS:
            raise ValueError(f"unknown open-loop kind: {self.kind!r}")
        _check_text("title", self.title)
        object.__setattr__(self, "stakes", _check_unit("stakes", self.stakes))
        object.__setattr__(self, "confidence", _check_unit("confidence", self.confidence))
        object.__setattr__(self, "benefit", _check_unit("benefit", self.benefit))
        if self.due is not None and self.due.tzinfo is None:
            object.__setattr__(self, "due", self.due.replace(tzinfo=UTC))

    def to_dict(self) -> dict:
        """JSON-safe dict of this loop (digest shape)."""
        return {
            "loop_id": self.loop_id,
            "kind": self.kind,
            "title": self.title,
            "detail": self.detail,
            "area": self.area,
            "reversible": self.reversible,
            "stakes": self.stakes,
            "confidence": self.confidence,
            "benefit": self.benefit,
            "due": self.due.isoformat() if self.due is not None else None,
            "source": self.source,
        }


def open_loops_from_snapshot(snapshot: dict) -> list[OpenLoop]:
    """The open loops in a durable-memory snapshot.

    Every pending commitment becomes one loop of kind
    ``"pending_commitment"``. Its closing action is unknown
    at scan time, so ``reversible`` is False and the stakes
    and confidence sit at the neutral 0.5: a commitment never
    runs silently on the strength of the snapshot alone — it
    goes to the digest unless the caller supplies a loop that
    says more about it. Loops are ordered as listed.
    """
    loops: list[OpenLoop] = []
    for item in snapshot.get("commitments") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        text = str(item.get("text") or "").strip()
        if not key or not text:
            continue
        loops.append(
            OpenLoop(
                loop_id=f"commitment:{key}",
                kind=LOOP_PENDING_COMMITMENT,
                title=text,
                detail=f"standing commitment {key!r} is still open",
                area="system",
                reversible=False,
                stakes=0.5,
                confidence=0.5,
                benefit=0.6,
                source=str(item.get("source") or "memory"),
            )
        )
    return loops


# -- derived sub-goals ----------------------------------------------------


@dataclass
class SubGoal:
    """One candidate sub-goal: the agent's own plan to close a loop.

    ``goal_key`` names the standing goal it serves (empty when
    no goal matches the loop — a loop alone still derives a
    candidate). ``loop_kind`` is the kind of the loop it closes.
    ``benefit`` / ``confidence`` / ``stakes`` / ``reversible``
    / ``area`` are copied from the loop; ``score`` is the
    benefit-versus-interruption number the tick sorts by
    (set by :func:`derive_subgoals`). ``status`` is
    ``"proposed"``, ``"committed"``, ``"done"``,
    ``"deferred"`` or ``"failed"``. ``gate_reason`` names why
    the silent gate passed or blocked it, so the digest can
    quote it.
    """

    subgoal_id: str
    title: str
    detail: str
    goal_key: str
    loop_id: str
    loop_kind: str
    area: str
    benefit: float
    confidence: float
    stakes: float
    reversible: bool
    score: float = 0.0
    status: str = "proposed"
    gate_reason: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict:
        """JSON-safe dict of this sub-goal (digest/queue shape)."""
        return {
            "subgoal_id": self.subgoal_id,
            "title": self.title,
            "detail": self.detail,
            "goal_key": self.goal_key,
            "loop_id": self.loop_id,
            "loop_kind": self.loop_kind,
            "area": self.area,
            "benefit": self.benefit,
            "confidence": self.confidence,
            "stakes": self.stakes,
            "reversible": self.reversible,
            "score": self.score,
            "status": self.status,
            "gate_reason": self.gate_reason,
            "created_at": self.created_at.isoformat(),
        }


def _goal_matches(goal: StandingGoal, loop: OpenLoop) -> bool:
    """True when the goal and the loop are about the same thing.

    A shared word of four or more letters between the goal's
    key/text and the loop's title/detail (case-insensitive).
    Deliberately word-based, not model-based: derivation must
    be explainable from the two texts, and a match the user
    can read is a match the user can dispute.
    """
    goal_words = {
        word
        for word in (goal.key + " " + goal.text).lower().replace(":", " ").split()
        if len(word) >= 4
    }
    loop_words = {
        word for word in (loop.title + " " + loop.detail).lower().split() if len(word) >= 4
    }
    return bool(goal_words & loop_words)


def derive_subgoals(
    goals: list[StandingGoal],
    loops: list[OpenLoop],
    *,
    now: datetime | None = None,
) -> list[SubGoal]:
    """Derive one candidate sub-goal per open loop.

    Each loop becomes the plan "close this loop", attached to
    the first standing goal it matches (or to none, when it
    matches no goal — a loop the user never framed as a goal
    is still worth proposing). The score is the expected
    benefit against the interruption cost: benefit times
    confidence, minus one digest ping's share of the K10
    budget. Candidates come back best score first.
    """
    moment = _as_utc(now)
    derived: list[SubGoal] = []
    for loop in loops:
        goal_key = ""
        for goal in goals:
            if _goal_matches(goal, loop):
                goal_key = goal.key
                break
        score = loop.benefit * loop.confidence - DIGEST_INTERRUPTION_COST
        derived.append(
            SubGoal(
                subgoal_id=f"subgoal-{loop.loop_id}-{uuid.uuid4().hex[:8]}",
                title=f"Close open loop: {loop.title}",
                detail=loop.detail,
                goal_key=goal_key,
                loop_id=loop.loop_id,
                loop_kind=loop.kind,
                area=loop.area,
                benefit=loop.benefit,
                confidence=loop.confidence,
                stakes=loop.stakes,
                reversible=loop.reversible,
                score=score,
                created_at=moment,
            )
        )
    derived.sort(key=lambda item: item.score, reverse=True)
    return derived


# -- action results and reports -------------------------------------------


@dataclass(frozen=True)
class ActionResult:
    """What one silent action produced.

    ``ok`` says the action ran to completion; it does not say
    the outcome is right — the engine records it as a scored
    decision either way. ``undo`` is the callable that puts
    the world back as it was (returning True when the state is
    restored); a reversible action without one is reported as
    a missing undo, never assumed undone. ``result`` carries
    the observed outcome for the report.
    """

    ok: bool
    detail: str
    undo: Callable[[], bool] | None = None
    result: dict | None = None

    def to_dict(self) -> dict:
        """JSON-safe dict of this result (report shape)."""
        return {
            "ok": self.ok,
            "detail": self.detail,
            "undo_available": self.undo is not None,
            "result": dict(self.result) if self.result is not None else None,
        }


@dataclass
class InitiativeReport:
    """The outcome of one self-directed action, with its undo.

    ``status`` is ``"done"`` (ran and the executor said ok),
    ``"failed"`` (ran and the executor said not ok), or
    ``"undone"`` (done, then undone by the user). ``undo``
    runs the stored undo callable exactly once and only
    counts as undone when it reports the state restored; a
    second call returns False and changes nothing, and a
    report with no undo callable returns False too. The
    report never claims an undo it does not hold.
    """

    subgoal: SubGoal
    status: str
    detail: str
    undo_callable: Callable[[], bool] | None = None
    reported_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.status not in ("done", "failed", "undone"):
            raise ValueError(f"unknown report status: {self.status!r}")

    @property
    def undo_available(self) -> bool:
        """True when this report holds a working undo handle."""
        return self.undo_callable is not None and self.status == "done"

    @property
    def undone(self) -> bool:
        """True once the undo has run and reported the state restored."""
        return self.status == "undone"

    def undo(self) -> bool:
        """Undo this action once; True when the state is restored.

        Returns False when there is no undo callable, when the
        report is not a done action (a failed action has
        nothing to undo), or when the undo already ran. The
        undo callable's own answer decides: only a True return
        marks the report undone.
        """
        if self.status != "done" or self.undo_callable is None:
            return False
        restored = bool(self.undo_callable())
        if restored:
            self.status = "undone"
        return restored

    def to_dict(self) -> dict:
        """JSON-safe dict of this report (digest/ledger shape)."""
        return {
            "subgoal_id": self.subgoal.subgoal_id,
            "title": self.subgoal.title,
            "status": self.status,
            "detail": self.detail,
            "undo_available": self.undo_available,
            "undone": self.undone,
            "reported_at": self.reported_at.isoformat(),
        }


# -- the daily digest ------------------------------------------------------


@dataclass
class DailyDigest:
    """One day's initiative summary: done, deferred, proposed.

    Done items are reports of silent work (each with its undo
    handle); deferred items are sub-goals the silent gate
    blocked, with the reason quoted; proposed items are
    candidates the user has not been asked about yet. The
    digest is a summary, never a silent execution: nothing in
    it ran without the gate.
    """

    day: str
    done: list[InitiativeReport] = field(default_factory=list)
    deferred: list[SubGoal] = field(default_factory=list)
    proposed: list[SubGoal] = field(default_factory=list)

    def __len__(self) -> int:
        """How many items the digest carries, all kinds together."""
        return len(self.done) + len(self.deferred) + len(self.proposed)

    def render(self) -> str:
        """The digest as plain text the user can read.

        Outcomes first (what was done, with undo noted),
        then what was held back and why, then what is proposed.
        A done item without an undo gets a warning line: the
        user must be able to see that it cannot be undone.
        """
        lines = [f"Daily initiative digest — {self.day}", ""]
        if self.done:
            lines.append("Done on my own:")
            for report in self.done:
                undo_note = (
                    "undo available" if report.undo_available else "WARNING: no undo available"
                )
                lines.append(f"- {report.subgoal.title}: {report.detail} ({undo_note})")
            lines.append("")
        if self.deferred:
            lines.append("Held back for you:")
            for item in self.deferred:
                lines.append(f"- {item.title} — {item.gate_reason}")
            lines.append("")
        if self.proposed:
            lines.append("Proposed:")
            for item in self.proposed:
                lines.append(f"- {item.title} (score {item.score:.2f})")
            lines.append("")
        if len(self) == 0:
            lines.append("Nothing to report: no open loops, no silent work.")
            lines.append("")
        return "\n".join(lines).rstrip("\n") + "\n"

    def deliver(self, budget: InterruptionBudget, *, day: date | datetime | str | None = None):
        """Ask the K10 budget whether this digest may ping the user.

        Kind ``"digest"``: one batched ping, cost 1. An empty
        digest never pings — there is nothing to say. Returns
        the budget's decision.
        """
        if len(self) == 0:
            raise ValueError("an empty digest has nothing to deliver")
        value = 0.5
        for item in self.deferred + self.proposed:
            value = max(value, min(1.0, item.score + DIGEST_INTERRUPTION_COST))
        if self.done:
            value = max(value, 0.5)
        return budget.try_spend(
            Initiative(kind="digest", value=value, label=f"initiative-digest-{self.day}"),
            day=day if day is not None else self.day,
        )

    def to_dict(self) -> dict:
        """JSON-safe dict of this digest (scheduler-job shape)."""
        return {
            "day": self.day,
            "done": [report.to_dict() for report in self.done],
            "deferred": [item.to_dict() for item in self.deferred],
            "proposed": [item.to_dict() for item in self.proposed],
            "rendered": self.render(),
        }


@dataclass
class TickResult:
    """What one scheduler tick did: silent work, deferrals, digest."""

    done: list[InitiativeReport]
    deferred: list[SubGoal]
    digest: DailyDigest
    delivery: object | None = None
    delivered: bool = False

    def to_dict(self) -> dict:
        """JSON-safe dict of this tick (ledger/job shape)."""
        return {
            "done": [report.to_dict() for report in self.done],
            "deferred": [item.to_dict() for item in self.deferred],
            "digest": self.digest.to_dict(),
            "delivered": self.delivered,
        }


# -- the engine ------------------------------------------------------------


class InitiativeEngine:
    """Derive sub-goals from standing goals and open loops, and act.

    ``metacognition`` (K20) calibrates each candidate's
    confidence against its area's history; without one the raw
    confidence stands. ``governor`` (V2-12) decides the depth
    from the calibrated uncertainty and the stakes; without one
    a fresh governor is used. ``dial`` (K11) gates silent work
    by area stage; without one the dial gate is skipped and the
    caller owns the dial (the engine says so in its gate
    reason). ``budget`` (K10) gates the digest ping; without
    one the digest is rendered but never marked delivered.
    ``executor`` runs a silent action (``SubGoal -> ActionResult``);
    without one nothing runs silently — every candidate defers.
    ``simulator`` dry-runs a candidate first (``SubGoal -> bool``:
    True when the simulated postcondition holds); a False
    answer defers the candidate.

    The intention queue (``queue``) holds the committed
    candidates from the last scan, best score first. All state
    is in memory; see the module docstring for the restart
    limit.
    """

    def __init__(
        self,
        *,
        metacognition=None,
        governor: DepthGovernor | None = None,
        dial=None,
        budget: InterruptionBudget | None = None,
        executor: Callable[[SubGoal], ActionResult] | None = None,
        simulator: Callable[[SubGoal], bool] | None = None,
    ) -> None:
        self._metacognition = metacognition
        self._governor = governor if governor is not None else DepthGovernor()
        self._dial = dial
        self._budget = budget
        self._executor = executor
        self._simulator = simulator
        self._queue: list[SubGoal] = []
        # Loop ids whose silent run already failed once: the
        # governor sends their retry deep, so they defer.
        self._failed_loop_ids: set[str] = set()
        self.history: list[TickResult] = []

    @property
    def queue(self) -> list[SubGoal]:
        """The committed intentions from the last scan, best first."""
        return list(self._queue)

    def scan(
        self,
        goals: list[StandingGoal],
        loops: list[OpenLoop],
        *,
        now: datetime | None = None,
    ) -> list[SubGoal]:
        """Derive and commit the candidates for these goals and loops.

        Derivation is :func:`derive_subgoals` (one candidate per
        loop, best score first). Committing means the queue holds
        them: a later tick runs from the queue, not from a fresh
        derivation. Candidates already in the queue keep their
        place; new ones join by score.
        """
        derived = derive_subgoals(goals, loops, now=now)
        # One candidate per loop: derivation mints fresh ids each
        # scan, so dedupe on the loop, not the id — otherwise
        # every tick would queue the same loop again. A loop
        # already queued keeps its first candidate; its numbers
        # are re-derived on the next scan only when it leaves
        # the queue (done, undone, or a fresh engine).
        known = {item.loop_id for item in self._queue}
        for item in derived:
            if item.loop_id not in known:
                item.status = "committed"
                self._queue.append(item)
        self._queue.sort(key=lambda item: item.score, reverse=True)
        return list(self._queue)

    def tick(
        self,
        goals: list[StandingGoal],
        loops: list[OpenLoop],
        *,
        now: datetime | None = None,
        day: date | datetime | str | None = None,
    ) -> TickResult:
        """One scheduler tick: run the silent work, digest the rest.

        Scans the goals and loops, then takes each candidate in
        score order: through the silent gate it runs at once
        (simulated first when a simulator is attached), and
        anything the gate blocks goes into the daily digest with
        the reason quoted. The digest ping is offered to the
        K10 budget when a budget is attached; without one the
        digest is rendered and the delivery stays unmarked.
        """
        moment = _as_utc(now)
        day_key = self._day_key(day, moment)
        candidates = self.scan(goals, loops, now=moment)
        done: list[InitiativeReport] = []
        deferred: list[SubGoal] = []
        for candidate in candidates:
            if candidate.status in ("done", "undone"):
                continue
            calibrated = self._calibrated_confidence(candidate)
            allowed, reason = self._silent_gate(candidate, calibrated)
            candidate.gate_reason = reason
            if not allowed:
                candidate.status = "deferred"
                deferred.append(candidate)
                continue
            report = self._run_silently(candidate, calibrated, moment)
            if report is None:
                candidate.status = "deferred"
                deferred.append(candidate)
                continue
            done.append(report)
        digest = DailyDigest(day=day_key, done=done, deferred=deferred)
        delivery = None
        delivered = False
        if self._budget is not None and len(digest) > 0:
            delivery = digest.deliver(self._budget, day=day_key)
            delivered = bool(delivery.allowed)
        result = TickResult(
            done=done, deferred=deferred, digest=digest, delivery=delivery, delivered=delivered
        )
        self.history.append(result)
        return result

    # -- calibration and gating -------------------------------------------

    def _calibrated_confidence(self, candidate: SubGoal) -> float:
        """This candidate's confidence, calibrated by area history."""
        if self._metacognition is None:
            return candidate.confidence
        return self._metacognition.calibrated_confidence(candidate.area, candidate.confidence)

    def _silent_gate(self, candidate: SubGoal, calibrated: float) -> tuple[bool, str]:
        """Whether this candidate may run silently, and why (not).

        Every check must pass: reversible, low stakes, high
        calibrated confidence, the governor not sending it deep
        (including a retry after a failed silent run), the dial
        allowing the area to run, an executor wired, and a failed
        repair never running silently. The reason names the first
        check that failed, so the digest quotes exactly what held
        the work back.
        """
        if candidate.loop_kind == LOOP_FAILED_REPAIR:
            return False, "a failed repair never runs silently; it needs a human look"
        if not candidate.reversible:
            return (
                False,
                "the closing action is not reversible (or unknown), so it never runs silently",
            )
        if candidate.stakes > SILENT_MAX_STAKES:
            return (
                False,
                f"stakes {candidate.stakes:.2f} are above the silent bar {SILENT_MAX_STAKES:.2f}",
            )
        if calibrated < SILENT_MIN_CONFIDENCE:
            return (
                False,
                (
                    f"calibrated confidence {calibrated:.2f} is below the silent bar "
                    f"{SILENT_MIN_CONFIDENCE:.2f}"
                ),
            )
        signal = self._step_signal(candidate, calibrated)
        decision = self._governor.decide(signal)
        if decision.depth is Depth.DEEP:
            return False, f"the depth governor sends this deep ({decision.reason})"
        dial_stage = ""
        if self._dial is not None:
            try:
                advice = self._dial.advise(candidate.area)
            except ValueError:
                return False, f"the autonomy dial knows no area {candidate.area!r}"
            if not advice.run:
                return (
                    False,
                    (
                        f"the autonomy dial holds {candidate.area} at {advice.stage}: "
                        "the action needs the user's go-ahead first"
                    ),
                )
            dial_stage = f", dial stage {advice.stage}"
        if self._executor is None:
            return False, "no executor is wired, so nothing runs silently"
        if self._simulator is not None and not self._simulator(candidate):
            return False, "the simulated dry-run failed its postcondition"
        return True, (
            f"silent: reversible, stakes {candidate.stakes:.2f}, calibrated confidence "
            f"{calibrated:.2f}, governor depth {decision.depth.value}{dial_stage}"
        )

    def _step_signal(self, candidate: SubGoal, calibrated: float) -> StepSignal:
        """The governor's step signal for this candidate.

        A candidate whose silent run already failed once carries
        ``previous_failure``: the governor sends it deep, so a
        failed silent action is never silently retried.
        """
        previous_failure = candidate.loop_id in self._failed_loop_ids
        if self._metacognition is not None:
            return self._metacognition.to_step_signal(
                candidate.area,
                candidate.confidence,
                candidate.stakes,
                reversible=candidate.reversible,
                previous_failure=previous_failure,
            )
        return StepSignal(
            uncertainty=1.0 - calibrated,
            stakes=candidate.stakes,
            reversible=candidate.reversible,
            previous_failure=previous_failure,
        )

    def _run_silently(
        self, candidate: SubGoal, calibrated: float, moment: datetime
    ) -> InitiativeReport | None:
        """Run one gated candidate through the executor.

        Returns None only when there is no executor (the gate
        already checked, so this is belt-and-braces). The
        outcome is scored back into metacognition and the dial
        either way: a silent action that fails drags its area's
        confidence and dial down, like any other scored
        decision.
        """
        if self._executor is None:
            return None
        result = self._executor(candidate)
        status = "done" if result.ok else "failed"
        candidate.status = status
        if not result.ok:
            self._failed_loop_ids.add(candidate.loop_id)
        report = InitiativeReport(
            subgoal=candidate,
            status=status,
            detail=result.detail,
            undo_callable=result.undo if result.ok else None,
            reported_at=moment,
        )
        if self._metacognition is not None:
            self._metacognition.record_decision(
                candidate.area, candidate.confidence, result.ok, stakes=candidate.stakes
            )
        if self._dial is not None:
            # The gate already validated this area, so a
            # ValueError here cannot happen on the same dial.
            self._dial.record_outcome(candidate.area, result.ok)
        return report

    @staticmethod
    def _day_key(day: date | datetime | str | None, moment: datetime) -> str:
        """Normalise a digest day to an ISO date string."""
        if day is None:
            return moment.date().isoformat()
        if isinstance(day, datetime):
            return day.date().isoformat()
        if isinstance(day, date):
            return day.isoformat()
        raw = str(day)
        date.fromisoformat(raw)  # raises ValueError for a bad date
        return raw


# -- the scheduled job -------------------------------------------------------


def run_daily_digest_job(
    user_id: str,
    *,
    dsn: str | None = None,
    snapshot: dict | None = None,
    engine: InitiativeEngine | None = None,
    extra_loops: list[OpenLoop] | None = None,
    now: datetime | None = None,
) -> dict:
    """Run one daily initiative digest for a user (scheduler job).

    Reads the standing goals and pending commitments from the
    durable-memory snapshot (``collect_snapshot`` when no
    snapshot is injected), adds any caller-supplied open loops,
    and ticks the engine. The job's own engine has no executor
    unless the caller supplies one, so the job only ever
    proposes: silent work happens only where an executor was
    deliberately wired. Returns the digest dict (date, done,
    deferred, rendered text, delivery decision): the scheduler
    branch for trigger type ``"initiative_digest"`` returns
    this as its result.
    """
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError(f"user_id must be a non-empty string, got {user_id!r}")
    moment = _as_utc(now)
    if snapshot is None:
        from puspa.memory.durable import collect_snapshot

        snapshot = collect_snapshot(str(user_id), dsn=dsn)["snapshot"]
    goals = standing_goals_from_snapshot(snapshot)
    loops = open_loops_from_snapshot(snapshot)
    loops.extend(extra_loops or [])
    worker = engine if engine is not None else InitiativeEngine(budget=InterruptionBudget())
    result = worker.tick(goals, loops, now=moment)
    rendered = result.digest.to_dict()
    rendered["user_id"] = str(user_id)
    return rendered
