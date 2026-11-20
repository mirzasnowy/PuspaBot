"""Agent payments, phase 1: prepare only (B8) — the service.

The agent prepares a payment — a verified recipient, an
amount in integer Rupiah, a named Indonesian rail, and the
evidence that proves the recipient and the amount — and the
user pays in their own bank or e-wallet app. Nothing here
executes a payment, calls a gateway, or touches a payment
credential: there is no executor in this module at all, and
:func:`execute_payment` — the one place an execution attempt
can land — always raises :class:`ExecutionRefused` and
records the refusal to the ledger.

The Rp0 execution limit is therefore an architectural fact,
not a setting: no value of any spending limit can make a
payment move, because no code path exists that moves one.
Raising a spending limit (``puspa.policy.spending``) changes
nothing here; phase 2 (agent executes) is not ticketed and
would need a gateway, credentials, and a new decision — see
``docs/agent-payments.md``. The ``payment_execution`` feature
flag (E-21, plan 9.6) is a second layer on top: when it is off,
:func:`execute_payment` raises ``FeatureDisabledError`` before
the refusal is even recorded; when it is on, the refusal above
still applies — the flag can never enable an execution, because
no executor exists.

Each preparation is approval-gated (G4) and ledger-recorded
(B2): ``prepare_payment`` writes a ``payment_prepared``
outcome record through the shared B2 writer and an
``approval_requested`` ledger event (the G4 approval
record); the row starts as ``awaiting_approval`` and becomes
payable only after :meth:`PreparationService.approve_preparation`.
``mark_paid_by_user`` records the user's own assertion that
they paid in their own app — as a user assertion, never as
an execution. Value types and validation live in
:mod:`puspa.payments.models`.

Rails (B5): QRIS, virtual account, bank transfer, GoPay, OVO,
DANA. Cash on delivery is refused: a prepared payment needs
an account to pay into, and COD has none.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from datetime import UTC, datetime

import psycopg
from psycopg.rows import dict_row

from puspa.accounts.users import resolve_dsn
from puspa.evidence.outcomes import write_outcome
from puspa.feature_flags import FLAG_PAYMENT_EXECUTION, require_enabled
from puspa.ledger import Ledger
from puspa.local_data.rails import PaymentRail
from puspa.policy.approvals import auto_review, classify_action

from .models import (
    EXECUTION_LIMIT_RUPIAH,
    ExecutionRefused,
    PaymentPreparationError,
    PreparedPayment,
    Recipient,
    _evidence_names_recipient,
    _require_evidence,
    _require_future_expiry,
    _require_int_rupiah,
    _validate_uuid,
    format_rupiah_amount,
    resolve_rail,
)

__all__ = [
    "ExecutionRefused",
    "PaymentPreparationError",
    "PreparationService",
    "approve_preparation",
    "execute_payment",
    "get_prepared_payment",
    "list_payable_payments",
    "list_prepared_payments",
    "mark_paid_by_user",
    "prepare_payment",
    "record_execution_refusal",
    "reject_preparation",
]


def _connect(dsn=None):
    return psycopg.connect(resolve_dsn(dsn), row_factory=dict_row)


def _row_to_preparation(row: dict) -> PreparedPayment:
    evidence = row["evidence"]
    if isinstance(evidence, str):
        evidence = json.loads(evidence)
    return PreparedPayment(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        recipient_name=row["recipient_name"],
        recipient_account=row["recipient_account"],
        recipient_verified_by=row["recipient_verified_by"],
        amount_rupiah=int(row["amount_rupiah"]),
        rail_id=row["rail_id"],
        rail_display=row["rail_display"],
        rail_kind=row["rail_kind"],
        rail_bank=row["rail_bank"] or "",
        evidence=[dict(entry) for entry in evidence],
        idempotency_key=row["idempotency_key"],
        status=row["status"],
        room_id=str(row["room_id"]) if row["room_id"] is not None else None,
        task_id=row["task_id"],
        outcome_id=row["outcome_id"],
        approval_request_id=row["approval_request_id"],
        prepared_by=str(row["prepared_by"]),
        prepared_at=row["prepared_at"],
        expires_at=row["expires_at"],
        approved_at=row["approved_at"],
        approved_by=str(row["approved_by"]) if row["approved_by"] is not None else None,
        paid_asserted_at=row["paid_asserted_at"],
        paid_asserted_by=(
            str(row["paid_asserted_by"]) if row["paid_asserted_by"] is not None else None
        ),
        paid_assertion=row["paid_assertion"],
    )


# -- service --------------------------------------------------------------


class PreparationService:
    """Prepare (never execute) payments for one user.

    ``ledger`` is optional on every method that writes to it:
    the preparation maths never depend on the ledger, but a
    preparation made without a ledger is not ledger-recorded,
    so callers that need the audit trail must pass one (and
    an ``agent_id`` with it). The PWA API always passes both.
    """

    def __init__(self, dsn=None):
        self.dsn = dsn

    # -- preparation ----------------------------------------------------

    def prepare_payment(
        self,
        *,
        user_id: str,
        recipient: dict | Recipient,
        amount_rupiah: int,
        rail: str | PaymentRail,
        evidence: list[dict] | None,
        prepared_by: str | None = None,
        agent_id: str | None = None,
        ledger: Ledger | None = None,
        idempotency_key: str | None = None,
        expires_at: datetime | None = None,
        room_id: str | None = None,
        task_id: str | None = None,
        now: datetime | None = None,
    ) -> PreparedPayment:
        """Prepare a payment: verified recipient + amount + rail
        + evidence, recorded, never executed.

        The preparation is written to the ledger as a
        ``payment_prepared`` outcome record (B2 writer) and an
        ``approval_requested`` event (the G4 approval record);
        the row starts as ``awaiting_approval`` and becomes
        payable only after :meth:`approve_preparation`. The
        same idempotency key for the same user returns the same
        preparation and writes nothing new; the same key with
        different content is refused.
        """
        owner = _validate_uuid(user_id, "user_id")
        preparer = _validate_uuid(prepared_by or user_id, "prepared_by")
        verified = recipient if isinstance(recipient, Recipient) else Recipient.from_dict(recipient)
        amount = _require_int_rupiah(amount_rupiah)
        resolved_rail = resolve_rail(rail)
        checked_evidence = _require_evidence(evidence)
        if not _evidence_names_recipient(checked_evidence, verified):
            raise PaymentPreparationError(
                f"recipient {verified.name!r} is not named by the evidence: "
                "at least one evidence entry must name the recipient "
                "(name or account reference)"
            )
        if ledger is not None and agent_id is None:
            raise PaymentPreparationError("agent_id is required when a ledger is passed")
        if idempotency_key is not None and (
            not isinstance(idempotency_key, str) or not idempotency_key.strip()
        ):
            raise PaymentPreparationError("idempotency_key must be a non-empty string")
        key = idempotency_key.strip() if idempotency_key is not None else None
        if room_id is not None:
            room_id = _validate_uuid(room_id, "room_id")
        moment = now or datetime.now(UTC)
        expiry = _require_future_expiry(expires_at, moment)
        preparation_id = str(uuid.uuid4())
        chain_id = (
            task_id.strip() if isinstance(task_id, str) and task_id.strip() else preparation_id
        )

        if key is not None:
            existing = self._find_by_key(owner, key)
            if existing is not None:
                self._assert_same_content(existing, verified, amount, resolved_rail)
                return existing

        # G4: the taxonomy recognises this as a pay action, and
        # the auto-review confirms it carries an amount. The
        # findings travel with the approval record.
        classification = classify_action("prepare_payment", page_text="payment preparation")
        findings = auto_review(classification, {"nominal": amount, "amount": amount})
        approval_content = {
            "kind": "payment_approval",
            "preparation_id": preparation_id,
            "user_id": owner,
            "recipient_name": verified.name,
            "recipient_account": verified.account_ref,
            "amount_rupiah": amount,
            "rail_id": resolved_rail.rail_id,
            "rail_display": resolved_rail.display_name,
            "classification": classification.kind,
            "classification_signals": list(classification.signals),
            "auto_review_findings": [finding.code for finding in findings],
            "status": "requested",
            # 15.2: an approval card shows consequences, not just an
            # approve button. Approving makes the payment payable;
            # it never moves money, and it can still be rejected
            # until the preparation expires.
            "consequences": [
                "approving makes this payment payable; it never moves money by itself",
                "you pay in your own bank or e-wallet app, to the named recipient only",
                "you can still reject this preparation until it expires",
            ],
            "note": (
                "approval to show this prepared payment as payable; "
                "approval never authorises execution, because there "
                "is no executor"
            ),
        }
        outcome_id: str | None = None
        approval_request_id: str | None = None
        if ledger is not None:
            outcome = write_outcome(
                ledger,
                task_id=chain_id,
                agent_id=agent_id,  # type: ignore[arg-type]
                outcome_type="payment_prepared",
                user_id=owner,
                room_id=room_id,
                subject_id=preparation_id,
                summary=(
                    f"payment of {format_rupiah_amount(amount)} to "
                    f"{verified.name} via {resolved_rail.display_name} prepared"
                ),
                evidence=checked_evidence,
                receipts=[
                    {
                        "kind": "prepared_payment",
                        "preparation_id": preparation_id,
                        "amount_rupiah": amount,
                        "rail_id": resolved_rail.rail_id,
                        "status": "awaiting_approval",
                    }
                ],
                details={
                    "kind": "payment_prepared",
                    "preparation_id": preparation_id,
                    "recipient_name": verified.name,
                    "recipient_account": verified.account_ref,
                    "amount_rupiah": amount,
                    "rail_id": resolved_rail.rail_id,
                    "rail_display": resolved_rail.display_name,
                    "rail_kind": resolved_rail.kind,
                    "idempotency_key": key,
                    "expires_at": expiry.isoformat(),
                    "executed": False,
                    "note": "prepared only; the user pays in their own bank/e-wallet app",
                },
            )
            outcome_id = outcome.outcome_id
            approval_event = ledger.append_event(
                task_id=chain_id,
                agent_id=agent_id,  # type: ignore[arg-type]
                event_type="approval_requested",
                content=approval_content,
            )
            approval_request_id = str(approval_event["id"])

        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO prepared_payments "
                "(id, user_id, recipient_name, recipient_account, recipient_verified_by, "
                " amount_rupiah, rail_id, rail_display, rail_kind, rail_bank, evidence, "
                " idempotency_key, status, room_id, task_id, outcome_id, approval_request_id, "
                " prepared_by, expires_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, "
                " 'awaiting_approval', %s, %s, %s, %s, %s, %s) RETURNING *",
                (
                    preparation_id,
                    owner,
                    verified.name,
                    verified.account_ref,
                    verified.verified_by,
                    amount,
                    resolved_rail.rail_id,
                    resolved_rail.display_name,
                    resolved_rail.kind,
                    resolved_rail.bank,
                    json.dumps(checked_evidence),
                    key,
                    room_id,
                    chain_id,
                    outcome_id,
                    approval_request_id,
                    preparer,
                    expiry,
                ),
            )
            preparation = _row_to_preparation(cur.fetchone())
            conn.commit()
        return preparation

    def _assert_same_content(
        self, existing: PreparedPayment, recipient: Recipient, amount: int, rail: PaymentRail
    ) -> None:
        """The same idempotency key with different content is a
        conflict, not a second preparation."""
        if (
            existing.recipient_name != recipient.name
            or existing.recipient_account != recipient.account_ref
            or existing.amount_rupiah != amount
            or existing.rail_id != rail.rail_id
        ):
            raise PaymentPreparationError(
                "idempotency_key already used for a different payment: "
                "same key, different recipient, amount, or rail"
            )

    def _find_by_key(self, user_id: str, key: str) -> PreparedPayment | None:
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM prepared_payments WHERE user_id = %s AND idempotency_key = %s",
                (user_id, key),
            )
            row = cur.fetchone()
        return _row_to_preparation(row) if row is not None else None

    # -- reads ----------------------------------------------------------

    def _expire_overdue(self, user_id: str, now: datetime) -> None:
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE prepared_payments SET status = 'expired' "
                "WHERE user_id = %s AND status IN ('awaiting_approval', 'approved') "
                "AND expires_at <= %s",
                (user_id, now),
            )
            conn.commit()

    def _require_owned(self, preparation_id: str, user_id: str) -> PreparedPayment:
        preparation_id = _validate_uuid(preparation_id, "preparation_id")
        owner = _validate_uuid(user_id, "user_id")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM prepared_payments WHERE id = %s AND user_id = %s",
                (preparation_id, owner),
            )
            row = cur.fetchone()
        if row is None:
            raise PaymentPreparationError(f"no prepared payment {preparation_id} for this user")
        return _row_to_preparation(row)

    def get_prepared_payment(
        self, preparation_id: str, *, user_id: str, now: datetime | None = None
    ) -> PreparedPayment | None:
        """One prepared payment, or None when it does not exist
        or belongs to another user (never leaked across users).
        Expired open preparations read back as ``expired``."""
        try:
            owner = _validate_uuid(user_id, "user_id")
            preparation_id = _validate_uuid(preparation_id, "preparation_id")
        except PaymentPreparationError:
            return None
        self._expire_overdue(owner, now or datetime.now(UTC))
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM prepared_payments WHERE id = %s AND user_id = %s",
                (preparation_id, owner),
            )
            row = cur.fetchone()
        return _row_to_preparation(row) if row is not None else None

    def list_prepared_payments(
        self, user_id: str, *, now: datetime | None = None
    ) -> list[PreparedPayment]:
        """Every prepared payment of one user, oldest first.
        Only this user's rows — a user can never read another
        user's preparations through this query."""
        owner = _validate_uuid(user_id, "user_id")
        moment = now or datetime.now(UTC)
        self._expire_overdue(owner, moment)
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM prepared_payments WHERE user_id = %s "
                "ORDER BY prepared_at ASC, id ASC",
                (owner,),
            )
            return [_row_to_preparation(row) for row in cur.fetchall()]

    def list_payable_payments(
        self, user_id: str, *, now: datetime | None = None
    ) -> list[PreparedPayment]:
        """The preparations the user can see as payable: approved
        and not expired. An unapproved preparation is never
        payable, whatever its expiry says."""
        moment = now or datetime.now(UTC)
        return [
            preparation
            for preparation in self.list_prepared_payments(user_id, now=moment)
            if preparation.is_payable(moment)
        ]

    # -- approval -------------------------------------------------------

    def approve_preparation(
        self,
        preparation_id: str,
        *,
        user_id: str,
        ledger: Ledger | None = None,
        agent_id: str | None = None,
        now: datetime | None = None,
    ) -> PreparedPayment:
        """Approve a prepared payment so the user can see it as
        payable. Only the owner may approve. Approval records an
        ``approval_granted`` ledger event; it never authorises
        execution, because there is no executor. An expired
        preparation cannot be approved."""
        owner = _validate_uuid(user_id, "user_id")
        if ledger is not None and agent_id is None:
            raise PaymentPreparationError("agent_id is required when a ledger is passed")
        moment = now or datetime.now(UTC)
        self._expire_overdue(owner, moment)
        preparation = self._require_owned(preparation_id, owner)
        if preparation.status == "expired" or preparation.is_expired(moment):
            raise PaymentPreparationError(
                f"prepared payment {preparation.id} expired at "
                f"{preparation.expires_at.isoformat()}; prepare it again"
            )
        if preparation.status == "approved":
            return preparation
        if preparation.status != "awaiting_approval":
            raise PaymentPreparationError(
                f"prepared payment {preparation.id} is {preparation.status}; "
                "only an awaiting-approval preparation can be approved"
            )
        if ledger is not None:
            ledger.append_event(
                task_id=preparation.task_id,
                agent_id=agent_id,  # type: ignore[arg-type]
                event_type="approval_granted",
                content={
                    "kind": "payment_approval",
                    "preparation_id": preparation.id,
                    "user_id": owner,
                    "amount_rupiah": preparation.amount_rupiah,
                    "rail_id": preparation.rail_id,
                    "status": "granted",
                    "note": (
                        "approval to show this prepared payment as payable; "
                        "approval never authorises execution"
                    ),
                },
            )
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE prepared_payments SET status = 'approved', "
                "approved_at = %s, approved_by = %s WHERE id = %s RETURNING *",
                (moment, owner, preparation.id),
            )
            updated = _row_to_preparation(cur.fetchone())
            conn.commit()
        return updated

    def reject_preparation(
        self,
        preparation_id: str,
        *,
        user_id: str,
        ledger: Ledger | None = None,
        agent_id: str | None = None,
        now: datetime | None = None,
    ) -> PreparedPayment:
        """Reject a prepared payment. Only the owner may reject.
        Records an ``approval_rejected`` ledger event."""
        owner = _validate_uuid(user_id, "user_id")
        if ledger is not None and agent_id is None:
            raise PaymentPreparationError("agent_id is required when a ledger is passed")
        moment = now or datetime.now(UTC)
        self._expire_overdue(owner, moment)
        preparation = self._require_owned(preparation_id, owner)
        if preparation.status != "awaiting_approval":
            raise PaymentPreparationError(
                f"prepared payment {preparation.id} is {preparation.status}; "
                "only an awaiting-approval preparation can be rejected"
            )
        if ledger is not None:
            ledger.append_event(
                task_id=preparation.task_id,
                agent_id=agent_id,  # type: ignore[arg-type]
                event_type="approval_rejected",
                content={
                    "kind": "payment_approval",
                    "preparation_id": preparation.id,
                    "user_id": owner,
                    "amount_rupiah": preparation.amount_rupiah,
                    "rail_id": preparation.rail_id,
                    "status": "rejected",
                    "note": "the user rejected this prepared payment",
                },
            )
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE prepared_payments SET status = 'rejected' WHERE id = %s RETURNING *",
                (preparation.id,),
            )
            updated = _row_to_preparation(cur.fetchone())
            conn.commit()
        return updated

    # -- user-paid assertion --------------------------------------------

    def mark_paid_by_user(
        self,
        preparation_id: str,
        *,
        user_id: str,
        assertion: str | None = None,
        ledger: Ledger | None = None,
        agent_id: str | None = None,
        now: datetime | None = None,
    ) -> PreparedPayment:
        """Record the user's assertion that they paid this
        preparation in their own bank or e-wallet app.

        This is a user assertion, recorded as such — never an
        execution, and never verified by Puspa (phase 1 has no
        gateway to check against). Only an approved, unexpired
        preparation can be marked; the assertion is written to
        the ledger as a ``payment_paid_by_user`` outcome record
        with the user's assertion as its evidence.
        """
        owner = _validate_uuid(user_id, "user_id")
        if ledger is not None and agent_id is None:
            raise PaymentPreparationError("agent_id is required when a ledger is passed")
        if assertion is not None and (not isinstance(assertion, str) or not assertion.strip()):
            raise PaymentPreparationError("assertion must be a non-empty string")
        moment = now or datetime.now(UTC)
        self._expire_overdue(owner, moment)
        preparation = self._require_owned(preparation_id, owner)
        if preparation.status == "expired" or preparation.is_expired(moment):
            raise PaymentPreparationError(
                f"prepared payment {preparation.id} expired at "
                f"{preparation.expires_at.isoformat()}; prepare it again"
            )
        if preparation.status != "approved":
            raise PaymentPreparationError(
                f"prepared payment {preparation.id} is {preparation.status}; "
                "only an approved preparation can be marked paid by the user"
            )
        note = assertion.strip() if assertion is not None else ""
        if ledger is not None:
            write_outcome(
                ledger,
                task_id=preparation.task_id,
                agent_id=agent_id,  # type: ignore[arg-type]
                outcome_type="payment_paid_by_user",
                user_id=owner,
                room_id=preparation.room_id,
                subject_id=preparation.id,
                summary=(
                    f"user reports paying {format_rupiah_amount(preparation.amount_rupiah)} "
                    f"to {preparation.recipient_name} in their own app"
                ),
                evidence=[
                    {
                        "kind": "user_assertion",
                        "asserted_by": owner,
                        "preparation_id": preparation.id,
                        "assertion": note or "paid in my own bank/e-wallet app",
                    }
                ],
                receipts=[
                    {
                        "kind": "prepared_payment",
                        "preparation_id": preparation.id,
                        "amount_rupiah": preparation.amount_rupiah,
                        "status": "paid_by_user",
                    }
                ],
                details={
                    "kind": "payment_paid_by_user",
                    "preparation_id": preparation.id,
                    "amount_rupiah": preparation.amount_rupiah,
                    "executed": False,
                    "paid_by_user": True,
                    "note": (
                        "a user assertion, not a verified payment: the agent "
                        "did not execute anything and phase 1 has no gateway "
                        "to check against"
                    ),
                },
            )
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE prepared_payments SET status = 'paid_by_user', "
                "paid_asserted_at = %s, paid_asserted_by = %s, paid_assertion = %s "
                "WHERE id = %s RETURNING *",
                (moment, owner, note or None, preparation.id),
            )
            updated = _row_to_preparation(cur.fetchone())
            conn.commit()
        return updated

    # -- execution guard --------------------------------------------------

    def execute_payment(
        self,
        preparation_id: str,
        *,
        user_id: str,
        ledger: Ledger | None = None,
        agent_id: str | None = None,
    ) -> None:
        """Refuse an execution attempt — always.

        Gated on the ``payment_execution`` flag first (E-21,
        plan 9.6): when the flag is off, the attempt is refused
        with :class:`FeatureDisabledError` before any ledger
        write. When the flag is on, the attempt is still refused
        with :class:`ExecutionRefused` — there is no executor in
        this module, so there is nothing to call: the attempt is
        recorded to the ledger as an ``approval_rejected`` event
        with content kind ``execution_refused`` (when a ledger is
        passed) and then :class:`ExecutionRefused` is raised.
        Approval, expiry, and the spending limit are all
        irrelevant here: even a fully approved preparation cannot
        be executed. A missing ``agent_id`` never turns a refusal
        into an execution: the refusal is still raised, only the
        audit trail is skipped.
        """
        require_enabled(FLAG_PAYMENT_EXECUTION)
        with contextlib.suppress(PaymentPreparationError):
            record_execution_refusal(
                preparation_id, user_id=user_id, ledger=ledger, agent_id=agent_id, dsn=self.dsn
            )
        raise ExecutionRefused(
            "payment execution is refused: Puspa prepares payments only in beta. "
            "The agent never pays; pay in your own bank or e-wallet app. "
            f"Execution limit: Rp{EXECUTION_LIMIT_RUPIAH} (architectural, not a setting)."
        )


def record_execution_refusal(
    preparation_id: str,
    *,
    user_id: str,
    ledger: Ledger | None = None,
    agent_id: str | None = None,
    dsn=None,
) -> None:
    """Record a refused execution attempt to the ledger, if a
    ledger is passed. The attempt is refused whether or not it
    is recorded; this only leaves the audit trail."""
    if ledger is None:
        return
    if agent_id is None:
        raise PaymentPreparationError("agent_id is required when a ledger is passed")
    owner = _validate_uuid(user_id, "user_id")
    known: PreparedPayment | None = None
    try:
        known = PreparationService(dsn).get_prepared_payment(preparation_id, user_id=owner)
    except PaymentPreparationError:
        known = None
    ledger.append_event(
        task_id=known.task_id if known is not None else str(preparation_id),
        agent_id=agent_id,
        event_type="approval_rejected",
        content={
            "kind": "execution_refused",
            "preparation_id": str(preparation_id),
            "user_id": owner,
            "amount_rupiah": known.amount_rupiah if known is not None else None,
            "rail_id": known.rail_id if known is not None else None,
            "execution_limit_rupiah": EXECUTION_LIMIT_RUPIAH,
            "note": (
                "execution attempt refused and recorded: no executor "
                "exists in beta, so no payment can move"
            ),
        },
    )


# --- Module-level functions (thin wrappers over a fresh service) --------


def _service(dsn=None) -> PreparationService:
    return PreparationService(dsn)


def prepare_payment(
    *,
    user_id: str,
    recipient: dict | Recipient,
    amount_rupiah: int,
    rail: str | PaymentRail,
    evidence: list[dict] | None,
    prepared_by: str | None = None,
    agent_id: str | None = None,
    ledger: Ledger | None = None,
    idempotency_key: str | None = None,
    expires_at: datetime | None = None,
    room_id: str | None = None,
    task_id: str | None = None,
    now: datetime | None = None,
    dsn=None,
) -> PreparedPayment:
    return _service(dsn).prepare_payment(
        user_id=user_id,
        recipient=recipient,
        amount_rupiah=amount_rupiah,
        rail=rail,
        evidence=evidence,
        prepared_by=prepared_by,
        agent_id=agent_id,
        ledger=ledger,
        idempotency_key=idempotency_key,
        expires_at=expires_at,
        room_id=room_id,
        task_id=task_id,
        now=now,
    )


def approve_preparation(
    preparation_id: str,
    *,
    user_id: str,
    ledger: Ledger | None = None,
    agent_id: str | None = None,
    now: datetime | None = None,
    dsn=None,
) -> PreparedPayment:
    return _service(dsn).approve_preparation(
        preparation_id, user_id=user_id, ledger=ledger, agent_id=agent_id, now=now
    )


def reject_preparation(
    preparation_id: str,
    *,
    user_id: str,
    ledger: Ledger | None = None,
    agent_id: str | None = None,
    now: datetime | None = None,
    dsn=None,
) -> PreparedPayment:
    return _service(dsn).reject_preparation(
        preparation_id, user_id=user_id, ledger=ledger, agent_id=agent_id, now=now
    )


def get_prepared_payment(
    preparation_id: str, *, user_id: str, now: datetime | None = None, dsn=None
) -> PreparedPayment | None:
    return _service(dsn).get_prepared_payment(preparation_id, user_id=user_id, now=now)


def list_prepared_payments(
    user_id: str, *, now: datetime | None = None, dsn=None
) -> list[PreparedPayment]:
    return _service(dsn).list_prepared_payments(user_id, now=now)


def list_payable_payments(
    user_id: str, *, now: datetime | None = None, dsn=None
) -> list[PreparedPayment]:
    return _service(dsn).list_payable_payments(user_id, now=now)


def mark_paid_by_user(
    preparation_id: str,
    *,
    user_id: str,
    assertion: str | None = None,
    ledger: Ledger | None = None,
    agent_id: str | None = None,
    now: datetime | None = None,
    dsn=None,
) -> PreparedPayment:
    return _service(dsn).mark_paid_by_user(
        preparation_id,
        user_id=user_id,
        assertion=assertion,
        ledger=ledger,
        agent_id=agent_id,
        now=now,
    )


def execute_payment(
    preparation_id: str,
    *,
    user_id: str,
    ledger: Ledger | None = None,
    agent_id: str | None = None,
    dsn=None,
) -> None:
    """Refuse an execution attempt — always (see
    :meth:`PreparationService.execute_payment`)."""
    return _service(dsn).execute_payment(
        preparation_id, user_id=user_id, ledger=ledger, agent_id=agent_id
    )
