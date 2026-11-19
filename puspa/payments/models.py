"""Payment value types and validation (B8).

The data a prepared payment is made of — the verified
recipient, the amount in integer Rupiah, the rail named
through B5, the evidence — plus the rules that refuse a bad
one. This module holds no database and no ledger: the
service that stores preparations lives in
:mod:`puspa.payments.prepare` and imports from here, never
the other way round.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from puspa.local_data.rails import (
    RAIL_BANK_TRANSFER,
    RAIL_CASH_ON_DELIVERY,
    RAIL_E_WALLET,
    RAIL_QRIS,
    RAIL_VIRTUAL_ACCOUNT,
    PaymentRail,
    detect_rail,
)

__all__ = [
    "DEFAULT_PREPARATION_TTL",
    "EXECUTION_LIMIT_RUPIAH",
    "PAY_NOTICE",
    "PREPARATION_STATUSES",
    "ExecutionRefused",
    "PaymentPreparationError",
    "PreparedPayment",
    "Recipient",
    "format_rupiah_amount",
    "render_prepared_payment",
    "resolve_rail",
]

#: The execution limit, in Rupiah. Zero, as a module constant:
#: no code path can execute a payment, so no setting can raise
#: this limit into an execution. Phase 2 would need a new
#: decision and a new executor; this constant stays zero in beta.
EXECUTION_LIMIT_RUPIAH = 0

#: How long a prepared payment stays payable (matches the G4
#: 24-hour approval window).
DEFAULT_PREPARATION_TTL = timedelta(hours=24)

#: Row statuses. ``expired`` is terminal; ``paid_by_user`` is
#: terminal; ``rejected`` is terminal.
PREPARATION_STATUSES = frozenset(
    {"awaiting_approval", "approved", "rejected", "expired", "paid_by_user"}
)

#: Statuses a preparation can still leave (expiry applies to
#: these only).
_OPEN_STATUSES = ("awaiting_approval", "approved")

#: The plain sentence every user-facing surface carries: the
#: agent never pays, the user pays in their own app.
PAY_NOTICE = "Puspa never pays. Pay in your own bank or e-wallet app."


class PaymentPreparationError(ValueError):
    """A prepared payment was refused or cannot move forward.

    Raised when the recipient is unverified, the amount is not
    a positive integer Rupiah, no rail is named, no evidence
    is given, the idempotency key conflicts, or the
    preparation is expired / already decided. Nothing is
    written when this is raised.
    """


class ExecutionRefused(RuntimeError):
    """An execution attempt was refused and recorded.

    There is no executor to call: every attempt raises this,
    after recording the refusal to the ledger when one is
    passed. The refusal is the guard, not a failure mode.
    """


# -- rail resolution (B5) -------------------------------------------------

#: Rails accepted by id (what an API caller passes), with the
#: display name and note B5 uses for each. ``detect_rail`` is
#: mention-based ("bayar pake qris"); an exact id is the other
#: honest input shape, so it is resolved here, not by guessing.
_RAILS_BY_ID: dict[str, PaymentRail] = {
    "qris": PaymentRail(
        rail_id="qris",
        display_name="QRIS",
        kind=RAIL_QRIS,
        note="QRIS: scan one code from any bank app or e-wallet.",
    ),
    "gopay": PaymentRail(
        rail_id="gopay",
        display_name="GoPay",
        kind=RAIL_E_WALLET,
        note="GoPay: pay from your GoPay balance.",
    ),
    "ovo": PaymentRail(
        rail_id="ovo",
        display_name="OVO",
        kind=RAIL_E_WALLET,
        note="OVO: pay from your OVO balance.",
    ),
    "dana": PaymentRail(
        rail_id="dana",
        display_name="DANA",
        kind=RAIL_E_WALLET,
        note="DANA: pay from your DANA balance.",
    ),
    "virtual_account": PaymentRail(
        rail_id="virtual_account",
        display_name="Virtual account",
        kind=RAIL_VIRTUAL_ACCOUNT,
        note="Virtual account: pay to the virtual account number the seller gives you.",
    ),
    "bank_transfer": PaymentRail(
        rail_id="bank_transfer",
        display_name="Bank transfer",
        kind=RAIL_BANK_TRANSFER,
        note="Bank transfer: transfer to the seller's account number and keep the receipt.",
    ),
}


def resolve_rail(rail: str | PaymentRail) -> PaymentRail:
    """Name the rail for a preparation, through B5.

    ``rail`` is a ``PaymentRail`` (used as-is), an exact rail id
    (``"qris"``, ``"gopay"``, ``"dana"``, ...), or a mention in
    everyday Indonesian (``"bayar pake qris"``), resolved by
    ``puspa.local_data.rails.detect_rail``. Cash on delivery is
    refused: a prepared payment needs an account to pay into.
    """
    if isinstance(rail, PaymentRail):
        resolved = rail
    elif isinstance(rail, str) and rail.strip():
        text = rail.strip()
        exact = _RAILS_BY_ID.get(text.lower())
        if exact is not None:
            resolved = exact
        else:
            detected = detect_rail(text)
            if detected is None:
                raise PaymentPreparationError(
                    f"no Indonesian payment rail named in {rail!r}: "
                    "name QRIS, a virtual account, a bank transfer, "
                    "GoPay, OVO, or DANA"
                )
            resolved = detected
    else:
        raise PaymentPreparationError("rail must be a PaymentRail or a non-empty string")
    if resolved.kind == RAIL_CASH_ON_DELIVERY:
        raise PaymentPreparationError(
            "cash on delivery cannot be prepared: a prepared payment "
            "needs an account to pay into, and COD has none"
        )
    return resolved


def format_rupiah_amount(amount_rupiah: int) -> str:
    """Integer Rupiah as ``Rp150.000`` (thousands separated by dots)."""
    if not isinstance(amount_rupiah, int) or isinstance(amount_rupiah, bool):
        raise PaymentPreparationError(f"amount_rupiah must be an integer, got {amount_rupiah!r}")
    if amount_rupiah < 0:
        raise PaymentPreparationError(f"amount_rupiah must not be negative, got {amount_rupiah}")
    return "Rp" + f"{amount_rupiah:,}".replace(",", ".")


# -- recipient ------------------------------------------------------------


@dataclass(frozen=True)
class Recipient:
    """A verified payment recipient.

    ``verified`` must be True: the agent only prepares a
    payment to a recipient somebody verified, and
    ``verified_by`` names what did the verifying (a seller
    page, a bank-app screenshot, a bill). ``account_ref`` is
    the destination the user pays into: an account number, a
    virtual account number, a wallet id, or a QRIS merchant id.
    """

    name: str
    account_ref: str
    verified: bool
    verified_by: str

    @classmethod
    def from_dict(cls, value: Any) -> Recipient:
        """Validate a recipient dict, or refuse it."""
        if not isinstance(value, dict):
            raise PaymentPreparationError("recipient must be a dict")
        name = value.get("name")
        account_ref = value.get("account_ref")
        verified_by = value.get("verified_by")
        if not isinstance(name, str) or not name.strip():
            raise PaymentPreparationError("recipient needs a non-empty 'name'")
        if not isinstance(account_ref, str) or not account_ref.strip():
            raise PaymentPreparationError("recipient needs a non-empty 'account_ref'")
        if value.get("verified") is not True:
            raise PaymentPreparationError(
                f"recipient {name.strip()!r} is not verified: only a verified "
                "recipient can be prepared for payment"
            )
        if not isinstance(verified_by, str) or not verified_by.strip():
            raise PaymentPreparationError(
                "recipient needs a non-empty 'verified_by': what verified this recipient"
            )
        return cls(
            name=name.strip(),
            account_ref=account_ref.strip(),
            verified=True,
            verified_by=verified_by.strip(),
        )

    def to_dict(self) -> dict:
        """The recipient as plain data."""
        return {
            "name": self.name,
            "account_ref": self.account_ref,
            "verified": True,
            "verified_by": self.verified_by,
        }


# -- prepared payment -----------------------------------------------------


@dataclass(frozen=True)
class PreparedPayment:
    """One prepared payment: verified recipient + amount + rail
    + evidence, recorded — never executed."""

    id: str
    user_id: str
    recipient_name: str
    recipient_account: str
    recipient_verified_by: str
    amount_rupiah: int
    rail_id: str
    rail_display: str
    rail_kind: str
    rail_bank: str
    evidence: list[dict]
    idempotency_key: str | None
    status: str
    room_id: str | None
    task_id: str
    outcome_id: str | None
    approval_request_id: str | None
    prepared_by: str
    prepared_at: datetime
    expires_at: datetime
    approved_at: datetime | None
    approved_by: str | None
    paid_asserted_at: datetime | None
    paid_asserted_by: str | None
    paid_assertion: str | None

    @property
    def recipient(self) -> Recipient:
        """The recipient, as a verified :class:`Recipient`."""
        return Recipient(
            name=self.recipient_name,
            account_ref=self.recipient_account,
            verified=True,
            verified_by=self.recipient_verified_by,
        )

    def is_expired(self, now: datetime | None = None) -> bool:
        """True when the preparation is past its expiry."""
        moment = now or datetime.now(UTC)
        if self.status == "expired":
            return True
        return self.status in _OPEN_STATUSES and moment >= self.expires_at

    def is_payable(self, now: datetime | None = None) -> bool:
        """True when the user can see this as payable: approved
        and not expired. An unapproved preparation is never
        payable, whatever its expiry says."""
        return self.status == "approved" and not self.is_expired(now)

    def to_dict(self) -> dict:
        """The preparation as plain data for the PWA/API.

        Every field the user needs to pay is here — recipient,
        amount, rail, evidence, expiry — plus the plain notice
        that the agent never pays.
        """
        return {
            "id": self.id,
            "user_id": self.user_id,
            "recipient": self.recipient.to_dict(),
            "amount_rupiah": self.amount_rupiah,
            "amount_display": format_rupiah_amount(self.amount_rupiah),
            "rail": {
                "rail_id": self.rail_id,
                "display_name": self.rail_display,
                "kind": self.rail_kind,
                "bank": self.rail_bank,
            },
            "evidence": [dict(entry) for entry in self.evidence],
            "idempotency_key": self.idempotency_key,
            "status": self.status,
            "payable": self.is_payable(),
            "room_id": self.room_id,
            "task_id": self.task_id,
            "outcome_id": self.outcome_id,
            "approval_request_id": self.approval_request_id,
            "prepared_by": self.prepared_by,
            "prepared_at": self.prepared_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "approved_by": self.approved_by,
            "paid_asserted_at": (
                self.paid_asserted_at.isoformat() if self.paid_asserted_at else None
            ),
            "paid_asserted_by": self.paid_asserted_by,
            "paid_assertion": self.paid_assertion,
            "pay_notice": PAY_NOTICE,
        }


def render_prepared_payment(preparation: PreparedPayment, *, now: datetime | None = None) -> str:
    """Plain-text summary of a prepared payment for the user.

    Always shows verified recipient + amount + rail +
    evidence + expiry, and states plainly that the agent
    never pays. An unapproved preparation says so: it is not
    payable yet. It also states the consequences of approving
    (15.2: an approval surface shows consequences, not just an
    approve button), not only the current status.
    """
    lines = [
        (
            f"Payment prepared: {format_rupiah_amount(preparation.amount_rupiah)} "
            f"to {preparation.recipient_name} ({preparation.recipient_account}) "
            f"via {preparation.rail_display}."
        ),
        f"Recipient verified by: {preparation.recipient_verified_by}.",
        (
            f"Evidence: {len(preparation.evidence)} item(s) attached "
            f"({', '.join(entry.get('kind', '?') for entry in preparation.evidence)})."
        ),
        f"Expires: {preparation.expires_at.isoformat()}.",
    ]
    if preparation.status == "awaiting_approval":
        lines.append("Status: waiting for your approval — not payable yet.")
        lines.append(
            "If you approve: this payment becomes payable, and you pay it "
            "yourself in your bank or e-wallet app. Approving never moves "
            "money by itself, and you can still reject it until it expires."
        )
    elif preparation.status == "approved":
        if preparation.is_expired(now):
            lines.append("Status: approved, but expired — prepare it again.")
        else:
            lines.append("Status: approved — you can pay it now.")
        lines.append(
            "Approving never moved money by itself: you always pay in your "
            "own bank or e-wallet app, to the named recipient only."
        )
    elif preparation.status == "paid_by_user":
        lines.append("Status: you reported paying this in your own app.")
    else:
        lines.append(f"Status: {preparation.status}.")
    lines.append(PAY_NOTICE)
    return "\n".join(lines)


# -- validation helpers ---------------------------------------------------


def _validate_uuid(value: Any, name: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise PaymentPreparationError(f"{name} must be a UUID, got {value!r}") from exc


def _require_int_rupiah(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise PaymentPreparationError(
            f"amount_rupiah must be a positive integer number of Rupiah, got {value!r}"
        )
    if value <= 0:
        raise PaymentPreparationError(f"amount_rupiah must be positive, got {value}")
    return value


def _require_evidence(evidence: Any) -> list[dict]:
    """The evidence rule: at least one entry, each naming its kind."""
    if not isinstance(evidence, list) or not evidence:
        raise PaymentPreparationError(
            "a prepared payment needs evidence: evidence must be a "
            'non-empty list of {"kind": ..., ...} dicts'
        )
    checked: list[dict] = []
    for entry in evidence:
        if not isinstance(entry, dict) or not entry:
            raise PaymentPreparationError("each evidence entry must be a non-empty dict")
        if not isinstance(entry.get("kind"), str) or not entry["kind"].strip():
            raise PaymentPreparationError('each evidence entry must name its "kind"')
        checked.append(dict(entry))
    return checked


def _evidence_names_recipient(evidence: list[dict], recipient: Recipient) -> bool:
    """True when some evidence entry names this recipient: the
    recipient's name or account reference appears in one of its
    string values. This is what "verified against evidence"
    means here — the evidence is not just attached, it names
    the person being paid."""
    needles = {recipient.name.lower(), recipient.account_ref.lower()}
    for entry in evidence:
        for value in entry.values():
            if isinstance(value, str) and any(
                needle and needle in value.lower() for needle in needles
            ):
                return True
    return False


def _require_future_expiry(expires_at: datetime | None, now: datetime) -> datetime:
    if expires_at is None:
        return now + DEFAULT_PREPARATION_TTL
    if not isinstance(expires_at, datetime) or expires_at.tzinfo is None:
        raise PaymentPreparationError("expires_at must be a timezone-aware datetime")
    if expires_at <= now:
        raise PaymentPreparationError("expires_at must be in the future")
    return expires_at
