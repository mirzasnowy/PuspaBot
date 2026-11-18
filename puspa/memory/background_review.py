"""Background review of one turn (G24).

After a turn ends, a fork of the agent reviews the turn in a
daemon thread, replays a snapshot of the messages, and writes
what it learned straight to the memory / skill stores. It never
touches the main conversation or its cache: the fork only ever
sees a deep copy of the snapshot, and its only way out is the
injected ``record_fact`` callable.

Pattern rewritten from hermes-agent ``agent/background_review.py``
(MIT, adapted, not copied): fork per turn in a daemon thread,
replay snapshot, write to memory/skill stores, never touch the
main conversation or cache, turn wait limit 120 seconds.

What the review does is deliberately deterministic: it splits
user messages into fact clauses with
``puspa.memory.extraction.extract_facts`` (no live LLM call),
keeps only the clauses ``is_memorable`` accepts (questions,
greetings, and filler never become facts), dedupes them against
what the injected ``recall_facts`` seam already knows, and
records the survivors through ``record_fact``.
An LLM summary of the turn is a follow-up (via
``puspa.router.complete``); it is not faked here.

``record_fact`` is the shared interface with the G7 facts
worker. An injected callable takes ``(user_id, record)`` and
returns a dict carrying at least the stored ``"id"``; a return
with ``"stored": False`` (the G7 trust gate refusing a fact)
counts as not recorded. The default is a lazy import of
``puspa.memory.facts.record_fact`` — lazy because that module
is built by the parallel G7 worker, and fail-soft: when the
module is not there yet the review reports ``"skipped"``
instead of crashing the turn. The default adapts the shared
record to G7's own signature,
``record_fact(user_id, key, content, source=..., agent_id=...,
now=...)``: the key is a hash of the normalised content (see
``_fact_key``), the source is the record's (``"inferred"`` for
review facts, which G7 stores as a proposal, never active).

``recall_facts`` is the seam against the G7 session search
(FTS5 belongs to G7; this module never builds an FTS5 index).
Its contract: ``recall_facts(query_text) -> list[dict]`` of
shared-shape records. It is optional; without it the review
only dedupes inside the turn itself.
"""

from __future__ import annotations

import copy
import hashlib
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .extraction import extract_facts, is_memorable

__all__ = [
    "TurnSnapshot",
    "ReviewResult",
    "ReviewHandle",
    "FactsModuleUnavailable",
    "TURN_TIMEOUT_SECONDS",
    "review_turn",
]

#: Turn wait limit, seconds (hermes-agent parity: 120).
TURN_TIMEOUT_SECONDS = 120


class FactsModuleUnavailable(Exception):
    """The default facts module (``puspa.memory.facts``) is absent."""


@dataclass
class TurnSnapshot:
    """One finished turn, as plain data.

    ``messages`` is a list of ``{"role": ..., "content": ...}``
    dicts. The review only reads user-role messages. The
    snapshot is deep-copied when a review starts, so later
    edits to the live conversation cannot leak into the fork.
    """

    user_id: str
    messages: list = field(default_factory=list)
    agent_id: str | None = None
    turn_id: str | None = None
    ended_at: datetime | None = None


@dataclass
class ReviewResult:
    """Outcome of one background review.

    ``status`` is ``"completed"`` (every candidate fact was
    attempted), ``"skipped"`` (no facts store was available, or
    the turn had no user content), ``"failed"`` (the review
    itself raised), or ``"timeout"`` (the turn wait limit hit).
    ``facts_recorded`` holds the stored ids; ``errors`` holds
    one string per failure and never raises.
    """

    status: str
    facts_recorded: list = field(default_factory=list)
    facts_considered: int = 0
    errors: list = field(default_factory=list)
    duration_seconds: float = 0.0
    turn_id: str | None = None


class ReviewHandle:
    """Handle for an async (daemon-thread) review.

    ``wait(timeout)`` blocks up to ``timeout`` seconds and
    returns the ``ReviewResult``; on a timeout it returns a
    ``"timeout"`` result instead of blocking forever. The
    thread is a daemon, so it can never hold process shutdown.
    """

    def __init__(self, thread, box, turn_id=None):
        self._thread = thread
        self._box = box  # {"result": ReviewResult | None}
        self.turn_id = turn_id

    def wait(self, timeout=TURN_TIMEOUT_SECONDS):
        """Wait for the review; ``"timeout"`` result when it overruns."""
        self._thread.join(timeout=timeout)
        result = self._box.get("result")
        if result is not None:
            return result
        return ReviewResult(
            status="timeout",
            errors=[f"review exceeded {timeout}s turn wait limit"],
            turn_id=self.turn_id,
        )

    @property
    def done(self):
        """True once the review thread has finished."""
        return not self._thread.is_alive()


def _fact_key(content):
    """Deterministic key for a review fact: a content hash.

    G7 facts are keyed (same key = same subject, a new version
    supersedes the old). A review fact is a free-form clause with
    no subject key of its own, so the key is a hash of the
    normalised content: identical clauses share a key (and
    supersede cleanly), different clauses never collide, and no
    accidental supersede can hide a distinct fact.
    """
    digest = hashlib.sha256(_normalise(content).encode("utf-8")).hexdigest()
    return f"fact:{digest[:16]}"


def _default_record_fact(user_id, record):
    """Default fact writer: lazy import of the G7 facts module.

    Fail-soft by design: when ``puspa.memory.facts`` does not
    exist yet (parallel G7 worker), raise
    ``FactsModuleUnavailable`` and let the caller report the
    review as skipped instead of crashing. Otherwise adapt the
    shared record to G7's ``record_fact(user_id, key, content,
    ...)`` signature (see ``_fact_key``).
    """
    try:
        from .facts import record_fact
    except ImportError as exc:  # G7 module not built yet
        raise FactsModuleUnavailable(
            "puspa.memory.facts is not available yet; "
            "background review skipped"
        ) from exc
    return record_fact(
        user_id,
        _fact_key(record["content"]),
        record["content"],
        source=record.get("source") or "inferred",
        agent_id=record.get("agent_id"),
        now=record.get("created_at"),
    )


def _user_texts(snapshot):
    texts = []
    for message in snapshot.messages or []:
        if not isinstance(message, dict):
            continue
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            texts.append(content)
    return texts


def _normalise(text):
    return " ".join(str(text).lower().split())


def _already_known(candidate, known_records):
    """True when a candidate fact is already in the known records.

    Normalised exact match, or the candidate is contained in a
    known record's content: recall returns whole memories, the
    review extracts clauses, so containment is the honest match.
    """
    needle = _normalise(candidate)
    for known in known_records or []:
        content = known.get("content") if isinstance(known, dict) else None
        if not content:
            continue
        haystack = _normalise(content)
        if needle == haystack or needle in haystack:
            return True
    return False


def _review(snapshot, *, record_fact, recall_facts, now):
    """The review itself: extract, dedupe, record. Pure of threads."""
    user_texts = _user_texts(snapshot)
    if not user_texts:
        return ReviewResult(
            status="skipped",
            errors=["turn had no user messages"],
            turn_id=snapshot.turn_id,
        )
    candidates = []
    seen = set()
    for text in user_texts:
        for fact in extract_facts(text):
            # is_memorable rejects noise the clause splitter lets
            # through: pure questions, greetings, filler.
            if not is_memorable(fact):
                continue
            key = _normalise(fact)
            if key and key not in seen:
                seen.add(key)
                candidates.append(fact)
    recorded = []
    errors = []
    for candidate in candidates:
        try:
            known = recall_facts(candidate) if recall_facts else []
        except Exception as exc:  # a failing recall seam must not kill the turn
            errors.append(f"recall failed for {candidate!r}: {exc}")
            known = []
        if _already_known(candidate, known):
            continue
        record = {
            "id": uuid.uuid4().hex,
            "content": candidate,
            "status": "active",
            "valid_from": now,
            "valid_until": None,
            "superseded_by": None,
            "source": "inferred",
            "created_at": now,
            "updated_at": now,
        }
        try:
            stored = record_fact(snapshot.user_id, record)
            if isinstance(stored, dict) and stored.get("stored") is False:
                # Trust gate refusal (G7): not stored, not recorded.
                errors.append(
                    f"fact not stored for {candidate!r}: "
                    f"{stored.get('verdict')}"
                )
                continue
            stored_id = stored.get("id") if isinstance(stored, dict) else None
            recorded.append(str(stored_id or record["id"]))
        except FactsModuleUnavailable:
            # No facts store at all: the whole review is skipped,
            # not one failed fact. Let _run_guarded map it.
            raise
        except Exception as exc:
            errors.append(f"record failed for {candidate!r}: {exc}")
    return ReviewResult(
        status="completed",
        facts_recorded=recorded,
        facts_considered=len(candidates),
        errors=errors,
        turn_id=snapshot.turn_id,
    )


def _run_guarded(snapshot, *, record_fact, recall_facts, box):
    """Thread entry: run the review, never let an exception escape."""
    started = datetime.now(timezone.utc)
    try:
        result = _review(
            snapshot,
            record_fact=record_fact,
            recall_facts=recall_facts,
            now=started,
        )
    except FactsModuleUnavailable as exc:
        result = ReviewResult(
            status="skipped", errors=[str(exc)], turn_id=snapshot.turn_id
        )
    except Exception as exc:  # the fork must never crash the caller
        result = ReviewResult(
            status="failed", errors=[str(exc)], turn_id=snapshot.turn_id
        )
    result.duration_seconds = (
        datetime.now(timezone.utc) - started
    ).total_seconds()
    box["result"] = result


def review_turn(snapshot, *, record_fact=None, recall_facts=None,
                sync=False, timeout_seconds=TURN_TIMEOUT_SECONDS):
    """Review one finished turn in the background.

    ``snapshot`` is deep-copied, so the fork never sees later
    edits to the live conversation. ``record_fact`` defaults to
    the lazy G7 import (fail-soft when the module is absent);
    tests inject a stub. ``sync=True`` runs the review inline
    and returns the ``ReviewResult`` directly (deterministic,
    for tests). Otherwise a daemon thread is spawned and a
    ``ReviewHandle`` is returned; ``handle.wait(timeout_seconds)``
    enforces the 120-second turn wait limit.
    """
    if record_fact is None:
        record_fact = _default_record_fact
    # Deep copy: the fork replays a frozen snapshot, never the
    # live conversation, its cache, or any shared mutable state.
    frozen = copy.deepcopy(snapshot)
    if sync:
        box = {}
        _run_guarded(
            frozen, record_fact=record_fact,
            recall_facts=recall_facts, box=box,
        )
        return box["result"]
    box = {}
    thread = threading.Thread(
        target=_run_guarded,
        args=(frozen,),
        kwargs={
            "record_fact": record_fact,
            "recall_facts": recall_facts,
            "box": box,
        },
        daemon=True,
        name=f"background-review-{frozen.turn_id or 'turn'}",
    )
    thread.start()
    return ReviewHandle(thread, box, turn_id=frozen.turn_id)
