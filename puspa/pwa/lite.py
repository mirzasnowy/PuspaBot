"""WebUI lite: the no-install trial surface (G32).

The lite mode is the same PWA served as server-rendered Jinja
templates (``puspa/pwa/templates/``) instead of the React build
(``apps/pwa/dist``). It exists for one job: let a trial user reach
Puspa with zero install friction — one chat page plus an upgrade
button (``templates/trial.html``), per the locked modes decision
(``docs/modes-decision.md``).

A trial is an entitlement, not a flag. The trial entitlement is
the billing entitlement port (``puspa.billing.ports``):
HMAC-signed, time-boxed Pro access (``trial_days`` from
``BillingConfig``), carried in an httpOnly cookie and verified
against the server secret on every request. The check is
fail-closed: a missing, malformed, tampered, wrong-user, or
unsigned trial never opens the chat — it shows the upgrade
invitation instead.

Honest limits of this module (see ``docs/webui-lite.md``):

- The billing package keeps no server-side record of issued
  entitlements (the G2 license endpoint is stateless), so one
  trial per user is enforced only as far as the presented
  cookie proves it: a user who presents a validly signed trial
  token (active, grace, or expired) cannot mint a second trial
  from this server, but a user who deletes the cookie can start
  over. A server-side trial ledger is future work, not this
  ticket.
- The secret comes from the ``PUSPA_ENTITLEMENT_SECRET``
  environment variable. With no secret configured the trial
  cannot start and every trial check reports "none": the lite
  mode never mints or trusts an entitlement without a secret.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import UTC, datetime

from puspa.billing.config import BillingConfig, default_config
from puspa.billing.ports import (
    Entitlement,
    entitlement_from_dict,
    issue_trial_entitlement,
    verify_entitlement,
)

__all__ = [
    "TRIAL_COOKIE",
    "TRIAL_TIER",
    "TrialState",
    "check_trial",
    "decode_trial_cookie",
    "encode_trial_cookie",
    "machine_id_for_user",
    "prior_trial_entitlement",
    "start_trial",
    "trial_cookie_max_age",
    "trial_secret_from_env",
    "upgrade_url",
]

#: Cookie carrying the signed trial entitlement (JSON of
#: ``Entitlement.to_dict()``, signature included).
TRIAL_COOKIE = "puspa_trial"

#: A free trial is time-boxed Pro access (modes decision).
TRIAL_TIER = "pro"

#: Env var holding the HMAC secret that signs trial entitlements.
#: The same secret signs every G2 billing entitlement; it never
#: leaves the server.
TRIAL_SECRET_ENV = "PUSPA_ENTITLEMENT_SECRET"


@dataclass(frozen=True)
class TrialState:
    """One trial check: what the user may see, and why.

    ``status`` is ``"active"`` (trial running), ``"grace"``
    (expired but inside the G2 grace window — still usable),
    ``"expired"`` (past the grace window), or ``"none"`` (no
    trial entitlement presented, or one that fails verification).
    Only ``"active"`` and ``"grace"`` open the chat.
    """

    status: str  # "active" | "grace" | "expired" | "none"
    is_trial: bool = False
    tier: str = ""
    expires_at: datetime | None = None
    days_left: int | None = None
    reason: str | None = None
    entitlement: Entitlement | None = None

    def to_dict(self) -> dict:
        """Template context: plain values, expiry as an ISO date."""
        return {
            "status": self.status,
            "is_trial": self.is_trial,
            "tier": self.tier,
            "expires_at": self.expires_at.date().isoformat() if self.expires_at else None,
            "days_left": self.days_left,
            "reason": self.reason,
        }


def trial_secret_from_env() -> str:
    """The entitlement HMAC secret from the environment ("" when unset)."""
    return os.environ.get(TRIAL_SECRET_ENV, "")


def upgrade_url(config: BillingConfig | None = None) -> str:
    """Where the upgrade button points: the billing portal base URL."""
    return (config or default_config()).portal_base_url


def trial_cookie_max_age(config: BillingConfig | None = None) -> int:
    """Cookie lifetime in seconds: trial window plus grace window,
    so the cookie disappears on its own once the trial is over."""
    cfg = config or default_config()
    return (cfg.trial_days + cfg.grace_days) * 24 * 60 * 60


def machine_id_for_user(user_id: str) -> str:
    """The machine-id a web trial entitlement is issued for.

    A trial user has no cloud machine yet, so the entitlement is
    bound to a stable per-user id instead of a machine. The id is
    namespaced so it can never collide with a real machine-id.
    """
    return f"lite-{user_id}"


def encode_trial_cookie(entitlement: Entitlement) -> str:
    """Serialise an entitlement for the trial cookie (signature kept)."""
    return json.dumps(entitlement.to_dict(), sort_keys=True)


def decode_trial_cookie(value: str | None) -> Entitlement | None:
    """Parse a trial cookie back into an entitlement.

    Returns None for a missing, non-JSON, or misshapen value —
    never raises, so a tampered cookie is just "no trial".
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        data = json.loads(value)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return entitlement_from_dict(data)
    except (KeyError, ValueError, TypeError):
        return None


def _days_left(expires_at: datetime, now: datetime) -> int:
    """Whole days remaining until expiry, rounded up so the first
    day of a 14-day trial shows 14, and the last day shows 1."""
    remaining = (expires_at - now).total_seconds()
    return max(0, math.ceil(remaining / (24 * 60 * 60))) if remaining > 0 else 0


def check_trial(
    user_id: str,
    *,
    secret: str,
    config: BillingConfig | None = None,
    now: datetime | None = None,
    token: Entitlement | None = None,
) -> TrialState:
    """Verify a presented trial entitlement for this user.

    The signature is checked first (G2 ``verify_entitlement``),
    then the user binding, then the dates. A paid (non-trial)
    entitlement that verifies is also accepted — a paying user
    landing on the trial page is entitled to chat — and is
    reported with ``is_trial=False`` so the page can label it.
    """
    now = now or datetime.now(UTC)
    if not secret:
        return TrialState(
            status="none",
            reason="trial is not configured on this server (no entitlement secret)",
        )
    if token is None:
        return TrialState(status="none", reason="no trial entitlement presented")
    if token.user_id != user_id:
        return TrialState(
            status="none",
            reason="entitlement belongs to another user",
            entitlement=token,
        )
    result = verify_entitlement(token, secret=secret, config=config, now=now)
    status = result["status"]
    if status == "invalid":
        return TrialState(
            status="none",
            reason=f"entitlement does not verify: {result['reason']}",
            entitlement=token,
        )
    if status == "expired":
        return TrialState(
            status="expired",
            is_trial=token.trial,
            tier=token.tier,
            expires_at=token.expires_at,
            days_left=0,
            reason=f"trial ended on {token.expires_at.date().isoformat()}",
            entitlement=token,
        )
    return TrialState(
        status=status,  # "active" or "grace"
        is_trial=token.trial,
        tier=token.tier,
        expires_at=token.expires_at,
        days_left=_days_left(token.expires_at, now),
        reason=result.get("reason"),
        entitlement=token,
    )


def prior_trial_entitlement(
    user_id: str,
    *,
    secret: str,
    config: BillingConfig | None = None,
    now: datetime | None = None,
    token: Entitlement | None = None,
) -> Entitlement | None:
    """A previously issued trial for this user, if the presented
    token proves one.

    Returns the entitlement when the token parses, belongs to this
    user, is flagged as a trial, and its signature verifies —
    whatever its dates say (active, grace, or expired). Returns
    None otherwise. The trial-start handler uses this to refuse a
    second trial while the first token is still presented.
    """
    if not secret or token is None:
        return None
    if token.user_id != user_id or not token.trial:
        return None
    result = verify_entitlement(token, secret=secret, config=config, now=now or datetime.now(UTC))
    if result["status"] == "invalid":
        return None
    return token


def start_trial(
    user_id: str,
    *,
    machine_id: str,
    secret: str,
    config: BillingConfig | None = None,
    now: datetime | None = None,
) -> Entitlement:
    """Mint a trial entitlement: Pro tier, ``trial_days`` from
    config, trial flag set. Raises ``ValueError`` with no secret
    configured (never mint unsigned) or for an unknown tier /
    empty machine id (from the G2 issuer).
    """
    if not secret:
        raise ValueError("cannot start a trial: no entitlement secret configured")
    return issue_trial_entitlement(
        user_id=user_id,
        machine_id=machine_id,
        tier=TRIAL_TIER,
        secret=secret,
        config=config,
        now=now,
    )
