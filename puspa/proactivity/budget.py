"""Interruption budget (Kosmos K10).

Default is silence. Kosmos "pays" for the user's attention from a
limited daily interruption budget and only moves on its own when
the benefit exceeds the cost of the interruption. Good proactivity
is mostly silent reversible action, not notifications.

The rules (also written in ``docs/interruption-budget.md`` — the
doc is the source of truth, this module is the enforcement):

1. One day carries ``DEFAULT_DAILY_BUDGET`` units of attention
   (5). Every proactive initiative that interrupts the user pays
   its kind's cost from that day's budget.
2. An initiative runs only when its assessed benefit (``value``,
   0..1) reaches its kind's minimum value AND the remaining
   budget covers its cost. Benefit must exceed the cost of the
   interruption: a low-value ping is refused even on a full
   budget.
3. Scarcity: when the remaining budget falls to
   ``LOW_REMAINING_THRESHOLD`` (2) or less, only high-value
   initiatives (``value`` >= ``HIGH_VALUE_THRESHOLD``, 0.85)
   pass. Low-value initiatives are suppressed first as the
   budget thins, so the last units are reserved for what
   matters most.
4. An empty budget means silence: no new notification, of any
   kind, at any value. (Silent reversible action is the one
   exception: kind ``"silent_action"`` costs 0 and never
   interrupts, so it never pays and is never gated.)
5. The budget resets every day (UTC). Days are independent:
   spending on one day never affects another.

Costs and minimum values per initiative kind:

- ``urgent_alert`` — cost 1, min value 0.50. Time-critical
  (a payment failed, an account is locked): cheap, because
  missing it costs the user more than the ping.
- ``bill_due`` — cost 1, min value 0.60. A bill about to fall
  due: time-critical, but the user usually already expects it.
- ``price_drop`` — cost 2, min value 0.70. Saves money but is
  rarely urgent; priced double so a flood of small drops
  cannot eat the day.
- ``reminder`` — cost 1, min value 0.60. A nudge about
  something the user asked to be reminded of.
- ``digest`` — cost 1, min value 0.50. A batched summary: one
  ping carrying many items, so it is cheap per item.
- ``suggestion`` — cost 2, min value 0.80. An unsolicited
  idea is the spammiest kind of proactivity, so it is priced
  the highest and needs the highest value.
- ``silent_action`` — cost 0, min value 0.00. A reversible
  action taken quietly (filing, drafting, preparing): no
  interruption, no payment, never gated.

Why 5 units a day: the product default is silence, and a
user who is pinged more than a handful of times a day by
their own assistant stops reading the pings at all. Five
fits the real mix — one bill, one or two price drops, one
reminder, one digest — with the scarcity rule reserving the
last units for the urgent. It is a default, not a law:
``InterruptionBudget(daily_budget=...)`` configures it.

Persistence, stated honestly: this budget is in-memory.
The spend log lives in the process that owns the budget
(the scheduler tick caller, the channel gateway runner).
A restart resets the day's spend, so a restart can let
through pings the old process would have suppressed. That
is the known limit of this first version (the same
in-memory choice as the K6 ignorance map): the budget is
rebuilt by the next day's spending, and nothing is silently
treated as known. Persisting the spend log (a Postgres
table + migration, the K3/K4/K5 pattern) is the follow-up
when the budget needs to survive restarts.

No ledger event type is used here, on purpose: the closed
set of 31 ledger event types (revisi 7) has no "suppressed"
type, and adding a 32nd is forbidden. Denials are audited
in the budget's own spend log (``spend_log`` / ``summary``),
not in the ledger.

Pure Python: no database, no network, no LLM call. Value
scores are assessed by the caller (the scheduler maps its
trigger types to fixed assessed values; see
``puspa.scheduler.scheduler.PROACTIVE_TRIGGER_TYPES``),
never by a model at spend time.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime

__all__ = [
    "ALLOW",
    "DEFAULT_DAILY_BUDGET",
    "DENIAL_REASONS",
    "DENY_BUDGET_EMPTY",
    "DENY_INSUFFICIENT_BUDGET",
    "DENY_LOW_VALUE_WHEN_SCARCE",
    "DENY_VALUE_BELOW_THRESHOLD",
    "HIGH_VALUE_THRESHOLD",
    "INITIATIVE_KINDS",
    "LOW_REMAINING_THRESHOLD",
    "MAX_KEPT_DAYS",
    "Decision",
    "Initiative",
    "InitiativeKind",
    "InterruptionBudget",
    "SpendRecord",
    "initiative_from",
]

#: Units of attention per day. See the module docstring for why 5.
DEFAULT_DAILY_BUDGET = 5

#: At or below this many remaining units, only high-value
#: initiatives pass (the scarcity rule).
LOW_REMAINING_THRESHOLD = 2

#: Minimum value to pass while the budget is scarce.
HIGH_VALUE_THRESHOLD = 0.85

#: Spend-log days kept in memory; older days are dropped.
MAX_KEPT_DAYS = 30

#: Decision reasons. ``"allowed"`` is the only allow reason.
ALLOW = "allowed"
DENY_VALUE_BELOW_THRESHOLD = "value_below_threshold"
DENY_BUDGET_EMPTY = "budget_empty"
DENY_INSUFFICIENT_BUDGET = "insufficient_budget"
DENY_LOW_VALUE_WHEN_SCARCE = "low_value_when_scarce"
DENIAL_REASONS = (
    DENY_VALUE_BELOW_THRESHOLD,
    DENY_BUDGET_EMPTY,
    DENY_INSUFFICIENT_BUDGET,
    DENY_LOW_VALUE_WHEN_SCARCE,
)


@dataclass(frozen=True)
class InitiativeKind:
    """One kind of proactive initiative: its price and its bar.

    ``cost`` is paid from the daily budget when the initiative
    runs. ``min_value`` is the lowest assessed benefit (0..1)
    that justifies interrupting the user at all. ``description``
    is a one-line plain description of the kind.
    """

    name: str
    cost: int
    min_value: float
    description: str


#: The closed set of initiative kinds. A kind not listed here is
#: rejected by :class:`Initiative` — a caller cannot invent a
#: cheap kind to dodge the budget.
INITIATIVE_KINDS: dict[str, InitiativeKind] = {
    "urgent_alert": InitiativeKind(
        "urgent_alert", 1, 0.50, "Time-critical alert (a payment failed, an account locked)."
    ),
    "bill_due": InitiativeKind("bill_due", 1, 0.60, "A bill about to fall due."),
    "price_drop": InitiativeKind("price_drop", 2, 0.70, "A watched product got cheaper."),
    "reminder": InitiativeKind(
        "reminder", 1, 0.60, "A nudge about something the user asked to be reminded of."
    ),
    "digest": InitiativeKind(
        "digest", 1, 0.50, "A batched summary carrying many items in one ping."
    ),
    "suggestion": InitiativeKind("suggestion", 2, 0.80, "An unsolicited idea from the system."),
    "silent_action": InitiativeKind(
        "silent_action", 0, 0.00, "A silent reversible action: no interruption, no payment."
    ),
}


@dataclass(frozen=True)
class Initiative:
    """One proposed proactive interruption.

    ``kind`` must be a key of :data:`INITIATIVE_KINDS`.
    ``value`` is the caller's assessed benefit, 0..1: how much
    this interruption is worth to the user right now. ``label``
    is free text for the audit log (a trigger id, a job id),
    never shown to the user.
    """

    kind: str
    value: float
    label: str = ""

    def __post_init__(self) -> None:
        if self.kind not in INITIATIVE_KINDS:
            raise ValueError(f"unknown initiative kind: {self.kind!r}")
        if not 0.0 <= float(self.value) <= 1.0:
            raise ValueError(f"initiative value must be within 0..1, got {self.value}")


def initiative_from(value: Initiative | Mapping[str, object]) -> Initiative:
    """Build an :class:`Initiative` from one, or from a dict.

    A dict carries ``kind`` and ``value`` (and an optional
    ``label``). Anything else raises ``ValueError`` / ``TypeError``.
    """
    if isinstance(value, Initiative):
        return value
    if isinstance(value, Mapping):
        kind = value.get("kind")
        raw_value = value.get("value")
        label = value.get("label", "")
        if not isinstance(kind, str) or not isinstance(raw_value, (int, float)):
            raise ValueError(f"initiative dict needs 'kind' and 'value', got {dict(value)!r}")
        return Initiative(kind=kind, value=float(raw_value), label=str(label))
    raise TypeError(f"cannot build an Initiative from {type(value).__name__}")


@dataclass(frozen=True)
class Decision:
    """The answer to one :meth:`InterruptionBudget.try_spend`.

    ``allowed`` says whether the initiative may interrupt the
    user. ``reason`` is ``"allowed"`` or one of
    :data:`DENIAL_REASONS`. ``remaining_after`` is the budget
    left for the day once this decision is applied (unchanged
    when denied). ``day`` is the ISO date the decision applies
    to.
    """

    allowed: bool
    reason: str
    kind: str
    value: float
    cost: int
    remaining_after: int
    day: str

    def to_dict(self) -> dict:
        """JSON-safe dict of this decision."""
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "kind": self.kind,
            "value": self.value,
            "cost": self.cost,
            "remaining_after": self.remaining_after,
            "day": self.day,
        }


@dataclass(frozen=True)
class SpendRecord:
    """One audited spend attempt: allowed or denied.

    Every :meth:`InterruptionBudget.try_spend` call writes one
    record, so the log shows both what interrupted the user
    and what the budget suppressed.
    """

    day: str
    kind: str
    label: str
    value: float
    cost: int
    allowed: bool
    reason: str

    def to_dict(self) -> dict:
        """JSON-safe dict of this record."""
        return {
            "day": self.day,
            "kind": self.kind,
            "label": self.label,
            "value": self.value,
            "cost": self.cost,
            "allowed": self.allowed,
            "reason": self.reason,
        }


def _day_key(day: date | datetime | str | None) -> str:
    """Normalise a day to an ISO date string (``YYYY-MM-DD``).

    ``None`` means today (UTC). A ``datetime`` takes its date
    part; a string must already be an ISO date.
    """
    if day is None:
        return datetime.now(UTC).date().isoformat()
    if isinstance(day, datetime):
        return day.date().isoformat()
    if isinstance(day, date):
        return day.isoformat()
    raw = str(day)
    date.fromisoformat(raw)  # raises ValueError for a bad date
    return raw


class InterruptionBudget:
    """A daily interruption budget: 5 units, reset every day.

    One instance serves one user for one process lifetime (the
    scheduler tick caller and the channel gateway runner each
    own one, or share one passed in). All state is in-memory;
    see the module docstring for the honest restart limit.
    """

    def __init__(
        self,
        daily_budget: int = DEFAULT_DAILY_BUDGET,
        *,
        low_remaining_threshold: int = LOW_REMAINING_THRESHOLD,
        high_value_threshold: float = HIGH_VALUE_THRESHOLD,
    ) -> None:
        """Create a budget.

        Raises ``ValueError`` for a negative daily budget, a
        negative scarcity threshold, or a high-value threshold
        outside 0..1.
        """
        if int(daily_budget) < 0:
            raise ValueError(f"daily budget must not be negative, got {daily_budget}")
        if int(low_remaining_threshold) < 0:
            raise ValueError(
                f"low-remaining threshold must not be negative, got {low_remaining_threshold}"
            )
        if not 0.0 <= float(high_value_threshold) <= 1.0:
            raise ValueError(
                f"high-value threshold must be within 0..1, got {high_value_threshold}"
            )
        self.daily_budget = int(daily_budget)
        self.low_remaining_threshold = int(low_remaining_threshold)
        self.high_value_threshold = float(high_value_threshold)
        self._records: dict[str, list[SpendRecord]] = {}

    # -- reads ---------------------------------------------------------
    def spent(self, day: date | datetime | str | None = None) -> int:
        """Units spent on ``day`` (today by default)."""
        key = _day_key(day)
        return sum(r.cost for r in self._records.get(key, []) if r.allowed)

    def remaining(self, day: date | datetime | str | None = None) -> int:
        """Units left on ``day`` (today by default). Never negative."""
        return max(0, self.daily_budget - self.spent(day))

    def spend_log(self, day: date | datetime | str | None = None) -> list[SpendRecord]:
        """The audit log: every spend attempt, allowed and denied.

        With ``day`` given, only that day's records; otherwise
        every kept day, oldest first. Returns a copy.
        """
        if day is not None:
            return list(self._records.get(_day_key(day), []))
        out: list[SpendRecord] = []
        for key in sorted(self._records):
            out.extend(self._records[key])
        return out

    def summary(self, day: date | datetime | str | None = None) -> dict:
        """One day's budget in numbers (today by default)."""
        key = _day_key(day)
        records = self._records.get(key, [])
        denied_by_reason: dict[str, int] = {}
        for record in records:
            if not record.allowed:
                denied_by_reason[record.reason] = denied_by_reason.get(record.reason, 0) + 1
        return {
            "day": key,
            "daily_budget": self.daily_budget,
            "spent": self.spent(key),
            "remaining": self.remaining(key),
            "allowed": sum(1 for r in records if r.allowed),
            "denied": sum(1 for r in records if not r.allowed),
            "denied_by_reason": denied_by_reason,
        }

    # -- writes --------------------------------------------------------
    def try_spend(
        self, initiative: Initiative, *, day: date | datetime | str | None = None
    ) -> Decision:
        """Ask the budget whether one initiative may interrupt.

        The decision is recorded in the spend log either way,
        and an allowed spend deducts its cost from the day.
        The checks run in this order:

        1. Value below the kind's minimum: denied, however
           full the budget is. Benefit must exceed the cost
           of interrupting.
        2. Silent action (cost 0): allowed. It never
           interrupts, so it never pays and is never gated.
        3. Budget empty: denied — silence, no new notification.
        4. Remaining below the cost: denied — the initiative
           does not fit in what is left.
        5. Budget scarce (remaining at or below the low
           threshold) and value below the high-value bar:
           denied — the last units are reserved for what
           matters most.
        6. Otherwise allowed, and the cost is deducted.
        """
        key = _day_key(day)
        kind = INITIATIVE_KINDS[initiative.kind]
        value = float(initiative.value)
        remaining = self.remaining(key)
        if value < kind.min_value:
            reason = DENY_VALUE_BELOW_THRESHOLD
            allowed = False
        elif kind.cost == 0:
            reason = ALLOW
            allowed = True
        elif remaining == 0:
            reason = DENY_BUDGET_EMPTY
            allowed = False
        elif remaining < kind.cost:
            reason = DENY_INSUFFICIENT_BUDGET
            allowed = False
        elif remaining <= self.low_remaining_threshold and value < self.high_value_threshold:
            reason = DENY_LOW_VALUE_WHEN_SCARCE
            allowed = False
        else:
            reason = ALLOW
            allowed = True
        record = SpendRecord(
            day=key,
            kind=initiative.kind,
            label=initiative.label,
            value=value,
            cost=kind.cost,
            allowed=allowed,
            reason=reason,
        )
        self._records.setdefault(key, []).append(record)
        self._prune_old_days(keep=key)
        return Decision(
            allowed=allowed,
            reason=reason,
            kind=initiative.kind,
            value=value,
            cost=kind.cost if allowed else 0,
            remaining_after=self.remaining(key),
            day=key,
        )

    def reset_day(self, day: date | datetime | str | None = None) -> int:
        """Forget one day's spend log (today by default).

        Returns how many records were dropped. A reset day
        reads as a full budget again.
        """
        key = _day_key(day)
        return len(self._records.pop(key, []))

    # -- housekeeping --------------------------------------------------
    def _prune_old_days(self, *, keep: str) -> None:
        """Drop spend-log days older than ``MAX_KEPT_DAYS``.

        The current day is always kept. Only the audit log is
        pruned — the budget for a pruned day reads as full,
        which is correct: a day that old is over.
        """
        keys = sorted(self._records)
        if len(keys) <= MAX_KEPT_DAYS:
            return
        for old in keys[: len(keys) - MAX_KEPT_DAYS]:
            if old != keep:
                del self._records[old]
