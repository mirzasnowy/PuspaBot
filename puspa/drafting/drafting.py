"""Co-drafting service (B13).

The agent drafts messages, documents, or emails in the user's
voice. The voice comes from the existing memory facts (G7/G24);
this module stores no voice of its own.

Draft assembly is deterministic template assembly, not free
generation: the brief supplies the content, the voice profile
supplies the greeting, closing, and rules, and the recorded
decisions are checked against the brief. There is no LLM call
here — a draft is only ever as good as the brief and the
recorded voice, and the design doc (``docs/drafting-b13.md``)
says so plainly.

Sending is approval-gated by construction (G4 — send is always
an approval):

- :meth:`DraftingService.send_draft` is the only send path in
  this package, and it requires an ``approval_id``.
- An approval is created only by :meth:`DraftingService.approve_draft`
  (the user's explicit approval) or by
  :meth:`DraftingService.request_send_approval` when a wired
  ``ApprovalPolicy`` returns ``allow`` for a stored tiered
  answer. With no policy wired in, a send always waits for the
  user's explicit approval.
- An approval binds to the SHA-256 of the draft text at approval
  time, expires after 24 hours, and is consumed by the one send
  it authorises. Editing a draft after approval voids the
  approval (the hash no longer matches); the user must approve
  the new text.
- The delivery itself is an injected ``sender`` callable. No
  sender is wired in by default: an approved send with no
  sender raises :class:`NoDeliveryChannelError` and the
  approval is NOT consumed, so nothing is lost and nothing is
  silently claimed as sent.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import psycopg
from psycopg.rows import dict_row

from puspa.accounts.users import resolve_dsn
from puspa.evidence.outcomes import write_outcome
from puspa.ledger import Ledger
from puspa.memory import facts as facts_module

from .errors import (
    DraftNotApprovedError,
    DraftNotFoundError,
    DraftValidationError,
    NoDeliveryChannelError,
)
from .voice import VoiceProfile, build_voice_profile

__all__ = [
    "APPROVAL_TTL_SECONDS",
    "DRAFT_KINDS",
    "Draft",
    "DraftApproval",
    "DraftingService",
    "MemoryDraftStore",
    "PostgresDraftStore",
    "content_hash",
]

DRAFT_KINDS = ("message", "document", "email")

#: Approval lifetime in seconds (24 hours, the G4 gate window).
APPROVAL_TTL_SECONDS = 24 * 3600

MAX_BRIEF_CHARS = 10_000
MAX_DRAFT_TEXT_CHARS = 20_000


def _now():
    return datetime.now(UTC)


def _new_id():
    return str(uuid.uuid4())


def content_hash(text: str) -> str:
    """SHA-256 of a draft text: what an approval binds to."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Draft:
    """One draft: the brief, the assembled text, and its state."""

    id: str
    user_id: str
    kind: str
    brief: str
    text: str
    status: str = "draft"  # "draft" | "approved" | "sent"
    recipient: str | None = None
    channel: str | None = None
    decision_flags: list[str] = field(default_factory=list)
    related_decisions: list[str] = field(default_factory=list)
    voice_rules_applied: list[str] = field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    sent_at: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "kind": self.kind,
            "brief": self.brief,
            "text": self.text,
            "status": self.status,
            "recipient": self.recipient,
            "channel": self.channel,
            "decision_flags": list(self.decision_flags),
            "related_decisions": list(self.related_decisions),
            "voice_rules_applied": list(self.voice_rules_applied),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
        }


@dataclass
class DraftApproval:
    """One recorded approval for one draft text (single-use)."""

    id: str
    draft_id: str
    user_id: str
    content_hash: str
    status: str = "granted"  # "granted" | "consumed"
    granted_at: datetime | None = None
    expires_at: datetime | None = None
    consumed_at: datetime | None = None

    def expired(self, now: datetime | None = None) -> bool:
        now = now or _now()
        return self.expires_at is not None and now >= self.expires_at

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "draft_id": self.draft_id,
            "user_id": self.user_id,
            "content_hash": self.content_hash,
            "status": self.status,
            "granted_at": self.granted_at.isoformat() if self.granted_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "consumed_at": self.consumed_at.isoformat() if self.consumed_at else None,
        }


# -- stores ------------------------------------------------------------


class MemoryDraftStore:
    """In-memory draft + approval store (tests, no database)."""

    def __init__(self):
        self._drafts: dict[str, Draft] = {}
        self._approvals: dict[str, DraftApproval] = {}

    def insert_draft(self, draft: Draft) -> Draft:
        self._drafts[draft.id] = draft
        return draft

    def get_draft(self, draft_id: str, user_id: str) -> Draft | None:
        draft = self._drafts.get(str(draft_id))
        if draft is None or str(draft.user_id) != str(user_id):
            return None
        return draft

    def list_drafts(self, user_id: str) -> list[Draft]:
        rows = [d for d in self._drafts.values() if str(d.user_id) == str(user_id)]
        rows.sort(key=lambda d: (d.created_at or _now(), d.id))
        return rows

    def update_draft(self, draft: Draft) -> Draft:
        self._drafts[draft.id] = draft
        return draft

    def insert_approval(self, approval: DraftApproval) -> DraftApproval:
        self._approvals[approval.id] = approval
        return approval

    def get_approval(self, approval_id: str, user_id: str) -> DraftApproval | None:
        approval = self._approvals.get(str(approval_id))
        if approval is None or str(approval.user_id) != str(user_id):
            return None
        return approval

    def update_approval(self, approval: DraftApproval) -> DraftApproval:
        self._approvals[approval.id] = approval
        return approval


def _draft_from_row(row) -> Draft:
    return Draft(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        kind=row["kind"],
        brief=row["brief"],
        text=row["text"],
        status=row["status"],
        recipient=row["recipient"],
        channel=row["channel"],
        decision_flags=list(row["decision_flags"] or []),
        related_decisions=list(row["related_decisions"] or []),
        voice_rules_applied=list(row["voice_rules_applied"] or []),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        sent_at=row["sent_at"],
    )


def _approval_from_row(row) -> DraftApproval:
    return DraftApproval(
        id=str(row["id"]),
        draft_id=str(row["draft_id"]),
        user_id=str(row["user_id"]),
        content_hash=row["content_hash"],
        status=row["status"],
        granted_at=row["granted_at"],
        expires_at=row["expires_at"],
        consumed_at=row["consumed_at"],
    )


class PostgresDraftStore:
    """Postgres draft + approval store over ``drafts`` /
    ``draft_approvals`` (see migration 0012_drafting)."""

    def __init__(self, dsn=None):
        self.dsn = resolve_dsn(dsn)

    def _connect(self):
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def insert_draft(self, draft: Draft) -> Draft:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO drafts (id, user_id, kind, brief, text, status, "
                    "recipient, channel, decision_flags, related_decisions, "
                    "voice_rules_applied, created_at, updated_at, sent_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "RETURNING *",
                    (
                        draft.id,
                        draft.user_id,
                        draft.kind,
                        draft.brief,
                        draft.text,
                        draft.status,
                        draft.recipient,
                        draft.channel,
                        _to_json(draft.decision_flags),
                        _to_json(draft.related_decisions),
                        _to_json(draft.voice_rules_applied),
                        draft.created_at,
                        draft.updated_at,
                        draft.sent_at,
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        return _draft_from_row(row)

    def get_draft(self, draft_id: str, user_id: str) -> Draft | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM drafts WHERE id = %s AND user_id = %s", (draft_id, user_id))
            row = cur.fetchone()
        return _draft_from_row(row) if row is not None else None

    def list_drafts(self, user_id: str) -> list[Draft]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM drafts WHERE user_id = %s ORDER BY created_at ASC, id ASC",
                (user_id,),
            )
            return [_draft_from_row(r) for r in cur.fetchall()]

    def update_draft(self, draft: Draft) -> Draft:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE drafts SET kind = %s, brief = %s, text = %s, status = %s, "
                    "recipient = %s, channel = %s, decision_flags = %s, "
                    "related_decisions = %s, voice_rules_applied = %s, "
                    "updated_at = %s, sent_at = %s "
                    "WHERE id = %s AND user_id = %s RETURNING *",
                    (
                        draft.kind,
                        draft.brief,
                        draft.text,
                        draft.status,
                        draft.recipient,
                        draft.channel,
                        _to_json(draft.decision_flags),
                        _to_json(draft.related_decisions),
                        _to_json(draft.voice_rules_applied),
                        draft.updated_at,
                        draft.sent_at,
                        draft.id,
                        draft.user_id,
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        return _draft_from_row(row)

    def insert_approval(self, approval: DraftApproval) -> DraftApproval:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO draft_approvals (id, draft_id, user_id, content_hash, "
                    "status, granted_at, expires_at, consumed_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
                    (
                        approval.id,
                        approval.draft_id,
                        approval.user_id,
                        approval.content_hash,
                        approval.status,
                        approval.granted_at,
                        approval.expires_at,
                        approval.consumed_at,
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        return _approval_from_row(row)

    def get_approval(self, approval_id: str, user_id: str) -> DraftApproval | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM draft_approvals WHERE id = %s AND user_id = %s",
                (approval_id, user_id),
            )
            row = cur.fetchone()
        return _approval_from_row(row) if row is not None else None

    def update_approval(self, approval: DraftApproval) -> DraftApproval:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE draft_approvals SET status = %s, consumed_at = %s "
                    "WHERE id = %s AND user_id = %s RETURNING *",
                    (approval.status, approval.consumed_at, approval.id, approval.user_id),
                )
                row = cur.fetchone()
            conn.commit()
        return _approval_from_row(row)


def _to_json(values: list[str]):
    import json

    return json.dumps(list(values), ensure_ascii=False)


# -- assembly ----------------------------------------------------------


def _split_points(brief: str) -> list[str]:
    """Brief lines as points (a multi-line brief is a list of points)."""
    points = [line.strip(" -•\t") for line in brief.splitlines()]
    return [point for point in points if point]


def _assemble(kind: str, brief: str, profile: VoiceProfile) -> str:
    """Assemble a draft from the brief in the profile's voice.

    No filler is invented: the brief is the content. A message
    or email opens with the profile greeting and (for email)
    closes with the profile closing; a document is a title plus
    the brief points. Brevity (a recorded ``keep it short``
    rule) renders document points as short bullets instead of
    paragraphs.
    """
    language = profile.detect_language(brief)
    points = _split_points(brief)
    body = brief.strip()
    if kind == "document":
        title = points[0] if points else "Draft"
        rest = points[1:]
        if profile.wants_brevity:
            lines = [title, ""] + [f"- {point}" for point in rest or [body]]
        else:
            lines = [title, ""] + (rest or [body])
        return "\n".join(lines).strip()
    greeting = profile.default_greeting(language)
    if kind == "email":
        closing = profile.default_closing(language)
        return f"{greeting}\n\n{body}\n\n{closing}".strip()
    return f"{greeting}\n\n{body}".strip()


# -- service -----------------------------------------------------------


class DraftingService:
    """Drafts in the user's voice, with approval-gated sending.

    ``draft_store`` holds drafts + approvals (in-memory by
    default; pass :class:`PostgresDraftStore` for persistence).
    ``fact_store`` is the G7/G24 memory fact store the voice is
    read from and corrections are recorded in (in-memory by
    default). ``policy`` is an optional ``ApprovalPolicy``
    (G4 taxonomy): when wired in, a stored tiered answer can
    approve a send without a fresh approval card; when absent,
    every send waits for the user's explicit approval.
    """

    def __init__(
        self,
        dsn=None,
        *,
        draft_store=None,
        fact_store=None,
        policy=None,
    ):
        self.dsn = dsn
        self.store = draft_store or MemoryDraftStore()
        self.fact_store = fact_store or facts_module.MemoryFactStore()
        self.policy = policy

    # -- voice --------------------------------------------------------
    def voice_profile(self, user_id: str) -> VoiceProfile:
        """The user's voice profile, built from their memory facts."""
        active = facts_module.list_active_facts(user_id, store=self.fact_store)
        return build_voice_profile(user_id, active)

    def correct_voice(self, user_id: str, correction: str) -> dict:
        """Record a voice correction so later drafts change.

        The correction is stored as a ``voice.correction.<id>``
        profile fact (source ``user_confirmed``) through the
        existing G7 fact machinery — history, trust gate, and
        supersede rules included. It is never stored anywhere
        else, so there is exactly one voice to read back.
        """
        if not isinstance(correction, str) or not correction.strip():
            raise DraftValidationError("correction must be a non-empty string")
        if len(correction) > 1_000:
            raise DraftValidationError("correction is too long (max 1000 characters)")
        key = f"voice.correction.{uuid.uuid4().hex[:12]}"
        return facts_module.record_fact(
            user_id,
            key,
            correction.strip(),
            source="user_confirmed",
            store=self.fact_store,
        )

    # -- drafting -----------------------------------------------------
    def draft(
        self,
        user_id: str,
        kind: str,
        brief: str,
        *,
        recipient: str | None = None,
        channel: str | None = None,
    ) -> Draft:
        """Draft a message, document, or email in the user's voice.

        The returned draft carries ``decision_flags`` when the
        brief contradicts a recorded past decision, and
        ``related_decisions`` for the decisions the brief touches.
        A flagged draft is still created — the flag is a warning
        for the user to see, not a silent rewrite — and it still
        needs approval before it can ever send.
        """
        if kind not in DRAFT_KINDS:
            raise DraftValidationError(f"unknown draft kind: {kind!r}")
        if not isinstance(brief, str) or not brief.strip():
            raise DraftValidationError("brief must be a non-empty string")
        if len(brief) > MAX_BRIEF_CHARS:
            raise DraftValidationError("brief is too long (max 10000 characters)")
        profile = self.voice_profile(user_id)
        flags, related = profile.flag_decisions(brief)
        text = _assemble(kind, brief.strip(), profile)
        text, applied, _unapplied = profile.apply_rules(text)
        if len(text) > MAX_DRAFT_TEXT_CHARS:
            raise DraftValidationError("draft text is too long")
        now = _now()
        draft = Draft(
            id=_new_id(),
            user_id=str(user_id),
            kind=kind,
            brief=brief.strip(),
            text=text,
            status="draft",
            recipient=recipient,
            channel=channel,
            decision_flags=flags,
            related_decisions=related,
            voice_rules_applied=applied,
            created_at=now,
            updated_at=now,
        )
        return self.store.insert_draft(draft)

    def get_draft(self, draft_id: str, *, user_id: str) -> Draft:
        """One draft of this user, or raise DraftNotFoundError."""
        draft = self.store.get_draft(draft_id, user_id)
        if draft is None:
            raise DraftNotFoundError(f"draft not found: {draft_id}")
        return draft

    def list_drafts(self, user_id: str) -> list[Draft]:
        """The user's drafts, oldest first."""
        return self.store.list_drafts(user_id)

    def revise_draft(self, draft_id: str, *, user_id: str, text: str) -> Draft:
        """Replace a draft's text (an edit by the user).

        The draft returns to ``draft`` status: any approval
        granted for the old text is void (its content hash no
        longer matches), so the revised text needs a fresh
        approval before it can send.
        """
        if not isinstance(text, str) or not text.strip():
            raise DraftValidationError("text must be a non-empty string")
        if len(text) > MAX_DRAFT_TEXT_CHARS:
            raise DraftValidationError("draft text is too long")
        draft = self.get_draft(draft_id, user_id=user_id)
        if draft.status == "sent":
            raise DraftValidationError("a sent draft cannot be revised")
        draft.text = text.strip()
        draft.status = "draft"
        draft.updated_at = _now()
        return self.store.update_draft(draft)

    # -- approval -----------------------------------------------------
    def approve_draft(
        self,
        draft_id: str,
        *,
        user_id: str,
        ledger: Ledger | None = None,
        agent_id: str | None = None,
    ) -> DraftApproval:
        """The user's explicit approval to send one draft.

        Records a single-use approval bound to the SHA-256 of
        the draft text right now, expiring after 24 hours, and
        marks the draft ``approved``. Approving does not send
        anything.

        When a ledger is given (``agent_id`` required with it),
        the approval is also recorded through the shared B2
        writer as a ``decision_recorded`` outcome record, with
        the approval itself as the evidence: the decision to
        send this draft, by this user, is on the record.
        """
        if ledger is not None and agent_id is None:
            raise ValueError("agent_id is required when a ledger is given")
        draft = self.get_draft(draft_id, user_id=user_id)
        if draft.status == "sent":
            raise DraftValidationError("a sent draft cannot be approved again")
        now = _now()
        approval = DraftApproval(
            id=_new_id(),
            draft_id=draft.id,
            user_id=str(user_id),
            content_hash=content_hash(draft.text),
            status="granted",
            granted_at=now,
            expires_at=now + timedelta(seconds=APPROVAL_TTL_SECONDS),
        )
        self.store.insert_approval(approval)
        draft.status = "approved"
        draft.updated_at = now
        self.store.update_draft(draft)
        if ledger is not None:
            write_outcome(
                ledger,
                task_id=draft.id,
                agent_id=agent_id,  # type: ignore[arg-type]
                outcome_type="decision_recorded",
                user_id=user_id,
                subject_id=draft.id,
                summary=f"draft approved for sending: {draft.kind}"
                + (f" to {draft.recipient}" if draft.recipient else ""),
                evidence=[
                    {
                        "kind": "draft_approval",
                        "approval_id": approval.id,
                        "draft_id": draft.id,
                        "content_hash": approval.content_hash,
                    }
                ],
                details={
                    "kind": draft.kind,
                    "channel": draft.channel,
                    "recipient": draft.recipient,
                    "decision_flags": draft.decision_flags,
                },
            )
        return approval

    def request_send_approval(
        self,
        draft_id: str,
        *,
        user_id: str,
        conversation_id: str | None = None,
        ledger: Ledger | None = None,
        agent_id: str | None = None,
    ) -> dict:
        """Ask for send approval through the G4 taxonomy policy.

        When a policy is wired in and it returns ``allow`` (a
        stored tiered answer covers sending), the approval is
        recorded at once and returned. Otherwise the result is
        ``awaiting_approval``: the user approves through
        :meth:`approve_draft`. Nothing here ever sends.
        """
        draft = self.get_draft(draft_id, user_id=user_id)
        if self.policy is not None:
            decision = self.policy.decide(
                tool_name="draft.send",
                content={"draft_id": draft.id, "recipient": draft.recipient},
                conversation_id=conversation_id,
            )
            if decision.outcome == "allow":
                approval = self.approve_draft(
                    draft_id, user_id=user_id, ledger=ledger, agent_id=agent_id
                )
                return {"status": "approved", "approval": approval.to_dict()}
            return {
                "status": "awaiting_approval",
                "reason": decision.reason,
            }
        return {"status": "awaiting_approval", "reason": "send_to_other always asks"}

    # -- sending ------------------------------------------------------
    def send_draft(self, draft_id: str, *, user_id: str, approval_id: str, sender=None) -> dict:
        """Send an approved draft. The only send path.

        Requires ``approval_id``: a recorded approval for this
        draft, granted by this user, still ``granted`` (not
        consumed), not expired, and bound to the draft's current
        text. Any failure raises :class:`DraftNotApprovedError`
        and nothing is delivered. ``sender`` is the delivery
        callable — ``sender(payload)`` where payload carries
        ``draft_id``, ``user_id``, ``recipient``, ``channel``,
        and ``text``. With no sender wired in, the send is
        refused with :class:`NoDeliveryChannelError` and the
        approval is NOT consumed.
        """
        draft = self.get_draft(draft_id, user_id=user_id)
        if not approval_id:
            raise DraftNotApprovedError("send needs a recorded approval (approval_id)")
        approval = self.store.get_approval(approval_id, user_id)
        if approval is None:
            raise DraftNotApprovedError("unknown approval")
        if approval.draft_id != draft.id:
            raise DraftNotApprovedError("approval belongs to another draft")
        if approval.status != "granted":
            raise DraftNotApprovedError("approval was already used")
        if approval.expired():
            raise DraftNotApprovedError("approval expired")
        if approval.content_hash != content_hash(draft.text):
            raise DraftNotApprovedError(
                "draft text changed after approval; approval is void, approve again"
            )
        if draft.status == "sent":
            raise DraftValidationError("draft was already sent")
        if sender is None:
            raise NoDeliveryChannelError(
                "no delivery channel is wired in; approval kept, nothing sent"
            )
        payload = {
            "draft_id": draft.id,
            "user_id": draft.user_id,
            "recipient": draft.recipient,
            "channel": draft.channel,
            "text": draft.text,
        }
        result = sender(payload)
        now = _now()
        approval.status = "consumed"
        approval.consumed_at = now
        self.store.update_approval(approval)
        draft.status = "sent"
        draft.sent_at = now
        draft.updated_at = now
        self.store.update_draft(draft)
        return {"draft": draft.to_dict(), "approval": approval.to_dict(), "delivery": result}
