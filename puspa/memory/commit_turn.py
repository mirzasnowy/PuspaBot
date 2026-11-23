"""Write-through turn commit: one call, one transaction.

The cognitive loop (``docs/kosmos-brain-v2.md``, step 8) ends every
turn with a write-through: the turn's ledger entry and the durable
memory land together, or the turn does not complete. This module is
that one call: :func:`commit_turn`.

What one call writes, in plain words:

- One ledger event per item the turn produced, each carrying its
  class (``decision``, ``correction``, ``commitment``, ``fact``,
  ``preference`` or ``outcome``) in its content. The ledger is the
  append-only record of what happened; the class says what kind of
  memory the entry is.
- One fact row per fact-like item (every class except
  ``outcome``), recorded the same way ``record_fact`` records one:
  the new value is active and the old active value with the same
  key is superseded, so history is kept, never overwritten.
- Outcome items go to today's narrative journal entry instead of
  the fact table: an outcome is something that happened, not a
  lasting fact about the user.
- The rebuilt MEMORY.md document, rendered from the same rows the
  call just wrote (the same snapshot builder and renderer the
  nightly rebuild uses), so the durable file reflects the turn
  immediately and the nightly rebuild reproduces it.

Atomicity: on Postgres all of that runs on one connection inside
one transaction (the ledger seam is ``Ledger.append_event_on``,
the durable seams are the ``*_on`` methods on
``PostgresDurableStore``). Any failure rolls the whole
transaction back, so a half-written turn is impossible: no ledger
event without its memory, no memory without its ledger event.
The failure is then queued for retry (see below) and
:func:`commit_turn` raises :class:`CommitTurnError`: it never
returns success for a turn that did not land. A turn that cannot
commit cannot complete.

Retry queue: a failed write goes to ``turn_commit_retries``
(migration 0030) in a separate transaction, so the queue entry
survives the rollback that wiped the failed write. The failure
stays visible three ways: the raised error names it, the turn
loop reports the turn as failed (see ``make_turn_committer``),
and :func:`list_pending_retries` lists every queued failure with
its error. :func:`retry_pending` re-attempts queued commits
oldest first; a commit that keeps failing is marked ``dead``
after :data:`MAX_RETRY_ATTEMPTS` tries and needs a human to look
at it, never retried again automatically.

Trust: every item runs through the memory trust gate first
(``puspa.memory.trust``), the same gate ``record_fact`` runs. A
blocked item (reads like an instruction) or a quarantined item
(from an outside origin) is refused: it is written nowhere, not
even the ledger, and is listed in the result under ``"refused"``
so the refusal is visible instead of silent. Secrets are
redacted before anything is written, the same as the gate does.

In-memory path: when the caller passes a ``MemoryDurableStore``
(plus a ledger object with ``append_event`` and a fact store),
the same steps run against the in-memory stores, with
snapshot-and-restore standing in for the transaction: on failure
the stores are put back exactly as they were. Tests use this
path; production uses Postgres.

What this module does NOT do: it does not decide what a turn
produced. Extracting decisions, corrections, and facts from a
turn's history is a separate item; :func:`make_turn_committer`
wires the loop seam with the one item the loop always knows, the
turn's outcome (its reply text). Callers that extract richer
items pass their own committer to the loop instead.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, NoReturn

from puspa.db import connect as _db_connect
from puspa.db import dict_row
from puspa.ledger import Ledger
from puspa.migrations import apply_migrations

from . import durable as durable_module
from . import facts as facts_module
from . import trust as trust_module
from .durable import (
    EPISODE_READ_LIMIT,
    JOURNAL_MAX_ENTRIES,
    SUMMARY_READ_LIMIT,
    MemoryDurableStore,
    PostgresDurableStore,
)
from .facts import MemoryFactStore
from .schema import resolve_dsn

__all__ = [
    "ENTRY_CLASSES",
    "FACT_CLASSES",
    "MAX_RETRY_ATTEMPTS",
    "CommitTurnError",
    "MemoryRetryQueue",
    "PostgresRetryQueue",
    "TurnItem",
    "commit_turn",
    "list_pending_retries",
    "make_turn_committer",
    "retry_pending",
]

#: Entry classes, one per ledger entry (design doc, Memory).
ENTRY_CLASSES = ("decision", "correction", "commitment", "fact", "preference", "outcome")

#: Classes stored as fact rows. Outcomes are journal entries, not facts.
FACT_CLASSES = ("decision", "correction", "commitment", "fact", "preference")

#: Fact sources, the same closed set ``record_fact`` enforces.
FACT_SOURCES = ("user_stated", "user_confirmed", "inferred")

#: Ledger event type for a committed item. The closed type set has
#: no memory type, so a commit entry rides on the turn's agent
#: message type, with its class in the content.
LEDGER_EVENT_TYPE = "agent_message"

#: Migration that creates the retry queue table.
_RETRY_QUEUE_MIGRATION = "0030"

#: Tries before a queued commit is marked dead (first try included).
MAX_RETRY_ATTEMPTS = 5

#: Longest reply text kept as one outcome journal line.
MAX_OUTCOME_CHARS = 500

#: Longest journal entry text (same cap the daily entry builder uses).
MAX_JOURNAL_CHARS = 2000

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _now():
    return datetime.now(UTC)


def _slug(text: str) -> str:
    """Short URL-safe slug of ``text`` for a derived fact key."""
    slug = _SLUG_RE.sub("-", str(text or "").lower()).strip("-")
    return slug[:60] or "item"


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _validate_user_id(user_id: str) -> str:
    """User id as a string; must parse as a UUID.

    The fact, summary, and episode tables key users by UUID,
    while the durable document keys by the same string, so a
    non-UUID id cannot be written through: reject it before
    any write, with a plain error, instead of failing halfway.
    """
    user_id = str(user_id)
    try:
        uuid.UUID(user_id)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"user_id must be a UUID string, got {user_id!r}") from exc
    return user_id


@dataclass(frozen=True)
class TurnItem:
    """One thing a turn produced, worth remembering.

    ``entry_class`` is the kind of memory (see
    :data:`ENTRY_CLASSES`). ``text`` is the content, in plain
    words. ``key`` is the fact key; when omitted it is derived
    as ``"<class>:<slug of text>"``. ``source`` is the fact
    source (``"user_stated"``, ``"user_confirmed"`` or
    ``"inferred"``; inferred items are stored as proposals,
    never active). ``origin`` is the trust-gate origin
    (``"user"`` by default; an outside origin such as ``"tool"``
    or ``"web"`` is quarantined, never stored as a fact).
    """

    entry_class: str
    text: str
    key: str | None = None
    source: str = "user_stated"
    origin: str = "user"

    def derived_key(self) -> str:
        """Fact key: the given key, or ``"<class>:<slug>"``."""
        if self.key:
            return self.key
        return f"{self.entry_class}:{_slug(self.text)}"

    def as_dict(self) -> dict:
        return {
            "entry_class": self.entry_class,
            "key": self.key,
            "text": self.text,
            "source": self.source,
            "origin": self.origin,
        }


def _normalize_items(items: Any) -> list[TurnItem]:
    """Validate raw items into :class:`TurnItem` objects.

    Accepts dicts (``{"entry_class", "text", ...}``) or
    :class:`TurnItem` objects. A bad class, an empty text, or
    an unknown source is a caller bug: it raises ``ValueError``
    before any write, and is never queued for retry.
    """
    if items is None:
        raise ValueError("items must be a list of turn items, got None")
    normalized: list[TurnItem] = []
    for raw in items:
        if isinstance(raw, TurnItem):
            item = raw
        elif isinstance(raw, dict):
            try:
                item = TurnItem(
                    entry_class=raw["entry_class"],
                    text=raw["text"],
                    key=raw.get("key"),
                    source=raw.get("source", "user_stated"),
                    origin=raw.get("origin", "user"),
                )
            except KeyError as exc:
                raise ValueError(f"turn item is missing {exc}") from exc
        else:
            raise ValueError(f"turn item must be a dict or TurnItem, got {raw!r}")
        if item.entry_class not in ENTRY_CLASSES:
            raise ValueError(
                f"unknown entry class: {item.entry_class!r} (must be one of {ENTRY_CLASSES})"
            )
        if not isinstance(item.text, str) or not item.text.strip():
            raise ValueError("turn item text must be a non-empty string")
        if item.source not in FACT_SOURCES:
            raise ValueError(f"unknown source: {item.source!r} (must be one of {FACT_SOURCES})")
        if item.key is not None and (not isinstance(item.key, str) or not item.key.strip()):
            raise ValueError("turn item key must be a non-empty string when given")
        normalized.append(item)
    return normalized


def _trust_check(items: list[TurnItem]) -> tuple[list[TurnItem], list[dict]]:
    """Run the trust gate over every item, before any write.

    Returns ``(allowed, refused)``: allowed items carry their
    redacted text, refused items carry their verdict and reason
    and are written nowhere. The gate runs up front so a refused
    item can never reach the ledger or the fact table.
    """
    allowed: list[TurnItem] = []
    refused: list[dict] = []
    for item in items:
        verdict = trust_module.check_text(item.text, origin=item.origin)
        if verdict["verdict"] != "allow":
            refused.append(
                {
                    "entry_class": item.entry_class,
                    "key": item.derived_key(),
                    "verdict": verdict["verdict"],
                    "reasons": verdict["reasons"],
                }
            )
            continue
        allowed.append(
            TurnItem(
                entry_class=item.entry_class,
                text=verdict["redacted_text"],
                key=item.key,
                source=item.source,
                origin=item.origin,
            )
        )
    return allowed, refused


class CommitTurnError(Exception):
    """A turn commit failed: the turn must not report success.

    ``reason`` is the failure in plain words. ``queued`` says
    whether the failed write is in the retry queue (a queue
    failure of its own leaves it False, and the reason says so).
    ``retry_id`` is the queue row id when queued, else None.
    """

    def __init__(
        self,
        reason: str,
        *,
        user_id: str = "",
        task_id: str = "",
        queued: bool = False,
        retry_id: str | None = None,
    ) -> None:
        self.reason = reason
        self.user_id = user_id
        self.task_id = task_id
        self.queued = queued
        self.retry_id = retry_id
        queued_note = (
            f"queued for retry as {retry_id}"
            if queued
            else "not queued for retry (the retry queue itself failed)"
        )
        super().__init__(
            f"turn commit failed for task {task_id!r}: {reason}; {queued_note}. "
            "The turn did not complete. See list_pending_retries for queued failures."
        )


# ----------------------------------------------------------------------
# Retry queue
# ----------------------------------------------------------------------


def _public_retry(row: dict) -> dict:
    """One retry row as a plain dict (ids as strings)."""
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "task_id": str(row["task_id"]),
        "agent_id": str(row["agent_id"]),
        "payload": payload,
        "error": row["error"],
        "attempts": int(row["attempts"]),
        "status": row["status"],
        "created_at": row["created_at"],
    }


class PostgresRetryQueue:
    """Failed turn commits in Postgres table ``turn_commit_retries``.

    Every method opens its own connection and commits, so an
    enqueue survives the rollback of the transaction that
    failed: the queue lives outside the failed write by design.
    """

    def __init__(self, dsn: str | None = None, *, ensure_schema: bool = True) -> None:
        self.dsn = resolve_dsn(dsn)
        if ensure_schema:
            apply_migrations(self.dsn, target=_RETRY_QUEUE_MIGRATION)

    def _connect(self):
        return _db_connect(self.dsn, row_factory=dict_row)

    def enqueue(self, payload: dict, error: str) -> str:
        """Queue one failed commit; return the queue row id."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO turn_commit_retries "
                    "(user_id, task_id, agent_id, payload, error) "
                    "VALUES (%s, %s, %s, %s::jsonb, %s) RETURNING id",
                    (
                        str(payload["user_id"]),
                        str(payload["task_id"]),
                        str(payload["agent_id"]),
                        json.dumps(payload),
                        str(error),
                    ),
                )
                row_id = str(cur.fetchone()["id"])
            conn.commit()
        return row_id

    def list_pending(self, limit: int = 50) -> list[dict]:
        """Pending failures, oldest first: what still needs a retry."""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM turn_commit_retries WHERE status = 'pending' "
                "ORDER BY created_at ASC LIMIT %s",
                (limit,),
            )
            return [_public_retry(row) for row in cur.fetchall()]

    def mark_done(self, entry_id: str) -> None:
        """A retry committed it: the queue row is done."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE turn_commit_retries SET status = 'done', updated_at = now() "
                    "WHERE id = %s",
                    (str(entry_id),),
                )
            conn.commit()

    def note_attempt(self, entry_id: str, error: str) -> str:
        """Record one more failed retry; return the new status.

        The attempt count grows; at :data:`MAX_RETRY_ATTEMPTS`
        the row goes ``dead`` and is never retried again
        automatically.
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE turn_commit_retries SET attempts = attempts + 1, error = %s, "
                    "updated_at = now(), status = CASE WHEN attempts + 1 >= %s "
                    "THEN 'dead' ELSE 'pending' END "
                    "WHERE id = %s RETURNING status",
                    (str(error), MAX_RETRY_ATTEMPTS, str(entry_id)),
                )
                row = cur.fetchone()
            conn.commit()
        return str(row["status"]) if row is not None else "pending"


class MemoryRetryQueue:
    """In-memory retry queue: same interface as the Postgres queue.

    Used by tests and by callers with no database. Entries keep
    insertion order, oldest first.
    """

    def __init__(self) -> None:
        self._entries: dict[str, dict] = {}

    def enqueue(self, payload: dict, error: str) -> str:
        entry_id = str(uuid.uuid4())
        self._entries[entry_id] = {
            "id": entry_id,
            "user_id": str(payload["user_id"]),
            "task_id": str(payload["task_id"]),
            "agent_id": str(payload["agent_id"]),
            "payload": copy.deepcopy(payload),
            "error": str(error),
            "attempts": 1,
            "status": "pending",
            "created_at": _now(),
        }
        return entry_id

    def list_pending(self, limit: int = 50) -> list[dict]:
        pending = [entry for entry in self._entries.values() if entry["status"] == "pending"]
        pending.sort(key=lambda entry: entry["created_at"])
        return [copy.deepcopy(entry) for entry in pending[:limit]]

    def mark_done(self, entry_id: str) -> None:
        entry = self._entries.get(str(entry_id))
        if entry is not None:
            entry["status"] = "done"

    def note_attempt(self, entry_id: str, error: str) -> str:
        entry = self._entries.get(str(entry_id))
        if entry is None:
            return "pending"
        entry["attempts"] += 1
        entry["error"] = str(error)
        if entry["attempts"] >= MAX_RETRY_ATTEMPTS:
            entry["status"] = "dead"
        return entry["status"]


# ----------------------------------------------------------------------
# Shared write steps
# ----------------------------------------------------------------------


def _ledger_content(item: TurnItem) -> dict:
    """Ledger event content for one item: class, key, text."""
    return {
        "memory_class": item.entry_class,
        "key": item.derived_key(),
        "text": item.text,
        "turn_commit": True,
    }


def _append_journal_text(existing_text: str, new_text: str) -> str:
    """Append one outcome line to today's journal entry text."""
    joined = f"{existing_text}; {new_text}" if existing_text else new_text
    if len(joined) > MAX_JOURNAL_CHARS:
        joined = joined[: MAX_JOURNAL_CHARS - 3] + "..."
    return joined


def _commit_payload(
    user_id: str,
    task_id: str,
    agent_id: str,
    items: list[TurnItem],
    reply_text: str | None,
) -> dict:
    """The retry payload: everything needed to rebuild the call."""
    return {
        "user_id": user_id,
        "task_id": task_id,
        "agent_id": agent_id,
        "items": [item.as_dict() for item in items],
        "reply_text": reply_text,
    }


# ----------------------------------------------------------------------
# Postgres path: one connection, one transaction
# ----------------------------------------------------------------------


def _insert_fact_on(
    cur,
    *,
    user_id: str,
    agent_id: str,
    item: TurnItem,
    now: datetime,
) -> str:
    """Insert one fact row on an open cursor; return its id.

    The active fact with the same key is superseded first, the
    same bitemporal rule ``record_fact`` applies: history is
    kept, never overwritten. Inferred items are stored as
    proposals, never active.
    """
    key = item.derived_key()
    status = "proposed" if item.source == "inferred" else "active"
    fact_id = str(uuid.uuid4())
    if status == "active":
        cur.execute(
            "UPDATE profile_facts SET status = 'superseded', valid_until = %s, "
            "superseded_by = %s, updated_at = %s "
            "WHERE user_id = %s AND key = %s AND status = 'active' "
            "AND agent_id IS NOT DISTINCT FROM %s",
            (now, fact_id, now, user_id, key, agent_id),
        )
    cur.execute(
        "INSERT INTO profile_facts (id, user_id, agent_id, key, value, source, "
        "status, valid_from, valid_until, superseded_by, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NULL, NULL, %s, %s) RETURNING id",
        (fact_id, user_id, agent_id, key, item.text, item.source, status, now, now, now),
    )
    return str(cur.fetchone()["id"])


def _read_facts_on(cur, user_id: str) -> list[dict]:
    """Active and proposed facts on an open cursor (rebuild input)."""
    cur.execute(
        "SELECT id, user_id, agent_id, key, value, source, status, valid_from, "
        "valid_until, superseded_by, confidence, source_detail, created_at, updated_at "
        "FROM profile_facts WHERE user_id = %s AND status IN ('active', 'proposed') "
        "ORDER BY valid_from ASC, created_at ASC",
        (user_id,),
    )
    out = []
    for row in cur.fetchall():
        out.append(
            {
                "id": str(row["id"]),
                "user_id": str(row["user_id"]),
                "agent_id": row["agent_id"],
                "key": row["key"],
                "content": row["value"],
                "status": row["status"],
                "valid_from": row["valid_from"],
                "valid_until": row["valid_until"],
                "superseded_by": (str(row["superseded_by"]) if row.get("superseded_by") else None),
                "source": row["source"],
                "confidence": row.get("confidence"),
                "source_detail": row.get("source_detail"),
                "created_at": row["created_at"],
                "updated_at": row.get("updated_at") or row["created_at"],
            }
        )
    return out


def _read_summaries_on(cur, user_id: str) -> list[dict]:
    """Newest summaries on an open cursor (rebuild input)."""
    cur.execute(
        "SELECT id, user_id, agent_id, period_start, period_end, summary, "
        "event_ids, created_at FROM summaries "
        "WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
        (user_id, SUMMARY_READ_LIMIT),
    )
    return [
        {
            "id": str(row["id"]),
            "user_id": str(row["user_id"]),
            "agent_id": row["agent_id"],
            "period_start": row["period_start"],
            "period_end": row["period_end"],
            "summary": row["summary"],
            "event_ids": [str(event_id) for event_id in (row["event_ids"] or [])],
            "created_at": row["created_at"],
        }
        for row in cur.fetchall()
    ]


def _read_episodes_on(cur, user_id: str) -> list[dict]:
    """Newest episodes on an open cursor (rebuild input)."""
    cur.execute(
        "SELECT id, user_id, agent_id, task_id, type, content, event_id, created_at "
        "FROM episodic WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
        (user_id, EPISODE_READ_LIMIT),
    )
    return [
        {
            "id": str(row["id"]),
            "user_id": str(row["user_id"]),
            "agent_id": str(row["agent_id"]) if row["agent_id"] else None,
            "task_id": str(row["task_id"]) if row["task_id"] else None,
            "type": row["type"],
            "content": row["content"],
            "event_id": str(row["event_id"]) if row["event_id"] else None,
            "created_at": row["created_at"],
        }
        for row in cur.fetchall()
    ]


def _commit_postgres(
    user_id: str,
    task_id: str,
    agent_id: str,
    allowed: list[TurnItem],
    refused: list[dict],
    *,
    dsn: str,
    ledger: Ledger,
    durable_store: PostgresDurableStore,
    now: datetime,
) -> dict:
    """Postgres write-through: ledger plus durable memory, one transaction."""
    conn = _db_connect(dsn, row_factory=dict_row)
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            event_ids: list[str] = []
            fact_ids: list[str] = []
            for item in allowed:
                event = ledger.append_event_on(
                    conn,
                    task_id=task_id,
                    agent_id=agent_id,
                    event_type=LEDGER_EVENT_TYPE,
                    content=_ledger_content(item),
                )
                event_ids.append(str(event["id"]))
                if item.entry_class in FACT_CLASSES:
                    fact_ids.append(
                        _insert_fact_on(cur, user_id=user_id, agent_id=agent_id, item=item, now=now)
                    )
                else:  # outcome: today's journal entry, not a fact row
                    existing = durable_store.get_journal_entry_on(conn, user_id, now.date())
                    previous_text = str(existing["text"]) if existing else ""
                    previous_ids = list(existing["source_event_ids"]) if existing else []
                    durable_store.save_journal_entry_on(
                        conn,
                        user_id,
                        now.date(),
                        _append_journal_text(previous_text, item.text),
                        [*previous_ids, event_ids[-1]],
                    )
            facts = _read_facts_on(cur, user_id)
            summaries = _read_summaries_on(cur, user_id)
            episodes = _read_episodes_on(cur, user_id)
            journal_entries = durable_store.list_journal_entries_on(
                conn, user_id, limit=JOURNAL_MAX_ENTRIES
            )
        collected = durable_module.collect_snapshot(
            user_id,
            facts=facts,
            summaries=summaries,
            episodes=episodes,
            journal_entries=journal_entries,
            now=now,
        )
        content = durable_module.render_memory_md(collected["snapshot"], generated_at=now)
        previous = durable_store.get_document_on(conn, user_id)
        record = durable_store.save_document_on(
            conn, user_id, content, _content_hash(content), now, collected["counts"]
        )
        conn.commit()
        return {
            "user_id": user_id,
            "task_id": task_id,
            "agent_id": agent_id,
            "completed": True,
            "event_ids": event_ids,
            "fact_ids": fact_ids,
            "document_version": record["version"],
            "document_changed": previous is None
            or previous["content_hash"] != record["content_hash"],
            "refused": refused,
            "retried_from_queue": None,
        }
    except Exception:
        # Any failure rolls the whole transaction back: no ledger
        # event without its memory, no memory without its event.
        conn.rollback()
        raise
    finally:
        conn.close()


# ----------------------------------------------------------------------
# In-memory path: snapshot and restore stand in for the transaction
# ----------------------------------------------------------------------


def _snapshot_state(obj: Any) -> dict:
    """Deep copy of one store's state, for rollback."""
    return copy.deepcopy(obj.__dict__)


def _restore_state(obj: Any, snapshot: dict) -> None:
    """Put one store's state back after a failed write."""
    obj.__dict__.clear()
    obj.__dict__.update(copy.deepcopy(snapshot))


def _commit_memory(
    user_id: str,
    task_id: str,
    agent_id: str,
    allowed: list[TurnItem],
    refused: list[dict],
    *,
    ledger: Any,
    durable_store: MemoryDurableStore,
    fact_store: MemoryFactStore,
    now: datetime,
) -> dict:
    """In-memory write-through: same steps, snapshot rollback."""
    snapshots = [
        (ledger, _snapshot_state(ledger)),
        (durable_store, _snapshot_state(durable_store)),
        (fact_store, _snapshot_state(fact_store)),
    ]
    try:
        event_ids: list[str] = []
        fact_ids: list[str] = []
        for item in allowed:
            event = ledger.append_event(
                task_id=task_id,
                agent_id=agent_id,
                event_type=LEDGER_EVENT_TYPE,
                content=_ledger_content(item),
            )
            event_ids.append(str(event["id"]))
            if item.entry_class in FACT_CLASSES:
                stored = facts_module.record_fact(
                    user_id,
                    item.derived_key(),
                    item.text,
                    source=item.source,
                    agent_id=agent_id,
                    store=fact_store,
                    trust_check=False,
                    now=now,
                )
                fact_ids.append(str(stored["id"]))
            else:  # outcome: today's journal entry, not a fact row
                existing = durable_store.get_journal_entry(user_id, now.date())
                previous_text = str(existing["text"]) if existing else ""
                previous_ids = list(existing["source_event_ids"]) if existing else []
                durable_store.save_journal_entry(
                    user_id,
                    now.date(),
                    _append_journal_text(previous_text, item.text),
                    [*previous_ids, event_ids[-1]],
                )
        result = durable_module.rebuild_memory(
            user_id,
            store=durable_store,
            fact_store=fact_store,
            summaries=[],
            episodes=[],
            journal_entries=durable_store.list_journal_entries(user_id),
            now=now,
        )
        return {
            "user_id": user_id,
            "task_id": task_id,
            "agent_id": agent_id,
            "completed": True,
            "event_ids": event_ids,
            "fact_ids": fact_ids,
            "document_version": result["version"],
            "document_changed": result["changed"],
            "refused": refused,
            "retried_from_queue": None,
        }
    except Exception:
        for obj, snapshot in snapshots:
            _restore_state(obj, snapshot)
        raise


# ----------------------------------------------------------------------
# The one call
# ----------------------------------------------------------------------


def commit_turn(
    user_id: str,
    task_id: str,
    agent_id: str,
    items: Any,
    *,
    dsn: str | None = None,
    ledger: Any = None,
    durable_store: Any = None,
    fact_store: Any = None,
    retry_queue: Any = None,
    reply_text: str | None = None,
    now: datetime | None = None,
    enqueue_on_failure: bool = True,
) -> dict:
    """Write one turn through: ledger plus durable memory, atomically.

    ``user_id`` must be a UUID string (the fact tables key users
    by UUID). ``task_id`` and ``agent_id`` must be non-empty
    strings. ``items`` is a list of dicts or :class:`TurnItem`
    objects (see :func:`_normalize_items` for the shape).

    On success returns ``{"completed": True, "event_ids": [...],
    "fact_ids": [...], "document_version": n,
    "document_changed": bool, "refused": [...]}``: the ledger
    events landed, the fact rows landed, and the rebuilt MEMORY.md
    document landed, all in one transaction. On any write failure
    the transaction rolls back, the failed write is queued for
    retry (unless ``enqueue_on_failure`` is False, which the
    retry runner uses so a failed retry does not queue a copy of
    itself), and :class:`CommitTurnError` is raised: the turn
    must not report success. Validation failures raise
    ``ValueError`` before any write and are never queued.

    Stores default to Postgres (resolved from ``dsn`` or the
    environment, the same as the other memory stores). Pass a
    ``MemoryDurableStore`` (plus a ledger object with
    ``append_event`` and a fact store) for the in-memory path.
    """
    now = now or _now()
    user_id = _validate_user_id(user_id)
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("task_id must be a non-empty string")
    if not isinstance(agent_id, str) or not agent_id:
        raise ValueError("agent_id must be a non-empty string")
    normalized = _normalize_items(items)
    if not normalized:
        return {
            "user_id": user_id,
            "task_id": task_id,
            "agent_id": agent_id,
            "completed": True,
            "event_ids": [],
            "fact_ids": [],
            "document_version": None,
            "document_changed": False,
            "refused": [],
            "retried_from_queue": None,
        }
    allowed, refused = _trust_check(normalized)
    if not allowed:
        return {
            "user_id": user_id,
            "task_id": task_id,
            "agent_id": agent_id,
            "completed": True,
            "event_ids": [],
            "fact_ids": [],
            "document_version": None,
            "document_changed": False,
            "refused": refused,
            "retried_from_queue": None,
        }
    resolved_dsn = resolve_dsn(dsn)
    memory_path = isinstance(durable_store, MemoryDurableStore)
    if memory_path:
        if ledger is None:
            raise ValueError("the in-memory path needs ledger= (an object with append_event)")
        queue = retry_queue if retry_queue is not None else MemoryRetryQueue()
        payload = _commit_payload(user_id, task_id, agent_id, normalized, reply_text)
        try:
            return _commit_memory(
                user_id,
                task_id,
                agent_id,
                allowed,
                refused,
                ledger=ledger,
                durable_store=durable_store,
                fact_store=fact_store or MemoryFactStore(),
                now=now,
            )
        except Exception as exc:
            return _fail(user_id, task_id, payload, exc, queue, enqueue_on_failure)
    queue = retry_queue if retry_queue is not None else PostgresRetryQueue(dsn=resolved_dsn)
    payload = _commit_payload(user_id, task_id, agent_id, normalized, reply_text)
    try:
        return _commit_postgres(
            user_id,
            task_id,
            agent_id,
            allowed,
            refused,
            dsn=resolved_dsn,
            ledger=ledger if ledger is not None else Ledger(resolved_dsn),
            durable_store=(
                durable_store
                if durable_store is not None
                else PostgresDurableStore(dsn=resolved_dsn)
            ),
            now=now,
        )
    except Exception as exc:
        return _fail(user_id, task_id, payload, exc, queue, enqueue_on_failure)


def _fail(
    user_id: str,
    task_id: str,
    payload: dict,
    exc: Exception,
    queue: Any,
    enqueue_on_failure: bool,
) -> NoReturn:
    """Queue a failed write, then raise the turn-stopping error.

    The queue write is best-effort: when it fails too, the error
    still raises, marked not queued, so the failure is never
    hidden behind a second failure.
    """
    queued = False
    retry_id: str | None = None
    if enqueue_on_failure:
        try:
            retry_id = queue.enqueue(payload, str(exc))
            queued = True
        except Exception:
            queued = False
            retry_id = None
    raise CommitTurnError(
        str(exc), user_id=user_id, task_id=task_id, queued=queued, retry_id=retry_id
    ) from exc


# ----------------------------------------------------------------------
# Reading the queue, retrying it
# ----------------------------------------------------------------------


def _default_queue(dsn: str | None) -> Any:
    """The retry queue for a DSN (Postgres, resolved like the other stores)."""
    return PostgresRetryQueue(dsn=dsn)


def list_pending_retries(
    dsn: str | None = None, *, retry_queue: Any = None, limit: int = 50
) -> list[dict]:
    """Queued failed commits, oldest first: the failures on show.

    Each entry carries its payload (so the call can be rebuilt),
    its error, its attempt count, and its queue id. An empty list
    means no failed write is waiting.
    """
    queue = retry_queue if retry_queue is not None else _default_queue(dsn)
    return queue.list_pending(limit=limit)


def retry_pending(
    dsn: str | None = None,
    *,
    retry_queue: Any = None,
    ledger: Any = None,
    durable_store: Any = None,
    fact_store: Any = None,
    limit: int = 10,
) -> dict:
    """Re-attempt queued commits, oldest first.

    A commit that lands marks its row done. A commit that fails
    again records the new error and grows its attempt count; at
    :data:`MAX_RETRY_ATTEMPTS` it goes dead and is never retried
    again automatically. Returns ``{"attempted", "succeeded",
    "still_pending", "dead"}`` with queue row ids in each list.
    A failed retry never queues a copy of itself.
    """
    queue = retry_queue if retry_queue is not None else _default_queue(dsn)
    pending = queue.list_pending(limit=limit)
    attempted = len(pending)
    succeeded: list[str] = []
    still_pending: list[str] = []
    dead: list[str] = []
    for entry in pending:
        payload = entry["payload"]
        try:
            commit_turn(
                payload["user_id"],
                payload["task_id"],
                payload["agent_id"],
                payload["items"],
                dsn=dsn,
                ledger=ledger,
                durable_store=durable_store,
                fact_store=fact_store,
                retry_queue=queue,
                reply_text=payload.get("reply_text"),
                enqueue_on_failure=False,
            )
            queue.mark_done(entry["id"])
            succeeded.append(entry["id"])
        except CommitTurnError as exc:
            status = queue.note_attempt(entry["id"], exc.reason)
            if status == "dead":
                dead.append(entry["id"])
            else:
                still_pending.append(entry["id"])
    return {
        "attempted": attempted,
        "succeeded": succeeded,
        "still_pending": still_pending,
        "dead": dead,
    }


# ----------------------------------------------------------------------
# Turn-loop seam
# ----------------------------------------------------------------------


def make_turn_committer(
    dsn: str | None = None,
    *,
    ledger: Any = None,
    durable_store: Any = None,
    fact_store: Any = None,
    retry_queue: Any = None,
    max_reply_chars: int = MAX_OUTCOME_CHARS,
):
    """Build the turn loop's write-through hook.

    Returns a committer with the loop's protocol: ``committer(*,
    user_id, task_id, agent_id, reply_text, history)``. It records
    the turn's reply as one ``outcome`` item through
    :func:`commit_turn`, so a completed turn always leaves its
    outcome in the ledger and the durable memory. An empty reply
    commits nothing. A failed write raises
    :class:`CommitTurnError`, which the loop turns into a failed
    turn (the turn never reports success for an uncommitted
    outcome). ``history`` is accepted for committers that extract
    richer items; this factory records the outcome only, and
    extracting decisions, corrections, and facts from history is
    a follow-up.
    """

    def committer(
        *,
        user_id: str,
        task_id: str | None,
        agent_id: str,
        reply_text: str,
        history: Any = None,
    ) -> dict:
        del history  # this factory records the outcome only; see the docstring
        if not isinstance(reply_text, str) or not reply_text.strip():
            return {"committed": False, "reason": "empty reply, nothing to commit"}
        return commit_turn(
            user_id,
            task_id or f"turn-{uuid.uuid4().hex[:12]}",
            agent_id,
            [
                {
                    "entry_class": "outcome",
                    "text": reply_text.strip()[:max_reply_chars],
                }
            ],
            dsn=dsn,
            ledger=ledger,
            durable_store=durable_store,
            fact_store=fact_store,
            retry_queue=retry_queue,
            reply_text=reply_text.strip()[:max_reply_chars],
        )

    return committer
