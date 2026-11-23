"""Local billing ports: budget, insights, and entitlements.

This module is the public side of the billing boundary. It defines
the three ports the hosted service implements against its real
credit ledger and that the local install implements against its
own Postgres database (or, where noted, in memory):

- the budget port — :func:`task_budget_remaining` over a usage
  store (:class:`MemoryUsageStore` for tests and local runs,
  :class:`PostgresUsageStore` for the ``billing_usage_rows`` table).
  The per-task cost ceiling is a policy on top of the ledger, not
  part of the ledger itself; it never returns a negative number.
- the insights port — :func:`insights_for_user` (plus the pure
  helpers :func:`validate_days`, :func:`format_idr`,
  :func:`insights_summary`). Showing a user their own usage is
  product surface; the ledger behind it stays hosted.
- the entitlement port — :class:`Entitlement` plus
  :func:`issue_trial_entitlement` and
  :func:`verify_entitlement` (HMAC-SHA256 over a canonical JSON
  of the entitlement fields, keyed by a server secret that never
  leaves the server). A trial is product surface; the server-side
  trial ledger stays hosted.

Store choice, stated plainly: when a caller passes a DSN, the
ports read the Postgres usage table (that is how the PWA screens
show real recorded turns); when no DSN is given, they use the
shared in-process memory store. Tests pass an explicit store.

The local implementations operate on Postgres through
:func:`puspa.accounts.ports.resolve_dsn`. The SQLite backend is a
separate future workstream: when it lands, these ports gain a
SQLite implementation behind the same names; nothing here
pretends SQLite is live today.

Deliberately NOT here: ``current_tier()`` / ``is_pro()``. The
design table lists them on the EntitlementPort, but no public
call site uses them — the trial surface only issues and verifies
entitlements — so they are not invented. If a call site needs
them later, they belong here.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta

from puspa.accounts.ports import resolve_dsn
from puspa.db import Jsonb, dict_row
from puspa.db import connect as _db_connect

from .config import TASK_COST_CEILING_IDR, BillingConfig, default_config

__all__ = [
    "Entitlement",
    "MemoryUsageStore",
    "PostgresUsageStore",
    "UsageRow",
    "default_memory_store",
    "entitlement_from_dict",
    "format_idr",
    "format_rate_per_1k_tokens",
    "insights_for_user",
    "insights_summary",
    "issue_entitlement",
    "issue_trial_entitlement",
    "task_budget_remaining",
    "validate_days",
    "verify_entitlement",
]

#: Longest window the insights API accepts (days). Longer windows
#: would re-read the whole ledger for a page nobody scrolls through.
MAX_DAYS = 90

#: How many of the most expensive tasks the insights report lists.
TOP_COSTLY_TASKS = 5


# ======================================================================
# Usage ledger (budget port)
# ======================================================================


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
        for name in ("input_tokens", "output_tokens", "cached_tokens", "steps", "tool_errors"):
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
            "recorded_at": self.recorded_at.isoformat() if self.recorded_at else None,
        }


class MemoryUsageStore:
    """In-memory usage store (tests and local runs)."""

    def __init__(self) -> None:
        self.rows: list[UsageRow] = []

    def append(self, row: UsageRow) -> UsageRow:
        self.rows.append(row)
        return row

    def rows_for_user(self, user_id: str, since: datetime | None = None) -> list[UsageRow]:
        rows = [r for r in self.rows if r.user_id == user_id]
        if since is not None:
            rows = [r for r in rows if r.recorded_at and r.recorded_at >= since]
        return rows

    def rows_for_task(self, user_id: str, task_id: str) -> list[UsageRow]:
        return [r for r in self.rows if r.user_id == user_id and r.task_id == task_id]

    def clear(self) -> None:
        self.rows.clear()


def create_usage_schema(dsn: str | None = None) -> None:
    """Apply the billing usage table (migration 0013_payments)."""
    from puspa.migrations import apply_migrations

    apply_migrations(resolve_dsn(dsn), target="0013")


class PostgresUsageStore:
    """Postgres-backed usage store (table ``billing_usage_rows``)."""

    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = resolve_dsn(dsn)
        create_usage_schema(self.dsn)

    def append(self, row: UsageRow) -> UsageRow:
        with _db_connect(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO billing_usage_rows"
                    " (recorded_at, user_id, bot_id, thread_id, task_id,"
                    "  driver, model, input_tokens, output_tokens,"
                    "  cached_tokens, cost_idr, trigger, outcome, steps,"
                    "  tools, tool_errors, currency, provider, module_id)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
                    "  %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        row.recorded_at,
                        row.user_id,
                        row.bot_id,
                        row.thread_id,
                        row.task_id,
                        row.driver,
                        row.model,
                        row.input_tokens,
                        row.output_tokens,
                        row.cached_tokens,
                        row.cost_idr,
                        row.trigger,
                        row.outcome,
                        row.steps,
                        Jsonb(row.tools),
                        row.tool_errors,
                        row.currency,
                        row.provider,
                        row.module_id,
                    ),
                )
            conn.commit()
        return row

    @staticmethod
    def _from_db(record: dict) -> UsageRow:
        return UsageRow(
            user_id=record["user_id"],
            bot_id=record["bot_id"],
            thread_id=record["thread_id"],
            task_id=record["task_id"],
            driver=record["driver"],
            model=record["model"],
            input_tokens=record["input_tokens"],
            output_tokens=record["output_tokens"],
            cached_tokens=record["cached_tokens"],
            cost_idr=record["cost_idr"],
            trigger=record["trigger"],
            outcome=record["outcome"],
            steps=record["steps"],
            tools=list(record["tools"] or []),
            tool_errors=record["tool_errors"],
            currency=record["currency"],
            provider=record["provider"],
            module_id=record["module_id"],
            recorded_at=record["recorded_at"],
        )

    def rows_for_user(self, user_id: str, since: datetime | None = None) -> list[UsageRow]:
        with _db_connect(self.dsn, row_factory=dict_row) as conn, conn.cursor() as cur:
            if since is None:
                cur.execute(
                    "SELECT * FROM billing_usage_rows WHERE user_id = %s ORDER BY recorded_at",
                    (user_id,),
                )
            else:
                cur.execute(
                    "SELECT * FROM billing_usage_rows"
                    " WHERE user_id = %s AND recorded_at >= %s"
                    " ORDER BY recorded_at",
                    (user_id, since),
                )
            return [self._from_db(r) for r in cur.fetchall()]

    def rows_for_task(self, user_id: str, task_id: str) -> list[UsageRow]:
        with _db_connect(self.dsn, row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM billing_usage_rows"
                " WHERE user_id = %s AND task_id = %s ORDER BY recorded_at",
                (user_id, task_id),
            )
            return [self._from_db(r) for r in cur.fetchall()]


#: Process-wide in-memory store used when a budget/insights call is
#: made without a DSN (the scheduler records turns in-process).
_DEFAULT_MEMORY_STORE = MemoryUsageStore()


def default_memory_store() -> MemoryUsageStore:
    """The shared in-process store (tests may append rows to it)."""
    return _DEFAULT_MEMORY_STORE


def task_budget_remaining(
    user_id: str,
    task_id: str,
    *,
    cap_rp: int = TASK_COST_CEILING_IDR,
    dsn: str | None = None,
    store=None,
) -> int:
    """Rupiah left for one task: cap minus recorded usage for that task.

    Shared interface with the scheduler: name and signature must
    not change. ``dsn`` selects the Postgres store; without it the
    in-process memory store is used. ``store`` is an explicit store
    (tests). Never returns a negative number.
    """
    if store is None:
        store = PostgresUsageStore(dsn) if dsn is not None else _DEFAULT_MEMORY_STORE
    spent = sum(row.cost_idr for row in store.rows_for_task(user_id, task_id))
    return max(0, cap_rp - spent)


# ======================================================================
# Insights (insights port)
# ======================================================================


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

    Thin store layer: reads the user's rows (``store`` overrides
    the store, for tests; a ``dsn`` selects the Postgres usage
    table, otherwise the shared in-memory store) and summarises
    them. A user with no recorded turns gets an honest all-zero
    report.
    """
    days = validate_days(days)
    now = now or datetime.now(UTC)
    if store is None:
        store = PostgresUsageStore(dsn) if dsn is not None else _DEFAULT_MEMORY_STORE
    rows = store.rows_for_user(user_id, since=now - timedelta(days=days))
    return insights_summary(rows, days=days, now=now)


# ======================================================================
# Entitlements (entitlement port)
# ======================================================================


@dataclass(frozen=True)
class Entitlement:
    """A signed grant of a tier to one user/machine."""

    user_id: str
    machine_id: str
    tier: str
    issued_at: datetime
    expires_at: datetime
    trial: bool = False
    clouds: tuple[str, ...] = ()
    signature: str = ""

    def to_dict(self, *, include_signature: bool = True) -> dict:
        data = {
            "user_id": self.user_id,
            "machine_id": self.machine_id,
            "tier": self.tier,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "trial": self.trial,
            "clouds": list(self.clouds),
        }
        if include_signature:
            data["signature"] = self.signature
        return data


def _canonical_payload(entitlement: Entitlement) -> bytes:
    """Canonical bytes that get signed (signature excluded)."""
    return json.dumps(
        entitlement.to_dict(include_signature=False),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sign(entitlement: Entitlement, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"), _canonical_payload(entitlement), hashlib.sha256
    ).hexdigest()


def issue_entitlement(
    *,
    user_id: str,
    machine_id: str,
    tier: str,
    secret: str,
    config: BillingConfig | None = None,
    valid_days: int = 30,
    now: datetime | None = None,
    trial: bool = False,
    clouds: tuple[str, ...] = (),
) -> Entitlement:
    """Issue a signed entitlement. Raises ``ValueError`` for an unknown tier."""
    cfg = config or default_config()
    try:
        cfg.tier(tier)
    except KeyError as exc:
        raise ValueError(f"unknown tier {tier!r}") from exc
    now = now or datetime.now(UTC)
    if trial:
        valid_days = cfg.trial_days
    if len(clouds) > cfg.max_clouds_per_account:
        raise ValueError(
            f"too many clouds: {len(clouds)} > max {cfg.max_clouds_per_account} per account"
        )
    unsigned = Entitlement(
        user_id=user_id,
        machine_id=machine_id,
        tier=tier,
        issued_at=now,
        expires_at=now + timedelta(days=valid_days),
        trial=trial,
        clouds=tuple(clouds),
    )
    return Entitlement(
        user_id=unsigned.user_id,
        machine_id=unsigned.machine_id,
        tier=unsigned.tier,
        issued_at=unsigned.issued_at,
        expires_at=unsigned.expires_at,
        trial=unsigned.trial,
        clouds=unsigned.clouds,
        signature=_sign(unsigned, secret),
    )


def issue_trial_entitlement(
    *,
    user_id: str,
    machine_id: str,
    tier: str,
    secret: str,
    config: BillingConfig | None = None,
    now: datetime | None = None,
) -> Entitlement:
    """Issue a trial entitlement: ``trial_days`` from config, trial flag set."""
    return issue_entitlement(
        user_id=user_id,
        machine_id=machine_id,
        tier=tier,
        secret=secret,
        config=config,
        now=now,
        trial=True,
    )


def verify_entitlement(
    entitlement: Entitlement,
    *,
    secret: str,
    config: BillingConfig | None = None,
    now: datetime | None = None,
) -> dict:
    """Verify an entitlement before it is used.

    Returns ``{"valid": bool, "status": ..., "reason": ...}`` where
    status is ``"active"``, ``"grace"``, ``"expired"``, or
    ``"invalid"``. The signature is checked first; an entitlement
    whose signature does not match is never trusted, whatever its
    dates say.
    """
    cfg = config or default_config()
    now = now or datetime.now(UTC)
    if not entitlement.signature:
        return {"valid": False, "status": "invalid", "reason": "missing_signature"}
    expected = _sign(entitlement, secret)
    if not hmac.compare_digest(entitlement.signature, expected):
        return {"valid": False, "status": "invalid", "reason": "bad_signature"}
    try:
        cfg.tier(entitlement.tier)
    except KeyError:
        return {"valid": False, "status": "invalid", "reason": "unknown_tier"}
    if len(entitlement.clouds) > cfg.max_clouds_per_account:
        return {"valid": False, "status": "invalid", "reason": "too_many_clouds"}
    if now <= entitlement.expires_at:
        return {"valid": True, "status": "active", "reason": None}
    grace_until = entitlement.expires_at + timedelta(days=cfg.grace_days)
    if now <= grace_until:
        return {"valid": True, "status": "grace", "reason": "expired_within_grace_period"}
    return {"valid": False, "status": "expired", "reason": "past_grace_period"}


def entitlement_from_dict(data: dict) -> Entitlement:
    """Rebuild an entitlement from a stored dict (signature included)."""
    return Entitlement(
        user_id=data["user_id"],
        machine_id=data["machine_id"],
        tier=data["tier"],
        issued_at=datetime.fromisoformat(data["issued_at"]),
        expires_at=datetime.fromisoformat(data["expires_at"]),
        trial=bool(data.get("trial", False)),
        clouds=tuple(data.get("clouds") or ()),
        signature=data.get("signature", ""),
    )
