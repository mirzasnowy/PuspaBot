"""Idle-time self-evolution review pass (COW-02, CowAgent pattern).

When a session goes idle, this pass decides what is worth
learning durably (a memory or a skill) or which unfinished task
can be pushed forward. Conservative by design: most sessions
produce no change at all.

CowAgent calls this self-evolution (zhayujie/CowAgent, MIT,
rewritten as PuspaBot code, never copied verbatim). The pass
is the idle trigger plus skill improvement plus task follow-up
on top of machinery that already exists — it does not rebuild
any of it:

- repeated successes are recognised the same way
  :mod:`puspa.memory.consolidate` recognises them, and the
  durable-memory proposals go through
  :func:`puspa.memory.consolidate.reflect_and_propose` (proposals,
  never active facts);
- a repeated success becomes a trial skill through
  :func:`puspa.memory.procedural.record_successful_task` — the
  same trial-procedure store the skill compiler
  (:mod:`puspa.governor.compiler`) writes through
  :class:`puspa.governor.skills.ProceduralSkillStore`, with the
  same bar: three successes are a pattern
  (:data:`puspa.governor.governor.COMPILE_AFTER_SUCCESSES`);
- every change is recorded in the ledger
  (:mod:`puspa.ledger`) *before* it applies, using only event
  types from the closed set — ``module_proposed`` for a trial
  skill, ``evidence_recorded`` for memory proposals,
  ``workflow_proposed`` for a task follow-up. Nothing is
  applied silently without a ledger entry.

Idle detection
--------------
A session is idle when its last recorded activity (latest
``created_at`` across the session's episodic rows and ledger
events) is at least ``idle_seconds`` old. The default
threshold is one hour: a session that went quiet an hour ago
is idle, a session active ten minutes ago is not. A session
with no recorded activity at all counts as idle — there is
simply nothing to review, so the pass runs and changes
nothing.

What the pass does, once idle
-----------------------------
1. Skill: episodic ``task_completed`` rows carrying a success
   marker are grouped by service (the text before the first
   colon, as in :mod:`puspa.memory.consolidate`). A service
   with at least three successful completions in the lookback
   window is a repeated-success pattern; its steps become a
   trial procedure. A pattern already covered by a procedure
   with the same steps is skipped, so a second pass never
   re-promotes the same pattern.
2. Memory: the window's episodes are reflected into profile
   proposals through :func:`consolidate.reflect_and_propose`
   (reliable service, repeated failure, user preference — each
   needs at least two evidence events, as in K9).
3. Tasks: rows in ``tasks`` with no ``completed_at`` and no
   terminal ledger event (``task_completed`` / ``task_failed`` /
   ``task_stopped``) are unfinished. One that is at least a
   day old and has no earlier self-evolution follow-up gets a
   ``workflow_proposed`` ledger event naming it and its
   purpose — the follow-up itself, visible to the digest — and
   an injected ``follow_up`` callable, when one is given, is
   called so a caller that can resume the task may do so.

A routine session — no repeated pattern, no new inference,
no stale unfinished task — produces zero changes. The
returned report lists every change with its ledger event id,
so "every applied change has a ledger entry" is checkable
from the return value alone.

Entry points: :func:`run_review_pass` (the pass itself,
callable from the scheduler and the CLI) and
:func:`run_self_evolution_job` (the scheduler job wrapper,
see ``puspa.scheduler.scheduler`` trigger type
``"self_evolution"``). The CLI trigger is
``puspa self-evolve`` (see ``puspa.cli``).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import psycopg
from psycopg.rows import dict_row

from puspa.ledger import Ledger

from . import consolidate, procedural
from .schema import resolve_dsn

__all__ = [
    "DEFAULT_IDLE_SECONDS",
    "FOLLOW_UP_MIN_AGE_DAYS",
    "LOOKBACK_DAYS",
    "MIN_REPEATS",
    "find_repeated_successes",
    "is_idle",
    "run_review_pass",
    "run_self_evolution_job",
    "session_last_activity",
]

#: Seconds of quiet before a session counts as idle. One hour:
#: high on purpose, so most sessions produce no change at all.
DEFAULT_IDLE_SECONDS = 3600

#: A repeated-success pattern needs this many successes — the
#: same bar as the skill compiler (three runs are a pattern).
MIN_REPEATS = 3

#: How far back the review looks for recent turns and outcomes.
LOOKBACK_DAYS = 7

#: An unfinished task must be at least this old before the pass
#: follows it up: a task started an hour ago is still being
#: worked on, not stale.
FOLLOW_UP_MIN_AGE_DAYS = 1

#: Terminal ledger events: a task with one of these is finished,
#: whatever its row in ``tasks`` says.
_TERMINAL_EVENT_TYPES = ("task_completed", "task_failed", "task_stopped")

#: Success markers on a ``task_completed`` episode (same words
#: as :mod:`puspa.memory.consolidate`).
_SUCCESS_MARKERS = ("successful", "berhasil", "success", "selesai")


def _now():
    return datetime.now(UTC)


def _validate_session_id(session_id):
    """A session id is a task id: None, or a UUID string."""
    if session_id is None:
        return None
    try:
        return str(uuid.UUID(str(session_id)))
    except ValueError as exc:
        raise ValueError(f"session id must be a task UUID, got {session_id!r}") from exc


def _connect(dsn=None):
    return psycopg.connect(resolve_dsn(dsn), row_factory=dict_row)


def session_last_activity(session_id, *, user_id=None, dsn=None):
    """Latest recorded activity of a session (or a whole user).

    The newest ``created_at`` across the session's episodic
    rows and its ledger events. With ``session_id=None`` the
    newest episodic row of the user (the ledger carries no
    user id, so it cannot scope a user-wide query). Returns
    None when nothing is recorded at all.

    The pass's own bookkeeping events (``content.job =
    "self_evolution"``) do not count as activity: a review
    pass must not keep its own session "active" and block the
    next pass.
    """
    session_id = _validate_session_id(session_id)
    with _connect(dsn) as conn, conn.cursor() as cur:
        if session_id is not None:
            cur.execute(
                "SELECT max(created_at) AS last_seen FROM ("
                " SELECT created_at FROM episodic WHERE task_id = %s::uuid"
                " UNION ALL"
                " SELECT created_at FROM events WHERE task_id = %s"
                " AND content ->> 'job' IS DISTINCT FROM 'self_evolution'"
                ") AS activity",
                (session_id, session_id),
            )
        else:
            if user_id is None:
                raise ValueError("user-wide activity needs a user_id")
            cur.execute(
                "SELECT max(created_at) AS last_seen FROM episodic WHERE user_id = %s",
                (user_id,),
            )
        row = cur.fetchone()
    return row["last_seen"] if row else None


def is_idle(session_id, *, idle_seconds=DEFAULT_IDLE_SECONDS, user_id=None, dsn=None, now=None):
    """True when a session has been quiet for ``idle_seconds``.

    A session with no recorded activity at all counts as idle:
    nothing has happened, so nothing can be interrupted — the
    pass then runs and finds nothing to change.
    """
    now = now or _now()
    last = session_last_activity(session_id, user_id=user_id, dsn=dsn)
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return (now - last).total_seconds() >= idle_seconds


def _service_of(content):
    """Service token of an episode: text before the first colon."""
    return str(content).split(":", 1)[0].strip().lower()


def find_repeated_successes(episodes, *, min_repeats=MIN_REPEATS):
    """Repeated successful completions, grouped by service.

    ``episodes`` is a list of dicts with keys ``type``,
    ``content`` and ``event_id`` (the shape
    :func:`puspa.memory.consolidate.reflect_episodes` takes).
    Returns one dict per service with at least ``min_repeats``
    successful ``task_completed`` episodes: ``service``,
    ``runs`` (the count), ``steps`` (the distinct successful
    episode contents, in first-seen order — the trial skill's
    steps) and ``evidence_event_ids`` (the supporting event
    ids, for the ledger entry). A service with fewer successes
    is not a pattern and is left out.
    """
    grouped: dict[str, dict] = {}
    for ep in episodes or []:
        if ep.get("type") != "task_completed":
            continue
        content = str(ep.get("content") or "")
        lowered = content.lower()
        if not any(marker in lowered for marker in _SUCCESS_MARKERS):
            continue
        service = _service_of(content)
        if not service:
            continue
        entry = grouped.setdefault(service, {"steps": [], "event_ids": []})
        if content not in entry["steps"]:
            entry["steps"].append(content)
        event_id = ep.get("event_id")
        if event_id:
            entry["event_ids"].append(str(event_id))
    patterns = []
    for service in sorted(grouped):
        entry = grouped[service]
        # Count distinct successful runs by evidence events;
        # fall back to distinct steps when events are missing.
        runs = max(len(entry["event_ids"]), len(entry["steps"]))
        if runs < min_repeats:
            continue
        patterns.append(
            {
                "service": service,
                "runs": runs,
                "steps": list(entry["steps"]),
                "evidence_event_ids": sorted(set(entry["event_ids"])),
            }
        )
    return patterns


def _recent_episodes(user_id, *, session_id, since, dsn):
    """Episodes of the user (and session) inside the lookback window."""
    with _connect(dsn) as conn, conn.cursor() as cur:
        if session_id is not None:
            cur.execute(
                "SELECT id, type, content, event_id, created_at FROM episodic "
                "WHERE user_id = %s AND task_id = %s::uuid AND created_at >= %s "
                "ORDER BY created_at ASC",
                (user_id, session_id, since),
            )
        else:
            cur.execute(
                "SELECT id, type, content, event_id, created_at FROM episodic "
                "WHERE user_id = %s AND created_at >= %s ORDER BY created_at ASC",
                (user_id, since),
            )
        return [
            {
                "id": str(r["id"]),
                "type": r["type"],
                "content": r["content"],
                "event_id": str(r["event_id"]) if r["event_id"] else None,
                "created_at": r["created_at"],
            }
            for r in cur.fetchall()
        ]


def _unfinished_tasks(user_id, *, older_than, dsn):
    """Unfinished tasks of the user, oldest first.

    A task is unfinished when its row has no ``completed_at``
    and the ledger holds no terminal event for it. Tasks whose
    terminal event exists (the row was never updated) are
    finished and left out.
    """
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, purpose, status, created_at FROM tasks "
            "WHERE user_id = %s AND completed_at IS NULL AND created_at < %s "
            "ORDER BY created_at ASC",
            (user_id, older_than),
        )
        rows = cur.fetchall()
        if not rows:
            return []
        task_ids = [r["id"] for r in rows]
        cur.execute(
            "SELECT DISTINCT task_id FROM events WHERE task_id = ANY(%s) AND type = ANY(%s)",
            (task_ids, list(_TERMINAL_EVENT_TYPES)),
        )
        finished = {r["task_id"] for r in cur.fetchall()}
    return [
        {
            "task_id": str(r["id"]),
            "purpose": r["purpose"],
            "status": r["status"],
            "created_at": r["created_at"],
        }
        for r in rows
        if r["id"] not in finished
    ]


def _followed_up_task_ids(dsn):
    """Task ids this pass already followed up (ledger, ever)."""
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT content FROM events WHERE type = 'workflow_proposed' "
            "AND content ->> 'job' = 'self_evolution' "
            "AND content ->> 'kind' = 'task_follow_up'",
        )
        return {
            str(r["content"].get("task_id"))
            for r in cur.fetchall()
            if isinstance(r["content"], dict) and r["content"].get("task_id")
        }


def _already_promoted(user_id, service, steps, *, dsn):
    """True when a procedure already covers this pattern's steps."""
    wanted = sorted(str(step) for step in steps)
    for proc in procedural.list_procedures(user_id, dsn=dsn):
        if proc["service"] != service:
            continue
        if sorted(str(step) for step in (proc["step"] or [])) == wanted:
            return True
    return False


def _record(ledger, *, task_id, agent_id, event_type, content):
    """Append one ledger event; return its id as a string."""
    event = ledger.append_event(
        task_id=task_id,
        agent_id=agent_id,
        event_type=event_type,
        content={"job": "self_evolution", **content},
    )
    return str(event["id"])


def run_review_pass(
    user_id,
    *,
    session_id=None,
    idle_seconds=DEFAULT_IDLE_SECONDS,
    lookback_days=LOOKBACK_DAYS,
    agent_id=None,
    dsn=None,
    now=None,
    force=False,
    ledger=None,
    record_skill=None,
    propose=None,
    follow_up=None,
):
    """Run one idle-time review pass for a user (and session).

    Returns a report dict. ``ran`` is False with reason
    ``"not_idle"`` when the session was active too recently
    (pass ``force=True`` to run regardless, e.g. from the CLI).
    Otherwise the report carries ``skills_proposed`` (trial
    skills stored), ``skills_skipped`` (patterns already
    covered by a procedure), ``memories_proposed`` (profile
    proposals created), ``tasks_followed_up`` (unfinished
    tasks with a follow-up ledger event), and ``changes`` — one
    entry per applied change, each with its
    ``ledger_event_id``. A routine session reports zero changes.

    Injectable seams (tests and callers that own the world):
    ``ledger`` (a :class:`puspa.ledger.Ledger`, default a real
    one on ``dsn``), ``record_skill`` (replaces
    ``procedural.record_successful_task``), ``propose``
    (replaces ``profile.add_fact`` inside the reflection) and
    ``follow_up`` (called with each followed-up task dict, so
    a caller that can resume the task may do so).
    """
    now = now or _now()
    session_id = _validate_session_id(session_id)
    agent_id = agent_id or "puspa"
    ledger = ledger if ledger is not None else Ledger(dsn)
    record_skill = record_skill or procedural.record_successful_task
    ledger_task_id = session_id or f"self-evolution:{user_id}"

    if not force:
        last = session_last_activity(session_id, user_id=user_id, dsn=dsn)
        if last is not None:
            if last.tzinfo is None:
                last = last.replace(tzinfo=UTC)
            idle_for = (now - last).total_seconds()
            if idle_for < idle_seconds:
                return {
                    "ran": False,
                    "reason": "not_idle",
                    "idle_seconds": idle_for,
                    "session_id": session_id,
                    "skills_proposed": [],
                    "skills_skipped": [],
                    "memories_proposed": 0,
                    "tasks_followed_up": [],
                    "changes": [],
                    "ledger_events": [],
                }
            idle_for_value: float | None = idle_for
        else:
            idle_for_value = None
    else:
        idle_for_value = None

    since = now - timedelta(days=lookback_days)
    episodes = _recent_episodes(user_id, session_id=session_id, since=since, dsn=dsn)

    changes: list[dict] = []
    ledger_events: list[str] = []
    skills_proposed: list[dict] = []
    skills_skipped: list[str] = []

    # 1. Repeated successes -> trial skills (ledger first).
    for pattern in find_repeated_successes(episodes):
        service = pattern["service"]
        if _already_promoted(user_id, service, pattern["steps"], dsn=dsn):
            skills_skipped.append(service)
            continue
        event_id = _record(
            ledger,
            task_id=ledger_task_id,
            agent_id=agent_id,
            event_type="module_proposed",
            content={
                "kind": "trial_skill",
                "service": service,
                "runs": pattern["runs"],
                "steps": pattern["steps"],
                "evidence_event_ids": pattern["evidence_event_ids"],
            },
        )
        ledger_events.append(event_id)
        procedure = record_skill(
            user_id,
            service,
            pattern["steps"],
            agent_id=agent_id,
            source_event_id=pattern["evidence_event_ids"][0]
            if pattern["evidence_event_ids"]
            else None,
            dsn=dsn,
        )
        entry = {
            "kind": "trial_skill",
            "service": service,
            "runs": pattern["runs"],
            "procedure_id": str(procedure["id"]),
            "ledger_event_id": event_id,
        }
        skills_proposed.append(entry)
        changes.append(entry)

    # 2. Durable memories: reflect into profile proposals (ledger first).
    inferences = consolidate.reflect_episodes(episodes)
    memories_proposed = 0
    if inferences:
        event_id = _record(
            ledger,
            task_id=ledger_task_id,
            agent_id=agent_id,
            event_type="evidence_recorded",
            content={
                "kind": "memory_proposal",
                "inferences": [{"key": inf["key"], "value": inf["value"]} for inf in inferences],
            },
        )
        ledger_events.append(event_id)
        proposals = consolidate.reflect_and_propose(
            user_id, episodes, agent_id=agent_id, propose=propose, dsn=dsn
        )
        memories_proposed = len(proposals)
        changes.append(
            {
                "kind": "memory_proposal",
                "inferences": len(inferences),
                "proposals_created": memories_proposed,
                "ledger_event_id": event_id,
            }
        )

    # 3. Unfinished tasks -> follow-up (the ledger event is the
    # follow-up; an injected callable may also resume the task).
    followed_up: list[dict] = []
    already_followed = _followed_up_task_ids(dsn)
    older_than = now - timedelta(days=FOLLOW_UP_MIN_AGE_DAYS)
    for task in _unfinished_tasks(user_id, older_than=older_than, dsn=dsn):
        if task["task_id"] in already_followed:
            continue
        event_id = _record(
            ledger,
            task_id=ledger_task_id,
            agent_id=agent_id,
            event_type="workflow_proposed",
            content={
                "kind": "task_follow_up",
                "task_id": task["task_id"],
                "purpose": task["purpose"],
                "next_step": "resume the task from its last recorded step",
            },
        )
        ledger_events.append(event_id)
        resumed = None
        if follow_up is not None:
            resumed = follow_up(task)
        entry = {
            "kind": "task_follow_up",
            "task_id": task["task_id"],
            "purpose": task["purpose"],
            "resumed": resumed,
            "ledger_event_id": event_id,
        }
        followed_up.append(entry)
        changes.append(entry)

    return {
        "ran": True,
        "reason": None,
        "idle_seconds": idle_for_value,
        "session_id": session_id,
        "skills_proposed": skills_proposed,
        "skills_skipped": skills_skipped,
        "memories_proposed": memories_proposed,
        "tasks_followed_up": followed_up,
        "changes": changes,
        "ledger_events": ledger_events,
    }


def run_self_evolution_job(user_id, *, session_id=None, agent_id=None, dsn=None, now=None):
    """Scheduler entry: one idle review pass for a user.

    The normal idle check applies — a scheduled run at night
    finds the session idle and reviews it; a session that was
    active recently reports ``ran: False`` instead of forcing
    a review. See ``puspa.scheduler.scheduler`` trigger type
    ``"self_evolution"``.
    """
    return run_review_pass(
        user_id,
        session_id=session_id,
        agent_id=agent_id,
        dsn=dsn,
        now=now,
    )
