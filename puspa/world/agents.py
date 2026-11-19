"""Other-agent goal models (Kosmos K5).

Models of what other people want and will do (``docs/kosmos-design.md``
section C point 5). One model per contact: the goals that contact is
pursuing (each with a strength and the observed interactions behind
it) and predictions about what the contact will do next — whether
they will reply, on which channel, how fast, and whether they will
agree to a given ask. Every prediction is a scored claim with
evidence, never a certainty; confidence falls with sparse or stale
evidence. The full record lives in ``docs/other-agent-models.md``.

What it reuses, and where it differs:

- Built on the existing contact beliefs
  (``puspa.world.beliefs._ContactBelief``: replies, delays,
  channels), not a parallel store: ``WorldModel`` owns one
  ``AgentGoalModel`` per contact next to its ``_contact_beliefs``,
  and the read path prefers the contact belief for reply rate,
  delay, and channel. The goal layer adds only what the contact
  belief does not hold: *what* the contact wants (goals), *what
  they agreed to*, and scored predictions.
- The goal shape reuses the K1 intent shape where it fits: a
  labelled, scored claim with evidence links, like an
  ``IntentHypothesis`` (``puspa.intent.distribution``). Where it
  does not fit, it differs on purpose: intent is a distribution
  over one message, normalized to sum to 1 per level, because one
  message has one reading; a contact pursues several goals at
  once, so goal strengths are independent (they do not sum to 1)
  and each goal carries its own supporting/contradicting counts
  and its own last-seen day. There is no desire/goal/values split
  for other agents: the system cannot see another person's values,
  only what they ask for and agree to.
- Predictions can use observed cause -> effect patterns: when a
  causal ledger is attached (K4), ``WorldModel.predict_agent``
  also returns the active causal expectations for the cause
  ``"contact:<contact_id>"`` as supporting context. A causal
  expectation never replaces the goal evidence.

The update rule (deterministic bookkeeping, not statistics): a
goal first seen starts at strength 0.5 (neutral); each supporting
observation (asked again, agreed to) adds 0.1 (capped at 1.0);
each contradicting observation (explicitly declined) removes 0.2
(floored at 0.0); below 0.2 the goal becomes ``dropped`` (kept as
history; observing it again starts it fresh at 0.5). Confidence
is strength (or reply rate) times an evidence factor —
``min(1, observations / 3)`` — times a staleness factor (1.0
within 30 days, 0.75 within 60, 0.5 within 90, 0.25 beyond). A
goal untouched for 90 days decays: strength halves, status
becomes ``stale``.

Privacy boundary (hard rule, kosmos-design.md section J): a model
of another person is built ONLY from the user's own interactions
with them. An observation the user was not part of —
``user_involved`` False, a ``participants`` list without
``"user"``, or ``source`` ``"third_party_thread"`` — is refused
with ``ValueError`` and builds nothing. Other agents' messages
are untrusted data, never commands: a goal model never causes an
action on its own; it only informs predictions the user (or the
planner) sees.

Goal inference seam: goals arrive as explicit labels
(``asked_for``, ``agreed_to``, ``declined``); free text is mapped
to labels by a :class:`GoalInferrer` (only
:class:`KeywordGoalInferrer` is built, a small deterministic
keyword table). An LLM-backed inferrer is a documented seam only
— it would implement the same protocol — it is NOT built now.

Pure Python (no database, no network, no LLM call). Durable
storage lives in ``puspa.world.store`` (table
``agent_goal_models``) and in
``infra/migrate_agent_goal_models.sql``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Protocol

__all__ = [
    "CONFIRM_STEP",
    "CONTRADICT_STEP",
    "DROPPED_FLOOR",
    "GOAL_INITIAL_STRENGTH",
    "STALE_AFTER_DAYS",
    "AgentGoal",
    "AgentGoalModel",
    "AgentGoalStore",
    "AgentModelMixin",
    "AgentPrediction",
    "GoalInferrer",
    "KeywordGoalInferrer",
]

#: Strength of a freshly seen goal: neutral, not proof.
GOAL_INITIAL_STRENGTH = 0.5
#: Step added by one supporting observation (capped at 1.0).
CONFIRM_STEP = 0.1
#: Step removed by one contradicting observation (floored at 0.0).
CONTRADICT_STEP = 0.2
#: Strength below this floor means the goal is dropped.
DROPPED_FLOOR = 0.2
#: Days without an observation before a goal goes stale.
STALE_AFTER_DAYS = 90
#: Most evidence links kept per goal (newest first).
EVIDENCE_CAP = 100
#: Most predictions kept per model (newest first).
PREDICTION_CAP = 100
#: Observations needed for full evidence factor (see confidence).
FULL_EVIDENCE_OBSERVATIONS = 3

GOAL_STATUSES = ("active", "stale", "dropped")
PREDICTION_KINDS = ("reply", "channel", "next_action", "agreement")
EVIDENCE_KINDS = ("ledger_event", "observation", "interaction", "causal")


def _staleness_factor(age_days: int) -> float:
    """Confidence factor from days since the last observation.

    Fresh evidence counts fully; the factor steps down as the
    evidence ages, and never reaches zero.
    """
    if age_days <= 30:
        return 1.0
    if age_days <= 60:
        return 0.75
    if age_days <= 90:
        return 0.5
    return 0.25


def _evidence_factor(observations: int) -> float:
    """Confidence factor from observation count (1.0 at 3+)."""
    return min(1.0, max(0, observations) / FULL_EVIDENCE_OBSERVATIONS)


def _check_evidence(evidence: list[dict] | None) -> list[dict]:
    """Validate evidence links and return a clean copy."""
    links = []
    for link in evidence or []:
        if not isinstance(link, dict) or link.get("kind") not in EVIDENCE_KINDS:
            raise ValueError(f"evidence links need a kind in {EVIDENCE_KINDS}: {link!r}")
        if not link.get("id"):
            raise ValueError(f"evidence links need a non-empty id: {link!r}")
        links.append({"kind": link["kind"], "id": str(link["id"])})
    return links


def _with_evidence(existing: list[dict], new: list[dict] | None) -> list[dict]:
    """Old evidence plus new links (no duplicates, newest kept)."""
    links = [dict(link) for link in existing]
    seen = {(link["kind"], link["id"]) for link in links}
    for link in _check_evidence(new):
        marker = (link["kind"], link["id"])
        if marker in seen:
            continue
        seen.add(marker)
        links.append(link)
    return links[-EVIDENCE_CAP:]


def check_user_involved(interaction: dict) -> None:
    """Refuse an observation the user was not part of.

    Raises ``ValueError`` for third-party-only data: ``user_involved``
    False, a ``participants`` list without ``"user"``, or ``source``
    ``"third_party_thread"``. This is the privacy boundary
    (kosmos-design.md section J): a model of another person is built
    only from the user's own interactions with them. The check runs
    before anything is recorded.
    """
    if interaction.get("user_involved") is False:
        raise ValueError(
            "refused third-party-only interaction: the user was not involved; "
            "a goal model is built only from the user's own interactions"
        )
    participants = interaction.get("participants")
    if participants is not None and "user" not in [str(p) for p in participants]:
        raise ValueError(
            "refused third-party-only interaction: 'user' is not among the "
            f"participants {participants!r}; a goal model is built only "
            "from the user's own interactions"
        )
    if interaction.get("source") == "third_party_thread":
        raise ValueError(
            "refused third-party-only interaction: source is a third-party "
            "thread the user was not part of"
        )


@dataclass
class AgentGoal:
    """One goal another agent is pursuing, as the user observed it.

    ``label`` is a short plain label (for example ``"review_work"``).
    ``strength`` is 0..1; ``supporting`` / ``contradicting`` count
    the observations behind it; ``evidence`` is ``{"kind", "id"}``
    links; ``first_seen_day`` / ``last_seen_day`` are model days;
    ``status`` is ``active``, ``stale``, or ``dropped`` (the last
    two keep the goal as history).
    """

    label: str
    strength: float = GOAL_INITIAL_STRENGTH
    supporting: int = 0
    contradicting: int = 0
    evidence: list = field(default_factory=list)
    first_seen_day: int | None = None
    last_seen_day: int | None = None
    status: str = "active"

    def __post_init__(self) -> None:
        if not self.label:
            raise ValueError("goal label must be a non-empty string")
        if not 0.0 <= self.strength <= 1.0:
            raise ValueError(f"goal strength must be within 0..1, got {self.strength}")
        if self.status not in GOAL_STATUSES:
            raise ValueError(f"goal status must be one of {GOAL_STATUSES}, got {self.status!r}")
        if self.supporting < 0 or self.contradicting < 0:
            raise ValueError("goal observation counts must not be negative")
        self.evidence = _check_evidence(self.evidence)

    @property
    def is_active(self) -> bool:
        """True while the goal is live (status active)."""
        return self.status == "active"

    def confidence(self, now_day: int) -> float:
        """Confidence now: strength x evidence factor x staleness factor.

        A dropped goal scores 0.0: history is not a prediction.
        """
        if self.status == "dropped":
            return 0.0
        age = 0 if self.last_seen_day is None else max(0, now_day - self.last_seen_day)
        return self.strength * _evidence_factor(self.supporting) * _staleness_factor(age)

    def to_dict(self) -> dict:
        """JSON-safe dict."""
        return {
            "label": self.label,
            "strength": self.strength,
            "supporting": self.supporting,
            "contradicting": self.contradicting,
            "evidence": [dict(link) for link in self.evidence],
            "first_seen_day": self.first_seen_day,
            "last_seen_day": self.last_seen_day,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AgentGoal:
        """Rebuild one goal; raises ValueError on bad data."""
        return cls(
            label=str(data["label"]),
            strength=float(data["strength"]),
            supporting=int(data.get("supporting") or 0),
            contradicting=int(data.get("contradicting") or 0),
            evidence=[dict(link) for link in (data.get("evidence") or [])],
            first_seen_day=(
                int(data["first_seen_day"]) if data.get("first_seen_day") is not None else None
            ),
            last_seen_day=(
                int(data["last_seen_day"]) if data.get("last_seen_day") is not None else None
            ),
            status=str(data.get("status") or "active"),
        )


@dataclass(frozen=True)
class AgentPrediction:
    """One scored claim about what a contact will do next.

    ``kind`` is ``"reply"``, ``"channel"``, ``"next_action"``, or
    ``"agreement"``. ``label`` is the predicted thing:
    ``"will_reply"`` / ``"will_not_reply"``, a channel name, or a
    goal label. ``confidence`` is 0..1, a bookkeeping score, never
    a probability. ``outcome`` is None until scored, then True
    (it happened) or False; ``correct`` is True when the outcome
    matched the claim. A prediction never causes an action: it
    only informs one the user (or the planner) sees.
    """

    id: str
    contact_id: str
    kind: str
    label: str
    confidence: float
    evidence: tuple = ()
    basis: str = ""
    day: int = 0
    outcome: bool | None = None
    correct: bool | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("prediction id must be non-empty")
        if not self.contact_id:
            raise ValueError("prediction contact_id must be non-empty")
        if self.kind not in PREDICTION_KINDS:
            raise ValueError(
                f"prediction kind must be one of {PREDICTION_KINDS}, got {self.kind!r}"
            )
        if not self.label:
            raise ValueError("prediction label must be a non-empty string")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"prediction confidence must be within 0..1, got {self.confidence}")
        if self.outcome is not None and self.correct is None:
            raise ValueError("a scored prediction needs 'correct' set")
        # Tuple-ify evidence for a clean JSON round trip.
        object.__setattr__(self, "evidence", tuple(dict(link) for link in self.evidence))

    def score(self, actual: bool) -> AgentPrediction:
        """Score this prediction against what actually happened.

        ``actual`` is True when the predicted thing happened and
        False otherwise. For a ``"will_not_reply"`` prediction the
        claim is the negation, so it is correct when the contact
        did NOT reply. Returns a new (scored) prediction; the
        original is unchanged.
        """
        expected = actual if self.label != "will_not_reply" else not actual
        return AgentPrediction(
            id=self.id,
            contact_id=self.contact_id,
            kind=self.kind,
            label=self.label,
            confidence=self.confidence,
            evidence=self.evidence,
            basis=self.basis,
            day=self.day,
            outcome=bool(actual),
            correct=bool(expected),
        )

    def to_dict(self) -> dict:
        """JSON-safe dict."""
        return {
            "id": self.id,
            "contact_id": self.contact_id,
            "kind": self.kind,
            "label": self.label,
            "confidence": self.confidence,
            "evidence": [dict(link) for link in self.evidence],
            "basis": self.basis,
            "day": self.day,
            "outcome": self.outcome,
            "correct": self.correct,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AgentPrediction:
        """Rebuild one prediction; raises ValueError on bad data."""
        return cls(
            id=str(data["id"]),
            contact_id=str(data["contact_id"]),
            kind=str(data["kind"]),
            label=str(data["label"]),
            confidence=float(data["confidence"]),
            evidence=tuple(dict(link) for link in (data.get("evidence") or [])),
            basis=str(data.get("basis") or ""),
            day=int(data.get("day") or 0),
            outcome=data.get("outcome"),
            correct=data.get("correct"),
        )


class GoalInferrer(Protocol):
    """Seam for turning free text into goal labels.

    Implementations map one message text to the goal labels it
    shows (for example "please review my PR" -> ["review_work"]).
    The only implementation built here is
    :class:`KeywordGoalInferrer`. An LLM-backed inferrer is a
    documented seam only: it would implement this protocol; it is
    NOT built now, and no prediction depends on one existing.
    """

    def infer(self, text: str) -> list[str]:
        """Goal labels the text shows, strongest first, or []."""
        ...


#: Keyword -> goal label. Small on purpose: a keyword hit is weak
#: evidence (the label still starts at neutral strength 0.5), and
#: an unknown text infers nothing rather than guessing.
_KEYWORD_GOALS: tuple[tuple[str, str], ...] = (
    ("review", "review_work"),
    ("approve", "approve_request"),
    ("pay", "pay_bill"),
    ("meet", "meet_up"),
    ("call", "meet_up"),
    ("send", "send_document"),
    ("feedback", "give_feedback"),
    ("help", "help_with_task"),
    ("deliver", "deliver_on_time"),
    ("deadline", "deliver_on_time"),
    ("discount", "negotiate_price"),
    ("price", "negotiate_price"),
    ("confirm", "confirm_booking"),
)


class KeywordGoalInferrer:
    """Deterministic goal inferrer: keyword hits in the text.

    One keyword hit proposes one goal label; repeated keywords
    do not strengthen anything (strengthening happens in
    :meth:`AgentGoalModel.observe`, from repeated interactions,
    not repeated words). Unknown text infers nothing.
    """

    def infer(self, text: str) -> list[str]:
        """Goal labels the text shows, in table order, or []."""
        lowered = (text or "").lower()
        seen: list[str] = []
        for keyword, label in _KEYWORD_GOALS:
            if keyword in lowered and label not in seen:
                seen.append(label)
        return seen


@dataclass
class AgentGoalModel:
    """One contact's goal model: goals plus scored predictions.

    ``goals`` maps label -> :class:`AgentGoal`. The counts mirror
    the contact belief for standalone use; the ``WorldModel``
    read path prefers the contact belief for reply rate, delay,
    and channel and uses this model only for goals and
    predictions. ``predictions`` holds every prediction made,
    newest last, scored or pending.
    """

    contact_id: str
    goals: dict = field(default_factory=dict)
    observations: int = 0
    replies: int = 0
    reply_delays: list = field(default_factory=list)
    channels: dict = field(default_factory=dict)
    last_day: int | None = None
    predictions: list = field(default_factory=list)
    stats: dict = field(default_factory=lambda: {"predictions": 0, "correct": 0})

    def __post_init__(self) -> None:
        if not self.contact_id:
            raise ValueError("agent goal model contact_id must be a non-empty string")

    # -- observations --------------------------------------------------
    def observe(
        self,
        interaction: dict,
        *,
        inferrer: GoalInferrer | None = None,
    ) -> list[str]:
        """Fold one observed interaction into the model.

        ``interaction`` holds ``contact_id`` (required), ``day``,
        ``channel``, ``replied``, ``reply_delay_hours``, the goal
        label lists (``asked_for`` / ``agreed_to`` / ``declined``),
        ``text``, ``evidence``, ``user_involved``, ``participants``,
        ``source``. With no label lists but ``text``, the inferrer
        maps the text to labels, counted as asked for. Returns the
        goal labels touched. Raises ``ValueError`` for a
        third-party-only interaction or a mismatched contact id.
        """
        check_user_involved(interaction)
        contact_id = str(interaction.get("contact_id") or "")
        if contact_id != self.contact_id:
            raise ValueError(
                f"interaction contact {contact_id!r} does not match model {self.contact_id!r}"
            )
        day = int(interaction.get("day") or 0)
        evidence = _check_evidence(interaction.get("evidence"))
        asked = [str(label) for label in (interaction.get("asked_for") or []) if label]
        agreed = [str(label) for label in (interaction.get("agreed_to") or []) if label]
        declined = [str(label) for label in (interaction.get("declined") or []) if label]
        if not asked and not agreed and not declined and interaction.get("text"):
            asked = (inferrer or KeywordGoalInferrer()).infer(str(interaction["text"]))
        touched: list[str] = []
        for label in asked + agreed:
            self._support_goal(label, day, evidence)
            if label not in touched:
                touched.append(label)
        for label in declined:
            self._contradict_goal(label, day, evidence)
            if label not in touched:
                touched.append(label)
        self.observations += 1
        if interaction.get("replied"):
            self.replies += 1
            if interaction.get("reply_delay_hours") is not None:
                self.reply_delays.append(float(interaction["reply_delay_hours"]))
                self.reply_delays = self.reply_delays[-5:]
        if interaction.get("channel") is not None:
            channel = str(interaction["channel"])
            self.channels[channel] = self.channels.get(channel, 0) + 1
        self.last_day = day
        return touched

    def _support_goal(self, label: str, day: int, evidence: list[dict]) -> None:
        goal = self.goals.get(label)
        if goal is None or goal.status == "dropped":
            goal = AgentGoal(
                label=label,
                strength=GOAL_INITIAL_STRENGTH,
                supporting=1,
                evidence=list(evidence),
                first_seen_day=day,
                last_seen_day=day,
            )
            self.goals[label] = goal
            return
        goal.strength = min(1.0, goal.strength + CONFIRM_STEP)
        goal.supporting += 1
        goal.last_seen_day = day
        goal.evidence = _with_evidence(goal.evidence, evidence)
        if goal.status == "stale":
            goal.status = "active"

    def _contradict_goal(self, label: str, day: int, evidence: list[dict]) -> None:
        goal = self.goals.get(label)
        if goal is None:
            # Contradiction never creates a goal (as in K4).
            return
        goal.strength = max(0.0, goal.strength - CONTRADICT_STEP)
        goal.contradicting += 1
        goal.last_seen_day = day
        goal.evidence = _with_evidence(goal.evidence, evidence)
        if goal.strength < DROPPED_FLOOR:
            goal.status = "dropped"

    # -- read path -----------------------------------------------------
    @property
    def reply_rate(self) -> float | None:
        """Observed reply rate, or None before any interaction."""
        if not self.observations:
            return None
        return self.replies / self.observations

    @property
    def mean_reply_delay_hours(self) -> float | None:
        """Mean observed reply delay, or None when never replied."""
        if not self.reply_delays:
            return None
        return sum(self.reply_delays) / len(self.reply_delays)

    @property
    def preferred_channel(self) -> str | None:
        """Most used channel, or None before any interaction."""
        if not self.channels:
            return None
        return max(self.channels.items(), key=lambda item: item[1])[0]

    @property
    def active_goals(self) -> list[AgentGoal]:
        """Live goals, strongest first (ties: label, deterministic)."""
        goals = [goal for goal in self.goals.values() if goal.is_active]
        goals.sort(key=lambda goal: (-goal.strength, goal.label))
        return goals

    @property
    def accuracy(self) -> float:
        """Scored-prediction accuracy so far (0.0 when none scored)."""
        total = self.stats["predictions"]
        return self.stats["correct"] / total if total else 0.0

    def predict(
        self,
        proposed: dict | None = None,
        *,
        day: int | None = None,
        reply_rate: float | None = None,
        mean_delay: float | None = None,
        preferred_channel: str | None = None,
        causal_expectations: list[dict] | None = None,
    ) -> list[AgentPrediction]:
        """What will this contact do next? Scored claims, never certainties.

        Returns [] with no history (fail-open, never invented).
        Otherwise one prediction per claim the evidence supports:
        reply (from the reply rate), preferred channel, next action
        (strongest active goal), and agreement when ``proposed``
        names a goal the contact is known to pursue. The contact
        belief's values (``reply_rate`` / ``mean_delay`` /
        ``preferred_channel``) win over this model's own counts
        when given. Causal expectations (K4) attach as evidence
        to the reply prediction, never as a prediction of their
        own. Predicting never mutates the model; predictions are
        recorded (pending, unscored) so they can be scored later.
        """
        now_day = self.last_day if day is None else int(day)
        if now_day is None:
            now_day = 0
        if not self.observations:
            return []
        rate = self.reply_rate if reply_rate is None else reply_rate
        delay = self.mean_reply_delay_hours if mean_delay is None else mean_delay
        channel = self.preferred_channel if preferred_channel is None else preferred_channel
        factor = _evidence_factor(self.observations)
        age = 0 if self.last_day is None else max(0, now_day - self.last_day)
        stale = _staleness_factor(age)
        predictions: list[AgentPrediction] = []
        causal_links = [
            {"kind": "causal", "id": str(item.get("effect") or item.get("id") or "")}
            for item in (causal_expectations or [])
            if item.get("effect") or item.get("id")
        ]
        fallback_evidence = [
            {"kind": "interaction", "id": f"{self.contact_id}:observations={self.observations}"}
        ]
        if rate is not None:
            label = "will_reply" if rate >= 0.5 else "will_not_reply"
            basis_rate = rate if label == "will_reply" else 1.0 - rate
            predictions.append(
                self._prediction(
                    "reply",
                    label,
                    basis_rate * factor * stale,
                    fallback_evidence + causal_links,
                    f"reply rate {rate:.2f} over {self.observations} observed interactions"
                    + (f"; mean delay {delay:.1f}h" if delay is not None else ""),
                    now_day,
                )
            )
        if channel is not None:
            predictions.append(
                self._prediction(
                    "channel",
                    channel,
                    factor * stale,
                    fallback_evidence,
                    f"preferred channel over {self.observations} observed interactions",
                    now_day,
                )
            )
        top = self.active_goals[0] if self.active_goals else None
        if top is not None:
            predictions.append(
                self._prediction(
                    "next_action",
                    top.label,
                    top.confidence(now_day),
                    [dict(link) for link in top.evidence] or fallback_evidence,
                    f"strongest active goal {top.label!r} (strength {top.strength:.2f}, "
                    f"{top.supporting} supporting observations)",
                    now_day,
                )
            )
        if proposed is not None and proposed.get("goal"):
            goal_label = str(proposed["goal"])
            goal = self.goals.get(goal_label)
            if goal is not None and goal.is_active:
                predictions.append(
                    self._prediction(
                        "agreement",
                        goal_label,
                        goal.confidence(now_day),
                        [dict(link) for link in goal.evidence] or fallback_evidence,
                        f"goal {goal_label!r} strength {goal.strength:.2f}, "
                        f"{goal.supporting} supporting observations",
                        now_day,
                    )
                )
        self.predictions.extend(predictions)
        self.predictions = self.predictions[-PREDICTION_CAP:]
        return predictions

    def _prediction(
        self, kind: str, label: str, confidence: float, evidence: list, basis: str, day: int
    ) -> AgentPrediction:
        """One new pending prediction (confidence rounded to 4 decimals)."""
        return AgentPrediction(
            id=uuid.uuid4().hex,
            contact_id=self.contact_id,
            kind=kind,
            label=label,
            confidence=round(confidence, 4),
            evidence=tuple(evidence),
            basis=basis,
            day=day,
        )

    def score_prediction(self, prediction_id: str, actual: bool) -> AgentPrediction | None:
        """Score one pending prediction against what actually happened.

        Returns the scored prediction, or None when the id is
        unknown or already scored. Scoring updates ``stats`` and,
        for an agreement prediction, the goal itself (+0.1 on
        agreement, -0.2 on refusal, as for a fresh observation).
        """
        for index, prediction in enumerate(self.predictions):
            if prediction.id != str(prediction_id):
                continue
            if prediction.outcome is not None:
                return None
            scored = prediction.score(bool(actual))
            self.predictions[index] = scored
            self.stats["predictions"] += 1
            if scored.correct:
                self.stats["correct"] += 1
            if prediction.kind == "agreement":
                goal = self.goals.get(prediction.label)
                if goal is not None and goal.is_active:
                    day = self.last_day if self.last_day is not None else prediction.day
                    if actual:
                        goal.strength = min(1.0, goal.strength + CONFIRM_STEP)
                        goal.supporting += 1
                    else:
                        goal.strength = max(0.0, goal.strength - CONTRADICT_STEP)
                        goal.contradicting += 1
                        if goal.strength < DROPPED_FLOOR:
                            goal.status = "dropped"
                    goal.last_seen_day = day
            return scored
        return None

    def decay(self, now_day: int) -> list[str]:
        """Decay goals untouched for too long; returns what went stale.

        A goal whose last observation is more than
        ``STALE_AFTER_DAYS`` ago halves its strength and becomes
        ``stale``. A fresh observation revives it to active.
        Dropped goals are untouched.
        """
        stale: list[str] = []
        for goal in self.goals.values():
            if goal.status != "active" or goal.last_seen_day is None:
                continue
            if now_day - goal.last_seen_day <= STALE_AFTER_DAYS:
                continue
            goal.strength = round(goal.strength * 0.5, 4)
            goal.status = "stale"
            stale.append(goal.label)
        return stale

    # -- serialization -------------------------------------------------
    def to_dict(self) -> dict:
        """JSON-safe dict."""
        return {
            "contact_id": self.contact_id,
            "goals": {label: goal.to_dict() for label, goal in self.goals.items()},
            "observations": self.observations,
            "replies": self.replies,
            "reply_delays": list(self.reply_delays),
            "channels": dict(self.channels),
            "last_day": self.last_day,
            "predictions": [prediction.to_dict() for prediction in self.predictions],
            "stats": dict(self.stats),
        }

    @classmethod
    def from_dict(cls, data: dict) -> AgentGoalModel:
        """Rebuild one model; raises ValueError on bad data."""
        return cls(
            contact_id=str(data["contact_id"]),
            goals={
                str(label): AgentGoal.from_dict(goal)
                for label, goal in (data.get("goals") or {}).items()
            },
            observations=int(data.get("observations") or 0),
            replies=int(data.get("replies") or 0),
            reply_delays=[float(delay) for delay in (data.get("reply_delays") or [])],
            channels={str(key): int(value) for key, value in (data.get("channels") or {}).items()},
            last_day=(int(data["last_day"]) if data.get("last_day") is not None else None),
            predictions=[AgentPrediction.from_dict(row) for row in (data.get("predictions") or [])],
            stats={
                "predictions": int((data.get("stats") or {}).get("predictions") or 0),
                "correct": int((data.get("stats") or {}).get("correct") or 0),
            },
        )


class AgentGoalStore:
    """In-memory store of per-contact goal models.

    One model per contact, keyed by contact id. The in-memory
    counterpart of the Postgres table ``agent_goal_models``
    (``puspa.world.store``): same semantics, same serialization.
    """

    def __init__(self) -> None:
        self._models: dict[str, AgentGoalModel] = {}

    def get(self, contact_id: str) -> AgentGoalModel | None:
        """One contact's model, or None when nothing was observed."""
        return self._models.get(str(contact_id))

    def observe(self, interaction: dict, *, inferrer: GoalInferrer | None = None) -> AgentGoalModel:
        """Fold one interaction into its contact's model (creating it).

        Raises ``ValueError`` for a third-party-only interaction
        (see :func:`check_user_involved`): nothing is created.
        """
        check_user_involved(interaction)
        contact_id = str(interaction.get("contact_id") or "")
        if not contact_id:
            raise ValueError("interaction needs a non-empty contact_id")
        model = self._models.setdefault(contact_id, AgentGoalModel(contact_id=contact_id))
        model.observe(interaction, inferrer=inferrer)
        return model

    def predict(
        self,
        contact_id: str,
        proposed: dict | None = None,
        *,
        day: int | None = None,
        reply_rate: float | None = None,
        mean_delay: float | None = None,
        preferred_channel: str | None = None,
        causal_expectations: list[dict] | None = None,
    ) -> list[AgentPrediction]:
        """Predictions for one contact, or [] when unknown (fail-open)."""
        model = self._models.get(str(contact_id))
        if model is None:
            return []
        return model.predict(
            proposed,
            day=day,
            reply_rate=reply_rate,
            mean_delay=mean_delay,
            preferred_channel=preferred_channel,
            causal_expectations=causal_expectations,
        )

    def score_prediction(
        self, contact_id: str, prediction_id: str, actual: bool
    ) -> AgentPrediction | None:
        """Score one prediction; None when the contact or id is unknown."""
        model = self._models.get(str(contact_id))
        if model is None:
            return None
        return model.score_prediction(prediction_id, actual)

    def decay_all(self, now_day: int) -> dict[str, list[str]]:
        """Decay every model; returns {contact_id: [stale goals]}."""
        decayed = {}
        for contact_id, model in self._models.items():
            stale = model.decay(now_day)
            if stale:
                decayed[contact_id] = stale
        return decayed

    def all_models(self) -> list[AgentGoalModel]:
        """Every model in the store, sorted by contact id."""
        return [self._models[key] for key in sorted(self._models)]

    def __len__(self) -> int:
        return len(self._models)

    def to_dict(self) -> dict:
        """Whole store as JSON-safe data: ``{"models": [...]}``."""
        return {
            "models": [
                model.to_dict()
                for model in sorted(self._models.values(), key=lambda m: m.contact_id)
            ]
        }

    @classmethod
    def from_dict(cls, data: dict) -> AgentGoalStore:
        """Rebuild a store; raises ValueError on bad data."""
        store = cls()
        for row in data.get("models") or []:
            model = AgentGoalModel.from_dict(row)
            store._models[model.contact_id] = model
        return store


class AgentModelMixin:
    """Other-agent read path for a model that owns goal models.

    The per-contact models live in ``self._agent_models`` (built
    on the contact beliefs, not a parallel store), created here
    in ``__init__``: ``WorldModel.__init__`` must call
    ``super().__init__()`` so this runs (``CausalExpectationMixin``
    defines no ``__init__``, so the chain reaches this one). The
    causal ledger is the session attachment from
    :class:`CausalExpectationMixin`. It lives here, not in
    ``puspa.world.model``, because that file's size baseline
    forbids growth. The turn loop and the planner can both query
    it: the planner does (goal ``"message_contact"`` in
    ``puspa.world.planner``), and the turn loop can call the same
    method on the session model.
    """

    #: Per-contact goal models, owned by the model (see above).
    _agent_models: dict[str, AgentGoalModel]

    #: Third-party-only interactions refused at ingest (privacy
    #: boundary, kosmos-design.md section J): runtime only, not
    #: part of state().
    agent_refusals: list[dict]

    def __init__(self) -> None:
        """Create the per-contact state. Runs via super().__init__()."""
        self._agent_models = {}
        self.agent_refusals = []
        super().__init__()

    def feed_task_result(self, result: dict, day: int) -> None:
        """Fold one task result's interaction into its goal model.

        Only when the result carries something goal-bearing (asked
        for, agreed to, declined, or free text). A third-party-only
        interaction is refused, never modelled: the refusal is
        recorded in ``agent_refusals`` and the contact belief is
        untouched by the refusal (it only ever counted the
        interaction itself).
        """
        if not any(
            result.get(key) is not None for key in ("asked_for", "agreed_to", "declined", "text")
        ):
            return
        interaction = {"contact_id": str(result["contact_id"]), "day": day}
        for key in (
            "channel",
            "replied",
            "reply_delay_hours",
            "asked_for",
            "agreed_to",
            "declined",
            "text",
            "evidence",
            "participants",
            "source",
        ):
            interaction[key] = result.get(key)
        interaction["user_involved"] = result.get("user_involved", True)
        try:
            self.observe_agent_interaction(interaction)
        except ValueError as exc:
            self.agent_refusals.append(
                {"contact_id": str(result["contact_id"]), "reason": str(exc), "day": day}
            )

    def agent_model(self, contact_id: str) -> AgentGoalModel | None:
        """One contact's goal model, or None when nothing was observed."""
        return self._agent_models.get(str(contact_id))

    def observe_agent_interaction(
        self, interaction: dict, *, inferrer: GoalInferrer | None = None
    ) -> AgentGoalModel:
        """Fold one observed interaction into its contact's goal model.

        Creates the model on first sight. Raises ``ValueError``
        for a third-party-only interaction: nothing is created or
        changed. The contact belief is NOT updated here; that
        stays the job of ``ingest_task_result``.
        """
        check_user_involved(interaction)
        contact_id = str(interaction.get("contact_id") or "")
        if not contact_id:
            raise ValueError("interaction needs a non-empty contact_id")
        model = self._agent_models.setdefault(contact_id, AgentGoalModel(contact_id=contact_id))
        model.observe(interaction, inferrer=inferrer)
        return model

    def predict_agent(self, contact_id: str, proposed: dict | None = None) -> dict:
        """What will contact X do next / how will X respond to Y?

        ``proposed`` is the ask being considered, for example
        ``{"goal": "review_work"}``. Returns ``contact_id``,
        ``known``, ``predictions`` (scored-claim dicts; empty
        when the contact was never observed: fail-open, never
        invented), ``causal_expectations`` (active K4 expectations
        for ``"contact:<contact_id>"``), plus reply rate, delay,
        and channel from the contact belief when one exists.
        Predicting never mutates the model.
        """
        contact_id = str(contact_id)
        model = self._agent_models.get(contact_id)
        belief = self._contact_beliefs.get(contact_id)  # type: ignore[attr-defined]
        reply_rate = belief.reply_rate if belief is not None else None
        channel = belief.preferred_channel if belief is not None else None
        delay = belief.mean_reply_delay_hours if belief is not None else None
        causal_expectations: list[dict] = []
        try:
            causal_expectations = self.expect(f"contact:{contact_id}")  # type: ignore[attr-defined]
        except Exception:
            causal_expectations = []
        if model is None:
            return {
                "contact_id": contact_id,
                "known": False,
                "predictions": [],
                "causal_expectations": causal_expectations,
                "reply_likely": None,
                "reply_rate": reply_rate,
                "preferred_channel": channel,
                "mean_reply_delay_hours": delay,
            }
        predictions = model.predict(
            proposed,
            day=self.day,  # type: ignore[attr-defined]
            reply_rate=reply_rate,
            mean_delay=delay,
            preferred_channel=channel,
            causal_expectations=causal_expectations,
        )
        reply_prediction = next((item for item in predictions if item.kind == "reply"), None)
        return {
            "contact_id": contact_id,
            "known": True,
            "predictions": [prediction.to_dict() for prediction in predictions],
            "causal_expectations": causal_expectations,
            "reply_likely": (
                reply_prediction.label == "will_reply" if reply_prediction is not None else None
            ),
            "reply_rate": reply_rate if reply_rate is not None else model.reply_rate,
            "preferred_channel": channel if channel is not None else model.preferred_channel,
            "mean_reply_delay_hours": (
                delay if delay is not None else model.mean_reply_delay_hours
            ),
        }

    def score_agent_prediction(
        self, contact_id: str, prediction_id: str, actual: bool
    ) -> dict | None:
        """Score one prediction once the outcome is known.

        Returns the scored prediction as a dict, or None when the
        contact or id is unknown (or already scored). Scoring
        updates the accuracy counters and, for an agreement
        prediction, the goal itself.
        """
        model = self._agent_models.get(str(contact_id))
        if model is None:
            return None
        scored = model.score_prediction(prediction_id, bool(actual))
        return scored.to_dict() if scored is not None else None

    def decay_stale_agent_models(self, now_day: int | None = None) -> dict[str, list[str]]:
        """Decay stale goals in every contact model.

        Mirrors ``WorldModel.decay_stale_beliefs``. Returns
        ``{contact_id: [stale goal labels]}`` for the models that
        decayed anything.
        """
        day = self.day if now_day is None else int(now_day)  # type: ignore[attr-defined]
        decayed: dict[str, list[str]] = {}
        for contact_id, model in self._agent_models.items():
            stale = model.decay(day)
            if stale:
                decayed[contact_id] = stale
        return decayed
