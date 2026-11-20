"""Guardrails for the curriculum runner (plan section 7.5).

The curriculum runner learns while the system is idle, so an
unchecked loop can entrench its own mistakes: a self-generated
outcome becomes a correction, the correction drives the next
retry, and the retry confirms the correction. These guardrails
break that circle at four points:

- Daily caps. On top of the per-task Rp2.000 ceiling, a
  :class:`DailyCaps` ledger caps learning work per user per day
  (task count and rupiah) and globally per day (task count and
  rupiah). The spend caps are enforced by reservation: a task
  may only start when the day's spend so far plus the per-task
  ceiling still fits the cap, so recorded spend can never pass
  a cap. The ledger is in-memory and shared by passing one
  instance to every runner; a runner that builds its own ledger
  enforces the caps only for itself.
- Verified-only teaching. An experience teaches (changes a
  prediction, grows a gap, marks a skill suspect) only when it
  is a verified outcome or a user-confirmed one. Anything else
  is quarantined: stored, listed by
  ``LearningLoop.list_quarantined``, and never read by
  ``predict`` until a user releases it with
  ``LearningLoop.confirm_experience``. See
  :func:`classify_experience` for what counts as quarantine.
- Quarantine triggers. An experience is quarantined when its
  numbers are orphan numbers (a meaningful figure with no
  evidence behind it, per
  ``puspa.evidence.renderer.find_orphan_numbers``) or when the
  caller marks it unverified. A user correction is quarantined
  when the corrected figure itself is an orphan number; a plain
  user correction still applies, because the user is the
  verifier for their own correction, and a correction that
  contradicts a learned answer already marks the affected skill
  suspect in the loop.
- Weekly skill cap and holdout evaluation. A
  :class:`WeeklySkillCap` caps newly learned skills per ISO
  week (repair drafts for an existing skill do not count: a
  repair maintains a skill, it does not learn a new one). A
  drafted skill may only be submitted for user approval after
  it passes an evaluation on a holdout set that differs from
  its verification cases; the holdout gate is enforced in the
  loop (see ``LearningLoop.submit_for_approval`` and
  ``LearningLoop.activate_skill``).

No network call is made on any path here, and no new ledger
event type is introduced: caps and quarantine live in memory
on the runner and the loop.
"""

from __future__ import annotations

from datetime import UTC, datetime

#: Maximum learning tasks per user per calendar day (UTC).
DEFAULT_MAX_TASKS_PER_USER_PER_DAY = 5

#: Maximum learning spend per user per calendar day (UTC), in
#: rupiah. Five tasks at the Rp2.000 per-task ceiling.
DEFAULT_MAX_SPEND_RP_PER_USER_PER_DAY = 10_000

#: Maximum learning tasks across all users per calendar day (UTC).
DEFAULT_MAX_TASKS_GLOBAL_PER_DAY = 50

#: Maximum learning spend across all users per calendar day
#: (UTC), in rupiah. Fifty tasks at the Rp2.000 ceiling.
DEFAULT_MAX_SPEND_RP_GLOBAL_PER_DAY = 100_000

#: Maximum newly learned skills per ISO week.
DEFAULT_MAX_NEW_SKILLS_PER_WEEK = 3

#: Verification states an experience can carry. Only the first
#: three teach; "quarantined" never does until it is released
#: by ``LearningLoop.confirm_experience``.
VERIFICATION_STATUSES = (
    "verified",
    "user_confirmed",
    "world_observation",
    "quarantined",
)

#: Quarantine reasons attached to a quarantined experience.
QUARANTINE_REASONS = ("orphan_numbers", "unverified")

#: Cap outcomes reported by :class:`DailyCaps` and the runner.
CAP_STATUSES = (
    "ok",
    "user_daily_cap_reached",
    "user_daily_spend_reached",
    "global_daily_cap_reached",
    "global_daily_spend_reached",
)


def find_orphan_numbers(text: str) -> list[str]:
    """Orphan numbers in ``text`` (meaningful figures with no evidence).

    Thin wrapper over ``puspa.evidence.renderer`` so the loop
    and the runner share the one orphan-number definition.
    Imported lazily: the evidence package must never import
    the memory package, and this keeps that direction one-way.
    """
    from puspa.evidence.renderer import find_orphan_numbers as _find

    return _find(text)


def classify_experience(
    *,
    predicted=None,
    observed=None,
    verified: bool | None = None,
) -> dict:
    """Classify one experience as teaching or quarantined.

    ``verified`` is True when an independent check (a verifier,
    a real ledger event, a user confirmation) backs the
    outcome, False when the caller knows it does not, and None
    when the caller says nothing (a world observation, trusted
    as before). Returns ``{"verification", "quarantined",
    "quarantine_reasons"}``. An experience is quarantined when
    it carries an orphan number in its predicted or observed
    text, or when ``verified`` is False. Quarantined experience
    never teaches until it is released.
    """
    reasons: list[str] = []
    if verified is False:
        reasons.append("unverified")
    text = " ".join(str(value) for value in (predicted, observed) if value is not None)
    if text and find_orphan_numbers(text):
        reasons.append("orphan_numbers")
    quarantined = bool(reasons)
    return {
        "verification": "quarantined"
        if quarantined
        else ("verified" if verified else "world_observation"),
        "quarantined": quarantined,
        "quarantine_reasons": reasons,
    }


def week_key(now: datetime | None = None) -> str:
    """ISO week key (``YYYY-Www``) for ``now`` (UTC, default now)."""
    current = now or datetime.now(UTC)
    year, week, _ = current.isocalendar()
    return f"{year}-W{week:02d}"


class WeeklySkillCap:
    """Cap on newly learned skills per ISO week.

    Repair drafts do not count: they maintain an existing
    skill instead of learning a new one. ``record`` raises
    ``ValueError`` when the week is already full, so a caller
    that drafts past the cap fails loudly instead of silently
    dropping the skill.
    """

    def __init__(self, max_per_week: int | None = None):
        if max_per_week is None:
            max_per_week = DEFAULT_MAX_NEW_SKILLS_PER_WEEK
        if max_per_week < 0:
            raise ValueError("max_per_week must be 0 or more")
        self.max_per_week = max_per_week
        self._drafted_by_week: dict[str, int] = {}

    def drafted_this_week(self, now: datetime | None = None) -> int:
        """New skills drafted in the week of ``now``."""
        return self._drafted_by_week.get(week_key(now), 0)

    def remaining_this_week(self, now: datetime | None = None) -> int:
        """New skills still draftable in the week of ``now``."""
        return max(0, self.max_per_week - self.drafted_this_week(now))

    def record(self, now: datetime | None = None) -> int:
        """Count one new skill for the week of ``now``.

        Returns the new count for the week. Raises ValueError
        when the week is already at the cap.
        """
        key = week_key(now)
        if self._drafted_by_week.get(key, 0) >= self.max_per_week:
            raise ValueError(
                f"weekly learned-skill cap reached: {self.max_per_week} "
                f"new skills per week (week {key})"
            )
        self._drafted_by_week[key] = self._drafted_by_week.get(key, 0) + 1
        return self._drafted_by_week[key]


class DailyCaps:
    """Per-user and global daily caps for curriculum work.

    Counts and spend are keyed by ISO calendar day (UTC) and
    kept in memory. One instance is shared by passing it to
    every runner; that is what makes the per-user and global
    caps hold across runner instances. A task may start only
    when both its user caps and the global caps still have
    room for the whole per-task ceiling (reservation): the
    check is ``spent + cost_ceiling_rp <= spend cap``, so
    recorded spend can never pass a cap even when a task
    spends its full ceiling.
    """

    def __init__(
        self,
        *,
        max_tasks_per_user_per_day: int | None = None,
        max_spend_rp_per_user_per_day: int | None = None,
        max_tasks_global_per_day: int | None = None,
        max_spend_rp_global_per_day: int | None = None,
    ):
        if max_tasks_per_user_per_day is None:
            max_tasks_per_user_per_day = DEFAULT_MAX_TASKS_PER_USER_PER_DAY
        if max_spend_rp_per_user_per_day is None:
            max_spend_rp_per_user_per_day = DEFAULT_MAX_SPEND_RP_PER_USER_PER_DAY
        if max_tasks_global_per_day is None:
            max_tasks_global_per_day = DEFAULT_MAX_TASKS_GLOBAL_PER_DAY
        if max_spend_rp_global_per_day is None:
            max_spend_rp_global_per_day = DEFAULT_MAX_SPEND_RP_GLOBAL_PER_DAY
        for name, value in (
            ("max_tasks_per_user_per_day", max_tasks_per_user_per_day),
            ("max_spend_rp_per_user_per_day", max_spend_rp_per_user_per_day),
            ("max_tasks_global_per_day", max_tasks_global_per_day),
            ("max_spend_rp_global_per_day", max_spend_rp_global_per_day),
        ):
            if value < 0:
                raise ValueError(f"{name} must be 0 or more")
        self.max_tasks_per_user_per_day = max_tasks_per_user_per_day
        self.max_spend_rp_per_user_per_day = max_spend_rp_per_user_per_day
        self.max_tasks_global_per_day = max_tasks_global_per_day
        self.max_spend_rp_global_per_day = max_spend_rp_global_per_day
        self._runs_by_user_day: dict[tuple[str, str], int] = {}
        self._spend_by_user_day: dict[tuple[str, str], int] = {}
        self._runs_by_day: dict[str, int] = {}
        self._spend_by_day: dict[str, int] = {}

    def runs_today(self, user_id: str, day: str | None = None) -> int:
        """Learning tasks already run for ``user_id`` on ``day``."""
        if day is None:
            day = datetime.now(UTC).date().isoformat()
        return self._runs_by_user_day.get((str(user_id), day), 0)

    def spent_today(self, user_id: str, day: str | None = None) -> int:
        """Learning rupiah already spent for ``user_id`` on ``day``."""
        if day is None:
            day = datetime.now(UTC).date().isoformat()
        return self._spend_by_user_day.get((str(user_id), day), 0)

    def global_runs_today(self, day: str | None = None) -> int:
        """Learning tasks already run for all users on ``day``."""
        if day is None:
            day = datetime.now(UTC).date().isoformat()
        return self._runs_by_day.get(day, 0)

    def global_spent_today(self, day: str | None = None) -> int:
        """Learning rupiah already spent for all users on ``day``."""
        if day is None:
            day = datetime.now(UTC).date().isoformat()
        return self._spend_by_day.get(day, 0)

    def check(self, user_id: str, *, day: str | None = None, cost_ceiling_rp: int = 0) -> dict:
        """Whether a learning task may start for ``user_id`` on ``day``.

        Returns ``{"allowed", "status", "reason"}``. ``status``
        is "ok" when the task may start, otherwise the cap that
        blocks it. The spend checks reserve the whole
        per-task ceiling, so a task that would spend up to its
        ceiling can never push the day past its spend cap.
        """
        if day is None:
            day = datetime.now(UTC).date().isoformat()
        if cost_ceiling_rp < 0:
            raise ValueError("cost_ceiling_rp must be 0 or more")
        user_id = str(user_id)
        if self.runs_today(user_id, day) >= self.max_tasks_per_user_per_day:
            return {
                "allowed": False,
                "status": "user_daily_cap_reached",
                "reason": (
                    f"user daily learning task cap reached "
                    f"({self.max_tasks_per_user_per_day} tasks/day)"
                ),
            }
        if self.spent_today(user_id, day) + cost_ceiling_rp > self.max_spend_rp_per_user_per_day:
            return {
                "allowed": False,
                "status": "user_daily_spend_reached",
                "reason": (
                    f"user daily learning spend cap reached "
                    f"(Rp{self.max_spend_rp_per_user_per_day:,}/day)"
                ),
            }
        if self.global_runs_today(day) >= self.max_tasks_global_per_day:
            return {
                "allowed": False,
                "status": "global_daily_cap_reached",
                "reason": (
                    f"global daily learning task cap reached "
                    f"({self.max_tasks_global_per_day} tasks/day)"
                ),
            }
        if self.global_spent_today(day) + cost_ceiling_rp > self.max_spend_rp_global_per_day:
            return {
                "allowed": False,
                "status": "global_daily_spend_reached",
                "reason": (
                    f"global daily learning spend cap reached "
                    f"(Rp{self.max_spend_rp_global_per_day:,}/day)"
                ),
            }
        return {"allowed": True, "status": "ok", "reason": "caps allow this task"}

    def record(self, user_id: str, *, day: str | None = None, cost_rp: int = 0) -> dict:
        """Record one finished learning task and its cost.

        Call once per task that actually ran, whatever its
        outcome: a failed task still spent its cost. Returns
        the new per-user and global totals for the day.
        """
        if day is None:
            day = datetime.now(UTC).date().isoformat()
        if cost_rp < 0:
            raise ValueError("cost_rp must be 0 or more")
        user_id = str(user_id)
        user_key = (user_id, day)
        self._runs_by_user_day[user_key] = self._runs_by_user_day.get(user_key, 0) + 1
        self._spend_by_user_day[user_key] = self._spend_by_user_day.get(user_key, 0) + cost_rp
        self._runs_by_day[day] = self._runs_by_day.get(day, 0) + 1
        self._spend_by_day[day] = self._spend_by_day.get(day, 0) + cost_rp
        return {
            "user_runs": self._runs_by_user_day[user_key],
            "user_spent_rp": self._spend_by_user_day[user_key],
            "global_runs": self._runs_by_day[day],
            "global_spent_rp": self._spend_by_day[day],
        }
