"""Cost and token insights over the usage ledger (G28).

The insights engine reads the rows of ``puspa.billing.ledger``
(one row per agent turn: tokens, cost in Rupiah, model, driver,
tools, module) and totals them over the last N days: tokens in /
out / cached, cost, per-model and per-driver breakdowns, tool and
module usage, activity per day, and the most expensive tasks.

Honest limits, stated plainly:

- Costs are integer Rupiah from the ledger itself. There is no
  USD estimate anywhere in this module: the repo has no exchange
  rate source, so inventing a dollar figure would be fake data.
- Small costs are never hidden behind a "~$0.00" style label.
  Exact totals use :func:`format_idr` (``"Rp1.500"``); the only
  fractional figure is the average cost per 1k tokens, shown with
  exactly 4 decimals (``"Rp500.0000 / 1k tokens"``), because a per-1k
  rate is genuinely fractional. When there are no tokens at all
  the rate is null, not zero: zero would claim a price that was
  never measured.
- The window is the ledger only. Turns that were never recorded
  (for example turns run before the ledger existed) do not appear,
  and the report says so by counting only recorded turns.

Most functions here are pure functions over ``UsageRow`` lists so
they are testable without a database; :func:`insights_for_user`
is the thin Postgres layer on top.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, time, timedelta

from .config import TASK_COST_CEILING_IDR
from .ledger import PostgresUsageStore, UsageRow

#: Longest window the API accepts (days). Longer windows would
#: re-read the whole ledger for a page nobody scrolls through.
MAX_DAYS = 90

#: How many of the most expensive tasks the report lists.
TOP_COSTLY_TASKS = 5


def validate_days(days: int) -> int:
    """Validate a window length: an integer from 1 to ``MAX_DAYS``."""
    if isinstance(days, bool) or not isinstance(days, int):
        raise ValueError(f"days must be an integer, got {days!r}")
    if days < 1 or days > MAX_DAYS:
        raise ValueError(f"days must be between 1 and {MAX_DAYS}, got {days}")
    return days


def format_idr(amount_idr: int | float) -> str:
    """Format an integer Rupiah amount exactly, e.g. 1500 -> ``"Rp1.500"``.

    Never rounds a small amount away and never prefixes a "~":
    what the ledger recorded is what the label shows.
    """
    return f"Rp{round(amount_idr):,}".replace(",", ".")


def format_rate_per_1k_tokens(cost_idr: int, tokens: int) -> str | None:
    """Average cost per 1k tokens with 4 decimals, or None.

    Returns None when ``tokens`` is 0: no rate was ever measured,
    so no rate is claimed. Otherwise ``"Rp500.0000 / 1k tokens"`` --
    four decimals, never "~Rp0".
    """
    if tokens <= 0:
        return None
    rate = cost_idr * 1000 / tokens
    return f"Rp{rate:.4f} / 1k tokens"


def _tokens(row: UsageRow) -> int:
    return row.input_tokens + row.output_tokens + row.cached_tokens


def _breakdown(rows: list[UsageRow], key: str) -> list[dict]:
    """Per-key totals (``model`` or ``driver``), most expensive first."""
    total_cost = sum(row.cost_idr for row in rows)
    grouped: dict[str, dict] = defaultdict(lambda: {"turns": 0, "tokens": 0, "cost_idr": 0})
    for row in rows:
        name = getattr(row, key) or "unknown"
        entry = grouped[name]
        entry["turns"] += 1
        entry["tokens"] += _tokens(row)
        entry["cost_idr"] += row.cost_idr
    out = [
        {
            key: name,
            "turns": entry["turns"],
            "tokens": entry["tokens"],
            "cost_idr": entry["cost_idr"],
            "share": (entry["cost_idr"] / total_cost) if total_cost else 0.0,
        }
        for name, entry in grouped.items()
    ]
    out.sort(key=lambda item: -item["cost_idr"])
    return out


def insights_summary(rows: list[UsageRow], *, days: int = 7, now: datetime | None = None) -> dict:
    """Summarise usage rows over the last ``days`` days.

    Pure function: ``rows`` may include older rows, which are
    ignored, and rows may be unsorted. ``now`` defaults to the
    current UTC time. Activity covers every day in the window,
    including days with no recorded turns (zeros, not gaps).
    """
    days = validate_days(days)
    now = now or datetime.now(UTC)
    # Day-aligned window: the last ``days`` calendar days ending
    # today, so the activity chart and the totals cover the same
    # days. A timestamp window (now - days) would start mid-day
    # and leave the first chart day half empty.
    first_day = now.date() - timedelta(days=days - 1)
    since = datetime.combine(first_day, time.min, tzinfo=UTC)
    recent = [row for row in rows if row.recorded_at and row.recorded_at >= since]

    total_tokens = {
        "input": sum(row.input_tokens for row in recent),
        "output": sum(row.output_tokens for row in recent),
        "cached": sum(row.cached_tokens for row in recent),
    }
    total_tokens["total"] = total_tokens["input"] + total_tokens["output"] + total_tokens["cached"]
    total_cost = sum(row.cost_idr for row in recent)
    turns = len(recent)
    avg_per_turn = (total_cost / turns) if turns else 0

    tool_counts: Counter = Counter()
    for row in recent:
        tool_counts.update(row.tools or [])
    module_grouped: dict[str, dict] = defaultdict(lambda: {"turns": 0, "cost_idr": 0})
    for row in recent:
        entry = module_grouped[row.module_id or "unknown"]
        entry["turns"] += 1
        entry["cost_idr"] += row.cost_idr
    task_grouped: dict[str, dict] = defaultdict(lambda: {"turns": 0, "tokens": 0, "cost_idr": 0})
    for row in recent:
        if not row.task_id:
            continue
        entry = task_grouped[row.task_id]
        entry["turns"] += 1
        entry["tokens"] += _tokens(row)
        entry["cost_idr"] += row.cost_idr
    top_tasks = [
        {"task_id": task_id, **entry}
        for task_id, entry in sorted(task_grouped.items(), key=lambda kv: -kv[1]["cost_idr"])[
            :TOP_COSTLY_TASKS
        ]
    ]

    per_day: dict[str, dict] = {}
    for offset in range(days):
        day = (first_day + timedelta(days=offset)).isoformat()
        per_day[day] = {"date": day, "turns": 0, "tokens": 0, "cost_idr": 0}
    for row in recent:
        if row.recorded_at is None:
            continue  # Unreachable: `recent` only holds dated rows.
        day = row.recorded_at.date().isoformat()
        if day in per_day:
            per_day[day]["turns"] += 1
            per_day[day]["tokens"] += _tokens(row)
            per_day[day]["cost_idr"] += row.cost_idr

    return {
        "window_days": days,
        "since": since.isoformat(),
        "until": now.isoformat(),
        "turns": turns,
        "tokens": total_tokens,
        "cost": {
            "total_idr": total_cost,
            "label": format_idr(total_cost),
            "avg_per_turn_idr": avg_per_turn,
            "avg_per_turn_label": format_idr(avg_per_turn),
            "per_1k_tokens_idr": (
                (total_cost * 1000 / total_tokens["total"]) if total_tokens["total"] else None
            ),
            "per_1k_tokens_label": format_rate_per_1k_tokens(total_cost, total_tokens["total"]),
        },
        "per_model": _breakdown(recent, "model"),
        "per_driver": _breakdown(recent, "driver"),
        "tool_usage": [{"tool": tool, "uses": uses} for tool, uses in tool_counts.most_common()],
        "module_usage": [
            {"module_id": module_id, "turns": entry["turns"], "cost_idr": entry["cost_idr"]}
            for module_id, entry in sorted(
                module_grouped.items(), key=lambda kv: -kv[1]["cost_idr"]
            )
        ],
        "activity_per_day": [per_day[day] for day in sorted(per_day)],
        "top_costly_tasks": top_tasks,
        "tool_errors": sum(row.tool_errors for row in recent),
        "task_cost_ceiling_idr": TASK_COST_CEILING_IDR,
    }


def insights_for_user(
    user_id: str,
    *,
    days: int = 7,
    dsn: str | None = None,
    store=None,
    now: datetime | None = None,
) -> dict:
    """Insights for one user over the last ``days`` days.

    Thin Postgres layer: reads the user's rows from the ledger
    (``store`` overrides the store, for tests) and summarises them.
    A user with no recorded turns gets an honest all-zero report.
    """
    days = validate_days(days)
    now = now or datetime.now(UTC)
    if store is None:
        store = PostgresUsageStore(dsn)
    rows = store.rows_for_user(user_id, since=now - timedelta(days=days))
    return insights_summary(rows, days=days, now=now)
