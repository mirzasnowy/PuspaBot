"""Three-layer memory write cycle (G7, DuskBot pattern).

1. Immediate: when the user says "remember ..." the fact is
   recorded at once (:func:`record_now`).
2. Background: after the chat goes quiet (2 minutes idle, or every
   6 turns) a background pass reflects recent episodes into
   profile proposals (:func:`background_pass`).
3. Nightly tidy: after 03:00 one combined pass expires stale facts,
   consolidates idle episodes and runs a DREAM pass
   (:func:`nightly_tidy`).

Wiring status: these are plain functions with injectable seams
(``store``, ``journal``, ``llm_complete``). They are not yet wired
to a live idle detector or a nightly scheduler trigger — the only
scheduler wiring in this item is the DREAM trigger (see
``puspa/scheduler/scheduler.py``: ``schedule_dream`` /
``run_scheduled_job`` branch ``"dream"``). The call sites for the
other two layers are documented on each function so the follow-up
wiring can attach them without inventing a seam.
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import consolidate, facts
from .dream import run_dream_job

#: Seconds of quiet chat before a background pass may run.
IDLE_SECONDS = 120

#: Background pass also runs every N turns.
TURNS_PER_BACKGROUND_PASS = 6

#: Nightly tidy runs in this hour (03:00-03:59 local scheduler time).
NIGHTLY_HOUR = 3

REMEMBER_PREFIXES = ("remember that ", "remember: ", "remember ")


def _now():
    return datetime.now(timezone.utc)


def record_now(user_id, text, *, store=None, journal=None, origin="user",
               key=None, now=None):
    """Immediate layer: record a "remember ..." utterance at once.

    Returns ``{"handled": False}`` when the text is not a remember
    request, otherwise the :func:`facts.record_fact` result (which
    carries ``"stored": False`` + a trust verdict when the trust
    gate blocks or quarantines it). Call site: the chat turn
    handler, on every user message — follow-up wiring attaches it
    there; it is not wired yet.
    """
    now = now or _now()
    lowered = (text or "").lower()
    matched = None
    for prefix in REMEMBER_PREFIXES:
        if lowered.startswith(prefix):
            matched = prefix
            break
    if matched is None:
        return {"handled": False}
    content = text[len(matched):].strip()
    if not content:
        return {"handled": False, "reason": "empty remember request"}
    result = facts.record_fact(
        user_id, key or "remembered", content, source="user_stated",
        store=store, origin=origin, journal=journal, now=now)
    result["handled"] = True
    return result


def background_pass(user_id, episodes, *, store=None, journal=None,
                    idle_seconds=None, turn_count=None, force=False,
                    now=None):
    """Background layer: reflect episodes into proposals when idle.

    Runs when ``idle_seconds >= 120`` or ``turn_count`` is a
    multiple of 6 (pass ``force=True`` to run regardless, e.g. in
    tests). Reflections go through ``consolidate.reflect_episodes``
    and are recorded with source ``"inferred"``, so they land as
    proposals, never active facts. Returns ``{"ran": bool,
    "proposals_created": int}``. Call site: the idle detector /
    turn counter in the chat runtime — follow-up wiring attaches
    it there; it is not wired yet.
    """
    now = now or _now()
    due = force
    if idle_seconds is not None and idle_seconds >= IDLE_SECONDS:
        due = True
    if turn_count is not None and turn_count > 0 \
            and turn_count % TURNS_PER_BACKGROUND_PASS == 0:
        due = True
    if not due:
        return {"ran": False, "proposals_created": 0}
    store = store or facts.default_store()
    if isinstance(store, facts.PostgresFactStore):
        # Postgres path: proposals go through profile.add_fact.
        proposals = consolidate.reflect_and_propose(
            user_id, episodes or [], dsn=store.dsn)
    else:
        # In-memory path: same inferences, recorded through facts.
        proposals = []
        for inference in consolidate.reflect_episodes(episodes or []):
            proposals.append(facts.record_fact(
                user_id, inference["key"], inference["value"],
                source="inferred", store=store, journal=journal, now=now))
    return {"ran": True, "proposals_created": len(proposals)}


def nightly_tidy(user_id, *, store=None, journal=None, llm_complete=None,
                 dsn=None, now=None, force=False):
    """Nightly layer: one combined pass after 03:00.

    Steps: (1) expire facts whose ``valid_until`` has passed;
    (2) consolidate idle episodes (needs ``dsn``; skipped with a
    note when no database is given); (3) run a DREAM pass (needs
    ``llm_complete``; skipped with status ``"no_llm"`` when none
    is available). Returns a summary dict with one entry per step.
    Call site: the nightly scheduler trigger — follow-up wiring
    attaches it there; only the DREAM part is scheduler-wired in
    this item (trigger type ``"dream"``).
    """
    now = now or _now()
    if not force and now.hour != NIGHTLY_HOUR:
        return {"ran": False, "reason": "not nightly hour"}
    store = store or facts.default_store()
    expired = 0
    for fact in store.list_user(user_id, statuses=("active",)):
        valid_until = fact.get("valid_until")
        if valid_until is not None:
            if valid_until.tzinfo is None:
                valid_until = valid_until.replace(tzinfo=timezone.utc)
            if valid_until <= now:
                facts.expire_fact(user_id, fact["id"], store=store,
                                  journal=journal, now=now)
                expired += 1
    summary = {"ran": True, "expired": expired}
    if dsn is not None:
        summary["consolidation"] = consolidate.consolidate_idle(
            user_id, dsn=dsn, now=now)
    else:
        summary["consolidation"] = {"skipped": "no dsn"}
    summary["dream"] = run_dream_job(
        user_id, llm_complete=llm_complete, dsn=dsn, store=store,
        journal=journal, now=now)
    return summary
