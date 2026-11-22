"""Global workspace (Kosmos K18): a bus modules compete on.

Philosophy: Baars' global workspace theory. Consciousness is a
stage; the loudest content gets the stage, and what is on the
stage drives the next action. Here the stage is a bus: each
module posts one scored candidate, the top score broadcasts,
and the broadcast is what the agent does next. Everything else
stays out of the way.

The shape is measured, not guessed. Before this module was
built, the K18 gain experiment (``hidden_files/k18-measurement``,
12 realistic multi-signal scenarios, ground truth fixed before
any arm ran) compared three ways of picking the next action:

- A, fixed order, first relevant candidate wins (the stand-in
  for "today", where modules never compete): 7/12.
- B, score competition only, top score broadcasts: 8/12.
- C, score competition plus a veto (a broadcast that violates
  another module's hard constraint is skipped and the next
  candidate broadcasts; all vetoed means telling the user why):
  11/12, losing only S6, where a flashy sale outscores the
  routine bill the user asked to pay first.

Score-only gains +1, below the pre-registered bar of +2, so
score alone does not justify the bus. The veto is what carries
the gain (+4 over the baseline), so the veto is part of this
module, not an optional extra: :meth:`WorkspaceBus.broadcast`
always consults the registered constraints, and a round where
every candidate is vetoed ends as ``"notify_blocked"`` — the
agent tells the user why instead of acting against a hard rule
or moving money silently.

Scoring: ``score = 0.5*urgency + 0.3*relevance + 0.2*confidence``.
The weights are the ones the experiment fixed before running;
they are documented as measured, not tuned. A tie is broken
deterministically: the candidate posted first wins. There is no
model, no network, and no randomness anywhere in this module.

Ledger: every broadcast is recorded when the bus is built with
a recorder (see :func:`ledger_recorder`, which appends one
``task_completed`` event with ``job == "workspace_broadcast"``
through the K4 ledger). A bus built without a recorder still
broadcasts, and the broadcast record it returns is the audit
trail the caller can carry. The morning briefing
(:func:`puspa.morning.summary.select_morning_focus`) is the
first live caller: it posts one candidate per briefing section
and lets the bus pick the day's focus.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

__all__ = [
    "ASK_USER_ACTION",
    "BLOCKED_ACTION",
    "CONFIDENCE_WEIGHT",
    "RELEVANCE_WEIGHT",
    "URGENCY_WEIGHT",
    "Broadcast",
    "Candidate",
    "WorkspaceBus",
    "ledger_recorder",
]

#: Score weights, fixed by the K18 measurement before it ran
#: (see the module docstring): urgency counts most, then
#: relevance, then confidence. Measured, not tuned.
URGENCY_WEIGHT = 0.5
RELEVANCE_WEIGHT = 0.3
CONFIDENCE_WEIGHT = 0.2

#: Action when the bus is empty: no module proposed anything,
#: so the agent asks the user instead of inventing an action.
ASK_USER_ACTION = "ask_user"
#: Action when every posted candidate was vetoed: the agent
#: tells the user why it is blocked instead of acting against
#: a hard rule.
BLOCKED_ACTION = "notify_blocked"


def _check_unit(name: str, value: float) -> float:
    """Validate a 0..1 value; ValueError with the name and value otherwise."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a number within 0..1, got {value!r}")
    if not 0.0 <= float(value) <= 1.0:
        raise ValueError(f"{name} must be within 0..1, got {value}")
    return float(value)


def _check_text(name: str, value: str) -> str:
    """Validate a non-empty string; ValueError otherwise."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string, got {value!r}")
    return value.strip()


@dataclass(frozen=True)
class Candidate:
    """One module's bid for the workspace: what it wants done,
    and how strongly, on the three measured axes.

    ``module`` names the bidding module (for example
    ``"bills"``), ``action`` is the action key the broadcast
    would drive (for example ``"pay_now"``), and ``content`` is
    the human-readable content of the bid (what the user would
    see). ``urgency``, ``relevance`` and ``confidence`` are all
    0..1. :attr:`score` combines them with the measured weights.
    """

    module: str
    action: str
    content: str
    urgency: float
    relevance: float
    confidence: float

    def __post_init__(self) -> None:
        """Validate the fields; a malformed bid is rejected
        before it can compete."""
        _check_text("module", self.module)
        _check_text("action", self.action)
        if not isinstance(self.content, str):
            raise ValueError(f"content must be a string, got {self.content!r}")
        _check_unit("urgency", self.urgency)
        _check_unit("relevance", self.relevance)
        _check_unit("confidence", self.confidence)

    @property
    def score(self) -> float:
        """The bid's score: 0.5*urgency + 0.3*relevance +
        0.2*confidence, with the measured weights."""
        return (
            URGENCY_WEIGHT * float(self.urgency)
            + RELEVANCE_WEIGHT * float(self.relevance)
            + CONFIDENCE_WEIGHT * float(self.confidence)
        )

    def to_dict(self) -> dict:
        """JSON-safe dict of this bid (ledger/audit shape)."""
        return {
            "module": self.module,
            "action": self.action,
            "content": self.content,
            "urgency": self.urgency,
            "relevance": self.relevance,
            "confidence": self.confidence,
            "score": round(self.score, 3),
        }


@dataclass(frozen=True)
class Broadcast:
    """The outcome of one broadcast round.

    ``winner`` is the candidate that took the stage, or None
    when nothing broadcast. ``action`` is what the agent does
    next: the winner's action, ``"ask_user"`` when the bus was
    empty, or ``"notify_blocked"`` when every candidate was
    vetoed. ``scores`` maps each bidding module to its best
    score; ``blocked`` lists every vetoed candidate with the
    module that vetoed it and why, so the blocked outcome can
    tell the user the reason. ``ledger_event_id`` is the id of
    the ledger event this broadcast was recorded as, or None
    when the bus has no recorder.
    """

    winner: Candidate | None
    action: str
    scores: dict[str, float] = field(default_factory=dict)
    blocked: list[dict] = field(default_factory=list)
    ledger_event_id: str | None = None

    @property
    def is_blocked(self) -> bool:
        """True when every candidate was vetoed."""
        return self.action == BLOCKED_ACTION

    def to_dict(self) -> dict:
        """JSON-safe dict of this broadcast (ledger/audit shape)."""
        return {
            "winner": None if self.winner is None else self.winner.to_dict(),
            "action": self.action,
            "scores": {module: round(score, 3) for module, score in self.scores.items()},
            "blocked": [dict(entry) for entry in self.blocked],
            "ledger_event_id": self.ledger_event_id,
        }


class WorkspaceBus:
    """The workspace bus: modules post, the top score
    broadcasts, vetoes skip, the broadcast is recorded.

    One round is one :meth:`post` per bidding module followed
    by one :meth:`broadcast`; the broadcast consumes the round,
    so a second broadcast without new posts finds an empty bus.
    Constraints are hard rules owned by a module (see
    :meth:`register_constraint`): a candidate that violates any
    registered constraint never broadcasts, however high it
    scores. ``record`` is an optional recorder callable taking
    the broadcast record dict and returning the ledger event id
    (see :func:`ledger_recorder`); when given, every broadcast
    is recorded through it.
    """

    def __init__(self, *, record: Callable[[dict], str | None] | None = None) -> None:
        if record is not None and not callable(record):
            raise ValueError(f"record must be callable, got {record!r}")
        self._record = record
        self._posted: list[tuple[int, Candidate]] = []
        self._constraints: list[tuple[str, Callable[[Candidate], bool], str]] = []
        self._sequence = 0

    def __len__(self) -> int:
        """How many candidates are posted in the current round."""
        return len(self._posted)

    def candidates(self) -> list[Candidate]:
        """The posted candidates, in posting order."""
        return [candidate for _, candidate in self._posted]

    def post(self, candidate: Candidate) -> Candidate:
        """Post one bid for the current round.

        The posting order is the tie-break: among equal scores
        the candidate posted first wins. A non-candidate raises
        TypeError before anything is recorded.
        """
        if not isinstance(candidate, Candidate):
            raise TypeError(f"post expects a Candidate, got {candidate!r}")
        self._sequence += 1
        self._posted.append((self._sequence, candidate))
        return candidate

    def register_constraint(
        self,
        module: str,
        predicate: Callable[[Candidate], bool],
        reason: str = "",
    ) -> None:
        """Register one module's hard constraint.

        ``predicate`` takes a candidate and returns True when
        the candidate violates the module's hard rule — that
        candidate is then skipped at broadcast, however high it
        scores. ``reason`` is the human-readable why, carried
        into the blocked list so the blocked outcome can tell
        the user. Constraints are consulted in registration
        order; the first veto decides.
        """
        _check_text("module", module)
        if not callable(predicate):
            raise ValueError(f"constraint predicate must be callable, got {predicate!r}")
        if not isinstance(reason, str):
            raise ValueError(f"constraint reason must be a string, got {reason!r}")
        self._constraints.append((module.strip(), predicate, reason))

    def clear(self) -> None:
        """Drop the posted candidates without broadcasting."""
        self._posted.clear()

    def broadcast(self) -> Broadcast:
        """Run one broadcast round and return its outcome.

        The highest score wins; ties go to the candidate posted
        first. A winner that violates a registered constraint is
        skipped and the next-highest candidate broadcasts; when
        every candidate is vetoed the action is
        ``"notify_blocked"`` with the vetoes listed, and when
        nothing was posted the action is ``"ask_user"``. The
        broadcast is recorded through the recorder when one was
        given, and the returned outcome carries its ledger event
        id. The round is consumed: the posted candidates are
        cleared whether or not anything broadcast.
        """
        if not self._posted:
            result = Broadcast(winner=None, action=ASK_USER_ACTION)
            self._posted.clear()
            return self._record_broadcast(result)
        ordered = sorted(self._posted, key=lambda item: (-item[1].score, item[0]))
        scores: dict[str, float] = {}
        for _, candidate in ordered:
            scores[candidate.module] = max(scores.get(candidate.module, 0.0), candidate.score)
        blocked: list[dict] = []
        winner: Candidate | None = None
        for _, candidate in ordered:
            veto: tuple[str, str] | None = None
            for module, predicate, reason in self._constraints:
                if predicate(candidate):
                    veto = (module, reason)
                    break
            if veto is None:
                winner = candidate
                break
            blocked.append(
                {
                    "module": candidate.module,
                    "action": candidate.action,
                    "blocked_by": veto[0],
                    "reason": veto[1],
                }
            )
        action = winner.action if winner is not None else BLOCKED_ACTION
        result = Broadcast(winner=winner, action=action, scores=scores, blocked=blocked)
        self._posted.clear()
        return self._record_broadcast(result)

    def _record_broadcast(self, result: Broadcast) -> Broadcast:
        """Record the broadcast through the recorder, if any.

        The recorder receives the broadcast record (without a
        ledger event id) and returns the event id. A bus with no
        recorder returns the outcome unchanged.
        """
        if self._record is None:
            return result
        record = result.to_dict()
        del record["ledger_event_id"]
        event_id = self._record(record)
        if event_id is None:
            return result
        return Broadcast(
            winner=result.winner,
            action=result.action,
            scores=dict(result.scores),
            blocked=[dict(entry) for entry in result.blocked],
            ledger_event_id=str(event_id),
        )


def ledger_recorder(ledger, *, task_id: str, agent_id: str) -> Callable[[dict], str | None]:
    """Build a bus recorder that appends to the K4 ledger.

    ``ledger`` is any object with an ``append_event`` method
    taking the K4 ledger's keyword arguments (the real
    :class:`puspa.ledger.ledger.Ledger`, or a test double). Each
    broadcast becomes one ``task_completed`` event with
    ``job == "workspace_broadcast"`` under ``task_id`` from
    ``agent_id``. The returned callable takes the broadcast
    record dict and returns the new event's id as a string.
    """
    _check_text("task_id", task_id)
    _check_text("agent_id", agent_id)
    append = getattr(ledger, "append_event", None)
    if not callable(append):
        raise ValueError(f"ledger must have an append_event method, got {ledger!r}")
    run_task_id = task_id.strip()
    run_agent_id = agent_id.strip()

    def record(broadcast_record: dict) -> str | None:
        event = append(
            task_id=run_task_id,
            agent_id=run_agent_id,
            event_type="task_completed",
            content={"job": "workspace_broadcast", **broadcast_record},
        )
        return str(event["id"])

    return record
