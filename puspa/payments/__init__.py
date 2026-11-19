"""Agent payments, phase 1: prepare only (B8).

Public surface:

- :class:`PreparationService` — prepare (never execute)
  payments: verified recipient + amount in integer Rupiah +
  a rail named through B5 + evidence, with an idempotency
  key and an expiry. Every preparation is written to the
  ledger as a ``payment_prepared`` outcome record (B2) and
  an ``approval_requested`` event (the G4 approval record),
  and becomes payable only after approval.
- :func:`execute_payment` — the execution guard: every
  attempt is refused, recorded to the ledger, and raises
  :class:`ExecutionRefused`. There is no executor.
- :func:`render_prepared_payment` — the plain-text summary
  the user sees: recipient + amount + rail + evidence +
  expiry, and the notice that Puspa never pays.

Honest limits (see ``docs/agent-payments.md``): no live
payment gateway, no real rails integration, no execution —
phase 1 only.
"""

from .models import (
    DEFAULT_PREPARATION_TTL,
    EXECUTION_LIMIT_RUPIAH,
    PAY_NOTICE,
    PREPARATION_STATUSES,
    ExecutionRefused,
    PaymentPreparationError,
    PreparedPayment,
    Recipient,
    format_rupiah_amount,
    render_prepared_payment,
    resolve_rail,
)
from .prepare import (
    PreparationService,
    approve_preparation,
    execute_payment,
    get_prepared_payment,
    list_payable_payments,
    list_prepared_payments,
    mark_paid_by_user,
    prepare_payment,
    record_execution_refusal,
    reject_preparation,
)
from .schema import PAYMENTS_TABLES, create_schema, reset_tables, schema_path

__all__ = [
    "DEFAULT_PREPARATION_TTL",
    "EXECUTION_LIMIT_RUPIAH",
    "PAYMENTS_TABLES",
    "PAY_NOTICE",
    "PREPARATION_STATUSES",
    "ExecutionRefused",
    "PaymentPreparationError",
    "PreparationService",
    "PreparedPayment",
    "Recipient",
    "approve_preparation",
    "create_schema",
    "execute_payment",
    "format_rupiah_amount",
    "get_prepared_payment",
    "list_payable_payments",
    "list_prepared_payments",
    "mark_paid_by_user",
    "prepare_payment",
    "record_execution_refusal",
    "reject_preparation",
    "render_prepared_payment",
    "reset_tables",
    "resolve_rail",
    "schema_path",
]
