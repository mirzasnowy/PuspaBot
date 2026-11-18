"""Tests: signed entitlements — HMAC signature, trial via config,
30-day grace, max 5 clouds per account."""

from datetime import datetime, timedelta, timezone

import pytest

from puspa.billing.config import BillingConfig
from puspa.billing.entitlements import (
    entitlement_from_dict,
    issue_entitlement,
    issue_trial_entitlement,
    verify_entitlement,
)

UTC = timezone.utc
SECRET = "test-entitlement-secret"
NOW = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)


def test_issue_and_verify_active():
    entitlement = issue_entitlement(
        user_id="user-1", machine_id="machine-1", tier="plus",
        secret=SECRET, now=NOW)
    assert entitlement.tier == "plus"
    assert entitlement.signature
    assert entitlement.expires_at == NOW + timedelta(days=30)
    result = verify_entitlement(entitlement, secret=SECRET, now=NOW)
    assert result == {"valid": True, "status": "active", "reason": None}


def test_tampered_field_fails_signature():
    entitlement = issue_entitlement(
        user_id="user-1", machine_id="machine-1", tier="plus",
        secret=SECRET, now=NOW)
    tampered = entitlement_from_dict({
        **entitlement.to_dict(), "tier": "team"})
    result = verify_entitlement(tampered, secret=SECRET, now=NOW)
    assert result["valid"] is False
    assert result["status"] == "invalid"
    assert result["reason"] == "bad_signature"


def test_wrong_secret_fails():
    entitlement = issue_entitlement(
        user_id="user-1", machine_id="machine-1", tier="plus",
        secret=SECRET, now=NOW)
    result = verify_entitlement(entitlement, secret="other-secret", now=NOW)
    assert result["valid"] is False
    assert result["reason"] == "bad_signature"


def test_missing_signature_invalid():
    entitlement = issue_entitlement(
        user_id="user-1", machine_id="machine-1", tier="plus",
        secret=SECRET, now=NOW)
    unsigned = entitlement_from_dict({**entitlement.to_dict(), "signature": ""})
    result = verify_entitlement(unsigned, secret=SECRET, now=NOW)
    assert result["valid"] is False
    assert result["reason"] == "missing_signature"


def test_trial_uses_config_days_and_flag():
    config = BillingConfig(trial_days=14)
    entitlement = issue_trial_entitlement(
        user_id="user-1", machine_id="machine-1", tier="plus",
        secret=SECRET, config=config, now=NOW)
    assert entitlement.trial is True
    assert entitlement.expires_at == NOW + timedelta(days=14)
    result = verify_entitlement(entitlement, secret=SECRET, config=config,
                                now=NOW)
    assert result["status"] == "active"


def test_grace_30_days_then_expired():
    entitlement = issue_entitlement(
        user_id="user-1", machine_id="machine-1", tier="plus",
        secret=SECRET, valid_days=30, now=NOW)
    # 10 days past expiry: inside the 30-day grace window.
    in_grace = verify_entitlement(
        entitlement, secret=SECRET, now=NOW + timedelta(days=40))
    assert in_grace == {"valid": True, "status": "grace",
                        "reason": "expired_within_grace_period"}
    # 31 days past expiry: past grace.
    expired = verify_entitlement(
        entitlement, secret=SECRET, now=NOW + timedelta(days=61))
    assert expired == {"valid": False, "status": "expired",
                       "reason": "past_grace_period"}


def test_grace_days_come_from_config():
    config = BillingConfig(grace_days=7)
    entitlement = issue_entitlement(
        user_id="user-1", machine_id="machine-1", tier="plus",
        secret=SECRET, config=config, valid_days=30, now=NOW)
    in_grace = verify_entitlement(
        entitlement, secret=SECRET, config=config,
        now=NOW + timedelta(days=35))
    assert in_grace["status"] == "grace"
    expired = verify_entitlement(
        entitlement, secret=SECRET, config=config,
        now=NOW + timedelta(days=38))
    assert expired["status"] == "expired"


def test_max_five_clouds_per_account():
    config = BillingConfig(max_clouds_per_account=5)
    clouds = tuple(f"cloud-{i}" for i in range(5))
    entitlement = issue_entitlement(
        user_id="user-1", machine_id="machine-1", tier="team",
        secret=SECRET, config=config, clouds=clouds, now=NOW)
    assert len(entitlement.clouds) == 5
    with pytest.raises(ValueError, match="too many clouds"):
        issue_entitlement(
            user_id="user-1", machine_id="machine-1", tier="team",
            secret=SECRET, config=config,
            clouds=tuple(f"cloud-{i}" for i in range(6)), now=NOW)


def test_unknown_tier_rejected():
    with pytest.raises(ValueError, match="unknown tier"):
        issue_entitlement(
            user_id="user-1", machine_id="machine-1", tier="ultra",
            secret=SECRET, now=NOW)


def test_round_trip_through_dict():
    entitlement = issue_entitlement(
        user_id="user-1", machine_id="machine-1", tier="pro",
        secret=SECRET, clouds=("cloud-a",), now=NOW)
    rebuilt = entitlement_from_dict(entitlement.to_dict())
    assert rebuilt == entitlement
    assert verify_entitlement(rebuilt, secret=SECRET, now=NOW)["valid"] is True
