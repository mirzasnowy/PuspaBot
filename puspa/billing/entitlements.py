"""Signed entitlements: who may use what, and for how long.

A cloud machine only runs with a signed entitlement for one
machine-id (or user-id) + tier + validity window. The signature is
HMAC-SHA256 over a canonical JSON of the entitlement fields, keyed by
a server secret that never leaves the server. Verification checks the
signature *before* the entitlement is used for anything.

Rules:
- Trial entitlements come from config (``trial_days``).
- After ``expires_at`` the entitlement is expired, but still usable
  during the ``grace_days`` window, flagged as ``"grace"``.
- At most ``max_clouds_per_account`` cloud machines per account
  (config, not hardcoded).
- A tampered field, an unknown tier, or a bad signature is rejected.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .config import BillingConfig, default_config

UTC = timezone.utc


@dataclass(frozen=True)
class Entitlement:
    """A signed grant of a tier to one user/machine."""

    user_id: str
    machine_id: str
    tier: str
    issued_at: datetime
    expires_at: datetime
    trial: bool = False
    clouds: tuple[str, ...] = ()
    signature: str = ""

    def to_dict(self, *, include_signature: bool = True) -> dict:
        data = {
            "user_id": self.user_id,
            "machine_id": self.machine_id,
            "tier": self.tier,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "trial": self.trial,
            "clouds": list(self.clouds),
        }
        if include_signature:
            data["signature"] = self.signature
        return data


def _canonical_payload(entitlement: Entitlement) -> bytes:
    """Canonical bytes that get signed (signature excluded)."""
    return json.dumps(
        entitlement.to_dict(include_signature=False),
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _sign(entitlement: Entitlement, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"), _canonical_payload(entitlement),
        hashlib.sha256).hexdigest()


def issue_entitlement(*, user_id: str, machine_id: str, tier: str,
                      secret: str, config: BillingConfig | None = None,
                      valid_days: int = 30,
                      now: datetime | None = None,
                      trial: bool = False,
                      clouds: tuple[str, ...] = ()) -> Entitlement:
    """Issue a signed entitlement. Raises ``ValueError`` for an unknown tier."""
    cfg = config or default_config()
    try:
        cfg.tier(tier)
    except KeyError as exc:
        raise ValueError(f"unknown tier {tier!r}") from exc
    now = now or datetime.now(UTC)
    if trial:
        valid_days = cfg.trial_days
    if len(clouds) > cfg.max_clouds_per_account:
        raise ValueError(
            f"too many clouds: {len(clouds)} > max "
            f"{cfg.max_clouds_per_account} per account"
        )
    unsigned = Entitlement(
        user_id=user_id, machine_id=machine_id, tier=tier,
        issued_at=now, expires_at=now + timedelta(days=valid_days),
        trial=trial, clouds=tuple(clouds))
    return Entitlement(
        user_id=unsigned.user_id, machine_id=unsigned.machine_id,
        tier=unsigned.tier, issued_at=unsigned.issued_at,
        expires_at=unsigned.expires_at, trial=unsigned.trial,
        clouds=unsigned.clouds,
        signature=_sign(unsigned, secret))


def issue_trial_entitlement(*, user_id: str, machine_id: str, tier: str,
                            secret: str,
                            config: BillingConfig | None = None,
                            now: datetime | None = None) -> Entitlement:
    """Issue a trial entitlement: ``trial_days`` from config, trial flag set."""
    return issue_entitlement(
        user_id=user_id, machine_id=machine_id, tier=tier, secret=secret,
        config=config, now=now, trial=True)


def verify_entitlement(entitlement: Entitlement, *, secret: str,
                       config: BillingConfig | None = None,
                       now: datetime | None = None) -> dict:
    """Verify an entitlement before it is used.

    Returns ``{"valid": bool, "status": ..., "reason": ...}`` where
    status is ``"active"``, ``"grace"``, ``"expired"``, or
    ``"invalid"``. The signature is checked first; an entitlement
    whose signature does not match is never trusted, whatever its
    dates say.
    """
    cfg = config or default_config()
    now = now or datetime.now(UTC)
    if not entitlement.signature:
        return {"valid": False, "status": "invalid",
                "reason": "missing_signature"}
    expected = _sign(entitlement, secret)
    if not hmac.compare_digest(entitlement.signature, expected):
        return {"valid": False, "status": "invalid",
                "reason": "bad_signature"}
    try:
        cfg.tier(entitlement.tier)
    except KeyError:
        return {"valid": False, "status": "invalid",
                "reason": "unknown_tier"}
    if len(entitlement.clouds) > cfg.max_clouds_per_account:
        return {"valid": False, "status": "invalid",
                "reason": "too_many_clouds"}
    if now <= entitlement.expires_at:
        return {"valid": True, "status": "active", "reason": None}
    grace_until = entitlement.expires_at + timedelta(days=cfg.grace_days)
    if now <= grace_until:
        return {"valid": True, "status": "grace",
                "reason": "expired_within_grace_period"}
    return {"valid": False, "status": "expired", "reason": "past_grace_period"}


def entitlement_from_dict(data: dict) -> Entitlement:
    """Rebuild an entitlement from a stored dict (signature included)."""
    return Entitlement(
        user_id=data["user_id"],
        machine_id=data["machine_id"],
        tier=data["tier"],
        issued_at=datetime.fromisoformat(data["issued_at"]),
        expires_at=datetime.fromisoformat(data["expires_at"]),
        trial=bool(data.get("trial", False)),
        clouds=tuple(data.get("clouds") or ()),
        signature=data.get("signature", ""),
    )
