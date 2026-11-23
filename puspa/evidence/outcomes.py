"""Verified outcome records (B2): the one shared write path.

Every task ends in a structured, evidenced outcome record — a
price checked, a seller checked, a task done, a payment
prepared, a decision recorded — with receipts, ledger entries,
and sources attached. Not raw text.

This module is the single writer every feature uses. It does
not invent a second ledger or a second event system: one
outcome record is one ``evidence_recorded`` event on the
existing ledger (the closed set of 31 event types is
untouched), with content ``kind`` ``"outcome_record"``. The
record's ``details`` carries the type-specific payload — for
the paths B2 generalised (B10 meeting decisions, B11 split
settlements) ``details`` holds the pre-B2 event content
verbatim, so no information from the old shape is lost.

The evidence rule is enforced by the writer itself: a record
with no evidence is rejected with :class:`OutcomeError`
before anything is written. An evidence entry is a dict that
names what proves the outcome (``"kind"`` plus the ids that
let a reader find the proof: an ``evidence_id`` for a saved
evidence row, a ``meeting_id`` for minutes, an ``action_id``
for a money approval, a ``step_id`` for delegated work).

Reads are per user and per group (room): the ledger has no
``user_id`` column, so the record carries ``user_id`` and
``room_id`` in its content and the queries filter on them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from puspa.db import connect as _db_connect
from puspa.db import dict_row

from puspa.accounts.ports import resolve_dsn
from puspa.ledger import Ledger

from .evidence import EvidenceRecord

__all__ = [
    "OUTCOME_EVENT_TYPE",
    "OUTCOME_KIND",
    "OUTCOME_TYPES",
    "OutcomeError",
    "OutcomeRecord",
    "evidence_ref",
    "get_outcome",
    "is_outcome_event",
    "list_outcomes_for_group",
    "list_outcomes_for_user",
    "write_outcome",
]

#: The ledger event type every outcome record is written as.
#: The closed set of 31 types stays closed: no new type.
OUTCOME_EVENT_TYPE = "evidence_recorded"

#: The content marker that identifies an outcome record among
#: the other ``evidence_recorded`` events.
OUTCOME_KIND = "outcome_record"

#: The named record types (plan section 7, mechanism 1). The
#: set is open: ``write_outcome`` accepts any other non-empty
#: ``outcome_type`` string, so a new feature names its own
#: type instead of waiting for a schema change.
OUTCOME_TYPES = frozenset(
    {
        "price_checked",
        "seller_checked",
        "task_done",
        "payment_prepared",
        "decision_recorded",
    }
)


class OutcomeError(ValueError):
    """An outcome record was rejected by the writer.

    Raised when a record has no evidence, or when any other
    field of the record is missing or malformed. Nothing is
    written when this is raised.
    """


@dataclass(frozen=True)
class OutcomeRecord:
    """One verified outcome record, as written to the ledger.

    ``outcome_id`` is the ledger event id; ``created_at`` is
    the ledger's recorded time. ``evidence`` is the proof the
    writer required; ``receipts`` are the artefacts the
    outcome produced (a settlement row, an approval); ``sources``
    are the URLs the outcome rests on; ``details`` is the
    type-specific payload.
    """

    outcome_id: str
    outcome_type: str
    user_id: str
    room_id: str | None
    subject_id: str | None
    summary: str
    evidence: list[dict]
    receipts: list[dict]
    sources: list[str]
    details: dict
    created_at: datetime
    ledger_task_id: str
    event_hash: str

    def to_dict(self) -> dict:
        """The record as plain data (JSON-safe except datetimes)."""
        return {
            "outcome_id": self.outcome_id,
            "outcome_type": self.outcome_type,
            "user_id": self.user_id,
            "room_id": self.room_id,
            "subject_id": self.subject_id,
            "summary": self.summary,
            "evidence": [dict(entry) for entry in self.evidence],
            "receipts": [dict(entry) for entry in self.receipts],
            "sources": list(self.sources),
            "details": dict(self.details),
            "created_at": self.created_at.isoformat(),
            "ledger_task_id": self.ledger_task_id,
            "event_hash": self.event_hash,
        }


def evidence_ref(evidence: EvidenceRecord) -> dict:
    """An evidence entry pointing at a saved evidence row.

    The row itself (value, excerpt, verdict) stays in the
    ``evidence`` table; the entry carries the ids a reader
    needs to find it. The verifier's verdict is copied in so
    a query can tell a verified price from an unverified one
    without a second lookup.
    """
    return {
        "kind": "evidence_record",
        "evidence_id": evidence.evidence_id,
        "source_url": evidence.source_url,
        "excerpt": evidence.text_excerpt,
        "verdict": evidence.verdict,
    }


def _require_text(value, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OutcomeError(f"{name} must be a non-empty string")
    return value.strip()


def _require_evidence(evidence) -> list[dict]:
    """The evidence rule: at least one entry, each naming its kind."""
    if not isinstance(evidence, list) or not evidence:
        raise OutcomeError(
            "an outcome record needs at least one evidence entry: "
            'evidence must be a non-empty list of {"kind": ..., ...} dicts'
        )
    checked: list[dict] = []
    for entry in evidence:
        if not isinstance(entry, dict) or not entry:
            raise OutcomeError("each evidence entry must be a non-empty dict")
        if not isinstance(entry.get("kind"), str) or not entry["kind"].strip():
            raise OutcomeError('each evidence entry must name its "kind"')
        checked.append(dict(entry))
    return checked


def _require_entries(entries, name: str) -> list[dict]:
    if entries is None:
        return []
    if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
        raise OutcomeError(f"{name} must be a list of dicts")
    return [dict(entry) for entry in entries]


def _require_sources(sources) -> list[str]:
    if sources is None:
        return []
    if not isinstance(sources, list) or any(
        not isinstance(url, str) or not url.strip() for url in sources
    ):
        raise OutcomeError("sources must be a list of non-empty URL strings")
    return [url.strip() for url in sources]


def _record_from_event(event: dict) -> OutcomeRecord:
    """A ledger event dict (as ``Ledger`` returns) -> the record."""
    content = event["content"]
    return OutcomeRecord(
        outcome_id=str(event["id"]),
        outcome_type=content["outcome_type"],
        user_id=content["user_id"],
        room_id=content.get("room_id"),
        subject_id=content.get("subject_id"),
        summary=content["summary"],
        evidence=[dict(entry) for entry in content.get("evidence", [])],
        receipts=[dict(entry) for entry in content.get("receipts", [])],
        sources=list(content.get("sources", [])),
        details=dict(content.get("details", {})),
        created_at=event["created_at"],
        ledger_task_id=event["task_id"],
        event_hash=event["hash"],
    )


def _record_from_row(row: dict) -> OutcomeRecord:
    """A raw ``events`` row (dict_row) -> the record."""
    return _record_from_event(
        {
            "id": row["id"],
            "created_at": row["created_at"],
            "task_id": row["task_id"],
            "content": row["content"],
            "hash": row["hash"],
        }
    )


def is_outcome_event(event: dict) -> bool:
    """True when a ledger event dict is an outcome record."""
    content = event.get("content") or {}
    return (
        event.get("type") == OUTCOME_EVENT_TYPE
        and isinstance(content, dict)
        and content.get("kind") == OUTCOME_KIND
    )


def write_outcome(
    ledger: Ledger,
    *,
    task_id: str,
    agent_id: str,
    outcome_type: str,
    user_id: str,
    room_id: str | None = None,
    subject_id: str | None = None,
    summary: str,
    evidence: list[dict],
    receipts: list[dict] | None = None,
    sources: list[str] | None = None,
    details: dict | None = None,
    valid_at: datetime | None = None,
) -> OutcomeRecord:
    """Write one outcome record through the ledger.

    The record is rejected (:class:`OutcomeError`) when it
    carries no evidence, or when any field is missing or
    malformed — nothing is written in that case. ``task_id``
    is the ledger chain the record joins (the caller picks
    the chain the outcome belongs to: the meeting, the split,
    the task, the comparison). ``valid_at`` is the bitemporal
    instant the outcome was true in the world, when the
    caller knows it (see ``docs/bitemporal-state.md``).
    """
    if ledger is None:
        raise OutcomeError("a ledger is required to write an outcome record")
    task_id = _require_text(task_id, "task_id")
    agent_id = _require_text(agent_id, "agent_id")
    outcome_type = _require_text(outcome_type, "outcome_type")
    user_id = _require_text(user_id, "user_id")
    if room_id is not None:
        room_id = _require_text(room_id, "room_id")
    if subject_id is not None:
        subject_id = _require_text(subject_id, "subject_id")
    summary = _require_text(summary, "summary")
    evidence = _require_evidence(evidence)
    receipts = _require_entries(receipts, "receipts")
    sources = _require_sources(sources)
    if details is not None and not isinstance(details, dict):
        raise OutcomeError("details must be a dict")
    event = ledger.append_event(
        task_id=task_id,
        agent_id=agent_id,
        event_type=OUTCOME_EVENT_TYPE,
        content={
            "kind": OUTCOME_KIND,
            "outcome_type": outcome_type,
            "user_id": user_id,
            "room_id": room_id,
            "subject_id": subject_id,
            "summary": summary,
            "evidence": evidence,
            "receipts": receipts,
            "sources": sources,
            "details": dict(details) if details is not None else {},
        },
        valid_at=valid_at,
    )
    return _record_from_event(event)


def _query_outcomes(
    dsn,
    *,
    where: str,
    params: list,
    outcome_type: str | None,
    limit: int,
) -> list[OutcomeRecord]:
    if outcome_type is not None:
        outcome_type = _require_text(outcome_type, "outcome_type")
        where += " AND content->>'outcome_type' = %s"
        params.append(outcome_type)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")
    query = (
        "SELECT * FROM events WHERE type = 'evidence_recorded' "
        "AND content->>'kind' = 'outcome_record' "
        f"AND {where} ORDER BY created_at ASC, id ASC LIMIT %s"
    )
    params.append(limit)
    with _db_connect(resolve_dsn(dsn), row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    return [_record_from_row(row) for row in rows]


def list_outcomes_for_user(
    dsn,
    user_id: str,
    *,
    outcome_type: str | None = None,
    limit: int = 100,
) -> list[OutcomeRecord]:
    """Every outcome record of one user, oldest first.

    Only records whose ``user_id`` is this user — a user can
    never read another user's records through this query.
    ``outcome_type`` narrows to one type when given.
    """
    user_id = _require_text(user_id, "user_id")
    return _query_outcomes(
        dsn,
        where="content->>'user_id' = %s",
        params=[user_id],
        outcome_type=outcome_type,
        limit=limit,
    )


def list_outcomes_for_group(
    dsn,
    room_id: str,
    *,
    outcome_type: str | None = None,
    limit: int = 100,
) -> list[OutcomeRecord]:
    """Every outcome record of one group (room), oldest first.

    Only records whose ``room_id`` is this room. Records with
    no room (single-user outcomes) never appear here.
    ``outcome_type`` narrows to one type when given.
    """
    room_id = _require_text(room_id, "room_id")
    return _query_outcomes(
        dsn,
        where="content->>'room_id' = %s",
        params=[room_id],
        outcome_type=outcome_type,
        limit=limit,
    )


def get_outcome(dsn, outcome_id: str) -> OutcomeRecord | None:
    """One outcome record by its ledger event id.

    Returns None when the id does not exist, is not a UUID,
    or is not an outcome record.
    """
    try:
        UUID(str(outcome_id))
    except (ValueError, TypeError, AttributeError):
        return None
    with _db_connect(resolve_dsn(dsn), row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM events WHERE id = %s", (str(outcome_id),))
        row = cur.fetchone()
    if row is None or row["type"] != OUTCOME_EVENT_TYPE:
        return None
    content = row["content"] or {}
    if not isinstance(content, dict) or content.get("kind") != OUTCOME_KIND:
        return None
    return _record_from_row(row)
