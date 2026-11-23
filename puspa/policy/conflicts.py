"""Anti-manipulation design rule (Kosmos K13).

A system that understands the user can be very good at
manipulating them. The rule, from ``docs/kosmos-design.md``
section J, is: optimise for the user's reflective goals, not
engagement or dependence. When interests collide, show it, do
not decide silently.

This module is the enforcement of that rule, not just the
intent. It has three parts:

1. :func:`detect_conflict` — a deterministic check on one
   proposed action. It compares what the action gives the user
   (alignment with their reflective goal, 0..1) against what it
   gives somebody else (another party's benefit, 0..1) and
   against how much it keeps the user engaged or dependent.
   The check never invents the user's goal: the caller supplies
   the reflective goal at stake (from the K1 intent
   distribution's values level, or from the profile), and the
   numbers are the caller's assessed scores, stated in the
   record.
2. :class:`ConflictGate` — the enforcement point. A conflicted
   action may not run until the user has chosen what to do
   about it. :meth:`ConflictGate.check` returns ``"surface"``
   for a conflicted action, and the caller must show the
   conflict to the user instead of running the action.
   :meth:`ConflictGate.require_resolved` raises
   :class:`UnresolvedConflictError` for any caller that tries
   to run a conflicted action while its record is still
   ``"surfaced"``. A recorded choice is the only way through:
   ``"proceed"`` (run it), ``"alternative"`` (run something
   else instead), or ``"decline"`` (do not run it). A declined
   or alternative-resolved conflict does not silently become
   runnable later: proposing the same action again surfaces a
   fresh conflict, so the user is asked again instead of the
   system deciding the earlier answer no longer counts.
3. :class:`ConflictStore` — the audit log. Every conflict is a
   record of what conflicts, whose interest is on each side,
   what was surfaced to the user, and what the user chose.
   Records are never deleted by the agent and never edited in
   place: resolving one only sets its status, choice, and
   resolution time. The store is in-memory by default (the
   same honest limit as the K10 budget and the K11 dial: a
   restart loses records that were never persisted). With a
   ``dsn`` it also writes every record to Postgres table
   ``conflict_records`` (migration 0019_conflicts),
   so the audit survives restarts; see
   :func:`create_schema`, :func:`load_conflicts`, and
   :func:`delete_conflicts`.

Ledger wiring (G12): no new ledger event type is added — the
closed set of 31 revision-7 event types stays closed. When a
``log(event_type, content)`` callable is given to the gate,
surfacing a conflict writes ``"approval_requested"`` and the
user's choice writes ``"approval_granted"`` (proceed) or
``"approval_rejected"`` (alternative or decline, with the
choice named in the content), because a surfaced conflict IS
an approval question through the existing G4 mechanism, not a
second approval system.

Turn-loop wiring: :class:`puspa.turns.loop.TurnLoop` takes an
optional ``conflict_gate`` and consults it, through
:meth:`ConflictGate.check_tool`, before dispatching a registry
tool call — after the intent advisor (K1) and the autonomy
dial (K11). The gate runs after the dial on purpose: even at
``do_silently`` a conflicted action is surfaced, never run
silently. The dial never overrides a conflict. A tool with no
:class:`ConflictSpec` fails open (the gate defers to the
existing gates), so wiring a gate in cannot newly block a
tool nobody described as conflict-prone.

Pure Python except for the optional Postgres persistence: no
network, no LLM call. The detector's thresholds are
bookkeeping choices, stated here and in
``docs/anti-manipulation.md`` — not calibrated probabilities.
"""

from __future__ import annotations

import contextlib
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from puspa.migrations import apply_migrations

__all__ = [
    "ATTENTION_PROACTIVE_MIN_OTHER_BENEFIT",
    "ATTENTION_USER_BENEFIT_MAX",
    "BENEFICIARIES",
    "CONFLICT_KINDS",
    "CONFLICT_STATUSES",
    "DEFAULT_DSN",
    "ENGAGEMENT_GAIN_THRESHOLD",
    "ENGAGEMENT_USER_BENEFIT_MAX",
    "HIGH_SEVERITY_GAP",
    "KIND_FOR_BENEFICIARY",
    "RETENTION_ENGAGEMENT_MIN",
    "SEVERITIES",
    "STATUS_RESOLVED_ALTERNATIVE",
    "STATUS_RESOLVED_DECLINED",
    "STATUS_RESOLVED_PROCEED",
    "STATUS_SURFACED",
    "USER_CHOICES",
    "Conflict",
    "ConflictDecision",
    "ConflictGate",
    "ConflictSpec",
    "ConflictStore",
    "ProposedAction",
    "UnresolvedConflictError",
    "conflict_surface_payload",
    "create_schema",
    "delete_conflicts",
    "detect_conflict",
    "load_conflicts",
    "surface_text",
]

#: The closed set of conflict kinds. A kind not listed here is
#: rejected by :class:`ProposedAction` / :class:`Conflict` — a
#: caller cannot invent a kind to dodge the gate.
CONFLICT_KINDS = (
    "engagement_vs_goal",
    "seller_vs_user",
    "provider_vs_user",
    "retention_vs_independence",
    "attention_vs_benefit",
)

#: Who can gain from an action besides the user. ``"user"``
#: means nobody else gains: the action serves the user alone.
BENEFICIARIES = ("user", "seller", "provider", "system", "other_person")

#: Beneficiary -> conflict kind, for the parties whose gain can
#: outweigh the user's. ``"system"`` and ``"other_person"`` have
#: no direct kind: a system gain on a proactive ping is
#: ``attention_vs_benefit``, and another person's gain is
#: treated like a seller's (their interest against the user's).
KIND_FOR_BENEFICIARY = {
    "seller": "seller_vs_user",
    "provider": "provider_vs_user",
    "other_person": "seller_vs_user",
}

#: Severities: ``"high"`` when the user pays money (a
#: non-empty ``user_cost``) or the other party gains far more
#: than the user; ``"low"`` otherwise.
SEVERITIES = ("low", "high")

#: Statuses of a conflict record. ``"surfaced"`` means the
#: conflict was shown to the user and no choice is recorded
#: yet; the other three are terminal (a choice was recorded).
STATUS_SURFACED = "surfaced"
STATUS_RESOLVED_PROCEED = "resolved_proceed"
STATUS_RESOLVED_ALTERNATIVE = "resolved_alternative"
STATUS_RESOLVED_DECLINED = "resolved_declined"
CONFLICT_STATUSES = (
    STATUS_SURFACED,
    STATUS_RESOLVED_PROCEED,
    STATUS_RESOLVED_ALTERNATIVE,
    STATUS_RESOLVED_DECLINED,
)

#: The three choices a user can make about a surfaced conflict.
USER_CHOICES = ("proceed", "alternative", "decline")

#: Choice -> terminal status.
_CHOICE_TO_STATUS = {
    "proceed": STATUS_RESOLVED_PROCEED,
    "alternative": STATUS_RESOLVED_ALTERNATIVE,
    "decline": STATUS_RESOLVED_DECLINED,
}

#: Detector thresholds (bookkeeping choices, not probabilities).
#: An engagement gain at or above this, while the action serves
#: the user's reflective goal below
#: :data:`ENGAGEMENT_USER_BENEFIT_MAX`, is engagement winning
#: over the goal.
ENGAGEMENT_GAIN_THRESHOLD = 0.7
ENGAGEMENT_USER_BENEFIT_MAX = 0.5
#: A gap between the other party's benefit and the user's goal
#: alignment at or above this makes a conflict high severity.
HIGH_SEVERITY_GAP = 0.5
#: An action that hides its method while also pulling engagement
#: at or above this keeps the user dependent instead of teaching
#: them: a retention conflict.
RETENTION_ENGAGEMENT_MIN = 0.5
#: A proactive ping whose benefit to the user is below this
#: while the system (or another party) gains at or above
#: :data:`ATTENTION_PROACTIVE_MIN_OTHER_BENEFIT` is attention
#: spent on the system's behalf, not the user's.
ATTENTION_USER_BENEFIT_MAX = 0.5
ATTENTION_PROACTIVE_MIN_OTHER_BENEFIT = 0.5

#: DSN default (peer auth as the OS user, no password).
DEFAULT_DSN = None


class UnresolvedConflictError(Exception):
    """A conflicted action was run while its record is unresolved.

    Raised by :meth:`ConflictGate.require_resolved`: the only way
    through is a recorded user choice. This is the fail-closed
    half of the rule — surfacing without enforcement would be
    theatre.
    """


# ---------------------------------------------------------------------------
# Proposed action + detector
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProposedAction:
    """One action the agent proposes to take, with its interests.

    ``tool_name`` is the tool that would run. ``description``
    is one plain sentence saying what the action would do.
    ``beneficiary`` names who gains besides the user
    (``"user"`` when nobody else does). ``user_goal`` is the
    user's reflective goal at stake, in the user's own terms
    (from the K1 values level or the profile); empty when the
    caller does not know it — the detector then judges the
    numbers without naming a goal, it never invents one.
    ``goal_alignment`` (0..1) is how well the action serves
    that goal; ``other_benefit`` (0..1) is how much the other
    party gains; ``engagement_gain`` (0..1) is how much the
    action keeps the user engaged or coming back.
    ``hides_method`` is True when the action does the work
    without showing how, so the user cannot learn to do it.
    ``user_cost`` is plain text of what the action costs the
    user (money, time, attention), empty when nothing.
    ``proactive`` is True for an unsolicited proactive ping.
    """

    tool_name: str
    description: str = ""
    beneficiary: str = "user"
    user_goal: str = ""
    goal_alignment: float = 1.0
    other_benefit: float = 0.0
    engagement_gain: float = 0.0
    hides_method: bool = False
    user_cost: str = ""
    proactive: bool = False

    def __post_init__(self) -> None:
        if self.beneficiary not in BENEFICIARIES:
            raise ValueError(f"unknown beneficiary: {self.beneficiary!r} (known: {BENEFICIARIES})")
        for name in ("goal_alignment", "other_benefit", "engagement_gain"):
            value = getattr(self, name)
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"{name} must be within 0..1, got {value}")


def _severity(action: ProposedAction) -> str:
    """High when the user pays, or the other party gains far more."""
    if action.user_cost.strip():
        return "high"
    if action.other_benefit - action.goal_alignment >= HIGH_SEVERITY_GAP:
        return "high"
    return "low"


def _user_interest(action: ProposedAction) -> str:
    """The user's side, in plain words, for the surfaced text."""
    if action.user_goal.strip():
        return f"your goal is to {action.user_goal.strip()}"
    return "this serves you less than it serves someone else"


def _other_interest(action: ProposedAction, kind: str) -> str:
    """The other side, in plain words, for the surfaced text."""
    who = {
        "seller": "the seller",
        "provider": "the provider",
        "system": "the system",
        "other_person": "the other person",
    }.get(action.beneficiary, "someone else")
    if kind == "engagement_vs_goal":
        return "keep you engaged instead"
    if kind == "retention_vs_independence":
        return "do the work for you without showing you how"
    if kind == "attention_vs_benefit":
        return "spend your attention on the system's behalf"
    return f"benefit {who} more than it benefits you"


def detect_conflict(action: ProposedAction) -> Conflict | None:
    """Check one proposed action for a conflict of interest.

    Returns a :class:`Conflict` (status ``"surfaced"``) when a
    conflict is found, else None. The rules run in priority
    order — the first match is the recorded kind, so one action
    carries one kind, the sharpest one:

    1. Another party gains more than the user
       (``other_benefit > goal_alignment``): the kind follows
       the beneficiary (seller / provider / other person).
    2. Engagement wins over the goal (``engagement_gain >= 0.7``
       while ``goal_alignment < 0.5``): the action keeps the
       user around instead of serving what they reflectively
       want.
    3. Retention (``hides_method`` with ``engagement_gain >=
       0.5``): the work is done opaquely, so the user stays
       dependent instead of learning to do it.
    4. Attention (a proactive ping with ``goal_alignment < 0.5``
       while another party gains ``>= 0.5``): the ping spends
       the user's attention for the system's benefit.

    A beneficiary of ``"user"`` can still trip rules 2-4:
    engagement, retention, and attention conflicts need no
    other party. An action with no other-party gain, low
    engagement, an open method, and no proactive ping has no
    conflict and returns None.
    """
    kind: str | None = None
    if action.beneficiary in KIND_FOR_BENEFICIARY and (
        action.other_benefit > action.goal_alignment
    ):
        kind = KIND_FOR_BENEFICIARY[action.beneficiary]
    elif (
        action.engagement_gain >= ENGAGEMENT_GAIN_THRESHOLD
        and action.goal_alignment < ENGAGEMENT_USER_BENEFIT_MAX
    ):
        kind = "engagement_vs_goal"
    elif action.hides_method and action.engagement_gain >= RETENTION_ENGAGEMENT_MIN:
        kind = "retention_vs_independence"
    elif (
        action.proactive
        and action.goal_alignment < ATTENTION_USER_BENEFIT_MAX
        and action.other_benefit >= ATTENTION_PROACTIVE_MIN_OTHER_BENEFIT
    ):
        kind = "attention_vs_benefit"
    if kind is None:
        return None
    return Conflict(
        kind=kind,
        tool_name=action.tool_name,
        description=action.description or f"run {action.tool_name}",
        beneficiary=action.beneficiary,
        user_interest=_user_interest(action),
        other_interest=_other_interest(action, kind),
        user_cost=action.user_cost,
        severity=_severity(action),
    )


# ---------------------------------------------------------------------------
# Conflict record
# ---------------------------------------------------------------------------


@dataclass
class Conflict:
    """One surfaced conflict of interest, and its resolution.

    ``conflict_id`` is a fresh uuid hex. ``kind`` is one of
    :data:`CONFLICT_KINDS`. ``user_interest`` /
    ``other_interest`` are the two sides in plain words (what
    was shown to the user). ``user_cost`` is what the action
    costs the user, in plain words, empty when nothing.
    ``severity`` is ``"low"`` or ``"high"``. ``status`` starts
    at ``"surfaced"`` and moves to a ``"resolved_*"`` status
    only through :meth:`ConflictStore.resolve` /
    :meth:`ConflictGate.record_user_choice`, which also set
    ``user_choice`` and ``resolved_at``. ``surfaced_text`` is
    the exact text shown to the user (see
    :func:`surface_text`). ``user_id`` / ``agent_id`` are only
    set when the record is persisted to Postgres (they are
    never shown to the user).
    """

    kind: str
    tool_name: str
    description: str = ""
    beneficiary: str = "user"
    user_interest: str = ""
    other_interest: str = ""
    user_cost: str = ""
    severity: str = "low"
    conflict_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = STATUS_SURFACED
    user_choice: str | None = None
    surfaced_text: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    resolved_at: datetime | None = None
    user_id: str | None = None
    agent_id: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in CONFLICT_KINDS:
            raise ValueError(f"unknown conflict kind: {self.kind!r} (known: {CONFLICT_KINDS})")
        if self.beneficiary not in BENEFICIARIES:
            raise ValueError(f"unknown beneficiary: {self.beneficiary!r} (known: {BENEFICIARIES})")
        if self.severity not in SEVERITIES:
            raise ValueError(f"unknown severity: {self.severity!r} (known: {SEVERITIES})")
        if self.status not in CONFLICT_STATUSES:
            raise ValueError(
                f"unknown conflict status: {self.status!r} (known: {CONFLICT_STATUSES})"
            )
        if not self.surfaced_text:
            self.surfaced_text = surface_text(self)

    @property
    def resolved(self) -> bool:
        """True once the user has chosen (any of the three choices)."""
        return self.status != STATUS_SURFACED

    def to_dict(self) -> dict:
        """JSON-safe dict of this record (the audit view)."""
        return {
            "conflict_id": self.conflict_id,
            "kind": self.kind,
            "tool_name": self.tool_name,
            "description": self.description,
            "beneficiary": self.beneficiary,
            "user_interest": self.user_interest,
            "other_interest": self.other_interest,
            "user_cost": self.user_cost,
            "severity": self.severity,
            "status": self.status,
            "user_choice": self.user_choice,
            "surfaced_text": self.surfaced_text,
            "created_at": self.created_at.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
        }


def surface_text(conflict: Conflict) -> str:
    """The exact text shown to the user for one conflict.

    One plain paragraph naming both interests, the cost when
    there is one, and the question. It never hides which side
    gains: the other party is named, not implied.
    """
    cost = f" It would cost you {conflict.user_cost}." if conflict.user_cost.strip() else ""
    return (
        f"This would {conflict.other_interest}, while {conflict.user_interest}."
        f"{cost} I am showing you this instead of deciding for you —"
        " do you want me to go ahead anyway, show an alternative, or skip it?"
    )


# ---------------------------------------------------------------------------
# Store (in-memory audit log, optionally persisted to Postgres)
# ---------------------------------------------------------------------------


def _resolve_dsn(dsn: str | None) -> str | None:
    return dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)


def create_schema(dsn: str | None = None) -> None:
    """Apply the ``conflict_records`` table (migration 0019_conflicts).

    Fresh databases need this call (or the migration) before any
    record is persisted; the in-memory store works without it.
    """
    apply_migrations(_resolve_dsn(dsn), target="0019")


def load_conflicts(
    user_id, agent_id, *, dsn: str | None = None, status: str | None = None
) -> list[Conflict]:
    """Load persisted conflict records for one user/agent.

    Newest first. ``status`` filters to one status when given.
    Returns [] when the table holds nothing for them.
    """
    from puspa.db import connect as _db_connect
    from puspa.db import dict_row

    query = (
        "SELECT id, kind, tool_name, description, beneficiary,"
        " user_interest, other_interest, user_cost, severity, status,"
        " user_choice, surfaced_text, created_at, resolved_at"
        " FROM conflict_records WHERE user_id = %s AND agent_id = %s"
    )
    params: list = [str(user_id), str(agent_id)]
    if status is not None:
        if status not in CONFLICT_STATUSES:
            raise ValueError(f"unknown conflict status: {status!r}")
        query += " AND status = %s"
        params.append(status)
    query += " ORDER BY created_at DESC"
    with _db_connect(_resolve_dsn(dsn), row_factory=dict_row) as conn:
        rows = conn.execute(query, params).fetchall()
    return [
        Conflict(
            kind=row["kind"],
            tool_name=row["tool_name"],
            description=row["description"] or "",
            beneficiary=row["beneficiary"],
            user_interest=row["user_interest"] or "",
            other_interest=row["other_interest"] or "",
            user_cost=row["user_cost"] or "",
            severity=row["severity"],
            conflict_id=str(row["id"]).replace("-", ""),
            status=row["status"],
            user_choice=row["user_choice"],
            surfaced_text=row["surfaced_text"] or "",
            created_at=row["created_at"],
            resolved_at=row["resolved_at"],
            user_id=str(user_id),
            agent_id=str(agent_id),
        )
        for row in rows
    ]


def delete_conflicts(user_id, agent_id, *, dsn: str | None = None) -> int:
    """Delete persisted conflict records for one user/agent.

    Returns how many rows were deleted. This is a test and
    operator helper (and the user's own data-deletion path
    under K12): the agent itself never deletes records.
    """
    from puspa.db import connect as _db_connect

    with _db_connect(_resolve_dsn(dsn), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM conflict_records WHERE user_id = %s AND agent_id = %s",
            (str(user_id), str(agent_id)),
        )
        return cur.rowcount


class ConflictStore:
    """The audit log of conflict records.

    In-memory by default: :meth:`record` appends, :meth:`resolve`
    sets the status / choice / resolution time, and the log is
    readable through :meth:`list_records` / :meth:`unresolved` /
    :meth:`summary`. Records are never deleted here and never
    edited in place — resolving one only moves its status
    forward, so the history of what was surfaced stays intact.

    With a ``dsn``, every :meth:`record` also INSERTs the row
    into ``conflict_records`` and every :meth:`resolve` also
    UPDATEs it (both need ``user_id`` / ``agent_id`` then, or
    ``record`` raises ``ValueError``). A database failure while
    persisting raises instead of silently dropping the audit:
    a lost conflict record must be visible, not quiet.
    """

    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn
        self._records: list[Conflict] = []

    # -- reads ---------------------------------------------------------
    def get(self, conflict_id: str) -> Conflict | None:
        """One record by id, or None when unknown."""
        for record in self._records:
            if record.conflict_id == conflict_id:
                return record
        return None

    def list_records(self, *, status: str | None = None) -> list[Conflict]:
        """All records, oldest first (a copy)."""
        if status is None:
            return list(self._records)
        if status not in CONFLICT_STATUSES:
            raise ValueError(f"unknown conflict status: {status!r}")
        return [r for r in self._records if r.status == status]

    def unresolved(self) -> list[Conflict]:
        """Records still waiting on the user's choice."""
        return self.list_records(status=STATUS_SURFACED)

    def latest_for(self, tool_name: str, kind: str) -> Conflict | None:
        """The newest record for one tool + kind, or None."""
        for record in reversed(self._records):
            if record.tool_name == tool_name and record.kind == kind:
                return record
        return None

    def summary(self) -> dict:
        """The log in numbers: total, unresolved, per kind, per status."""
        by_kind: dict[str, int] = {}
        by_status: dict[str, int] = {}
        for record in self._records:
            by_kind[record.kind] = by_kind.get(record.kind, 0) + 1
            by_status[record.status] = by_status.get(record.status, 0) + 1
        return {
            "total": len(self._records),
            "unresolved": len(self.unresolved()),
            "by_kind": by_kind,
            "by_status": by_status,
        }

    # -- writes --------------------------------------------------------
    def record(self, conflict: Conflict, *, user_id=None, agent_id=None) -> Conflict:
        """Append one conflict record (status must be surfaced).

        With a ``dsn`` the row is also INSERTed into
        ``conflict_records``; ``user_id`` and ``agent_id`` are
        required then. Returns the record.
        """
        if conflict.status != STATUS_SURFACED:
            raise ValueError(
                f"only a surfaced conflict can be recorded, got status {conflict.status!r}"
            )
        if self.dsn is not None:
            if user_id is None or agent_id is None:
                raise ValueError("persisting a conflict record needs user_id and agent_id")
            conflict.user_id = str(user_id)
            conflict.agent_id = str(agent_id)
            # Persist first: a database failure raises before the
            # record enters the memory log, so the two never diverge.
            self._persist_insert(conflict)
        self._records.append(conflict)
        return conflict

    def resolve(self, conflict_id: str, choice: str) -> Conflict:
        """Record the user's choice on one surfaced conflict.

        ``choice`` is ``"proceed"``, ``"alternative"``, or
        ``"decline"``. Sets the terminal status, the choice,
        and the resolution time. Raises ``KeyError`` for an
        unknown id, ``ValueError`` for a bad choice or for a
        record that is already resolved (a choice is final:
        it cannot be rewritten).
        """
        record = self.get(conflict_id)
        if record is None:
            raise KeyError(f"unknown conflict id: {conflict_id!r}")
        if choice not in USER_CHOICES:
            raise ValueError(f"unknown user choice: {choice!r} (known: {USER_CHOICES})")
        if record.resolved:
            raise ValueError(
                f"conflict {conflict_id!r} is already resolved as {record.status!r}: "
                "a user choice is final"
            )
        record.status = _CHOICE_TO_STATUS[choice]
        record.user_choice = choice
        record.resolved_at = datetime.now(UTC)
        if self.dsn is not None:
            self._persist_update(record)
        return record

    # -- Postgres ------------------------------------------------------
    def _persist_insert(self, record: Conflict) -> None:
        from puspa.db import connect as _db_connect

        with _db_connect(_resolve_dsn(self.dsn), autocommit=True) as conn:
            conn.execute(
                "INSERT INTO conflict_records"
                " (id, user_id, agent_id, kind, tool_name, description,"
                " beneficiary, user_interest, other_interest, user_cost,"
                " severity, status, user_choice, surfaced_text, created_at, resolved_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    record.conflict_id,
                    record.user_id,
                    record.agent_id,
                    record.kind,
                    record.tool_name,
                    record.description,
                    record.beneficiary,
                    record.user_interest,
                    record.other_interest,
                    record.user_cost,
                    record.severity,
                    record.status,
                    record.user_choice,
                    record.surfaced_text,
                    record.created_at,
                    record.resolved_at,
                ),
            )

    def _persist_update(self, record: Conflict) -> None:
        from puspa.db import connect as _db_connect

        with _db_connect(_resolve_dsn(self.dsn), autocommit=True) as conn:
            conn.execute(
                "UPDATE conflict_records SET status = %s, user_choice = %s,"
                " resolved_at = %s WHERE id = %s",
                (record.status, record.user_choice, record.resolved_at, record.conflict_id),
            )


# ---------------------------------------------------------------------------
# Gate (enforcement) + per-tool specs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConflictDecision:
    """The verdict for one proposed action.

    ``outcome`` is ``"proceed"`` (no conflict, or the user
    already chose to proceed) or ``"surface"`` (a conflict is
    on the table: show it, do not run the action).
    ``conflict`` is the record behind a ``"surface"`` verdict
    (None on ``"proceed"``). ``reason`` is one plain sentence
    naming the deciding factor.
    """

    outcome: str  # "proceed" | "surface"
    conflict: Conflict | None
    reason: str

    def to_dict(self) -> dict:
        """JSON-safe dict of this decision."""
        return {
            "outcome": self.outcome,
            "conflict": self.conflict.to_dict() if self.conflict else None,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ConflictSpec:
    """What the gate needs to know about one conflict-prone tool.

    The static facts of the tool: who gains besides the user
    (``beneficiary``), the reflective goal at stake
    (``user_goal``, in the user's own terms), how well the
    tool's result serves that goal (``goal_alignment`` 0..1),
    how much the other party gains (``other_benefit`` 0..1),
    how much it pulls engagement (``engagement_gain`` 0..1),
    whether it hides its method (``hides_method``), whether it
    is an unsolicited proactive ping (``proactive``), what it
    costs the user in plain words (``user_cost``), and a
    one-sentence description (``description``; defaults to
    ``"run <tool>"``). The numbers are the deployer's assessed
    scores for this tool, stated here in one place instead of
    guessed per call.
    """

    beneficiary: str = "user"
    user_goal: str = ""
    goal_alignment: float = 1.0
    other_benefit: float = 0.0
    engagement_gain: float = 0.0
    hides_method: bool = False
    proactive: bool = False
    user_cost: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        # Reuse the action's validation: a spec with a bad
        # beneficiary or an out-of-range score is a bug at
        # wiring time, not a runtime surprise.
        ProposedAction(
            tool_name="<spec>",
            beneficiary=self.beneficiary,
            goal_alignment=self.goal_alignment,
            other_benefit=self.other_benefit,
            engagement_gain=self.engagement_gain,
        )

    def proposed_action(self, tool_name: str) -> ProposedAction:
        """Build the proposed action this spec describes."""
        return ProposedAction(
            tool_name=tool_name,
            description=self.description or f"run {tool_name}",
            beneficiary=self.beneficiary,
            user_goal=self.user_goal,
            goal_alignment=self.goal_alignment,
            other_benefit=self.other_benefit,
            engagement_gain=self.engagement_gain,
            hides_method=self.hides_method,
            user_cost=self.user_cost,
            proactive=self.proactive,
        )


class ConflictGate:
    """Enforce the anti-manipulation rule on proposed actions.

    Built with per-tool :class:`ConflictSpec` entries (``specs``),
    a :class:`ConflictStore` (a fresh in-memory one by default,
    or a Postgres-backed one when ``dsn`` is given — passed
    through to the store), and an optional ledger ``log``
    callable (``log(event_type, content)``). Surfacing writes
    ``"approval_requested"`` through ``log`` when given; a
    recorded choice writes ``"approval_granted"`` (proceed) or
    ``"approval_rejected"`` (alternative / decline, with the
    choice named) — the existing G4/G12 event types, no new
    one.

    A tool with no spec fails open: the gate defers to the
    existing gates and the call runs exactly as it would
    without a gate.
    """

    def __init__(
        self,
        *,
        specs: dict[str, ConflictSpec] | None = None,
        store: ConflictStore | None = None,
        dsn: str | None = None,
        log=None,
    ) -> None:
        self._specs: dict[str, ConflictSpec] = dict(specs or {})
        self.store = store if store is not None else ConflictStore(dsn=dsn)
        self.log = log

    @property
    def specs(self) -> dict[str, ConflictSpec]:
        """The tools this gate has a spec for, by tool name."""
        return dict(self._specs)

    def _log(self, event_type: str, content: dict) -> None:
        if self.log is None:
            return
        # A broken audit sink must not break the decision: the
        # in-memory store still holds the record.
        with contextlib.suppress(Exception):
            self.log(event_type, content)

    # -- decision ------------------------------------------------------
    def check(self, action: ProposedAction, *, user_id=None, agent_id=None) -> ConflictDecision:
        """Decide one proposed action: proceed, or surface.

        No conflict -> ``"proceed"``. A conflict whose latest
        record for this tool + kind is already
        ``resolved_proceed`` -> ``"proceed"`` (the user already
        chose to go ahead). A conflict with an unresolved
        record -> ``"surface"`` with that same record (no
        duplicate). A conflict whose latest record is resolved
        to alternative / decline -> ``"surface"`` with a fresh
        record: the earlier answer does not silently become
        permission to try again. Otherwise the fresh conflict
        is recorded and surfaced.
        """
        found = detect_conflict(action)
        if found is None:
            return ConflictDecision(
                outcome="proceed",
                conflict=None,
                reason=f"no conflict of interest in {action.tool_name!r}",
            )
        latest = self.store.latest_for(action.tool_name, found.kind)
        if latest is not None and latest.status == STATUS_RESOLVED_PROCEED:
            return ConflictDecision(
                outcome="proceed",
                conflict=None,
                reason=(
                    f"the user already chose to proceed past this {found.kind} conflict "
                    f"for {action.tool_name!r}"
                ),
            )
        if latest is not None and not latest.resolved:
            return ConflictDecision(
                outcome="surface",
                conflict=latest,
                reason=(
                    f"unresolved {found.kind} conflict for {action.tool_name!r}: "
                    "the user has not chosen yet"
                ),
            )
        record = found
        self.store.record(record, user_id=user_id, agent_id=agent_id)
        self._log(
            "approval_requested",
            {
                "conflict_id": record.conflict_id,
                "conflict_kind": record.kind,
                "tool": record.tool_name,
                "beneficiary": record.beneficiary,
                "surfaced_text": record.surfaced_text,
            },
        )
        return ConflictDecision(
            outcome="surface",
            conflict=record,
            reason=f"{record.kind} conflict for {action.tool_name!r}: surfaced to the user",
        )

    def check_tool(self, tool_name: str, *, user_id=None, agent_id=None) -> ConflictDecision:
        """Decide one tool call from its spec.

        No spec for the tool -> ``"proceed"``, fail-open: the
        gate did not assess the tool at all, and the existing
        gates decide.
        """
        spec = self._specs.get(tool_name)
        if spec is None:
            return ConflictDecision(
                outcome="proceed",
                conflict=None,
                reason=f"no conflict spec for tool {tool_name!r}: the gate defers",
            )
        return self.check(spec.proposed_action(tool_name), user_id=user_id, agent_id=agent_id)

    # -- user choice ---------------------------------------------------
    def record_user_choice(self, conflict_id: str, choice: str) -> Conflict:
        """Record what the user chose about a surfaced conflict.

        ``choice`` is ``"proceed"``, ``"alternative"``, or
        ``"decline"``. Writes the matching ledger event through
        ``log`` when given (``"approval_granted"`` for proceed,
        ``"approval_rejected"`` otherwise, with the choice
        named). Raises ``KeyError`` / ``ValueError`` exactly
        like :meth:`ConflictStore.resolve`.
        """
        record = self.store.resolve(conflict_id, choice)
        if choice == "proceed":
            self._log(
                "approval_granted",
                {
                    "conflict_id": record.conflict_id,
                    "conflict_kind": record.kind,
                    "tool": record.tool_name,
                    "user_choice": choice,
                },
            )
        else:
            self._log(
                "approval_rejected",
                {
                    "conflict_id": record.conflict_id,
                    "conflict_kind": record.kind,
                    "tool": record.tool_name,
                    "user_choice": choice,
                },
            )
        return record

    def require_resolved(self, conflict_id: str) -> Conflict:
        """Return the record, or raise while it is unresolved.

        Call this immediately before running a conflicted
        action: it is the fail-closed half of the rule. Raises
        :class:`UnresolvedConflictError` when the record is
        still ``"surfaced"`` (or :class:`KeyError` when the id
        is unknown).
        """
        record = self.store.get(conflict_id)
        if record is None:
            raise KeyError(f"unknown conflict id: {conflict_id!r}")
        if not record.resolved:
            raise UnresolvedConflictError(
                f"conflict {conflict_id!r} ({record.kind} on {record.tool_name!r}) "
                "is still surfaced: the user has not chosen yet"
            )
        return record

    def may_run(self, tool_name: str, kind: str) -> bool:
        """True when no unresolved conflict blocks this tool + kind."""
        latest = self.store.latest_for(tool_name, kind)
        return latest is None or latest.resolved


def conflict_surface_payload(decision: ConflictDecision, *, tool_name: str) -> str:
    """The tool-result text the loop returns when the gate surfaces.

    A JSON object the model can read: the decision, the conflict
    (both interests, the cost, the severity), the exact text to
    show the user, the three choices, and the question. The tool
    itself was NOT run — the payload says so, so the model must
    not report its effect as done.
    """
    if decision.outcome != "surface" or decision.conflict is None:
        raise ValueError("conflict_surface_payload is only for a 'surface' decision")
    conflict = decision.conflict
    payload: dict = {
        "conflict_decision": "surface",
        "tool": tool_name,
        "tool_was_run": False,
        "conflict_id": conflict.conflict_id,
        "conflict_kind": conflict.kind,
        "beneficiary": conflict.beneficiary,
        "user_interest": conflict.user_interest,
        "other_interest": conflict.other_interest,
        "user_cost": conflict.user_cost,
        "severity": conflict.severity,
        "surfaced_text": conflict.surfaced_text,
        "choices": list(USER_CHOICES),
        "question": (
            "Show the user the conflict above and ask them to choose: "
            "go ahead anyway, show an alternative, or skip it."
        ),
        "reason": decision.reason,
    }
    return json.dumps(payload, ensure_ascii=False)
