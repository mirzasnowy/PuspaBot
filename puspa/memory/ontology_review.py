"""Daily sleep consolidation and monthly ontology review (Kosmos K9).

Two jobs, both recorded in the ledger:

- the daily sleep consolidation runs in the scheduler
  (``run_scheduled_job`` with event type ``"consolidation"``):
  old episodic traces are summarised, successful patterns are
  promoted to procedural memory, and stale causal hypotheses
  are pruned (``prune_stale_hypotheses`` below);
- the monthly ontology review (this module) re-reads the
  profile facts and causal hypotheses as one ontology and
  flags what does not hold together: contradictory facts,
  proposed facts that were never confirmed, hypotheses whose
  evidence pulls both ways, and inferred facts dominated by
  one value on thin evidence.

The review itself never corrects anything: every finding is a
flag for review, with a suggestion, never an automatic fix.
A finding is only a record in the ledger (a ``task_completed``
event with ``job == "ontology_review"``), so the monthly gate
(``reviewed_this_month``) can tell whether this month already
had its review.

The world model's dynamics re-derive from traces on read (K7
medium speed), so there is no separate "retraining" step to
run here — the consolidation pass is the retraining, in the
sense that the next forecast reads the consolidated traces.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from puspa.db import connect as _db_connect

from . import profile
from .schema import resolve_dsn

#: A proposed fact older than this was never confirmed.
STALE_FACT_DAYS = 90

#: A sampling-bias flag needs at least this many inferred facts
#: for one key, with one value holding at least this share of
#: them while resting on fewer than
#: ``SAMPLING_MIN_EVIDENCE_IDS`` distinct evidence events.
SAMPLING_MIN_FACTS = 5
SAMPLING_DOMINANT_SHARE = 0.8
SAMPLING_MIN_EVIDENCE_IDS = 3

#: System jobs record their ledger events under this agent id:
#: no human ran them, and no per-user agent row is required.
SYSTEM_AGENT_ID = "puspa"

FINDING_KINDS = (
    "contradictory_fact",
    "stale_fact",
    "hidden_variable",
    "sampling_bias",
)


def _require_aware(moment, name):
    if moment is not None and moment.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return moment


def _as_datetime(value):
    """A datetime, or an ISO string from a serialised hypothesis/fact dict."""
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise ValueError(f"not a datetime or ISO string: {value!r}")


def _is_active_hypothesis(hypothesis, now):
    """A hypothesis is live when its status is active and its TTL has not passed."""
    if hypothesis.get("status") != "active":
        return False
    expires_at = _as_datetime(hypothesis.get("expires_at"))
    return not (expires_at is not None and expires_at <= now)


def _finding(kind, severity, summary, evidence, suggestion):
    return {
        "kind": kind,
        "severity": severity,
        "summary": summary,
        "evidence": evidence,
        "suggestion": suggestion,
    }


def review_ontology(*, facts, hypotheses, now=None):
    """Review one ontology: profile facts plus causal hypotheses.

    ``facts`` are profile-fact dicts in the ``profile.list_facts``
    shape (``key``, ``value``, ``source``, ``status``,
    ``created_at``, ``event_id``, ``id``); ``hypotheses`` are
    causal-hypothesis dicts in the ``CausalLedger.list_all`` shape
    (``id``, ``cause``, ``effect``, ``condition``, ``status``,
    ``supporting``, ``contradicting``, ``expires_at``).

    Returns a list of findings, each a dict with ``kind``,
    ``severity`` (``"warning"`` or ``"info"``), ``summary``,
    ``evidence`` and ``suggestion``. The list is sorted by
    (kind, summary) so the output is deterministic. Nothing
    here writes anywhere: the review only flags.
    """
    now = _require_aware(now, "now") or datetime.now(UTC)
    findings = []

    active_facts = [f for f in (facts or []) if f.get("status") == "active"]
    by_key = {}
    for fact in active_facts:
        by_key.setdefault(str(fact.get("key") or ""), []).append(fact)
    for key in sorted(by_key):
        values = {str(f.get("value")) for f in by_key[key]}
        if len(values) > 1:
            findings.append(
                _finding(
                    "contradictory_fact",
                    "warning",
                    f"key {key!r} holds {len(values)} different active values",
                    sorted(
                        (
                            {"fact_id": str(f.get("id")), "value": str(f.get("value"))}
                            for f in by_key[key]
                        ),
                        key=lambda e: e["fact_id"],
                    ),
                    "Confirm which value is current; replace the others "
                    "instead of leaving them active.",
                )
            )

    stale_cutoff = now - timedelta(days=STALE_FACT_DAYS)
    for fact in sorted(facts or [], key=lambda f: str(f.get("id"))):
        if fact.get("status") != "proposed":
            continue
        created_at = _as_datetime(fact.get("created_at"))
        if created_at is None or created_at >= stale_cutoff:
            continue
        findings.append(
            _finding(
                "stale_fact",
                "info",
                f"proposed fact {str(fact.get('key'))!r} was never confirmed "
                f"in {STALE_FACT_DAYS}+ days",
                [
                    {
                        "fact_id": str(fact.get("id")),
                        "key": str(fact.get("key")),
                        "value": str(fact.get("value")),
                        "created_at": created_at.isoformat(),
                    }
                ],
                "Confirm it, or drop the proposal: a stale proposal "
                "keeps suggesting a fact nobody stands behind.",
            )
        )

    live = [h for h in (hypotheses or []) if _is_active_hypothesis(h, now)]
    for hypothesis in sorted(live, key=lambda h: str(h.get("id"))):
        supporting = int(hypothesis.get("supporting") or 0)
        contradicting = int(hypothesis.get("contradicting") or 0)
        if supporting >= 1 and contradicting >= 1:
            findings.append(
                _finding(
                    "hidden_variable",
                    "warning",
                    f"hypothesis {str(hypothesis.get('id'))!r} "
                    f"({hypothesis.get('cause')!s} -> {hypothesis.get('effect')!s}) "
                    "has both supporting and contradicting observations "
                    "under one condition",
                    [
                        {
                            "hypothesis_id": str(hypothesis.get("id")),
                            "cause": str(hypothesis.get("cause")),
                            "effect": str(hypothesis.get("effect")),
                            "condition": str(hypothesis.get("condition") or ""),
                            "supporting": supporting,
                            "contradicting": contradicting,
                        }
                    ],
                    "A hidden variable may decide when the claim holds: "
                    "split the condition, or look for the missing variable.",
                )
            )
    by_cause_condition = {}
    for hypothesis in live:
        by_cause_condition.setdefault(
            (str(hypothesis.get("cause")), str(hypothesis.get("condition") or "")),
            [],
        ).append(hypothesis)
    for cause, condition in sorted(by_cause_condition):
        group = by_cause_condition[(cause, condition)]
        effects = {str(h.get("effect")) for h in group}
        if len(effects) > 1:
            findings.append(
                _finding(
                    "hidden_variable",
                    "warning",
                    f"two live hypotheses for the same cause {cause!r} "
                    f"under the same condition {condition!r} "
                    "predict different effects",
                    [
                        {
                            "hypothesis_id": str(h.get("id")),
                            "effect": str(h.get("effect")),
                        }
                        for h in sorted(group, key=lambda h: str(h.get("id")))
                    ],
                    "One of them is wrong under this condition: find what "
                    "distinguishes the cases instead of keeping both.",
                )
            )

    inferred_by_key = {}
    for fact in active_facts:
        if fact.get("source") != "inferred":
            continue
        inferred_by_key.setdefault(str(fact.get("key") or ""), []).append(fact)
    for key in sorted(inferred_by_key):
        group = inferred_by_key[key]
        if len(group) < SAMPLING_MIN_FACTS:
            continue
        by_value = {}
        for fact in group:
            by_value.setdefault(str(fact.get("value")), []).append(fact)
        for value in sorted(by_value):
            holders = by_value[value]
            share = len(holders) / len(group)
            if share < SAMPLING_DOMINANT_SHARE:
                continue
            evidence_ids = {f.get("event_id") for f in holders if f.get("event_id")}
            if len(evidence_ids) >= SAMPLING_MIN_EVIDENCE_IDS:
                continue
            findings.append(
                _finding(
                    "sampling_bias",
                    "info",
                    f"inferred facts for key {key!r} are dominated by "
                    f"{value!r} ({len(holders)}/{len(group)}) on only "
                    f"{len(evidence_ids)} distinct evidence event(s)",
                    [
                        {
                            "key": key,
                            "value": value,
                            "count": len(holders),
                            "total": len(group),
                            "evidence_event_ids": sorted(evidence_ids),
                        }
                    ],
                    "Gather counter-evidence before trusting the dominant "
                    "value: it may be one observation counted many times.",
                )
            )

    findings.sort(key=lambda f: (f["kind"], f["summary"]))
    return findings


def resolve_agent_id(user_id, *, dsn=None):
    """The agent id for a user, or None when the user has no agent row."""
    with _db_connect(resolve_dsn(dsn)) as conn, conn.cursor() as cur:
        cur.execute("SELECT agent_id FROM agents WHERE user_id = %s LIMIT 1", (str(user_id),))
        row = cur.fetchone()
    return str(row[0]) if row and row[0] else None


def prune_stale_hypotheses(user_id, *, dsn=None) -> int:
    """Mark a user's expired causal hypotheses expired; return how many.

    The read path sweeps expiry anyway, so this is the explicit
    daily pass the sleep consolidation records (K9). A user
    with no saved causal ledger has nothing to prune.
    """
    from puspa.world.store import load_causal_ledger, save_causal_hypotheses

    agent_id = resolve_agent_id(user_id, dsn=dsn)
    if agent_id is None:
        return 0
    ledger = load_causal_ledger(str(user_id), agent_id, dsn=dsn)
    if ledger is None:
        return 0
    expired = ledger.prune_expired()
    if expired:
        save_causal_hypotheses(str(user_id), agent_id, ledger, dsn=dsn)
    return expired


def reviewed_this_month(user_id, *, dsn, now=None) -> bool:
    """True when an ontology review was already recorded this calendar month."""
    now = _require_aware(now, "now") or datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    with _db_connect(resolve_dsn(dsn)) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM events WHERE type = 'task_completed' "
            "AND content ->> 'job' = 'ontology_review' "
            "AND content ->> 'user_id' = %s AND created_at >= %s LIMIT 1",
            (str(user_id), month_start),
        )
        return cur.fetchone() is not None


def run_ontology_review(
    user_id,
    *,
    dsn=None,
    facts=None,
    hypotheses=None,
    ledger=None,
    agent_id=None,
    task_id=None,
    now=None,
):
    """Run one ontology review and record it in the ledger.

    Loads the profile facts (``profile.list_facts``) and causal
    hypotheses (``load_causal_ledger``) for the user unless they
    are passed in; reviews them with :func:`review_ontology`;
    appends one ``task_completed`` event with
    ``job == "ontology_review"`` when a ledger is given or a
    ``dsn`` is passed. ``task_id`` defaults to a fresh id.

    Returns ``{"status": "ok", "findings": [...], "counts": {...},
    "ledger_event_id": ...}``; ``ledger_event_id`` is None when
    nothing was recorded (no ledger and no dsn).
    """
    now = _require_aware(now, "now") or datetime.now(UTC)
    if facts is None:
        facts = profile.list_facts(str(user_id), dsn=dsn) if dsn is not None else []
    if hypotheses is None:
        hypotheses = []
        if dsn is not None:
            resolved = agent_id if agent_id is not None else resolve_agent_id(user_id, dsn=dsn)
            if resolved is not None:
                from puspa.world.store import load_causal_ledger

                loaded = load_causal_ledger(str(user_id), resolved, dsn=dsn)
                if loaded is not None:
                    hypotheses = loaded.list_all(include_terminal=True)
    findings = review_ontology(facts=facts, hypotheses=hypotheses, now=now)
    counts = {kind: 0 for kind in FINDING_KINDS}
    for finding in findings:
        counts[finding["kind"]] += 1
    counts["total"] = len(findings)

    ledger_event_id = None
    if ledger is not None or dsn is not None:
        from puspa.ledger.ledger import Ledger

        writer = ledger if ledger is not None else Ledger(dsn)
        run_task_id = task_id or f"ontology-review-{uuid.uuid4().hex[:12]}"
        event = writer.append_event(
            task_id=run_task_id,
            agent_id=SYSTEM_AGENT_ID,
            event_type="task_completed",
            content={
                "job": "ontology_review",
                "user_id": str(user_id),
                "findings": findings,
                "counts": counts,
            },
        )
        ledger_event_id = str(event["id"])
    return {
        "status": "ok",
        "findings": findings,
        "counts": counts,
        "ledger_event_id": ledger_event_id,
    }
