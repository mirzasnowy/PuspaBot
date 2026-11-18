"""Usage ledger: one row per turn, plus Battery view, forecast, and
the per-task cost ceiling policy.

A ``UsageRow`` records one agent turn: when, who, which bot/thread,
which driver/model, input/output/cached tokens, cost in Rupiah, what
triggered it, the outcome, steps, tools used, and tool errors. The
currency is always ``"IDR"``.

This ledger is the billing package's own table
(``billing_usage_rows``). It deliberately does NOT add event types to
the core causal ledger (``puspa/ledger``): that ledger's 31 event
types must stay identical in three places, and usage rows are billing
data, not causal events.

Two stores share one interface:
- ``MemoryUsageStore``: in-memory, for tests.
- ``PostgresUsageStore``: the same rows in Postgres (see
  ``create_schema`` / ``USAGE_SCHEMA_SQL``).

On top of the rows:
- ``battery_view``: totals for the last 7 days per bot/routine, each
  row's share of the total, and the cap.
- ``forecast_routines``: per routine, ``"frequent"`` (runs often) or
  ``"costly"`` (expensive per run) or both.
- ``task_budget_remaining``: the Rp2.000/task ceiling as a *policy*
  on top of the ledger (not part of the ledger itself). Its name and
  signature are a shared interface with the scheduler worker (G8):
  do not rename it.
- ``upsell_due``: the "invested" thresholds (3 bots / 50 messages /
  3 routines) plus a 10-day snooze after dismissal.
"""

from __future__ import annotations

import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .config import TASK_COST_CEILING_IDR, BillingConfig, default_config

UTC = timezone.utc
DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"

USAGE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS billing_usage_rows (
    id BIGSERIAL PRIMARY KEY,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    thread_id TEXT NOT NULL DEFAULT '',
    task_id TEXT NOT NULL DEFAULT '',
    driver TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    input_tokens BIGINT NOT NULL DEFAULT 0,
    output_tokens BIGINT NOT NULL DEFAULT 0,
    cached_tokens BIGINT NOT NULL DEFAULT 0,
    cost_idr BIGINT NOT NULL DEFAULT 0,
    trigger TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT '',
    steps INT NOT NULL DEFAULT 0,
    tools JSONB NOT NULL DEFAULT '[]',
    tool_errors INT NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'IDR',
    provider TEXT NOT NULL DEFAULT '',
    module_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_billing_usage_user_time
    ON billing_usage_rows (user_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_billing_usage_task
    ON billing_usage_rows (user_id, task_id);
CREATE INDEX IF NOT EXISTS idx_billing_usage_bot_time
    ON billing_usage_rows (bot_id, recorded_at);
"""


@dataclass
class UsageRow:
    """One agent turn, as recorded in the usage ledger."""

    user_id: str
    bot_id: str
    thread_id: str = ""
    task_id: str = ""
    driver: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_idr: int = 0
    trigger: str = ""
    outcome: str = ""
    steps: int = 0
    tools: list[str] = field(default_factory=list)
    tool_errors: int = 0
    currency: str = "IDR"
    provider: str = ""
    module_id: str = ""
    recorded_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.currency != "IDR":
            raise ValueError("usage ledger currency is always IDR")
        if self.cost_idr < 0:
            raise ValueError("cost_idr must be >= 0")
        for name in ("input_tokens", "output_tokens", "cached_tokens",
                     "steps", "tool_errors"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0")
        if self.recorded_at is None:
            self.recorded_at = datetime.now(UTC)

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "bot_id": self.bot_id,
            "thread_id": self.thread_id,
            "task_id": self.task_id,
            "driver": self.driver,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_tokens": self.cached_tokens,
            "cost_idr": self.cost_idr,
            "trigger": self.trigger,
            "outcome": self.outcome,
            "steps": self.steps,
            "tools": list(self.tools),
            "tool_errors": self.tool_errors,
            "currency": self.currency,
            "provider": self.provider,
            "module_id": self.module_id,
            "recorded_at": self.recorded_at.isoformat()
            if self.recorded_at else None,
        }


class MemoryUsageStore:
    """In-memory usage store (tests and local runs)."""

    def __init__(self) -> None:
        self.rows: list[UsageRow] = []

    def append(self, row: UsageRow) -> UsageRow:
        self.rows.append(row)
        return row

    def rows_for_user(self, user_id: str,
                      since: datetime | None = None) -> list[UsageRow]:
        rows = [r for r in self.rows if r.user_id == user_id]
        if since is not None:
            rows = [r for r in rows if r.recorded_at and r.recorded_at >= since]
        return rows

    def rows_for_task(self, user_id: str, task_id: str) -> list[UsageRow]:
        return [r for r in self.rows
                if r.user_id == user_id and r.task_id == task_id]

    def clear(self) -> None:
        self.rows.clear()


def create_schema(dsn: str | None = None) -> None:
    """Create the billing usage table (idempotent)."""
    import psycopg

    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    with psycopg.connect(dsn) as conn:
        conn.execute(USAGE_SCHEMA_SQL)
        conn.commit()


class PostgresUsageStore:
    """Postgres-backed usage store (table ``billing_usage_rows``)."""

    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
        create_schema(self.dsn)

    def append(self, row: UsageRow) -> UsageRow:
        import psycopg
        from psycopg.types.json import Jsonb

        with psycopg.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO billing_usage_rows"
                    " (recorded_at, user_id, bot_id, thread_id, task_id,"
                    "  driver, model, input_tokens, output_tokens,"
                    "  cached_tokens, cost_idr, trigger, outcome, steps,"
                    "  tools, tool_errors, currency, provider, module_id)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
                    "  %s, %s, %s, %s, %s, %s, %s, %s)",
                    (row.recorded_at, row.user_id, row.bot_id, row.thread_id,
                     row.task_id, row.driver, row.model, row.input_tokens,
                     row.output_tokens, row.cached_tokens, row.cost_idr,
                     row.trigger, row.outcome, row.steps, Jsonb(row.tools),
                     row.tool_errors, row.currency, row.provider,
                     row.module_id))
            conn.commit()
        return row

    @staticmethod
    def _from_db(record: dict) -> UsageRow:
        return UsageRow(
            user_id=record["user_id"], bot_id=record["bot_id"],
            thread_id=record["thread_id"], task_id=record["task_id"],
            driver=record["driver"], model=record["model"],
            input_tokens=record["input_tokens"],
            output_tokens=record["output_tokens"],
            cached_tokens=record["cached_tokens"],
            cost_idr=record["cost_idr"], trigger=record["trigger"],
            outcome=record["outcome"], steps=record["steps"],
            tools=list(record["tools"] or []),
            tool_errors=record["tool_errors"],
            currency=record["currency"], provider=record["provider"],
            module_id=record["module_id"],
            recorded_at=record["recorded_at"])

    def rows_for_user(self, user_id: str,
                      since: datetime | None = None) -> list[UsageRow]:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                if since is None:
                    cur.execute(
                        "SELECT * FROM billing_usage_rows"
                        " WHERE user_id = %s ORDER BY recorded_at",
                        (user_id,))
                else:
                    cur.execute(
                        "SELECT * FROM billing_usage_rows"
                        " WHERE user_id = %s AND recorded_at >= %s"
                        " ORDER BY recorded_at",
                        (user_id, since))
                return [self._from_db(r) for r in cur.fetchall()]

    def rows_for_task(self, user_id: str, task_id: str) -> list[UsageRow]:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM billing_usage_rows"
                    " WHERE user_id = %s AND task_id = %s ORDER BY recorded_at",
                    (user_id, task_id))
                return [self._from_db(r) for r in cur.fetchall()]


#: Process-wide in-memory store used when ``task_budget_remaining`` is
#: called without a DSN (the scheduler records turns in-process).
_DEFAULT_MEMORY_STORE = MemoryUsageStore()


def default_memory_store() -> MemoryUsageStore:
    """The shared in-process store (tests may append rows to it)."""
    return _DEFAULT_MEMORY_STORE


def task_budget_remaining(user_id: str, task_id: str, *,
                          cap_rp: int = TASK_COST_CEILING_IDR,
                          dsn: str | None = None,
                          store=None) -> int:
    """Rupiah left for one task: cap minus recorded usage for that task.

    Shared interface with the scheduler (G8): name and signature must
    not change. ``dsn`` selects the Postgres store; without it the
    in-process memory store is used. ``store`` is an explicit store
    (tests). Never returns a negative number.
    """
    if store is None:
        store = PostgresUsageStore(dsn) if dsn is not None else _DEFAULT_MEMORY_STORE
    spent = sum(row.cost_idr for row in store.rows_for_task(user_id, task_id))
    return max(0, cap_rp - spent)


def battery_view(rows: list[UsageRow], *, now: datetime | None = None,
                 days: int = 7, cap_idr: int | None = None) -> dict:
    """Battery view: totals for the last ``days`` days, per bot/routine.

    Each entry carries its total cost, its share of the total, and the
    cap. ``routine`` here is the row's ``trigger`` value (what fired
    the turn); bot totals group by ``bot_id``.
    """
    now = now or datetime.now(UTC)
    since = now - timedelta(days=days)
    recent = [r for r in rows if r.recorded_at and r.recorded_at >= since]
    total = sum(r.cost_idr for r in recent)
    per_bot: dict[str, int] = defaultdict(int)
    per_routine: dict[str, int] = defaultdict(int)
    for row in recent:
        per_bot[row.bot_id] += row.cost_idr
        per_routine[row.trigger or "manual"] += row.cost_idr

    def _entries(grouped: dict[str, int]) -> list[dict]:
        return [
            {"key": key, "total_idr": value,
             "share": (value / total) if total else 0.0}
            for key, value in sorted(grouped.items(),
                                     key=lambda kv: -kv[1])
        ]

    return {
        "window_days": days,
        "since": since.isoformat(),
        "total_idr": total,
        "turns": len(recent),
        "cap_idr": cap_idr,
        "over_cap": cap_idr is not None and total > cap_idr,
        "per_bot": _entries(per_bot),
        "per_routine": _entries(per_routine),
    }


def forecast_routines(rows: list[UsageRow], *,
                      frequent_min_runs: int = 5,
                      costly_min_avg_idr: int = 500) -> list[dict]:
    """Forecast per routine: ``"frequent"`` and/or ``"costly"``.

    A routine is frequent when it ran at least ``frequent_min_runs``
    times, costly when its average cost per run is at least
    ``costly_min_avg_idr``. Only routines matching at least one flag
    are returned, most expensive first.
    """
    runs: Counter = Counter()
    cost: Counter = Counter()
    for row in rows:
        key = row.trigger or "manual"
        runs[key] += 1
        cost[key] += row.cost_idr
    forecast = []
    for key in runs:
        avg = cost[key] // runs[key] if runs[key] else 0
        flags = []
        if runs[key] >= frequent_min_runs:
            flags.append("frequent")
        if avg >= costly_min_avg_idr:
            flags.append("costly")
        if flags:
            forecast.append({
                "routine": key, "runs": runs[key],
                "total_idr": cost[key], "avg_idr": avg, "flags": flags,
            })
    forecast.sort(key=lambda item: -item["total_idr"])
    return forecast


def upsell_due(*, bots: int, messages: int, routines: int,
               snoozed_until: datetime | None = None,
               now: datetime | None = None,
               config: BillingConfig | None = None) -> dict:
    """Should the upsell be shown? Invested thresholds + 10-day snooze.

    A user counts as invested at 3 bots, 50 messages, or 3 routines
    (any one is enough). A snooze set within the last 10 days
    suppresses the upsell.
    """
    cfg = config or default_config()
    now = now or datetime.now(UTC)
    invested = (
        bots >= cfg.invested_min_bots
        or messages >= cfg.invested_min_messages
        or routines >= cfg.invested_min_routines
    )
    snoozed = snoozed_until is not None and now < snoozed_until
    due = invested and not snoozed
    return {
        "due": due,
        "invested": invested,
        "snoozed": snoozed,
        "snooze_until": (now + timedelta(days=cfg.upsell_snooze_days)).isoformat()
        if due else None,
        "thresholds": {
            "bots": cfg.invested_min_bots,
            "messages": cfg.invested_min_messages,
            "routines": cfg.invested_min_routines,
        },
    }
