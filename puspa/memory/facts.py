"""Versioned profile facts (G7, DuskBot pattern rewritten for Puspa).

Facts live in the existing ``profile_facts`` table (see
migration 0002_memory). G7 widens that table: the status set
gains ``"superseded"`` and ``"expired"``, and two columns are added
(``superseded_by``, ``updated_at``). A replaced / expired fact
becomes history, never deleted, so a question about the past can
still be answered from the version valid at that date
(:func:`get_fact_at`). Any superseded or expired fact can be brought
back with :func:`restore_fact`.

Shared record shape (the interface agreed with the G24 worker)::

    {"id", "user_id", "key", "content", "status",
     "valid_from", "valid_until", "superseded_by", "source",
     "created_at", "updated_at"}

``content`` maps to the ``value`` column of ``profile_facts``;
``status`` is ``"active"`` / ``"superseded"`` / ``"expired"``
(plus the legacy ``"proposed"`` / ``"rejected"`` / ``"replaced"``
values the older ``profile.py`` layer still writes, which history
reads tolerate). Facts with source ``"inferred"`` are stored as
``"proposed"``, never active: the Rev 7 rule that inferred facts
need user confirmation before entering the profile.

Trust gate: :func:`record_fact` runs the fact through
``puspa/memory/trust.py`` first. Only facts that pass become
permanent; blocked or external-origin content is not stored as a
fact at all — it is written to the journal (kind ``"blocked"`` /
``"quarantined"``) with its ``[blocked: ...]`` / ``[quarantined:
...]`` marker, for user review.

Stores: every operation takes a ``store``. ``PostgresFactStore``
is the production store; ``MemoryFactStore`` is the in-memory
store used by tests and by callers that need no database. Both
implement the same methods, so logic is tested without Postgres
and the Postgres path is covered by the DB tests.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import psycopg
from psycopg.rows import dict_row

from .schema import resolve_dsn

STATUSES = ("active", "superseded", "expired", "proposed", "rejected", "replaced")
ACTIVE_STATUSES = ("active",)
HISTORY_STATUSES = ("superseded", "expired", "replaced", "rejected")


def _now():
    return datetime.now(UTC)


def _new_id():
    return str(uuid.uuid4())


def _public(row):
    """Normalize one store row to the shared record shape."""
    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "key": row["key"],
        "content": row["value"] if "value" in row else row["content"],
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


class MemoryFactStore:
    """In-memory fact store: same interface as the Postgres store."""

    def __init__(self):
        self._facts = {}

    def insert(self, record):
        stored = dict(record)
        self._facts[stored["id"]] = stored
        return dict(stored)

    def get(self, fact_id, user_id):
        row = self._facts.get(str(fact_id))
        if row is None or str(row["user_id"]) != str(user_id):
            return None
        return dict(row)

    def list_for_key(self, user_id, key, agent_id=None):
        rows = [
            r
            for r in self._facts.values()
            if str(r["user_id"]) == str(user_id)
            and r["key"] == key
            and (agent_id is None or r.get("agent_id") == agent_id)
        ]
        rows.sort(key=lambda r: (r["valid_from"], r["created_at"]))
        return [dict(r) for r in rows]

    def list_user(self, user_id, statuses=None, agent_id=None):
        rows = [
            r
            for r in self._facts.values()
            if str(r["user_id"]) == str(user_id)
            and (statuses is None or r["status"] in statuses)
            and (agent_id is None or r.get("agent_id") == agent_id)
        ]
        rows.sort(key=lambda r: (r["valid_from"], r["created_at"]))
        return [dict(r) for r in rows]

    def update(self, fact_id, user_id, **fields):
        row = self._facts.get(str(fact_id))
        if row is None or str(row["user_id"]) != str(user_id):
            return None
        row.update(fields)
        return dict(row)

    def apply_snapshot(self, snapshot):
        row = self._facts.get(str(snapshot["id"]))
        if row is None:
            return None
        for field in (
            "content",
            "status",
            "valid_from",
            "valid_until",
            "superseded_by",
            "source",
            "confidence",
            "source_detail",
        ):
            if field in snapshot:
                row[field] = snapshot[field]
        row["updated_at"] = _now()
        return dict(row)

    def delete_rows(self, fact_ids, user_id):
        """Hard-delete rows by id (forget path). Returns ids deleted."""
        deleted = []
        for fact_id in fact_ids:
            row = self._facts.get(str(fact_id))
            if row is not None and str(row["user_id"]) == str(user_id):
                del self._facts[str(fact_id)]
                deleted.append(str(fact_id))
        return deleted

    def expire_snapshot(self, record):
        return self.update(
            record["id"], record["user_id"], status="expired", valid_until=_now(), updated_at=_now()
        )


class PostgresFactStore:
    """Postgres fact store over the ``profile_facts`` table."""

    def __init__(self, dsn=None):
        self.dsn = resolve_dsn(dsn)

    def _connect(self):
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def insert(self, record):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO profile_facts (id, user_id, agent_id, key, "
                    "value, source, status, valid_from, valid_until, "
                    "superseded_by, confidence, source_detail, created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "RETURNING *",
                    (
                        record["id"],
                        record["user_id"],
                        record.get("agent_id"),
                        record["key"],
                        record["content"],
                        record["source"],
                        record["status"],
                        record["valid_from"],
                        record["valid_until"],
                        record["superseded_by"],
                        record.get("confidence"),
                        record.get("source_detail"),
                        record["created_at"],
                        record["updated_at"],
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        return _public(row)

    def get(self, fact_id, user_id):
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM profile_facts WHERE id = %s AND user_id = %s",
                (fact_id, user_id),
            )
            row = cur.fetchone()
        return _public(row) if row is not None else None

    def list_for_key(self, user_id, key, agent_id=None):
        with self._connect() as conn, conn.cursor() as cur:
            if agent_id is None:
                cur.execute(
                    "SELECT * FROM profile_facts WHERE user_id = %s "
                    "AND key = %s ORDER BY valid_from ASC, created_at ASC",
                    (user_id, key),
                )
            else:
                cur.execute(
                    "SELECT * FROM profile_facts WHERE user_id = %s "
                    "AND key = %s AND agent_id IS NOT DISTINCT FROM %s "
                    "ORDER BY valid_from ASC, created_at ASC",
                    (user_id, key, agent_id),
                )
            return [_public(r) for r in cur.fetchall()]

    def list_user(self, user_id, statuses=None, agent_id=None):
        query = "SELECT * FROM profile_facts WHERE user_id = %s"
        params = [user_id]
        if statuses is not None:
            query += " AND status = ANY(%s)"
            params.append(list(statuses))
        if agent_id is not None:
            query += " AND agent_id IS NOT DISTINCT FROM %s"
            params.append(agent_id)
        query += " ORDER BY valid_from ASC, created_at ASC"
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(query, params)
            return [_public(r) for r in cur.fetchall()]

    def update(self, fact_id, user_id, **fields):
        sets = []
        params = []
        for field, value in fields.items():
            col = "value" if field == "content" else field
            sets.append(f"{col} = %s")
            params.append(value)
        if not sets:
            return self.get(fact_id, user_id)
        params.extend([fact_id, user_id])
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE profile_facts SET {', '.join(sets)} "
                    "WHERE id = %s AND user_id = %s RETURNING *",
                    params,
                )
                row = cur.fetchone()
            conn.commit()
        return _public(row) if row is not None else None

    def apply_snapshot(self, snapshot):
        return self.update(
            snapshot["id"],
            snapshot["user_id"],
            content=snapshot["content"],
            status=snapshot["status"],
            valid_from=snapshot["valid_from"],
            valid_until=snapshot["valid_until"],
            superseded_by=snapshot["superseded_by"],
            source=snapshot["source"],
            confidence=snapshot.get("confidence"),
            source_detail=snapshot.get("source_detail"),
            updated_at=_now(),
        )

    def delete_rows(self, fact_ids, user_id):
        """Hard-delete rows by id (forget path). Returns ids deleted."""
        ids = [str(fact_id) for fact_id in fact_ids]
        if not ids:
            return []
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM profile_facts WHERE user_id = %s AND id = ANY(%s::uuid[]) "
                    "RETURNING id",
                    (user_id, ids),
                )
                deleted = [str(row["id"]) for row in cur.fetchall()]
            conn.commit()
        return deleted

    def expire_snapshot(self, record):
        return self.update(
            record["id"], record["user_id"], status="expired", valid_until=_now(), updated_at=_now()
        )


def default_store(dsn=None):
    """Production store (Postgres)."""
    return PostgresFactStore(dsn=dsn)


def _validate_confidence(confidence):
    if confidence is None:
        return None
    value = float(confidence)
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"confidence must be between 0 and 1, got {confidence!r}")
    return value


def record_fact(
    user_id,
    key,
    content,
    source="user_stated",
    agent_id=None,
    store=None,
    origin="user",
    trust_check=True,
    journal=None,
    now=None,
    confidence=None,
    source_detail=None,
):
    """Record a fact, superseding the active version of the same key.

    Runs the trust gate first (unless ``trust_check`` is False):
    blocked or quarantined content is never stored as a fact. It is
    logged to ``journal`` (when given) with its marker and the
    return value carries ``"stored": False`` and ``"verdict"``.
    Otherwise the fact is stored with ``"stored": True`` and any
    active fact with the same key (+ agent) is superseded, linked
    through ``superseded_by``. Source ``"inferred"`` stores the
    fact as ``"proposed"``, never active.

    ``confidence`` (0.0-1.0, optional) is persisted with the fact
    so the memory screen can show it; ``source_detail`` is a short
    human-readable provenance note (for example "from the chat on
    3 November 2026") shown next to the fact.
    """
    now = now or _now()
    if not key or not isinstance(key, str):
        raise ValueError("key must be a non-empty string")
    if not isinstance(content, str) or content == "":
        raise ValueError("content must be a non-empty string")
    if source not in ("user_stated", "user_confirmed", "inferred"):
        raise ValueError(f"unknown source: {source!r}")
    confidence = _validate_confidence(confidence)
    if source_detail is not None and not isinstance(source_detail, str):
        raise ValueError("source_detail must be a string")
    store = store or default_store()
    if trust_check:
        from . import trust as trust_module

        verdict = trust_module.check_text(content, origin=origin)
        if verdict["verdict"] in ("blocked", "quarantine"):
            if journal is not None:
                kind = "quarantined" if verdict["verdict"] == "quarantine" else "blocked"
                journal.log(
                    "quarantine" if verdict["verdict"] == "quarantine" else "blocked",
                    fact_id=None,
                    before=None,
                    after=None,
                    kind=kind,
                    note=verdict["marked_text"],
                    origin=origin,
                )
            return {
                "stored": False,
                "quarantined": True,
                "verdict": verdict["verdict"],
                "reasons": verdict["reasons"],
                "marked_text": verdict["marked_text"],
                "redacted_text": verdict["redacted_text"],
            }
        content = verdict["redacted_text"]
    status = "proposed" if source == "inferred" else "active"
    fact_id = _new_id()
    record = {
        "id": fact_id,
        "user_id": str(user_id),
        "agent_id": agent_id,
        "key": key,
        "content": content,
        "status": status,
        "valid_from": now,
        "valid_until": None,
        "superseded_by": None,
        "source": source,
        "confidence": confidence,
        "source_detail": source_detail,
        "created_at": now,
        "updated_at": now,
    }
    if status == "active":
        for old in store.list_for_key(user_id, key, agent_id=agent_id):
            if old["status"] == "active":
                store.update(
                    old["id"],
                    user_id,
                    status="superseded",
                    valid_until=now,
                    superseded_by=fact_id,
                    updated_at=now,
                )
    stored = store.insert(record)
    stored["stored"] = True
    if journal is not None:
        journal.log("record", fact_id=stored["id"], before=None, after=stored, origin=origin)
    return stored


def supersede_fact(
    user_id,
    fact_id,
    replacement_content=None,
    source="user_confirmed",
    store=None,
    journal=None,
    now=None,
):
    """Supersede a fact, optionally recording a replacement.

    With ``replacement_content`` the replacement is recorded as a
    new active fact with the same key and linked through
    ``superseded_by``; returns ``{"old", "new"}``. Without it the
    fact is marked ``"superseded"`` with no successor; returns
    ``{"old", "new": None}``. Raises KeyError for an unknown fact.
    """
    now = now or _now()
    store = store or default_store()
    old = store.get(fact_id, user_id)
    if old is None:
        raise KeyError(f"fact not found: {fact_id}")
    if replacement_content is not None:
        new = record_fact(
            user_id,
            old["key"],
            replacement_content,
            source=source,
            agent_id=old.get("agent_id"),
            store=store,
            journal=None,
            now=now,
        )
        if journal is not None:
            journal.log("supersede", fact_id=old["id"], before=old, after=new)
        return {"old": store.get(fact_id, user_id), "new": new}
    updated = store.update(fact_id, user_id, status="superseded", valid_until=now, updated_at=now)
    if journal is not None:
        journal.log("supersede", fact_id=fact_id, before=old, after=updated)
    return {"old": updated, "new": None}


def expire_fact(user_id, fact_id, store=None, journal=None, now=None):
    """Expire a fact: status ``"expired"``, ``valid_until`` set.

    The row stays in history. Raises KeyError for an unknown fact.
    """
    now = now or _now()
    store = store or default_store()
    old = store.get(fact_id, user_id)
    if old is None:
        raise KeyError(f"fact not found: {fact_id}")
    updated = store.update(fact_id, user_id, status="expired", valid_until=now, updated_at=now)
    if journal is not None:
        journal.log("expire", fact_id=fact_id, before=old, after=updated)
    return updated


def restore_fact(user_id, fact_id, store=None, journal=None, now=None):
    """Restore a superseded / expired fact to active.

    Any currently active fact with the same key is superseded by
    the restored one (linked through ``superseded_by``). Raises
    KeyError for an unknown fact and ValueError when the fact is
    already active or is a rejected proposal.
    """
    now = now or _now()
    store = store or default_store()
    old = store.get(fact_id, user_id)
    if old is None:
        raise KeyError(f"fact not found: {fact_id}")
    if old["status"] == "active":
        raise ValueError(f"fact is already active: {fact_id}")
    if old["status"] in ("proposed", "rejected"):
        raise ValueError(f"cannot restore a {old['status']} fact: {fact_id}")
    for other in store.list_for_key(user_id, old["key"], agent_id=old.get("agent_id")):
        if other["status"] == "active" and other["id"] != fact_id:
            store.update(
                other["id"],
                user_id,
                status="superseded",
                valid_until=now,
                superseded_by=fact_id,
                updated_at=now,
            )
    restored = store.update(
        fact_id, user_id, status="active", valid_until=None, superseded_by=None, updated_at=now
    )
    if journal is not None:
        journal.log("restore", fact_id=fact_id, before=old, after=restored)
    return restored


def get_fact_history(user_id, key, store=None, agent_id=None):
    """All versions of one fact key, oldest first (history included)."""
    store = store or default_store()
    return store.list_for_key(user_id, key, agent_id=agent_id)


def get_fact_at(user_id, key, at, store=None, agent_id=None):
    """The fact version valid at datetime ``at`` (past questions).

    Returns the version whose ``[valid_from, valid_until)`` range
    contains ``at``, or None when no version was valid then.
    """
    store = store or default_store()
    candidates = []
    for fact in store.list_for_key(user_id, key, agent_id=agent_id):
        valid_from = fact["valid_from"]
        valid_until = fact["valid_until"]
        if valid_from <= at and (valid_until is None or at < valid_until):
            candidates.append(fact)
    if not candidates:
        return None
    candidates.sort(key=lambda f: f["valid_from"])
    return candidates[-1]


def list_active_facts(user_id, store=None, agent_id=None):
    """Active facts of the user (the current profile layer)."""
    store = store or default_store()
    return store.list_user(user_id, statuses=("active",), agent_id=agent_id)


def forget_fact(user_id, fact_id, store=None, journal=None):
    """Forget one fact: hard-delete it and all its past versions.

    This is the data-subject deletion right ("forget this"), not
    the soft delete: the fact row and every superseded / expired /
    rejected version of the same key are removed from the store,
    so no copy of the fact remains in the profile layer. Raises
    KeyError for an unknown fact.

    Honest limit: forgetting removes the fact, not the chat
    episodes it was learned from — episodes are the raw history
    and stay until account deletion. A forgotten fact is never
    recalled as a profile fact again, and no fact content is
    written to the journal entry for the forget (only the fact id
    and key), so the forget itself keeps no copy of the content.
    """
    store = store or default_store()
    fact = store.get(fact_id, user_id)
    if fact is None:
        raise KeyError(f"fact not found: {fact_id}")
    versions = store.list_for_key(user_id, fact["key"], agent_id=fact.get("agent_id"))
    forgotten = store.delete_rows([version["id"] for version in versions], user_id)
    if journal is not None:
        journal.log(
            "forget",
            fact_id=str(fact_id),
            before=None,
            after=None,
            kind="change",
            note=f"forgot fact {fact['key']}",
            origin="user",
        )
    return {"forgotten_ids": forgotten, "key": fact["key"], "user_id": str(user_id)}
