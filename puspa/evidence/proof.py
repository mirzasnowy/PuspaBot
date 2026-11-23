"""Trust / proof surface (B4): the ledger, shown to the user.

The hash chain, approval receipts, and audit trail already exist
(``puspa.ledger``, ``puspa.policy``, ``puspa.sentinel``) but lived
in logs. This module turns them into something a user can read:

- :class:`Receipt`: a human-readable receipt for one ledger event
  — what happened, when, the event hash, its position in the hash
  chain, and the evidence it rests on. Every money or send action
  gets one; the receipt says so explicitly (``action_kind``).
- Evidence on answers: :func:`attach_proof` takes an answer (or an
  outcome record) and returns it with its evidence list and receipt
  attached. An answer with no evidence is flagged
  (``proven: False``), never silently shown as proven.
- Audit-trail export: :func:`build_audit_trail_export` dumps one
  user's own events — with receipts — as one portable JSON
  document, bounded and paginated. It reuses the K12 export shape
  (``puspa.ledger.export``: ``format`` / ``version`` /
  ``exported_at`` / ``counts`` / ``excluded``) and the K12
  ownership rule (the ledger has no ``user_id`` column, so events
  belong to a user through their agents, plus outcome records
  that carry the ``user_id`` in their content).

Nothing here writes a second ledger or a second audit log: every
receipt is a read over the one existing hash chain, and every
verification recomputes the chain hash from the stored event.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from puspa.db import connect as _db_connect
from puspa.db import dict_row
from puspa.ledger import Ledger, compute_event_hash
from puspa.ledger.audit import MAX_EXPORT_ROWS
from puspa.memory.trust import redact_secrets
from puspa.policy.approvals import GATE_KIND_FOR_ACTION, classify_action

if TYPE_CHECKING:
    from puspa.check_before_buy.answer import CheckAnswer
    from puspa.evidence.outcomes import OutcomeRecord

__all__ = [
    "AUDIT_TRAIL_EXCLUDED",
    "AUDIT_TRAIL_FORMAT",
    "AUDIT_TRAIL_VERSION",
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "Receipt",
    "answer_proof",
    "attach_proof",
    "build_audit_trail_export",
    "build_receipt",
    "classify_receipt_action",
    "describe_event",
    "event_belongs_to_user",
    "get_event",
    "linked_evidence",
    "list_user_events",
    "outcome_with_proof",
    "user_agent_ids",
    "verify_receipt",
]

#: Default DSN (peer auth as the OS user, no password).
DEFAULT_DSN = None

#: Format name + version of the audit-trail export document.
AUDIT_TRAIL_FORMAT = "puspa-audit-trail"
AUDIT_TRAIL_VERSION = 1

#: Default and maximum page size for the audit trail. The hard
#: bound reuses the K12/G6 export cap (``MAX_EXPORT_ROWS``): no
#: single export ever returns more rows than that.
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = MAX_EXPORT_ROWS

#: What the audit-trail export deliberately leaves out (also
#: written into the document itself, so the gap is visible).
AUDIT_TRAIL_EXCLUDED = (
    "other users' events: every query filters on this user's agents or user id",
    "vault ciphertext: secret values are never exported",
    "password hashes and session token hashes: never exported",
    "secret-looking text the user typed is redacted before export",
)

#: Plain-language summary per ledger event type. Every type in the
#: closed set of 31 has one line; an unknown type falls back to
#: the generic line in :func:`describe_event`.
EVENT_SUMMARIES = {
    "task_created": "Task created",
    "plan_created": "Plan created",
    "subagent_created": "Sub-agent created",
    "handoff_proposed": "Handover proposed",
    "handoff_accepted": "Handover accepted",
    "handoff_handed_over": "Handover completed",
    "handoff_rejected": "Handover rejected",
    "handoff_expired": "Handover expired",
    "heartbeat": "Heartbeat check",
    "tool_called": "Tool called",
    "tool_completed": "Tool finished",
    "evidence_recorded": "Evidence recorded",
    "verification_completed": "Verification completed",
    "approval_requested": "Approval requested",
    "approval_granted": "Approval granted",
    "approval_rejected": "Approval rejected",
    "user_message": "Message from the user",
    "agent_message": "Message from the agent",
    "budget_exhausted": "Budget exhausted",
    "task_completed": "Task completed",
    "task_failed": "Task failed",
    "task_stopped": "Task stopped",
    "workflow_proposed": "Workflow proposed",
    "workflow_activated": "Workflow activated",
    "workflow_paused": "Workflow paused",
    "trigger_fired": "Trigger fired",
    "trigger_missed": "Trigger missed",
    "module_proposed": "Module proposed",
    "module_activated": "Module activated",
    "false_report": "False report flagged",
    "data_consent": "Data consent recorded",
}


def _jsonable(value):
    """Recursively convert a value to plain JSON data.

    Datetimes become ISO-8601 strings, UUIDs become strings, and
    every string is passed through secret redaction — the same
    shape as the K12 user export (``puspa.ledger.export``).
    """
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat() if value.tzinfo else value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _resolve_dsn(dsn: str | None) -> str | None:
    return dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)


def _connect(dsn: str | None):
    return _db_connect(_resolve_dsn(dsn), row_factory=dict_row)


def describe_event(event: dict) -> str:
    """One plain-language line saying what a ledger event did."""
    summary = EVENT_SUMMARIES.get(event.get("type", ""), "Event recorded")
    content = event.get("content") or {}
    detail = ""
    if isinstance(content, dict):
        detail = (
            content.get("summary")
            or content.get("purpose")
            or content.get("description")
            or content.get("title")
            or ""
        )
    if detail:
        return f"{summary}: {detail}"
    return summary


def classify_receipt_action(event: dict) -> str:
    """``"money"``, ``"send"``, or ``"other"`` for a ledger event.

    A receipt must say whether the action moved money or sent
    something out, because those are the actions the user has to
    be able to check. The classification reads the same markers
    the approval taxonomy already writes (G4): the content's
    ``action_kind`` / ``gate_kind``, an outcome record's
    ``outcome_type``, and — for ``tool_called`` events — the tool
    name through :func:`puspa.policy.approvals.classify_action`.
    Anything unrecognized is ``"other"``: the receipt never
    claims an action was harmless, it only labels what it can
    prove.
    """
    content = event.get("content") or {}
    if not isinstance(content, dict):
        return "other"

    def _from_gate_kind(gate_kind: str | None) -> str | None:
        if gate_kind == "prepare_payment":
            return "money"
        if gate_kind == "send":
            return "send"
        return None

    action_kind = content.get("action_kind")
    if isinstance(action_kind, str) and action_kind:
        gate_kind = GATE_KIND_FOR_ACTION.get(action_kind)
        mapped = _from_gate_kind(gate_kind)
        if mapped is not None:
            return mapped
    gate_kind = content.get("gate_kind")
    if isinstance(gate_kind, str):
        mapped = _from_gate_kind(gate_kind)
        if mapped is not None:
            return mapped
    kind = content.get("kind")
    if kind == "outcome_record":
        if content.get("outcome_type") == "payment_prepared":
            return "money"
        if content.get("outcome_type") == "settlement_prepared":
            return "money"
    if kind in ("settlement_prepared", "payment_prepared"):
        return "money"
    if kind in ("send_prepared", "message_sent", "draft_sent"):
        return "send"
    if event.get("type") == "tool_called":
        tool_name = content.get("tool") or content.get("tool_name") or ""
        if isinstance(tool_name, str) and tool_name:
            classification = classify_action(tool_name)
            if classification.kind == "pay":
                return "money"
            if classification.kind in ("send_to_other", "share", "post"):
                return "send"
    return "other"


def linked_evidence(event: dict) -> list[dict]:
    """The evidence entries linked from a ledger event's content.

    Outcome records carry their evidence list; other events may
    carry ``evidence_ids``. The entries are returned as stored —
    this function links, it does not verify the evidence itself.
    """
    content = event.get("content") or {}
    if not isinstance(content, dict):
        return []
    evidence = content.get("evidence")
    if isinstance(evidence, list):
        return [dict(entry) for entry in evidence if isinstance(entry, dict)]
    evidence_ids = content.get("evidence_ids")
    if isinstance(evidence_ids, list):
        return [{"kind": "evidence_record", "evidence_id": str(entry)} for entry in evidence_ids]
    return []


@dataclass(frozen=True)
class Receipt:
    """A human-readable receipt for one ledger event.

    ``chain_position`` is 1-based within the event's task chain,
    or None when the receipt was built without a chain lookup;
    ``chain_length`` is the chain's length when the receipt was
    built. ``chain_ok`` says whether the whole task chain
    verified at build time. ``verified`` is the receipt's own
    hash check: the stored hash recomputed from the stored
    event fields.
    """

    event_id: str
    task_id: str
    event_type: str
    summary: str
    action_kind: str
    created_at: datetime
    valid_at: datetime | None
    event_hash: str
    previous_hash: str
    chain_position: int | None
    chain_length: int
    chain_ok: bool
    verified: bool
    initiator: dict | None = None
    evidence: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        """The receipt as plain JSON-safe data."""
        return {
            "event_id": self.event_id,
            "task_id": self.task_id,
            "event_type": self.event_type,
            "summary": self.summary,
            "action_kind": self.action_kind,
            "created_at": self.created_at.isoformat(),
            "valid_at": self.valid_at.isoformat() if self.valid_at is not None else None,
            "event_hash": self.event_hash,
            "previous_hash": self.previous_hash,
            "chain_position": self.chain_position,
            "chain_length": self.chain_length,
            "chain_ok": self.chain_ok,
            "verified": self.verified,
            "initiator": dict(self.initiator) if self.initiator is not None else None,
            "evidence": [dict(entry) for entry in self.evidence],
        }


def get_event(dsn, event_id: str) -> dict | None:
    """One ledger event by id, as :class:`Ledger` returns it.

    Returns None when the id does not exist or is not a UUID.
    """
    try:
        UUID(str(event_id))
    except (ValueError, TypeError, AttributeError):
        return None
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM events WHERE id = %s", (str(event_id),))
        row = cur.fetchone()
    if row is None:
        return None
    return {
        "id": str(row["id"]),
        "created_at": row["created_at"],
        "valid_at": row.get("valid_at"),
        "task_id": row["task_id"],
        "agent_id": row["agent_id"],
        "subagent_id": row["subagent_id"],
        "type": row["type"],
        "content": row["content"],
        "parent_id": str(row["parent_id"]) if row["parent_id"] is not None else None,
        "previous_hash": row["previous_hash"],
        "hash": row["hash"],
    }


def _recompute_hash(event: dict) -> str:
    return compute_event_hash(
        task_id=event["task_id"],
        agent_id=event["agent_id"],
        subagent_id=event["subagent_id"],
        event_type=event["type"],
        content=event["content"],
        parent_id=event["parent_id"],
        previous_hash=event["previous_hash"],
        created_at=event["created_at"],
    )


def build_receipt(ledger: Ledger, event_id: str) -> Receipt | None:
    """Build the receipt for one ledger event.

    Returns None when the event does not exist. The receipt's
    ``verified`` flag is the hash recomputed from the stored
    fields; ``chain_ok`` is the whole task chain verified at
    build time. A receipt is a read: building one writes
    nothing.
    """
    event = get_event(ledger.dsn, event_id)
    if event is None:
        return None
    chain = ledger.events(event["task_id"])
    position = next(
        (index + 1 for index, item in enumerate(chain) if item["id"] == event["id"]),
        0,
    )
    chain_ok, _broken = ledger.verify_chain(event["task_id"])
    content = event["content"] or {}
    initiator = content.get("initiator") if isinstance(content, dict) else None
    return Receipt(
        event_id=event["id"],
        task_id=event["task_id"],
        event_type=event["type"],
        summary=describe_event(event),
        action_kind=classify_receipt_action(event),
        created_at=event["created_at"],
        valid_at=event.get("valid_at"),
        event_hash=event["hash"],
        previous_hash=event["previous_hash"],
        chain_position=position,
        chain_length=len(chain),
        chain_ok=chain_ok,
        verified=_recompute_hash(event) == event["hash"],
        initiator=dict(initiator) if isinstance(initiator, dict) else None,
        evidence=linked_evidence(event),
    )


def verify_receipt(ledger: Ledger, receipt: Receipt | dict) -> tuple[bool, list[str]]:
    """Re-verify a receipt against the live ledger.

    Returns ``(True, [])`` when the receipt still matches the
    stored event — same hash, same chain position, same chain
    link — or ``(False, [reasons])`` when anything drifted. A
    tampered event (content, time, or hash edited after the
    receipt was built) fails here, because the recomputed hash
    no longer matches the receipt.
    """
    if isinstance(receipt, Receipt):
        receipt = receipt.to_dict()
    reasons: list[str] = []
    event = get_event(ledger.dsn, receipt["event_id"])
    if event is None:
        return False, ["event no longer exists in the ledger"]
    if _recompute_hash(event) != receipt["event_hash"]:
        reasons.append("stored event hash does not match the receipt hash")
    if event["hash"] != receipt["event_hash"]:
        reasons.append("event hash changed since the receipt was built")
    if event["previous_hash"] != receipt["previous_hash"]:
        reasons.append("event chain link changed since the receipt was built")
    chain = ledger.events(event["task_id"])
    position = next(
        (index + 1 for index, item in enumerate(chain) if item["id"] == event["id"]),
        0,
    )
    if receipt.get("chain_position") is not None and position != receipt["chain_position"]:
        reasons.append("event chain position changed since the receipt was built")
    chain_ok, _broken = ledger.verify_chain(event["task_id"])
    if not chain_ok:
        reasons.append("task hash chain does not verify")
    return (not reasons, reasons)


def attach_proof(
    answer: dict,
    evidence: list[dict],
    receipt: Receipt | dict | None = None,
) -> dict:
    """Return an answer with its evidence and receipt attached.

    The returned dict is a copy of ``answer`` plus a ``"proof"``
    block::

        {"evidence": [...], "receipt": {...} | None,
         "proven": bool, "missing_evidence": [...]}

    ``proven`` is True only when at least one evidence entry is
    attached. An answer with no evidence gets ``proven: False``
    and a ``missing_evidence`` note — it is flagged, never
    silently shown as proven. A receipt without evidence still
    leaves ``proven`` False: a hash proves the event happened,
    not that the claim behind it is true.
    """
    if not isinstance(answer, dict):
        raise ValueError("answer must be a dict")
    if not isinstance(evidence, list) or any(not isinstance(entry, dict) for entry in evidence):
        raise ValueError("evidence must be a list of dicts")
    checked = [dict(entry) for entry in evidence]
    missing: list[str] = []
    if not checked:
        missing.append("no evidence attached to this answer")
    if receipt is not None and not isinstance(receipt, (Receipt, dict)):
        raise ValueError("receipt must be a Receipt, a dict, or None")
    receipt_dict = receipt.to_dict() if isinstance(receipt, Receipt) else receipt
    result = dict(answer)
    result["proof"] = {
        "evidence": checked,
        "receipt": receipt_dict,
        "proven": bool(checked),
        "missing_evidence": missing,
    }
    return result


def answer_proof(answer: CheckAnswer, ledger_event: dict, ledger: Ledger | None = None) -> dict:
    """The proof block for one Check Before You Buy answer.

    Evidence is the answer's verified price-evidence ids; the
    receipt is built from the ledger event the answer was
    recorded as. When the answer could not verify anything,
    ``missing_evidence`` names the unverified sections, so the
    user sees exactly what is unproven instead of a bare
    "cannot verify". ``ledger`` is optional: with it, the
    receipt carries the event's real chain position; without
    it, the position is reported as unknown (None).
    """
    evidence = [
        {"kind": "evidence_record", "evidence_id": evidence_id}
        for evidence_id in (answer.fair_price.evidence_ids or [])
    ]
    position: int | None = None
    length = 0
    chain_ok = True
    if ledger is not None:
        chain = ledger.events(ledger_event["task_id"])
        position = next(
            (index + 1 for index, item in enumerate(chain) if item["id"] == ledger_event["id"]),
            None,
        )
        length = len(chain)
        chain_ok, _broken = ledger.verify_chain(ledger_event["task_id"])
    receipt = Receipt(
        event_id=str(ledger_event["id"]),
        task_id=ledger_event["task_id"],
        event_type=ledger_event["type"],
        summary=describe_event(ledger_event),
        action_kind=classify_receipt_action(ledger_event),
        created_at=ledger_event["created_at"],
        valid_at=ledger_event.get("valid_at"),
        event_hash=ledger_event["hash"],
        previous_hash=ledger_event["previous_hash"],
        chain_position=position,
        chain_length=length,
        chain_ok=chain_ok,
        verified=_recompute_hash(ledger_event) == ledger_event["hash"],
        initiator=None,
        evidence=evidence,
    )
    proof = attach_proof(answer.to_dict(), evidence, receipt)["proof"]
    for section in answer.cannot_verify:
        proof["missing_evidence"].append(f"could not verify: {section}")
    return proof


def outcome_with_proof(record: OutcomeRecord, ledger: Ledger) -> dict:
    """One outcome record with its evidence and receipt attached.

    The receipt is the ledger event the record was written as,
    so the hash on the receipt is the hash of the record itself.
    """
    receipt = build_receipt(ledger, record.outcome_id)
    return attach_proof(record.to_dict(), record.evidence, receipt)


def user_agent_ids(user_id: str, dsn=None) -> list[str]:
    """Agent ids owned by one user (the K12 ownership rule)."""
    if not user_id:
        raise ValueError("user_id must be a non-empty string")
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT agent_id FROM agents WHERE user_id = %s", (user_id,))
        return [row["agent_id"] for row in cur.fetchall()]


def event_belongs_to_user(
    event: dict, user_id: str, agent_ids: list[str] | None = None, *, dsn=None
) -> bool:
    """True when a ledger event belongs to one user.

    An event belongs to the user when its agent is one of the
    user's agents, or when its content carries the user's id
    (outcome records do). Anything else is another user's event,
    and a receipt for it must never be served to this user.
    """
    if agent_ids is None:
        agent_ids = user_agent_ids(user_id, dsn=dsn)
    if event.get("agent_id") in agent_ids:
        return True
    content = event.get("content") or {}
    return isinstance(content, dict) and content.get("user_id") == user_id


def list_user_events(
    user_id: str, *, dsn=None, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
) -> tuple[list[dict], int]:
    """One user's ledger events, newest last, paginated.

    Returns ``(events, total)``: the page of events (as
    :class:`Ledger` returns them) and the total count, so the
    caller can page through the whole trail. ``limit`` is
    clamped to ``MAX_PAGE_SIZE``; ``offset`` must not be
    negative.
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError("offset must be a non-negative integer")
    limit = min(limit, MAX_PAGE_SIZE)
    agent_ids = user_agent_ids(user_id, dsn=dsn)
    with _connect(dsn) as conn, conn.cursor() as cur:
        if agent_ids:
            cur.execute(
                "SELECT COUNT(*) AS total FROM events "
                "WHERE agent_id = ANY(%s) OR content->>'user_id' = %s",
                (agent_ids, user_id),
            )
        else:
            cur.execute(
                "SELECT COUNT(*) AS total FROM events WHERE content->>'user_id' = %s",
                (user_id,),
            )
        total = cur.fetchone()["total"]
        if agent_ids:
            cur.execute(
                "SELECT * FROM events "
                "WHERE agent_id = ANY(%s) OR content->>'user_id' = %s "
                "ORDER BY created_at ASC, id ASC LIMIT %s OFFSET %s",
                (agent_ids, user_id, limit, offset),
            )
        else:
            cur.execute(
                "SELECT * FROM events WHERE content->>'user_id' = %s "
                "ORDER BY created_at ASC, id ASC LIMIT %s OFFSET %s",
                (user_id, limit, offset),
            )
        rows = cur.fetchall()
    events = [
        {
            "id": str(row["id"]),
            "created_at": row["created_at"],
            "valid_at": row.get("valid_at"),
            "task_id": row["task_id"],
            "agent_id": row["agent_id"],
            "subagent_id": row["subagent_id"],
            "type": row["type"],
            "content": row["content"],
            "parent_id": str(row["parent_id"]) if row["parent_id"] is not None else None,
            "previous_hash": row["previous_hash"],
            "hash": row["hash"],
        }
        for row in rows
    ]
    return events, total


def build_audit_trail_export(
    user_id: str, *, dsn=None, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
) -> dict:
    """Build one user's audit trail as a portable JSON document.

    The document reuses the K12 export shape — ``format``,
    ``version``, ``exported_at``, ``counts``, ``excluded`` —
    with one section: the user's ledger events, each with its
    receipt attached. The page is bounded (``limit`` clamps to
    ``MAX_PAGE_SIZE``) and the ``pagination`` block carries the
    total, so the user pages through their whole trail instead
    of getting a silently cut file. Only the requesting user's
    events are ever included; secret-looking text is redacted.
    """
    if not user_id:
        raise ValueError("user_id must be a non-empty string")
    try:
        UUID(str(user_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"unknown user: {user_id!r}") from exc
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT id, email FROM users WHERE id = %s", (user_id,))
        user_row = cur.fetchone()
    if user_row is None:
        raise ValueError(f"unknown user: {user_id!r}")
    events, total = list_user_events(user_id, dsn=dsn, limit=limit, offset=offset)
    ledger = Ledger(_resolve_dsn(dsn))
    # One chain lookup per task, not one per event.
    chain_cache: dict[str, list[dict]] = {}

    def _receipt_for(event: dict) -> dict:
        chain = chain_cache.setdefault(event["task_id"], ledger.events(event["task_id"]))
        position = next(
            (index + 1 for index, item in enumerate(chain) if item["id"] == event["id"]),
            0,
        )
        chain_ok, _broken = ledger.verify_chain(event["task_id"])
        content = event["content"] or {}
        initiator = content.get("initiator") if isinstance(content, dict) else None
        receipt = Receipt(
            event_id=event["id"],
            task_id=event["task_id"],
            event_type=event["type"],
            summary=describe_event(event),
            action_kind=classify_receipt_action(event),
            created_at=event["created_at"],
            valid_at=event.get("valid_at"),
            event_hash=event["hash"],
            previous_hash=event["previous_hash"],
            chain_position=position,
            chain_length=len(chain),
            chain_ok=chain_ok,
            verified=_recompute_hash(event) == event["hash"],
            initiator=dict(initiator) if isinstance(initiator, dict) else None,
            evidence=linked_evidence(event),
        )
        return receipt.to_dict()

    entries = [{"event": _jsonable(event), "receipt": _receipt_for(event)} for event in events]
    page_limit = min(limit, MAX_PAGE_SIZE)
    return {
        "format": AUDIT_TRAIL_FORMAT,
        "version": AUDIT_TRAIL_VERSION,
        "user": {"id": str(user_row["id"]), "email": user_row["email"]},
        "exported_at": datetime.now(UTC).isoformat(),
        "pagination": {
            "total": total,
            "limit": page_limit,
            "offset": offset,
            "returned": len(entries),
        },
        "events": entries,
        "counts": {"events": len(entries), "total_events": total},
        "excluded": list(AUDIT_TRAIL_EXCLUDED),
        "notes": (
            "Each event carries its receipt: the event hash, its "
            "position in the task hash chain, and whether the chain "
            "verified. A receipt proves the event is on the ledger "
            "unchanged; it does not prove the claim behind the event. "
            "Re-verify any receipt against the live ledger with the "
            "hash chain before relying on it."
        ),
    }
