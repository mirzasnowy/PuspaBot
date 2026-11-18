"""Intent as a distribution over three levels (Kosmos K1).

The design document (``docs/kosmos-design.md`` section B) reads
intent as a distribution over three levels: the current desire,
the goal in progress, and long-term values. One message is read
at all three levels at once: "I'm so tired" is a desire to rest
now, a goal to protect sleep, and a value around health.

This module holds the distribution itself: three levels, each a
scored set of hypotheses whose weights are normalized to 1. An
empty level is allowed and recorded as such — no evidence means
no hypothesis, never an invented one. Inference lives in
:mod:`puspa.intent.inference`; the act-vs-ask use lives in
:mod:`puspa.intent.act_ask`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "CLEAR_CONFIDENCE",
    "CONFLICT_MARGIN",
    "LEVELS",
    "IntentDistribution",
    "IntentHypothesis",
    "IntentLevel",
]

#: The three levels, in order from most immediate to most durable.
LEVELS = ("desire", "goal", "values")

#: Top-two weight gap below which a level counts as conflicting:
#: two readings that close together cannot be told apart, so the
#: turn must ask instead of guessing.
CONFLICT_MARGIN = 0.15

#: Top-hypothesis weight at or above which the desire level counts
#: as clear enough to act on (given a reversible, low-stakes action).
#: A threshold, not a calibrated probability: the deterministic
#: inferrer scores keyword overlap, so this number only separates
#: "one reading clearly wins" from "the readings are muddled".
CLEAR_CONFIDENCE = 0.6

#: Tolerance for floating-point drift when checking a level sums to 1.
_WEIGHT_TOLERANCE = 1e-6


@dataclass(frozen=True)
class IntentHypothesis:
    """One reading of the user's intent at one level.

    ``label`` is a short human-readable reading (for example
    ``"pay bill pln"`` or ``"rest"``). ``weight`` is the
    hypothesis's share of its level, normalized so the level sums
    to 1. ``evidence`` lists the matched keywords that produced
    the reading, so any hypothesis can be traced back to the words
    (or the profile/world entries) behind it. ``source`` names
    where the candidate came from: ``"profile"``, ``"world"``, or
    ``"message"``.
    """

    label: str
    weight: float
    evidence: tuple[str, ...] = ()
    source: str = "message"


@dataclass
class IntentLevel:
    """One level of the distribution: a scored set of hypotheses.

    Built through :meth:`from_scores`, which drops zero-score
    candidates, sorts strongest first (ties broken by label, so
    the order is deterministic), and normalizes the weights to
    sum to 1. A level with no scoring candidate is empty:
    ``hypotheses`` is ``()``, ``top`` is None, and ``confidence``
    is 0.0. Direct construction with weights that do not sum to
    1 (and are not empty) raises ``ValueError`` — a level must
    never silently carry unnormalized weights.
    """

    name: str
    hypotheses: tuple[IntentHypothesis, ...] = ()

    def __post_init__(self) -> None:
        if self.name not in LEVELS:
            raise ValueError(f"unknown intent level: {self.name!r} (known: {LEVELS})")
        for hypothesis in self.hypotheses:
            if not 0.0 <= hypothesis.weight <= 1.0:
                raise ValueError(
                    f"hypothesis weight out of range: {hypothesis.label!r} "
                    f"has weight {hypothesis.weight}"
                )
        total = sum(h.weight for h in self.hypotheses)
        if self.hypotheses and abs(total - 1.0) > _WEIGHT_TOLERANCE:
            raise ValueError(
                f"level {self.name!r} weights sum to {total}, not 1: "
                "build levels through IntentLevel.from_scores"
            )

    @classmethod
    def from_scores(
        cls, name: str, scored: list[tuple[str, float, tuple[str, ...], str]]
    ) -> IntentLevel:
        """Build a level from ``(label, score, evidence, source)`` rows.

        Rows with a score of 0 or below are dropped (no evidence,
        no hypothesis). The rest are normalized so the weights sum
        to 1. An empty input (or all-zero scores) builds an empty
        level, honestly.
        """
        kept = [(label, score, evidence, source) for label, score, evidence, source in scored]
        kept = [row for row in kept if row[1] > 0]
        if not kept:
            return cls(name=name, hypotheses=())
        # Strongest first; label breaks ties so the order is stable
        # across runs for the same input.
        kept.sort(key=lambda row: (-row[1], row[0]))
        total = sum(row[1] for row in kept)
        hypotheses = tuple(
            IntentHypothesis(label=label, weight=score / total, evidence=evidence, source=source)
            for label, score, evidence, source in kept
        )
        return cls(name=name, hypotheses=hypotheses)

    @property
    def empty(self) -> bool:
        """True when no hypothesis scored at this level."""
        return not self.hypotheses

    @property
    def top(self) -> IntentHypothesis | None:
        """The strongest hypothesis, or None when the level is empty."""
        return self.hypotheses[0] if self.hypotheses else None

    @property
    def confidence(self) -> float:
        """The top hypothesis's weight, or 0.0 when the level is empty."""
        top = self.top
        return top.weight if top is not None else 0.0

    @property
    def total_weight(self) -> float:
        """Sum of the level's weights: 1.0, or 0.0 when empty."""
        return sum(h.weight for h in self.hypotheses)

    @property
    def conflicting(self) -> bool:
        """True when the top two hypotheses are too close to separate.

        Needs at least two hypotheses: a single reading, however
        weak, is not a conflict — it is low confidence, which the
        act-vs-ask rule handles separately.
        """
        if len(self.hypotheses) < 2:
            return False
        return self.hypotheses[0].weight - self.hypotheses[1].weight < CONFLICT_MARGIN

    def to_dict(self) -> dict:
        """Plain-data form of the level (JSON-safe)."""
        return {
            "name": self.name,
            "empty": self.empty,
            "hypotheses": [
                {
                    "label": h.label,
                    "weight": h.weight,
                    "evidence": list(h.evidence),
                    "source": h.source,
                }
                for h in self.hypotheses
            ],
        }


@dataclass
class IntentDistribution:
    """The full intent reading: one level per Kosmos level.

    ``message`` is the user message the reading came from (kept
    for audit, never re-read for scoring after inference).
    ``source`` names the inferrer that produced it
    (``"deterministic"`` by default; a future LLM inferrer names
    itself here so a reader can tell which signal they trust).
    """

    desire: IntentLevel = field(default_factory=lambda: IntentLevel("desire"))
    goal: IntentLevel = field(default_factory=lambda: IntentLevel("goal"))
    values: IntentLevel = field(default_factory=lambda: IntentLevel("values"))
    message: str = ""
    source: str = "deterministic"

    def level(self, name: str) -> IntentLevel:
        """One level by name (``"desire"``, ``"goal"``, or ``"values"``)."""
        if name == "desire":
            return self.desire
        if name == "goal":
            return self.goal
        if name == "values":
            return self.values
        raise ValueError(f"unknown intent level: {name!r} (known: {LEVELS})")

    @property
    def is_empty(self) -> bool:
        """True when no level read anything at all.

        This is the fail-open case for the act-vs-ask rule: with
        no signal anywhere, the distribution changes nothing and
        the existing behaviour (approval policy, gates) stands.
        """
        return self.desire.empty and self.goal.empty and self.values.empty

    @property
    def conflicting(self) -> bool:
        """True when any level holds two readings too close to separate."""
        return self.desire.conflicting or self.goal.conflicting or self.values.conflicting

    @property
    def confidence(self) -> float:
        """Confidence in the immediate reading: the desire level's top weight.

        The act-vs-ask rule decides on what the user wants right
        now; the goal and values levels inform the question asked,
        not the confidence number.
        """
        return self.desire.confidence

    def to_dict(self) -> dict:
        """Plain-data form of the distribution (JSON-safe)."""
        return {
            "message": self.message,
            "source": self.source,
            "empty": self.is_empty,
            "conflicting": self.conflicting,
            "confidence": self.confidence,
            "levels": {
                "desire": self.desire.to_dict(),
                "goal": self.goal.to_dict(),
                "values": self.values.to_dict(),
            },
        }
