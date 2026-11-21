"""Sentinel: self-observability for Puspa (G12).

Pattern studied (rewritten, not copied):

- DuskBot ``server/sentinel/`` (PolyForm Noncommercial, study
  only): a periodic scanner over the server's own records with
  five detectors (toolError, turnStop, queueWait, cost, retryLoop),
  default thresholds, and a state file
  (``readState/readIssues/markCounted/upsertIssues/pruneSeen/
  writeSentinelFiles``). ``server/decision-log.ts``: every decision
  recorded (actor, source, via) into daily files with 180-day
  retention. ``server/usage-trace/``: span trees. Anonymous
  analytics + crash reports that can be switched off.
- NousResearch/hermes-agent (MIT): ``hermes_logging.py``,
  ``hermes_startup_watchdog.py``, ``agent/usage_*.py``,
  ``agent/insights.py`` (session insights from ``state.db``: tokens,
  cost estimates with sub-cent labels at 4 decimals, tool/skill
  usage, activity, model/platform breakdown), ``batch_runner.py``
  (batch trajectory runs over a prompt dataset with checkpoints
  and resume), ``agent/trajectory.py`` (trajectory storage +
  redaction before saving), ``trajectory-format.md`` (ShareGPT
  format for tool-calling training data).

Puspa's implementation, in Puspa's own code, built on top of what
already exists here:

- Detectors read ledger events (``puspa/ledger/``) — the same
  hash-chained record the rest of Puspa already writes — plus the
  learning loop's correction records where relevant. Nothing is
  re-recorded: the sentinel only reads.
- The decision log is daily JSONL files (``decision_log.py``),
  separate from the in-memory ``puspa.decider.log.DecisionLog``
  (G9), which stays in-memory by design.
- The trajectory runner runs on top of the existing eval harness
  (``puspa/evidence/eval_harness.py``), it does not replace it.
- Session insights read the ledger and the G26 session database
  (``state.db``), they do not build a second store.

K8 fold (independent verifier): the sentinel's detectors are the
verifier's eyes, but the verifier module (``verifier.py``) is
separate from the detector module (``detectors.py``) so the two
can evolve independently. A detector finds a candidate; only the
verifier confirms it, by re-reading fresh data and re-running the
single detector for that issue's scope.

Verification discipline: this package follows
``docs/verification.md`` — isolated fixtures, never verify a
mutation against live user data. The sentinel itself only ever
reads the ledger; its state, decision log, traces, and analytics
files live under a dedicated home directory, never in the user's
live data.
"""

from __future__ import annotations

from puspa.sentinel.analytics import Analytics
from puspa.sentinel.decision_log import DailyDecisionLog
from puspa.sentinel.detectors import (
    DETECTORS,
    detect_cost_overruns,
    detect_outcome_failures,
    detect_queue_waits,
    detect_retry_loops,
    detect_tool_errors,
    detect_turn_stops,
    run_detectors,
)
from puspa.sentinel.failure_taxonomy import (
    MAX_AUTO_REPAIR_ATTEMPTS,
    REPAIR_BY_CLASS,
    Classification,
    FailureClass,
    FailureEvent,
    FailureSignal,
    RepairAction,
    classify,
    from_late_failure,
)
from puspa.sentinel.insights import (
    SessionInsights,
    format_usd,
    insights_from_events,
    insights_from_state_db,
)
from puspa.sentinel.scanner import Sentinel, run_sentinel_scan
from puspa.sentinel.state import (
    acknowledge_issue,
    mark_counted,
    new_state,
    prune_seen,
    read_issues,
    read_state,
    resolve_issue,
    upsert_issues,
    write_sentinel_files,
)
from puspa.sentinel.thresholds import SentinelThresholds
from puspa.sentinel.traces import Span, Trace, trace_from_events
from puspa.sentinel.trajectory import (
    BatchTrajectoryRunner,
    Trajectory,
    TrajectoryStep,
    load_prompt_dataset,
    redact_trajectory,
)
from puspa.sentinel.verifier import VerificationVerdict, verify_issue

__all__ = [
    "DETECTORS",
    "MAX_AUTO_REPAIR_ATTEMPTS",
    "REPAIR_BY_CLASS",
    "Analytics",
    "BatchTrajectoryRunner",
    "Classification",
    "DailyDecisionLog",
    "FailureClass",
    "FailureEvent",
    "FailureSignal",
    "RepairAction",
    "Sentinel",
    "SentinelThresholds",
    "SessionInsights",
    "Span",
    "Trace",
    "Trajectory",
    "TrajectoryStep",
    "VerificationVerdict",
    "acknowledge_issue",
    "classify",
    "detect_cost_overruns",
    "detect_outcome_failures",
    "detect_queue_waits",
    "detect_retry_loops",
    "detect_tool_errors",
    "detect_turn_stops",
    "format_usd",
    "from_late_failure",
    "insights_from_events",
    "insights_from_state_db",
    "load_prompt_dataset",
    "mark_counted",
    "new_state",
    "prune_seen",
    "read_issues",
    "read_state",
    "redact_trajectory",
    "resolve_issue",
    "run_detectors",
    "run_sentinel_scan",
    "trace_from_events",
    "upsert_issues",
    "verify_issue",
    "write_sentinel_files",
]
