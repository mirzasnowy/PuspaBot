"""Session insights (G12).

The hermes-agent ``agent/insights.py`` pattern, rewritten for
Puspa: a session-insights engine over the ledger and the G26
session database (``state.db``) — tokens, cost estimate, tool
and skill usage, activity, model/platform breakdown.

Cost labels: a sub-cent amount is labelled at 4 decimals
(``$0.0023``), never rounded to ``~$0.00`` — the hermes rule.
Amounts of $0.01 and above use 2 decimals. The estimate is
always labelled with the model and counter it came from, so a
number is never presented without its basis.

Token counting reuses ``puspa.router.compression.count_tokens``
(tiktoken ``cl100k_base``, chars/4 fallback) — the same counter
G19 uses. Cost uses the router catalog through
``puspa.router.router`` (cheapest reader model + ``compute_cost``,
Rupiah per 1,000 tokens), converted to dollars with the
configurable ``idr_per_usd`` rate. Both are injected seams
(``count_tokens`` / ``cost_model`` parameters) so tests never
need the real tokenizer or catalog.

What is NOT invented here: when a message carries no text, or
an event carries no cost, it contributes nothing — the report
says what was counted (``counter_name``) and leaves unknown
costs as ``None`` instead of guessing a price.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from puspa.sentinel.thresholds import SentinelThresholds

__all__ = [
    "SessionInsights",
    "format_usd",
    "insights_from_events",
    "insights_from_state_db",
]


def format_usd(amount_usd: float) -> str:
    """Format a dollar amount: 4 decimals below one cent, else 2.

    Sub-cent amounts are never shown as ``~$0.00`` — the label
    keeps 4 decimals so ``$0.0023`` stays ``$0.0023``.
    """
    if amount_usd < 0.01:
        return f"${amount_usd:.4f}"
    return f"${amount_usd:.2f}"


@dataclass
class SessionInsights:
    """Insights for one session scope (a task, or a whole state.db)."""

    scope: str
    message_counts: dict = field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_rp: int | None = None
    cost_usd: float | None = None
    cost_label: str = "unknown"
    cost_model: str | None = None
    counter_name: str = "tiktoken-cl100k_base"
    tool_usage: dict = field(default_factory=dict)
    activity_by_day: dict = field(default_factory=dict)
    model_breakdown: dict = field(default_factory=dict)
    sessions: int = 0

    @property
    def tokens_total(self) -> int:
        return self.tokens_in + self.tokens_out

    def to_dict(self) -> dict:
        return {
            "scope": self.scope,
            "message_counts": dict(self.message_counts),
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "tokens_total": self.tokens_total,
            "cost_rp": self.cost_rp,
            "cost_usd": self.cost_usd,
            "cost_label": self.cost_label,
            "cost_model": self.cost_model,
            "counter_name": self.counter_name,
            "tool_usage": dict(self.tool_usage),
            "activity_by_day": dict(self.activity_by_day),
            "model_breakdown": dict(self.model_breakdown),
            "sessions": self.sessions,
        }


def _default_cost_model(input_tokens: int, output_tokens: int) -> tuple[int, str]:
    """Cheapest reader model from the router catalog -> (Rp, model)."""
    from puspa.router.router import choose, compute_cost

    choice = choose("pembaca")
    if choice is None:
        raise ValueError("no reader model in the router catalog")
    return compute_cost(choice, input_tokens, output_tokens), choice.model


def _content(event: dict) -> dict:
    content = event.get("content")
    return content if isinstance(content, dict) else {}


def _message_text(content: dict) -> str:
    text = content.get("content")
    if isinstance(text, str):
        return text
    text = content.get("text")
    return text if isinstance(text, str) else ""


def insights_from_events(
    events: list[dict],
    *,
    scope: str = "ledger",
    thresholds: SentinelThresholds | None = None,
    count_tokens=None,
    cost_model=None,
) -> SessionInsights:
    """Insights over ledger events (optionally one task's events).

    Tokens come from message text (``user_message`` counts as
    input, ``agent_message`` as output). ``cost_rp`` sums the
    ``content.cost_rp`` the ledger already records; when no
    event carries a cost, the token-based estimate is used
    instead and labelled with its model. Tool usage counts
    ``tool_called`` per tool; activity counts events per day;
    the model breakdown reads ``content.model`` when an event
    carries one.
    """
    thresholds = thresholds or SentinelThresholds()
    if count_tokens is None:
        from puspa.router.compression import count_tokens as _count_tokens

        count_tokens = _count_tokens
    if cost_model is None:
        cost_model = _default_cost_model
    insights = SessionInsights(scope=scope)
    counted_with: set[str] = set()
    recorded_rp = 0
    has_recorded_cost = False
    for event in events:
        event_type = event.get("type")
        content = _content(event)
        created_at = event.get("created_at")
        if isinstance(created_at, datetime):
            day = created_at.date().isoformat()
            insights.activity_by_day[day] = insights.activity_by_day.get(day, 0) + 1
        cost = content.get("cost_rp")
        if isinstance(cost, (int, float)):
            recorded_rp += int(cost)
            has_recorded_cost = True
        model = content.get("model")
        if isinstance(model, str) and model:
            breakdown = insights.model_breakdown.setdefault(model, {"events": 0, "cost_rp": 0})
            breakdown["events"] += 1
            if isinstance(cost, (int, float)):
                breakdown["cost_rp"] += int(cost)
        if event_type in ("user_message", "agent_message"):
            insights.message_counts[event_type] = insights.message_counts.get(event_type, 0) + 1
            tokens, counter = count_tokens(_message_text(content))
            counted_with.add(counter)
            if event_type == "user_message":
                insights.tokens_in += tokens
            else:
                insights.tokens_out += tokens
        elif event_type == "tool_called":
            tool = str(content.get("tool") or "?")
            insights.tool_usage[tool] = insights.tool_usage.get(tool, 0) + 1
    if counted_with:
        insights.counter_name = min(counted_with)
    if has_recorded_cost:
        insights.cost_rp = recorded_rp
        insights.cost_usd = round(recorded_rp / thresholds.idr_per_usd, 4)
        insights.cost_label = format_usd(insights.cost_usd)
        insights.cost_model = "ledger cost_rp (recorded)"
    elif insights.tokens_total > 0:
        try:
            estimated_rp, model = cost_model(insights.tokens_in, insights.tokens_out)
        except Exception:
            estimated_rp, model = None, None
        if estimated_rp is not None:
            insights.cost_rp = int(estimated_rp)
            insights.cost_usd = round(estimated_rp / thresholds.idr_per_usd, 4)
            insights.cost_label = format_usd(insights.cost_usd)
            insights.cost_model = f"estimate via {model}"
    return insights


def insights_from_state_db(
    path: str | Path,
    *,
    thresholds: SentinelThresholds | None = None,
    count_tokens=None,
    cost_model=None,
) -> SessionInsights:
    """Insights over a G26 session database (``state.db``), read-only.

    Reads ``sessions`` (id, model) and ``messages`` (session_id,
    role, content, created_at) through a read-only SQLite
    connection (``mode=ro``), so insights can never write to the
    live session store. User messages count as input tokens,
    assistant messages as output; tool-role messages count as
    tool activity without a tool name (the table stores no tool
    name — the count is reported under ``tool_messages`` in the
    scope, not invented as a tool). Cost is the token-based
    estimate, labelled with its model.
    """
    thresholds = thresholds or SentinelThresholds()
    if count_tokens is None:
        from puspa.router.compression import count_tokens as _count_tokens

        count_tokens = _count_tokens
    if cost_model is None:
        cost_model = _default_cost_model
    path = Path(path)
    uri = f"file:{path}?mode=ro"
    insights = SessionInsights(scope=f"state.db:{path.name}")
    counted_with: set[str] = set()
    tool_messages = 0
    with sqlite3.connect(uri, uri=True) as conn:
        sessions = conn.execute("SELECT id, model FROM sessions").fetchall()
        insights.sessions = len(sessions)
        for _session_id, model in sessions:
            if isinstance(model, str) and model:
                insights.model_breakdown.setdefault(model, {"events": 0, "cost_rp": 0})
                insights.model_breakdown[model]["events"] += 1
        rows = conn.execute(
            "SELECT session_id, role, content, created_at FROM messages ORDER BY created_at"
        ).fetchall()
    for _session_id, role, content, created_at in rows:
        insights.message_counts[role] = insights.message_counts.get(role, 0) + 1
        if isinstance(created_at, str) and len(created_at) >= 10:
            day = created_at[:10]
            insights.activity_by_day[day] = insights.activity_by_day.get(day, 0) + 1
        if role == "tool":
            tool_messages += 1
            continue
        text = content if isinstance(content, str) else ""
        tokens, counter = count_tokens(text)
        counted_with.add(counter)
        if role == "user":
            insights.tokens_in += tokens
        elif role == "assistant":
            insights.tokens_out += tokens
    if tool_messages:
        insights.tool_usage["<tool messages>"] = tool_messages
    if counted_with:
        insights.counter_name = min(counted_with)
    if insights.tokens_total > 0:
        try:
            estimated_rp, model = cost_model(insights.tokens_in, insights.tokens_out)
        except Exception:
            estimated_rp, model = None, None
        if estimated_rp is not None:
            insights.cost_rp = int(estimated_rp)
            insights.cost_usd = round(estimated_rp / thresholds.idr_per_usd, 4)
            insights.cost_label = format_usd(insights.cost_usd)
            insights.cost_model = f"estimate via {model}"
    return insights
