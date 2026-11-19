"""Belief types of the world model (split out of ``model.py``).

The world model was 1,605 lines and its file-size baseline forbids
growth, so the belief data holders live here: the six belief
dataclasses, the price-key helper, and the three constants only
the beliefs need (``DEFAULT_CONFIDENCE`` is the default confidence
of a fresh belief; the other two tune habit patterns). The model
itself (``puspa.world.model.WorldModel``) imports them back, so
``puspa.world.model._PriceBelief`` and friends keep resolving.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Default confidence of a fresh belief.
DEFAULT_CONFIDENCE = 0.5
# A habit pattern only counts as a pattern when it repeats: the
# same hour, the same weekday, or a stable interval seen at
# least twice. One task alone never forms a habit.
HABIT_MIN_REPEATS = 2
#: A stable interval needs gaps within twice this tolerance (days).
INTERVAL_TOLERANCE_DAYS = 1.0


@dataclass
class _PriceBelief:
    last_price: float | None = None
    last_day: int | None = None
    daily_delta: float = 0.0
    trend_adopted: bool = False
    delta_history: list = field(default_factory=list)
    observations: int = 0
    confidence: float = DEFAULT_CONFIDENCE


@dataclass
class _BillBelief:
    amount: float | None = None
    due_day: int | None = None
    paid: bool = False
    observations: int = 0
    confidence: float = DEFAULT_CONFIDENCE


@dataclass
class _ScheduleBelief:
    """How long one kind of task usually takes, learned from results.

    Only observations teach this belief: ``durations`` holds recent
    observed durations in days, ``on_time`` counts the ones that
    finished within their deadline. ``version`` rises whenever the
    belief is replaced (contradiction promoted or decay); the old
    version is kept in ``history`` and never overwritten in place.
    ``candidates`` holds observations that contradicted the verified
    belief and are waiting for confirmation.
    """

    task_kind: str = ""
    durations: list = field(default_factory=list)
    observations: int = 0
    on_time: int = 0
    with_deadline: int = 0
    confidence: float = DEFAULT_CONFIDENCE
    version: int = 1
    status: str = "trial"
    last_day: int | None = None
    history: list = field(default_factory=list)
    candidates: list = field(default_factory=list)

    @property
    def mean_duration(self) -> float | None:
        if not self.durations:
            return None
        return sum(self.durations) / len(self.durations)

    @property
    def on_time_rate(self) -> float | None:
        if not self.with_deadline:
            return None
        return self.on_time / self.with_deadline


@dataclass
class _CapabilityBelief:
    """What the agent can and cannot do for one kind of task.

    Learned only from task outcomes: every finished task result,
    success or failure, is one observation. ``version`` rises
    whenever the belief is replaced; the old version is kept in
    ``history``. A single outcome that contradicts a verified
    belief is held in ``candidates``; only consecutive
    contradictions replace the belief.
    """

    task_kind: str = ""
    successes: int = 0
    failures: int = 0
    observations: int = 0
    confidence: float = DEFAULT_CONFIDENCE
    version: int = 1
    status: str = "trial"
    last_day: int | None = None
    consecutive_contradictions: int = 0
    contradiction_value: bool | None = None
    history: list = field(default_factory=list)
    candidates: list = field(default_factory=list)

    @property
    def success_rate(self) -> float | None:
        total = self.successes + self.failures
        if not total:
            return None
        return self.successes / total


@dataclass
class _ContactBelief:
    """How one contact usually responds, learned only from observed
    interactions.

    ``replies`` counts observed replies out of ``observations``
    interactions; ``reply_delays`` holds recent observed reply
    delays in hours (replies only); ``channels`` counts which
    channel each observed interaction used, so the preferred
    channel is the most used one, never assumed. Nothing is
    known before the first observed interaction: no belief
    exists until ``ingest_task_result`` (or a ``contact_reply``
    observation) names the contact. ``version`` rises whenever
    the belief is replaced (contradiction promoted or decay);
    the old version is kept in ``history``. A reply pattern
    that contradicts a verified belief is held in
    ``candidates``; only consecutive contradictions replace it.
    """

    contact_id: str = ""
    observations: int = 0
    replies: int = 0
    reply_delays: list = field(default_factory=list)
    channels: dict = field(default_factory=dict)
    confidence: float = DEFAULT_CONFIDENCE
    version: int = 1
    status: str = "trial"
    last_day: int | None = None
    consecutive_contradictions: int = 0
    contradiction_value: bool | None = None
    history: list = field(default_factory=list)
    candidates: list = field(default_factory=list)

    @property
    def reply_rate(self) -> float | None:
        if not self.observations:
            return None
        return self.replies / self.observations

    @property
    def mean_reply_delay_hours(self) -> float | None:
        if not self.reply_delays:
            return None
        return sum(self.reply_delays) / len(self.reply_delays)

    @property
    def preferred_channel(self) -> str | None:
        if not self.channels:
            return None
        return max(self.channels.items(), key=lambda item: item[1])[0]


@dataclass
class _HabitBelief:
    """A recurring pattern in the user's own task history.

    One task kind, learned only from repeated tasks of that
    kind. ``hours`` and ``weekdays`` count when observed tasks
    happened (hour 0-23 and weekday 0-6, as string keys so the
    JSON round trip is exact); ``intervals`` holds recent gaps
    in days between consecutive observed tasks. A pattern only
    exists when it repeats: ``peak_hour`` / ``peak_weekday``
    need the same value at least twice, and a stable interval
    needs at least two similar gaps. One task alone forms no
    habit. ``version`` rises whenever the belief is replaced
    (contradiction promoted or decay); the old version is kept
    in ``history``. Observations at a different hour than a
    verified peak hour are held in ``candidates``; only three
    confirming observations promote a new version.
    """

    task_kind: str = ""
    observations: int = 0
    hours: dict = field(default_factory=dict)
    weekdays: dict = field(default_factory=dict)
    intervals: list = field(default_factory=list)
    confidence: float = DEFAULT_CONFIDENCE
    version: int = 1
    status: str = "trial"
    last_day: int | None = None
    consecutive_contradictions: int = 0
    contradiction_hour: int | None = None
    history: list = field(default_factory=list)
    candidates: list = field(default_factory=list)

    @property
    def peak_hour(self) -> int | None:
        if not self.hours:
            return None
        hour, count = max(self.hours.items(), key=lambda item: item[1])
        if count < HABIT_MIN_REPEATS:
            return None
        return int(hour)

    @property
    def peak_weekday(self) -> int | None:
        if not self.weekdays:
            return None
        weekday, count = max(self.weekdays.items(), key=lambda item: item[1])
        if count < HABIT_MIN_REPEATS:
            return None
        return int(weekday)

    @property
    def mean_interval_days(self) -> float | None:
        if not self.intervals:
            return None
        return sum(self.intervals) / len(self.intervals)

    @property
    def has_repeated_pattern(self) -> bool:
        """True when some pattern in the history actually repeats."""
        if self.peak_hour is not None or self.peak_weekday is not None:
            return True
        if len(self.intervals) >= HABIT_MIN_REPEATS:
            ordered = sorted(self.intervals)
            return ordered[-1] - ordered[0] <= 2 * INTERVAL_TOLERANCE_DAYS
        return False


def _price_key(product_id: str, store: str) -> str:
    return f"{product_id}@{store}"
