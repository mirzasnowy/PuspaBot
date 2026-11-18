"""Sentinel detectors (G12).

Five pure detectors over ledger events — the same five DuskBot's
sentinel runs, rewritten for Puspa's ledger shapes:

- ``tool_error`` — a tool failing repeatedly (``tool_completed``
  with ``content.status == "error"``, the shape
  ``puspa/chassis/tools.py`` logs).
- ``turn_stop`` — a task that stopped (``task_stopped``), or a
  task that stalled: created, never reached a terminal event, and
  silent for longer than ``stalled_hours``. A ``task_stopped``
  with ``content.deleted == True`` is a tombstone (see
  ``docs/contracts.md``), not a stopped turn, and is excluded.
- ``queue_wait`` — a trigger that missed (``trigger_missed``), or
  a trigger whose task started later than ``queue_wait_minutes``
  after the trigger fired, or never started. ``trigger_fired``
  events carry ``content.trigger_id`` and ``content.task_id``
  (the shape ``puspa/scheduler/scheduler.py`` logs), so the
  wait is measured trigger -> ``task_created`` with the same
  ``task_id``.
- ``cost`` — one task whose summed ``content.cost_rp`` (the key
  ``puspa/runtime/budget.py`` records) reaches ``cost_usd`` in
  the lookback window.
- ``retry_loop`` — the same tool called ``retry_min`` times in
  one task without a single success in between.

Detectors are pure functions: they take event dicts (the shape
``puspa/ledger/ledger.py`` returns), a thresholds object, and a
clock, and return issue dicts. They never touch the database,
the state file, or the network, so they are testable with
synthetic events and reusable by the independent verifier
(``verifier.py``), which re-runs exactly one detector per issue.

An issue dict carries a stable ``key`` (``<detector>:<scope>``),
so the same condition found on two scans is the same issue.
``first_seen`` / ``last_seen`` are ISO-8601 UTC strings taken
from the evidence events, never from the scan clock.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from puspa.sentinel.thresholds import SentinelThresholds

__all__ = [
    "DETECTORS",
    "TERMINAL_EVENT_TYPES",
    "detect_cost_overruns",
    "detect_queue_waits",
    "detect_retry_loops",
    "detect_tool_errors",
    "detect_turn_stops",
    "run_detectors",
]

#: Event types after which a task is finished (no further activity
#: expected). A stalled task is one that never reached any of these.
TERMINAL_EVENT_TYPES = frozenset({"task_completed", "task_failed", "task_stopped"})

#: Detector names, in the order ``run_detectors`` runs them.
DETECTORS = ("tool_error", "turn_stop", "queue_wait", "cost", "retry_loop")


def _as_utc(moment: datetime) -> datetime:
    """Treat a naive datetime as UTC; leave an aware one alone."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _iso(moment: datetime) -> str:
    return _as_utc(moment).isoformat()


def _in_lookback(events: list[dict], *, now: datetime, lookback_days: int) -> list[dict]:
    """Events inside the lookback window, in insertion order."""
    cutoff = _as_utc(now) - timedelta(days=lookback_days)
    kept = []
    for event in events:
        created_at = event.get("created_at")
        if not isinstance(created_at, datetime):
            continue
        if _as_utc(created_at) >= cutoff:
            kept.append(event)
    return kept


def _content(event: dict) -> dict:
    content = event.get("content")
    return content if isinstance(content, dict) else {}


def _issue(
    *,
    detector: str,
    key: str,
    severity: str,
    summary: str,
    task_id: str | None,
    evidence: dict,
    first_seen: datetime,
    last_seen: datetime,
) -> dict:
    return {
        "detector": detector,
        "key": key,
        "severity": severity,
        "summary": summary,
        "task_id": task_id,
        "evidence": evidence,
        "first_seen": _iso(first_seen),
        "last_seen": _iso(last_seen),
    }


def detect_tool_errors(
    events: list[dict], *, thresholds: SentinelThresholds | None = None, now: datetime | None = None
) -> list[dict]:
    """One issue per tool failing ``tool_error_min`` times in the window."""
    thresholds = thresholds or SentinelThresholds()
    now = _as_utc(now or datetime.now(UTC))
    window = _in_lookback(events, now=now, lookback_days=thresholds.lookback_days)
    by_tool: dict[str, list[dict]] = {}
    for event in window:
        if event.get("type") != "tool_completed":
            continue
        content = _content(event)
        if content.get("status") != "error":
            continue
        tool = str(content.get("tool") or "?")
        by_tool.setdefault(tool, []).append(event)
    issues = []
    for tool in sorted(by_tool):
        group = by_tool[tool]
        if len(group) < thresholds.tool_error_min:
            continue
        task_ids = sorted({str(e.get("task_id")) for e in group if e.get("task_id")})
        issues.append(
            _issue(
                detector="tool_error",
                key=f"tool_error:{tool}",
                severity="warning",
                summary=f"Tool {tool} failed {len(group)} times in the last "
                f"{thresholds.lookback_days} days",
                task_id=None,
                evidence={
                    "tool": tool,
                    "error_count": len(group),
                    "task_ids": task_ids[:10],
                    "event_ids": [str(e.get("id")) for e in group[:10]],
                },
                first_seen=min(e["created_at"] for e in group),
                last_seen=max(e["created_at"] for e in group),
            )
        )
    return issues


def detect_turn_stops(
    events: list[dict], *, thresholds: SentinelThresholds | None = None, now: datetime | None = None
) -> list[dict]:
    """One issue per stopped task, plus one per stalled task.

    Stopped: a ``task_stopped`` event that is not a delete
    tombstone. Stalled: a ``task_created`` with no terminal event
    and no activity for longer than ``stalled_hours``.
    """
    thresholds = thresholds or SentinelThresholds()
    now = _as_utc(now or datetime.now(UTC))
    window = _in_lookback(events, now=now, lookback_days=thresholds.lookback_days)
    issues: list[dict] = []
    stopped: dict[str, dict] = {}
    created: dict[str, dict] = {}
    last_activity: dict[str, datetime] = {}
    terminal: set[str] = set()
    for event in window:
        task_id = event.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            continue
        created_at = event.get("created_at")
        if not isinstance(created_at, datetime):
            continue
        created_at = _as_utc(created_at)
        previous = last_activity.get(task_id)
        if previous is None or created_at > previous:
            last_activity[task_id] = created_at
        event_type = event.get("type")
        if event_type == "task_created":
            created.setdefault(task_id, event)
        elif event_type == "task_stopped":
            if not _content(event).get("deleted"):
                stopped.setdefault(task_id, event)
            terminal.add(task_id)
        elif event_type in TERMINAL_EVENT_TYPES:
            terminal.add(task_id)
    for task_id in sorted(stopped):
        event = stopped[task_id]
        issues.append(
            _issue(
                detector="turn_stop",
                key=f"turn_stop:{task_id}",
                severity="warning",
                summary=f"Task {task_id} stopped without completing",
                task_id=task_id,
                evidence={
                    "task_id": task_id,
                    "stopped_at": _iso(event["created_at"]),
                    "event_id": str(event.get("id")),
                },
                first_seen=event["created_at"],
                last_seen=event["created_at"],
            )
        )
    stalled_cutoff = now - timedelta(hours=thresholds.stalled_hours)
    for task_id in sorted(created):
        if task_id in terminal or task_id in stopped:
            continue
        last = last_activity.get(task_id)
        if last is None or last > stalled_cutoff:
            continue
        first = created[task_id]
        idle_hours = (now - last).total_seconds() / 3600
        issues.append(
            _issue(
                detector="turn_stop",
                key=f"turn_stall:{task_id}",
                severity="warning",
                summary=f"Task {task_id} stalled: no terminal event, "
                f"silent for {idle_hours:.1f} hours",
                task_id=task_id,
                evidence={
                    "task_id": task_id,
                    "created_at": _iso(first["created_at"]),
                    "last_activity_at": _iso(last),
                    "idle_hours": round(idle_hours, 1),
                },
                first_seen=first["created_at"],
                last_seen=last,
            )
        )
    return issues


def detect_queue_waits(
    events: list[dict], *, thresholds: SentinelThresholds | None = None, now: datetime | None = None
) -> list[dict]:
    """One issue per missed trigger and per trigger whose task was late.

    A ``trigger_missed`` event is an issue on its own. For a
    ``trigger_fired`` event, the wait is measured to the
    ``task_created`` event carrying the same ``task_id``; a wait
    longer than ``queue_wait_minutes``, or no ``task_created`` at
    all by scan time, is an issue.
    """
    thresholds = thresholds or SentinelThresholds()
    now = _as_utc(now or datetime.now(UTC))
    window = _in_lookback(events, now=now, lookback_days=thresholds.lookback_days)
    wait_limit = timedelta(minutes=thresholds.queue_wait_minutes)
    created_at_by_task: dict[str, datetime] = {}
    for event in events:
        if event.get("type") != "task_created":
            continue
        task_id = event.get("task_id")
        created_at = event.get("created_at")
        if isinstance(task_id, str) and isinstance(created_at, datetime):
            created_at_by_task[task_id] = _as_utc(created_at)
    issues: list[dict] = []
    seen_keys: set[str] = set()
    for event in window:
        event_type = event.get("type")
        content = _content(event)
        fired_at = event.get("created_at")
        if not isinstance(fired_at, datetime):
            continue
        fired_at = _as_utc(fired_at)
        if event_type == "trigger_missed":
            trigger_id = str(content.get("trigger_id") or event.get("task_id") or "?")
            key = f"queue_wait:missed:{trigger_id}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            issues.append(
                _issue(
                    detector="queue_wait",
                    key=key,
                    severity="warning",
                    summary=f"Trigger {trigger_id} missed its run",
                    task_id=str(content.get("task_id")) if content.get("task_id") else None,
                    evidence={
                        "trigger_id": trigger_id,
                        "missed_at": _iso(fired_at),
                        "event_id": str(event.get("id")),
                    },
                    first_seen=fired_at,
                    last_seen=fired_at,
                )
            )
        elif event_type == "trigger_fired":
            trigger_id = str(content.get("trigger_id") or "?")
            task_id = content.get("task_id")
            key = f"queue_wait:{trigger_id}"
            if key in seen_keys:
                continue
            started = created_at_by_task.get(task_id) if isinstance(task_id, str) else None
            if started is None:
                if now - fired_at < wait_limit:
                    continue
                seen_keys.add(key)
                issues.append(
                    _issue(
                        detector="queue_wait",
                        key=key,
                        severity="warning",
                        summary=f"Trigger {trigger_id} fired but its task never started",
                        task_id=str(task_id) if task_id else None,
                        evidence={
                            "trigger_id": trigger_id,
                            "fired_at": _iso(fired_at),
                            "wait_seconds": round((now - fired_at).total_seconds(), 1),
                            "event_id": str(event.get("id")),
                        },
                        first_seen=fired_at,
                        last_seen=fired_at,
                    )
                )
            elif started - fired_at > wait_limit:
                seen_keys.add(key)
                wait_seconds = (started - fired_at).total_seconds()
                issues.append(
                    _issue(
                        detector="queue_wait",
                        key=key,
                        severity="warning",
                        summary=f"Trigger {trigger_id} waited {wait_seconds:.0f}s "
                        f"for its task (limit {thresholds.queue_wait_minutes:.0f}m)",
                        task_id=str(task_id),
                        evidence={
                            "trigger_id": trigger_id,
                            "fired_at": _iso(fired_at),
                            "started_at": _iso(started),
                            "wait_seconds": round(wait_seconds, 1),
                            "event_id": str(event.get("id")),
                        },
                        first_seen=fired_at,
                        last_seen=started,
                    )
                )
    return issues


def detect_cost_overruns(
    events: list[dict], *, thresholds: SentinelThresholds | None = None, now: datetime | None = None
) -> list[dict]:
    """One issue per task whose cost in the window reaches ``cost_usd``."""
    thresholds = thresholds or SentinelThresholds()
    now = _as_utc(now or datetime.now(UTC))
    window = _in_lookback(events, now=now, lookback_days=thresholds.lookback_days)
    totals: dict[str, float] = {}
    spans: dict[str, list[datetime]] = {}
    for event in window:
        cost = _content(event).get("cost_rp")
        if not isinstance(cost, (int, float)):
            continue
        task_id = event.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            continue
        created_at = event.get("created_at")
        if not isinstance(created_at, datetime):
            continue
        totals[task_id] = totals.get(task_id, 0.0) + float(cost)
        spans.setdefault(task_id, []).append(_as_utc(created_at))
    issues = []
    for task_id in sorted(totals):
        cost_rp = totals[task_id]
        cost_usd = cost_rp / thresholds.idr_per_usd
        if cost_usd < thresholds.cost_usd:
            continue
        moments = spans[task_id]
        issues.append(
            _issue(
                detector="cost",
                key=f"cost:{task_id}",
                severity="critical",
                summary=f"Task {task_id} cost ${cost_usd:.4f} "
                f"(Rp{cost_rp:,.0f}) in the last {thresholds.lookback_days} days",
                task_id=task_id,
                evidence={
                    "task_id": task_id,
                    "cost_rp": cost_rp,
                    "cost_usd": round(cost_usd, 4),
                    "threshold_usd": thresholds.cost_usd,
                },
                first_seen=min(moments),
                last_seen=max(moments),
            )
        )
    return issues


def detect_retry_loops(
    events: list[dict], *, thresholds: SentinelThresholds | None = None, now: datetime | None = None
) -> list[dict]:
    """One issue per (task, tool) called ``retry_min`` times with no success.

    The run counts consecutive ``tool_called`` events for the same
    tool where every completed call in the run ended in error; a
    single ``status == "ok"`` completion resets the run to zero.
    """
    thresholds = thresholds or SentinelThresholds()
    now = _as_utc(now or datetime.now(UTC))
    window = _in_lookback(events, now=now, lookback_days=thresholds.lookback_days)
    runs: dict[tuple[str, str], int] = {}
    run_started: dict[tuple[str, str], datetime] = {}
    worst: dict[tuple[str, str], dict] = {}
    for event in window:
        event_type = event.get("type")
        if event_type not in ("tool_called", "tool_completed"):
            continue
        content = _content(event)
        tool = str(content.get("tool") or "?")
        task_id = event.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            continue
        scope = (task_id, tool)
        created_at = event.get("created_at")
        if not isinstance(created_at, datetime):
            continue
        created_at = _as_utc(created_at)
        if event_type == "tool_called":
            if runs.get(scope, 0) == 0:
                run_started[scope] = created_at
            runs[scope] = runs.get(scope, 0) + 1
        elif content.get("status") == "ok":
            runs[scope] = 0
            run_started.pop(scope, None)
            # An error completion leaves the run as it is: the
            # calls already counted are the failed attempts.
        current = runs.get(scope, 0)
        best = worst.get(scope)
        if best is None or current > best["attempts"]:
            worst[scope] = {
                "attempts": current,
                "first_seen": run_started.get(scope, created_at),
                "last_seen": created_at,
                "event_id": str(event.get("id")),
            }
    issues = []
    for task_id, tool in sorted(worst):
        record = worst[(task_id, tool)]
        if record["attempts"] < thresholds.retry_min:
            continue
        issues.append(
            _issue(
                detector="retry_loop",
                key=f"retry_loop:{task_id}:{tool}",
                severity="warning",
                summary=f"Tool {tool} retried {record['attempts']} times "
                f"in task {task_id} without success",
                task_id=task_id,
                evidence={
                    "task_id": task_id,
                    "tool": tool,
                    "attempts": record["attempts"],
                    "threshold": thresholds.retry_min,
                    "event_id": record["event_id"],
                },
                first_seen=record["first_seen"],
                last_seen=record["last_seen"],
            )
        )
    return issues


def run_detectors(
    events: list[dict], *, thresholds: SentinelThresholds | None = None, now: datetime | None = None
) -> list[dict]:
    """Run all five detectors over the events, in detector order."""
    thresholds = thresholds or SentinelThresholds()
    now = _as_utc(now or datetime.now(UTC))
    found: list[dict] = []
    found.extend(detect_tool_errors(events, thresholds=thresholds, now=now))
    found.extend(detect_turn_stops(events, thresholds=thresholds, now=now))
    found.extend(detect_queue_waits(events, thresholds=thresholds, now=now))
    found.extend(detect_cost_overruns(events, thresholds=thresholds, now=now))
    found.extend(detect_retry_loops(events, thresholds=thresholds, now=now))
    return found
