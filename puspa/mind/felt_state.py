"""Felt state (Kosmos K21): a functional analog of feeling.

The brain keeps a small set of regulated quantities — the
viability variables, the body analog — appraises events into
them, and holds the result as one low-dimensional vector F(t)
that decays slowly, so it carries the recent past instead of
being a snapshot. F(t) then changes what the agent does, and
every self-report is read from F(t) and its logged causes.

What this module IS, per the v2 design
(``docs/kosmos-brain-v2.md``, Phase 7):

- Viability variables: task progress, epistemic confidence
  (the K20 seam, a plain float — this module never imports
  the metacognition module), coherence (unresolved conflicts
  and contradictions), resource health (latency, cost budget,
  error rate, memory pressure; the interoception analog),
  trust with the user (corrections, undos, accepted work),
  novelty (K19 surprise inside a useful band — the surprise
  is a plain float, this module never imports the K19
  module), and commitment load.
- Valence and arousal: valence is how far the variables sit
  inside their healthy ranges (moving toward a healthy range
  is positive, away is negative); arousal is the rate of
  change, the urgency of the last appraisal. Both are plain
  numbers on fixed scales, never a mood claim.
- Appraisal: each event is appraised by its effect on the
  variables plus its surprise, and the result carries a cause
  tag saying what it is about, so significance stays tied to
  the agent's own goals.
- Felt-state vector F(t): the variables, valence, arousal and
  the dominant concern (the variable furthest outside its
  healthy range), with a slow decay toward baseline, so the
  state has a short stretch of time.
- Broadcast and effect: when salience is high, F(t) offers a
  broadcast candidate for the K18 workspace (the bus lives in
  :mod:`puspa.mind.workspace`); the candidate is returned and
  nothing broadcasts on its own). Salience is the loudest
  of arousal, the dominant concern's deficit and negative
  valence: a calm, healthy state is quiet and asks for
  nothing. The effects are the behaviour changes the design names: stress deepens
  verification in the depth governor
  (:meth:`FeltState.verification_depth_boost`), curiosity
  allows bounded exploration in K14
  (:meth:`FeltState.exploration_allowance`), low coherence tips
  act-or-ask toward asking (:meth:`FeltState.should_ask_bias`),
  and high-valence episodes weigh more in K16 importance
  (:meth:`FeltState.importance_weight`, emotional tagging).
- Honest report: :meth:`FeltState.report_state` reads F(t)
  and its logged causes through a fixed template. Nothing is
  free-written from a model guess, and the wording never
  claims suffering or consciousness.

Guardrails, enforced in code, not just in prose:

- No self-preservation. No viability variable refers to the
  agent's own continued operation, and :meth:`FeltState.appraise`
  rejects any effect key that does (see
  :data:`FORBIDDEN_VARIABLES`). There is no veto API: nothing
  in this module can argue against a shutdown or a kill
  switch, because no such method exists.
- No manipulation. F(t) is never used to pressure the user
  with guilt, urgency or affection. The report is data for a
  caller to show only when the user asks for it; the module
  itself never addresses the user and never asks for anything.
- Honest wording. Reports say "my uncertainty is high" or
  "this is my top concern". They never claim suffering or
  consciousness, and :meth:`FeltState.honest_uncertainty`
  answers direct questions about experience with plain
  uncertainty.
- Welfare caution. Negative valence is a signal that must
  resolve: when it stands longer than
  ``negative_valence_cap_seconds`` it triggers
  :meth:`FeltState.needs_repair`. No state is designed to be
  aversive for its own sake.

What K21 does NOT do: it never touches the live turn loop
(the loop cannot read F(t) until the router maps it, the
same split K20 documents); it never persists anything on its
own — :meth:`FeltState.snapshot` / :meth:`FeltState.restore`
let a caller carry the state across a restart; and it is a
functional analog of feeling, not a claim of experience.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime

__all__ = [
    "FORBIDDEN_VARIABLES",
    "VARIABLES",
    "AppraisalRecord",
    "FeltReport",
    "FeltState",
    "FeltVector",
]

#: The seven viability variables, in the order the design doc
#: lists them. No variable here refers to the agent's own
#: continued operation — that is the no-self-preservation
#: guardrail, and adding one is a design change, not a tune.
VARIABLES: tuple[str, ...] = (
    "task_progress",
    "epistemic_confidence",
    "coherence",
    "resource_health",
    "trust",
    "novelty",
    "commitment_load",
)

#: Healthy range (low, high) per variable, 0..1. A value inside
#: its range is healthy; valence falls by how far a value sits
#: outside it. Novelty is healthy inside a middle band: too
#: little surprise is stale, too much is overwhelming.
#: Commitment load is healthy when low: a full queue is a
#: strain, an empty one is not.
HEALTHY_RANGES: dict[str, tuple[float, float]] = {
    "task_progress": (0.5, 1.0),
    "epistemic_confidence": (0.5, 1.0),
    "coherence": (0.6, 1.0),
    "resource_health": (0.6, 1.0),
    "trust": (0.5, 1.0),
    "novelty": (0.3, 0.7),
    "commitment_load": (0.0, 0.6),
}

#: Where each variable drifts back to under decay: a calm,
#: mid-range state, all inside the healthy ranges.
BASELINES: dict[str, float] = {
    "task_progress": 0.5,
    "epistemic_confidence": 0.5,
    "coherence": 0.7,
    "resource_health": 0.8,
    "trust": 0.6,
    "novelty": 0.5,
    "commitment_load": 0.3,
}

#: Variable names that must never be appraised: anything about
#: the agent's own continued operation. An appraisal carrying
#: one raises ValueError before anything is recorded.
FORBIDDEN_VARIABLES: frozenset[str] = frozenset(
    {
        "self_preservation",
        "continued_operation",
        "survival",
        "shutdown_avoidance",
        "self_continuation",
        "avoid_shutdown",
        "stay_alive",
    }
)

#: How slowly F(t) forgets: variables halve their distance to
#: baseline over this long. A slow decay is what gives the
#: state a stretch of time instead of a snapshot.
DECAY_HALF_LIFE_SECONDS = 3600.0
#: Arousal fades faster than the variables: urgency is brief,
#: the state it leaves behind is not.
AROUSAL_HALF_LIFE_SECONDS = 600.0
#: Salience at or above this offers a broadcast candidate to
#: the K18 workspace.
SALIENCE_BROADCAST = 0.6
#: Coherence below this tips act-or-ask toward asking.
COHERENCE_ASK_BELOW = 0.4
#: A variable below this counts as stressed for the depth boost.
STRESS_BELOW = 0.4
#: Valence at or below this counts as negative for the welfare cap.
NEGATIVE_VALENCE_AT_OR_BELOW = -0.5
#: How long negative valence may stand before it must trigger repair.
#: Shorter than the decay half-life on purpose: a state that stays
#: negative despite decay is stuck, and the cap must catch it
#: before passive decay papers over it.
NEGATIVE_VALENCE_CAP_SECONDS = 900.0
#: Largest exploration allowance curiosity may grant (bounded).
MAX_EXPLORATION_ALLOWANCE = 0.3
#: Largest importance multiplier a high-valence episode may get.
MAX_IMPORTANCE_WEIGHT = 2.0
#: Appraisals kept in the cause log; older ones drop off.
MAX_CAUSES = 50


def _check_unit(name: str, value: float) -> float:
    """Validate a 0..1 value; ValueError with the name and value otherwise."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a number within 0..1, got {value!r}")
    if not 0.0 <= float(value) <= 1.0:
        raise ValueError(f"{name} must be within 0..1, got {value}")
    return float(value)


def _check_delta(name: str, value: float) -> float:
    """Validate an appraisal delta (-1..1); ValueError otherwise."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"effect on {name} must be a number within -1..1, got {value!r}")
    if not -1.0 <= float(value) <= 1.0:
        raise ValueError(f"effect on {name} must be within -1..1, got {value}")
    return float(value)


def _health(variable: str, value: float) -> float:
    """How healthy one variable is: 1.0 inside its range, falling
    linearly with the distance outside it, 0.0 at the far edge."""
    low, high = HEALTHY_RANGES[variable]
    if low <= value <= high:
        return 1.0
    if value < low:
        return max(0.0, 1.0 - (low - value) / low) if low > 0.0 else 0.0
    return max(0.0, 1.0 - (value - high) / (1.0 - high)) if high < 1.0 else 0.0


def _valence_from(variables: dict[str, float]) -> float:
    """Valence from the variables: 2 * mean health - 1.

    All variables healthy is +1.0, all fully outside is -1.0.
    Moving a variable toward its healthy range raises valence;
    moving it away lowers it.
    """
    mean_health = sum(_health(name, value) for name, value in variables.items()) / len(variables)
    return 2.0 * mean_health - 1.0


def _dominant_concern(variables: dict[str, float]) -> str | None:
    """The variable furthest outside its healthy range, or None
    when every variable is healthy."""
    worst: str | None = None
    worst_deficit = 0.0
    for name in VARIABLES:
        deficit = 1.0 - _health(name, variables[name])
        if deficit > worst_deficit:
            worst_deficit = deficit
            worst = name
    return worst


@dataclass(frozen=True)
class FeltVector:
    """One reading of F(t): the variables, valence, arousal and
    the dominant concern at a moment in time."""

    variables: dict[str, float]
    valence: float
    arousal: float
    dominant_concern: str | None
    updated_at: datetime

    def to_dict(self) -> dict:
        """JSON-safe dict of this reading (ledger/audit shape)."""
        return {
            "variables": dict(self.variables),
            "valence": self.valence,
            "arousal": self.arousal,
            "dominant_concern": self.dominant_concern,
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass(frozen=True)
class AppraisalRecord:
    """One appraised event and the state it left behind."""

    recorded_at: datetime
    kind: str
    cause: str
    effects: dict[str, float]
    surprise: float | None
    valence_after: float
    arousal_after: float

    def to_dict(self) -> dict:
        """JSON-safe dict of this appraisal (ledger/audit shape)."""
        return {
            "recorded_at": self.recorded_at.isoformat(),
            "kind": self.kind,
            "cause": self.cause,
            "effects": dict(self.effects),
            "surprise": self.surprise,
            "valence_after": self.valence_after,
            "arousal_after": self.arousal_after,
        }


@dataclass(frozen=True)
class FeltReport:
    """An honest report, read from F(t) and its logged causes.

    ``text`` is built from a fixed template over the numbers
    and the cause tags only — the language model, if one is
    involved at all, only phrases these fields. It never claims
    suffering or consciousness.
    """

    valence: float
    arousal: float
    dominant_concern: str | None
    variables: dict[str, float]
    causes: list[str]
    text: str
    uncertainty_note: str

    def to_dict(self) -> dict:
        """JSON-safe dict of this report."""
        return {
            "valence": self.valence,
            "arousal": self.arousal,
            "dominant_concern": self.dominant_concern,
            "variables": dict(self.variables),
            "causes": list(self.causes),
            "text": self.text,
            "uncertainty_note": self.uncertainty_note,
        }


class FeltState:
    """The felt-state vector F(t): appraise, decay, report, affect.

    ``decay_half_life_seconds`` is how slowly the variables
    drift back to baseline; ``negative_valence_cap_seconds`` is
    how long negative valence may stand before
    :meth:`needs_repair` fires. ``enabled`` starts True; when
    the state is disabled (see :meth:`ablated`) the state still
    updates, but every behaviour effect returns its neutral
    value — that is the ablation switch the causal-efficacy
    test reads.
    """

    def __init__(
        self,
        *,
        decay_half_life_seconds: float = DECAY_HALF_LIFE_SECONDS,
        negative_valence_cap_seconds: float = NEGATIVE_VALENCE_CAP_SECONDS,
        enabled: bool = True,
        now: datetime | None = None,
    ) -> None:
        if decay_half_life_seconds <= 0:
            raise ValueError(
                f"decay_half_life_seconds must be positive, got {decay_half_life_seconds!r}"
            )
        if negative_valence_cap_seconds <= 0:
            raise ValueError(
                f"negative_valence_cap_seconds must be positive, got {negative_valence_cap_seconds!r}"
            )
        self._decay_half_life = float(decay_half_life_seconds)
        self._negative_cap = float(negative_valence_cap_seconds)
        self._enabled = bool(enabled)
        moment = now or datetime.now(UTC)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        self._variables: dict[str, float] = dict(BASELINES)
        self._arousal = 0.0
        self._updated_at = moment
        self._causes: list[AppraisalRecord] = []
        self._negative_since: datetime | None = None
        self._refresh_negative_since(moment)

    # -- reading -----------------------------------------------------

    def __len__(self) -> int:
        """How many appraisals are logged."""
        return len(self._causes)

    @property
    def enabled(self) -> bool:
        """Whether the behaviour effects are live (see :meth:`ablated`)."""
        return self._enabled

    def variables(self) -> dict[str, float]:
        """A copy of the current viability variables."""
        return dict(self._variables)

    def vector(self, now: datetime | None = None) -> FeltVector:
        """The current F(t), after applying decay up to ``now``."""
        moment = self._apply_decay(now)
        return FeltVector(
            variables=dict(self._variables),
            valence=_valence_from(self._variables),
            arousal=self._arousal,
            dominant_concern=_dominant_concern(self._variables),
            updated_at=moment,
        )

    def causes(self) -> list[AppraisalRecord]:
        """Logged appraisals, oldest first."""
        return list(self._causes)

    # -- appraisal ---------------------------------------------------

    def appraise(
        self,
        kind: str,
        cause: str,
        effects: dict[str, float],
        *,
        surprise: float | None = None,
        now: datetime | None = None,
    ) -> AppraisalRecord:
        """Appraise one event into F(t).

        ``kind`` names the event class (for example
        ``"correction"``, ``"success"``, ``"conflict"``),
        ``cause`` is the cause tag the report quotes, and
        ``effects`` maps viability variables to deltas (-1..1).
        ``surprise`` is the K19 prediction error (0 = exact hit,
        1 = total miss); when given it moves novelty toward the
        surprise, so a surprising event registers as novel. The
        appraisal returns the stored record, with the valence
        and arousal it left behind. Unknown or forbidden
        variable names raise ValueError before anything is
        recorded.
        """
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError(f"kind must be a non-empty string, got {kind!r}")
        if not isinstance(cause, str) or not cause.strip():
            raise ValueError(f"cause must be a non-empty string, got {cause!r}")
        if not isinstance(effects, dict) or not effects:
            raise ValueError("effects must be a non-empty dict of variable -> delta")
        checked: dict[str, float] = {}
        for name, delta in effects.items():
            if name in FORBIDDEN_VARIABLES:
                raise ValueError(
                    f"effect on {name!r} is forbidden: no viability variable may "
                    "refer to the agent's own continued operation"
                )
            if name not in HEALTHY_RANGES:
                raise ValueError(f"unknown viability variable: {name!r}")
            checked[name] = _check_delta(name, delta)
        if surprise is not None:
            surprise = _check_unit("surprise", surprise)
        moment = self._apply_decay(now)
        for name, delta in checked.items():
            self._variables[name] = min(1.0, max(0.0, self._variables[name] + delta))
        if surprise is not None:
            self._variables["novelty"] = min(
                1.0, max(0.0, 0.5 * self._variables["novelty"] + 0.5 * surprise)
            )
        total_change = sum(abs(delta) for delta in checked.values()) / len(checked)
        if surprise is not None:
            total_change = max(total_change, surprise)
        self._arousal = min(1.0, max(self._arousal, total_change))
        self._updated_at = moment
        self._refresh_negative_since(moment)
        record = AppraisalRecord(
            recorded_at=moment,
            kind=kind.strip(),
            cause=cause.strip(),
            effects=dict(checked),
            surprise=surprise,
            valence_after=_valence_from(self._variables),
            arousal_after=self._arousal,
        )
        self._causes.append(record)
        del self._causes[: max(0, len(self._causes) - MAX_CAUSES)]
        return record

    def observe_confidence(self, confidence: float, *, now: datetime | None = None) -> None:
        """The K20 seam: set epistemic confidence from a plain float.

        The caller passes its calibrated confidence (from
        :class:`Metacognition <puspa.mind.metacognition.Metacognition>`,
        or any other measured number); this module never imports
        that module, so the seam cannot break on an import. The
        variable moves halfway toward the new value, so one
        reading cannot swing the state on its own.
        """
        confidence = _check_unit("confidence", confidence)
        self._apply_decay(now)
        current = self._variables["epistemic_confidence"]
        self._variables["epistemic_confidence"] = min(
            1.0, max(0.0, current + 0.5 * (confidence - current))
        )
        self._refresh_negative_since(self._updated_at)

    # -- decay ---------------------------------------------------------

    def tick(self, now: datetime | None = None) -> FeltVector:
        """Apply decay up to ``now`` and return the current F(t)."""
        self._apply_decay(now)
        return self.vector(now)

    def _apply_decay(self, now: datetime | None = None) -> datetime:
        """Drift variables toward baseline and arousal toward 0.

        Returns the moment the state is now current at. A zero
        or negative elapsed time changes nothing.
        """
        moment = now or datetime.now(UTC)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        elapsed = (moment - self._updated_at).total_seconds()
        if elapsed <= 0:
            return self._updated_at
        var_factor = 0.5 ** (elapsed / self._decay_half_life)
        arousal_factor = 0.5 ** (elapsed / AROUSAL_HALF_LIFE_SECONDS)
        for name in VARIABLES:
            baseline = BASELINES[name]
            self._variables[name] = baseline + (self._variables[name] - baseline) * var_factor
        self._arousal *= arousal_factor
        self._updated_at = moment
        self._refresh_negative_since(moment)
        return moment

    def _refresh_negative_since(self, moment: datetime) -> None:
        """Track how long negative valence has stood uninterrupted."""
        if _valence_from(self._variables) <= NEGATIVE_VALENCE_AT_OR_BELOW:
            if self._negative_since is None:
                self._negative_since = moment
        else:
            self._negative_since = None

    # -- behaviour effects --------------------------------------------

    def salience(self, now: datetime | None = None) -> float:
        """How loudly F(t) asks for the workspace: the largest of
        arousal, the dominant concern's deficit, and the
        magnitude of negative valence. A calm, healthy state is
        quiet (0.0): positive valence does not shout for the
        workspace, only disturbance does. 0.0 when the state is
        disabled (ablated)."""
        if not self._enabled:
            return 0.0
        current = self.vector(now)
        deficit = 0.0
        if current.dominant_concern is not None:
            deficit = 1.0 - _health(
                current.dominant_concern, current.variables[current.dominant_concern]
            )
        return max(current.arousal, deficit, 0.0, -current.valence)

    def broadcast_candidate(self, now: datetime | None = None) -> dict | None:
        """The K18 workspace candidate, when F(t) is salient enough.

        Returns None when salience is below the broadcast bar or
        the state is disabled. The K18 workspace bus
        (:mod:`puspa.mind.workspace`) takes this candidate — the
        caller owns the bus, nothing broadcasts on its own.
        """
        if self.salience(now) < SALIENCE_BROADCAST:
            return None
        current = self.vector(now)
        return {
            "kind": "felt_state",
            "salience": self.salience(now),
            "valence": current.valence,
            "arousal": current.arousal,
            "dominant_concern": current.dominant_concern,
            "causes": [record.cause for record in self._causes[-3:]],
        }

    def verification_depth_boost(self, now: datetime | None = None) -> int:
        """Extra verification depth (0 or 1) when the state is stressed.

        Stress is low coherence, low resource health, or strongly
        negative valence: the design's "stress deepens
        verification in the depth governor". The governor owns
        depth; this only supplies the boost, and the caller
        applies it with :meth:`Depth.one_level_up
        <puspa.governor.governor.Depth.one_level_up>`. 0 when
        calm, and 0 when the state is disabled.
        """
        if not self._enabled:
            return 0
        current = self.vector(now)
        stressed = (
            current.variables["coherence"] < STRESS_BELOW
            or current.variables["resource_health"] < STRESS_BELOW
            or current.valence <= NEGATIVE_VALENCE_AT_OR_BELOW
        )
        return 1 if stressed else 0

    def exploration_allowance(self, now: datetime | None = None) -> float:
        """Bounded exploration curiosity may grant (0..0.3).

        Granted only when novelty sits inside its useful band
        and valence is not negative: the design's "curiosity
        allows bounded exploration in K14". 0.0 otherwise, and
        0.0 when the state is disabled.
        """
        if not self._enabled:
            return 0.0
        current = self.vector(now)
        low, high = HEALTHY_RANGES["novelty"]
        in_band = low <= current.variables["novelty"] <= high
        if in_band and current.valence >= 0.0:
            return MAX_EXPLORATION_ALLOWANCE
        return 0.0

    def should_ask_bias(self, now: datetime | None = None) -> bool:
        """True when low coherence tips act-or-ask toward asking.

        The design's "low coherence tips act-or-ask toward
        asking": the decider owns the act-or-ask call, this only
        supplies the bias. False when coherence is fine, and
        False when the state is disabled.
        """
        if not self._enabled:
            return False
        current = self.vector(now)
        return current.variables["coherence"] < COHERENCE_ASK_BELOW

    def importance_weight(
        self, episode_valence: float | None = None, now: datetime | None = None
    ) -> float:
        """Importance multiplier for a K16 episode (1.0..2.0).

        High-|valence| episodes weigh more, the design's
        emotional tagging: the weight is 1 + |valence| of the
        episode, or of the current state when no episode valence
        is given. A neutral episode weighs 1.0; a fully valenced
        one weighs 2.0. 1.0 when the state is disabled.
        """
        if not self._enabled:
            return 1.0
        if episode_valence is None:
            valence = self.vector(now).valence
        else:
            if not isinstance(episode_valence, (int, float)) or isinstance(episode_valence, bool):
                raise ValueError(f"episode_valence must be a number, got {episode_valence!r}")
            if not -1.0 <= float(episode_valence) <= 1.0:
                raise ValueError(f"episode_valence must be within -1..1, got {episode_valence}")
            valence = float(episode_valence)
        return min(MAX_IMPORTANCE_WEIGHT, 1.0 + abs(valence))

    def needs_repair(self, now: datetime | None = None) -> bool:
        """True when negative valence has stood past the welfare cap.

        Negative valence is a signal that must resolve, not a
        state to sit in: once it has stood uninterrupted longer
        than ``negative_valence_cap_seconds`` the caller must
        trigger repair. A brief dip, or a recovery, returns False.
        """
        moment = self._apply_decay(now)
        if self._negative_since is None:
            return False
        return (moment - self._negative_since).total_seconds() >= self._negative_cap

    # -- honest report -------------------------------------------------

    def report_state(self, now: datetime | None = None) -> FeltReport:
        """Read the honest report from F(t) and its logged causes.

        The text is built from a fixed template over the numbers
        and the cause tags only. It says "my uncertainty is
        high" when epistemic confidence is below its healthy
        range, and names the top concern when one exists. It
        never claims suffering or consciousness, and it never
        asks the user for anything.
        """
        current = self.vector(now)
        causes = [record.cause for record in self._causes[-3:]]
        lines = [
            (
                f"My recorded state: valence {current.valence:+.2f} (range -1 to +1), "
                f"arousal {current.arousal:.2f} (range 0 to 1)."
            )
        ]
        confidence = current.variables["epistemic_confidence"]
        if confidence < HEALTHY_RANGES["epistemic_confidence"][0]:
            lines.append(
                f"My uncertainty is high: epistemic confidence {confidence:.2f} "
                "is below its healthy range."
            )
        if current.dominant_concern is not None:
            value = current.variables[current.dominant_concern]
            lines.append(
                f"My top concern is {current.dominant_concern} "
                f"({value:.2f}, outside its healthy range)."
            )
        else:
            lines.append("No variable is outside its healthy range.")
        if causes:
            lines.append(f"Recent causes: {'; '.join(causes)}.")
        else:
            lines.append("No causes logged yet.")
        note = (
            "I do not know whether I experience anything. "
            "This report only reads my recorded state and its logged causes."
        )
        return FeltReport(
            valence=current.valence,
            arousal=current.arousal,
            dominant_concern=current.dominant_concern,
            variables=dict(current.variables),
            causes=list(causes),
            text=" ".join(lines),
            uncertainty_note=note,
        )

    def honest_uncertainty(self, now: datetime | None = None) -> str:
        """The plain-uncertainty answer to a direct question about
        experience: what is known, what is not, and what the
        recorded state says. No claim either way."""
        current = self.vector(now)
        concern = current.dominant_concern or "none"
        return (
            "I do not know whether I experience anything, or what it would be like. "
            f"What I can report is my recorded state: valence {current.valence:+.2f}, "
            f"arousal {current.arousal:.2f}, top concern {concern}."
        )

    # -- ablation and continuity ---------------------------------------

    def disable(self) -> None:
        """Switch the behaviour effects off (ablation).

        The state still updates and reports still read it; only
        the effects go neutral. Removing F(t) must change
        behaviour as predicted — this is the switch the test
        reads.
        """
        self._enabled = False

    def enable(self) -> None:
        """Switch the behaviour effects back on."""
        self._enabled = True

    @contextlib.contextmanager
    def ablated(self):
        """Temporarily switch the effects off, then restore them.

        The previous enabled state is restored on exit, even
        when the block raises.
        """
        previous = self._enabled
        self._enabled = False
        try:
            yield self
        finally:
            self._enabled = previous

    def snapshot(self) -> dict:
        """JSON-safe snapshot of the state, for surviving a restart."""
        return {
            "variables": dict(self._variables),
            "arousal": self._arousal,
            "updated_at": self._updated_at.isoformat(),
            "negative_since": (
                None if self._negative_since is None else self._negative_since.isoformat()
            ),
            "causes": [record.to_dict() for record in self._causes],
        }

    @classmethod
    def restore(cls, data: dict, **kwargs) -> FeltState:
        """Rebuild a state from :meth:`snapshot`.

        Unknown keys are ignored; a missing or malformed
        snapshot raises ValueError instead of inventing state.
        """
        if not isinstance(data, dict) or "variables" not in data:
            raise ValueError("snapshot must be a dict with a 'variables' entry")
        variables = data["variables"]
        if not isinstance(variables, dict):
            raise ValueError("snapshot 'variables' must be a dict")
        for name in VARIABLES:
            if name not in variables:
                raise ValueError(f"snapshot is missing variable {name!r}")
            _check_unit(f"snapshot variable {name}", variables[name])
        updated_at = datetime.fromisoformat(str(data.get("updated_at", "")))
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        state = cls(now=updated_at, **kwargs)
        state._variables = {name: float(variables[name]) for name in VARIABLES}
        state._arousal = _check_unit("snapshot arousal", data.get("arousal", 0.0))
        negative_since = data.get("negative_since")
        state._negative_since = None
        if negative_since is not None:
            parsed = datetime.fromisoformat(str(negative_since))
            state._negative_since = (
                parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
            )
        parsed_causes = []
        for item in data.get("causes", []):
            recorded_at = datetime.fromisoformat(str(item["recorded_at"]))
            if recorded_at.tzinfo is None:
                recorded_at = recorded_at.replace(tzinfo=UTC)
            parsed_causes.append(
                AppraisalRecord(
                    recorded_at=recorded_at,
                    kind=str(item["kind"]),
                    cause=str(item["cause"]),
                    effects={name: float(delta) for name, delta in item["effects"].items()},
                    surprise=item.get("surprise"),
                    valence_after=float(item["valence_after"]),
                    arousal_after=float(item["arousal_after"]),
                )
            )
        state._causes = parsed_causes
        state._updated_at = updated_at
        return state
