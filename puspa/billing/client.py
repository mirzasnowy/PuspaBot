"""Billing client state machine (hermes-style, rewritten for Puspa).

The client never assumes a billing state it has not fetched. Every
state has an explicit render branch; a failed fetch is fail-closed
(the overlay is not opened and no state is assumed). Every refusal
code has typed copy plus a recovery action; an unknown code falls
through to a default that still shows the server message, never an
empty screen or a crash.

Charging:
- every charge carries an idempotency key; a key conflict starts a
  new charge with a fresh key, it is never retried under the same key.
- settlement is polled every 2 seconds, capped at 5 minutes; the
  sleep and clock are injectable so tests use a fake clock instead of
  really sleeping 5 minutes. A poll timeout is reported as a timeout,
  not a failure, and an unconfirmed result is always framed as
  "check your balance first".
- the effect of a charge (charge_now / scheduled / no_op / blocked)
  is previewed before confirmation.
- in the upgrade matrix the *reason* is checked before the status.
- a scope step-up is resumable: the flow keeps its state and can be
  resumed after the user grants the scope.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from .config import BillingConfig, default_config
from .providers import KeyConflictError, PaymentProvider

#: Client states. Each one has an explicit render branch in
#: ``STATE_RENDER``; there is no fall-through to an empty screen.
CLIENT_STATES = (
    "idle",
    "loading",
    "ready",
    "needs_payment_method",
    "needs_consent",
    "needs_scope_stepup",
    "awaiting_settlement",
    "settled",
    "failed",
    "timeout",
    "upgrade_blocked",
    "session_revoked",
)

#: Render table: state -> what the UI shows. ``actions`` are action
#: ids the UI wires to recovery handlers.
STATE_RENDER: dict[str, dict] = {
    "idle": {
        "title": "Billing",
        "body": "Billing is not open.",
        "actions": ["open_billing"],
    },
    "loading": {
        "title": "Loading billing…",
        "body": "Fetching your plan, credits, and invoices.",
        "actions": [],
    },
    "ready": {
        "title": "Billing",
        "body": "Your plan, credits, and invoices are up to date.",
        "actions": ["buy_credits", "change_plan", "view_invoices"],
    },
    "needs_payment_method": {
        "title": "Add a payment method",
        "body": "Add a payment method to continue with this purchase.",
        "actions": ["add_payment_method", "cancel"],
    },
    "needs_consent": {
        "title": "Consent needed",
        "body": "Confirm you agree to the charge before we continue.",
        "actions": ["give_consent", "cancel"],
    },
    "needs_scope_stepup": {
        "title": "Extra permission needed",
        "body": "This action needs an extra permission. Grant it to continue where you left off.",
        "actions": ["grant_scope", "cancel"],
    },
    "awaiting_settlement": {
        "title": "Waiting for payment",
        "body": "Your payment is being confirmed. This usually takes a few seconds.",
        "actions": ["check_status"],
    },
    "settled": {
        "title": "Payment confirmed",
        "body": "Your payment went through and your credits are ready.",
        "actions": ["view_receipt", "done"],
    },
    "failed": {
        "title": "Payment failed",
        "body": "The payment did not go through. No money was taken.",
        "actions": ["retry", "add_payment_method", "contact_support"],
    },
    "timeout": {
        "title": "Still confirming",
        "body": "We could not confirm the payment yet. Check your balance first before trying again, so you are not charged twice.",
        "actions": ["check_balance", "check_status", "contact_support"],
    },
    "upgrade_blocked": {
        "title": "Upgrade blocked",
        "body": "This upgrade cannot go through right now. See the reason below.",
        "actions": ["view_reason", "contact_support"],
    },
    "session_revoked": {
        "title": "Session ended",
        "body": "Your billing session ended. Sign in again to continue.",
        "actions": ["sign_in_again"],
    },
}

#: Refusal codes: typed copy + recovery action for each code.
REFUSAL_CODES: dict[str, dict] = {
    "insufficient_scope": {
        "message": "This action needs a permission your session does not have.",
        "recovery": "Grant the missing permission, then retry. Your place is kept.",
        "action": "grant_scope",
        "state": "needs_scope_stepup",
    },
    "session_revoked": {
        "message": "Your session was revoked.",
        "recovery": "Sign in again to continue.",
        "action": "sign_in_again",
        "state": "session_revoked",
    },
    "role_required": {
        "message": "This action needs a role you do not have (for example, a team admin).",
        "recovery": "Ask a team admin to do it, or switch to an account that has the role.",
        "action": "switch_account",
        "state": "upgrade_blocked",
    },
    "consent_required": {
        "message": "We need your consent before charging.",
        "recovery": "Review the charge and confirm to continue.",
        "action": "give_consent",
        "state": "needs_consent",
    },
    "monthly_cap_exceeded": {
        "message": "You hit this month's spending cap.",
        "recovery": "Wait until next month, raise the cap, or upgrade your plan.",
        "action": "raise_cap",
        "state": "upgrade_blocked",
    },
    "no_payment_method": {
        "message": "No payment method is saved on this account.",
        "recovery": "Add a payment method to continue.",
        "action": "add_payment_method",
        "state": "needs_payment_method",
    },
    "upgrade_cap_exceeded": {
        "message": "This upgrade would exceed the upgrade cap for your account.",
        "recovery": "Pick a smaller plan, or ask support to raise the cap.",
        "action": "contact_support",
        "state": "upgrade_blocked",
    },
    "stripe_unavailable": {
        "message": "The payment provider is unavailable right now.",
        "recovery": "Try again in a few minutes. No charge was made.",
        "action": "retry",
        "state": "failed",
    },
    "payment_failed": {
        "message": "The payment was declined.",
        "recovery": "Check the payment method or try another one.",
        "action": "retry",
        "state": "failed",
    },
}

#: Reasons that block an upgrade no matter what the status says.
#: Checked *before* status in the upgrade matrix.
BLOCKING_UPGRADE_REASONS = frozenset(
    {
        "monthly_cap_exceeded",
        "upgrade_cap_exceeded",
        "role_required",
        "consent_required",
    }
)


def render_state(state: str) -> dict:
    """Render branch for a state. Unknown states render as failed,
    never empty."""
    branch = STATE_RENDER.get(state)
    if branch is None:
        return {
            "title": "Something went wrong",
            "body": f"Unknown billing state {state!r}. Nothing was charged.",
            "actions": ["contact_support"],
        }
    return dict(branch)


def refusal_for(code: str, *, server_message: str = "") -> dict:
    """Typed copy + recovery for a refusal code.

    An unknown code falls through to a default that still shows the
    server message, never an empty screen or a crash.
    """
    known = REFUSAL_CODES.get(code)
    if known is not None:
        return {
            "code": code,
            "message": known["message"],
            "recovery": known["recovery"],
            "action": known["action"],
            "state": known["state"],
            "server_message": server_message,
        }
    message = server_message or f"The request was refused ({code})."
    return {
        "code": code,
        "message": message,
        "recovery": "Try again, or contact support if it keeps happening.",
        "action": "contact_support",
        "state": "failed",
        "server_message": server_message,
    }


def new_idempotency_key() -> str:
    """A fresh idempotency key for a charge."""
    return uuid.uuid4().hex


def fetch_billing_state(fetch_fn) -> dict:
    """Fetch billing state, fail-closed.

    ``fetch_fn`` is a zero-arg callable returning the state dict.
    On any error the overlay stays closed and no state is assumed:
    ``{"state": "failed", "overlay_open": False, ...}``.
    """
    try:
        state = fetch_fn()
    except Exception as exc:
        return {
            "state": "failed",
            "overlay_open": False,
            "error": str(exc)[:200],
            "assumed": None,
        }
    if not isinstance(state, dict) or "state" not in state:
        return {
            "state": "failed",
            "overlay_open": False,
            "error": "billing state was malformed",
            "assumed": None,
        }
    return {"state": state["state"], "overlay_open": True, **state}


def preview_charge_effect(
    *, amount_idr: int, balance_idr: int, scheduled: bool = False, blocked_reason: str | None = None
) -> dict:
    """Preview what a charge would do, before confirmation.

    Effect is one of ``"charge_now"``, ``"scheduled"``, ``"no_op"``
    (amount is zero), or ``"blocked"`` (with a reason).
    """
    if blocked_reason:
        return {"effect": "blocked", "reason": blocked_reason, "amount_idr": amount_idr}
    if amount_idr <= 0:
        return {"effect": "no_op", "reason": "zero_amount", "amount_idr": amount_idr}
    if scheduled:
        return {"effect": "scheduled", "reason": None, "amount_idr": amount_idr}
    return {
        "effect": "charge_now",
        "reason": None,
        "amount_idr": amount_idr,
        "balance_after_idr": balance_idr - amount_idr,
    }


def evaluate_upgrade(
    *,
    current_tier: str,
    target_tier: str,
    reason: str | None,
    status: str,
    config: BillingConfig | None = None,
) -> dict:
    """Upgrade matrix. The *reason* is checked before the *status*.

    A blocking reason blocks the upgrade even when the status looks
    fine. Otherwise the status decides: ``"ok"`` allows, anything
    else blocks with the status as the reason.
    """
    cfg = config or default_config()
    if reason in BLOCKING_UPGRADE_REASONS:
        return {
            "allowed": False,
            "effect": "blocked",
            "reason": reason,
            "current_tier": current_tier,
            "target_tier": target_tier,
        }
    if status != "ok":
        return {
            "allowed": False,
            "effect": "blocked",
            "reason": status or "unknown_status",
            "current_tier": current_tier,
            "target_tier": target_tier,
        }
    try:
        cfg.tier(target_tier)
    except KeyError:
        return {
            "allowed": False,
            "effect": "blocked",
            "reason": "unknown_tier",
            "current_tier": current_tier,
            "target_tier": target_tier,
        }
    return {
        "allowed": True,
        "effect": "charge_now" if cfg.tier(target_tier).price_idr_per_month > 0 else "no_op",
        "reason": None,
        "current_tier": current_tier,
        "target_tier": target_tier,
    }


def poll_settlement(
    check_status,
    *,
    interval_s: float = 2.0,
    timeout_s: float = 300.0,
    sleep=time.sleep,
    monotonic=time.monotonic,
) -> dict:
    """Poll until a payment settles, fails, or times out.

    ``check_status`` is a zero-arg callable returning ``"pending"``,
    ``"settled"``, or ``"failed"`` (or a dict with a ``"status"``
    key). ``sleep`` and ``monotonic`` are injectable so tests can use
    a fake clock instead of really waiting up to 5 minutes. A timeout
    is reported as ``"timeout"``, never as a failure, and the caller
    must frame it as "check your balance first".
    """
    deadline = monotonic() + timeout_s
    attempts = 0
    while True:
        attempts += 1
        raw = check_status()
        status = raw.get("status") if isinstance(raw, dict) else raw
        if status == "settled":
            return {"outcome": "settled", "attempts": attempts, "framing": "confirmed"}
        if status == "failed":
            return {"outcome": "failed", "attempts": attempts, "framing": "failed"}
        if monotonic() >= deadline:
            return {"outcome": "timeout", "attempts": attempts, "framing": "check_balance_first"}
        sleep(interval_s)


@dataclass
class StepUpFlow:
    """A resumable scope step-up: the flow keeps its place.

    ``start`` records what the user was doing; ``resume`` continues
    it after the scope is granted instead of starting over.
    """

    action: str
    payload: dict = field(default_factory=dict)
    state: str = "waiting_for_scope"

    def resume(self, *, scope_granted: bool) -> dict:
        if not scope_granted:
            self.state = "cancelled"
            return {"resumed": False, "state": self.state, "action": self.action}
        self.state = "resumed"
        return {
            "resumed": True,
            "state": self.state,
            "action": self.action,
            "payload": dict(self.payload),
        }


def portal_link(tier_id: str, *, config: BillingConfig | None = None) -> str:
    """Portal deep link for a tier: ``.../billing?plan=<tier_id>``.

    The ``plan`` parameter is stable across CLI and web, so the same
    link works from both.
    """
    cfg = config or default_config()
    return f"{cfg.portal_base_url.rstrip('/')}/billing?plan={tier_id}"


@dataclass
class BillingClient:
    """Client over a payment provider: checkout + settlement polling.

    Idempotency: a charge starts with a fresh key. When the provider
    reports a key conflict, a new charge is started with a fresh key
    (up to ``max_key_retries``); the conflicting key is never reused.
    """

    provider: PaymentProvider
    config: BillingConfig = field(default_factory=default_config)
    max_key_retries: int = 3

    def start_checkout(
        self, *, amount_idr: int, description: str, idempotency_key: str | None = None
    ):
        """Create a checkout, retrying a key conflict with a fresh key."""
        key = idempotency_key or new_idempotency_key()
        attempts = 0
        while True:
            attempts += 1
            try:
                checkout = self.provider.create_checkout(
                    amount_idr=amount_idr, description=description, idempotency_key=key
                )
                return {"checkout": checkout, "attempts": attempts, "idempotency_key": key}
            except KeyConflictError:
                if attempts > self.max_key_retries:
                    raise
                key = new_idempotency_key()

    def charge(
        self,
        *,
        amount_idr: int,
        description: str,
        balance_idr: int,
        check_status,
        sleep=time.sleep,
        monotonic=time.monotonic,
    ) -> dict:
        """Preview, charge, then poll settlement.

        Returns the preview, the checkout, and the poll outcome. An
        unconfirmed outcome is framed "check_balance_first".
        """
        preview = preview_charge_effect(amount_idr=amount_idr, balance_idr=balance_idr)
        if preview["effect"] in ("no_op", "blocked"):
            return {"preview": preview, "checkout": None, "poll": None}
        started = self.start_checkout(amount_idr=amount_idr, description=description)
        poll = poll_settlement(
            check_status,
            interval_s=self.config.settlement_poll_interval_s,
            timeout_s=self.config.settlement_poll_timeout_s,
            sleep=sleep,
            monotonic=monotonic,
        )
        return {"preview": preview, "checkout": started["checkout"], "poll": poll}
