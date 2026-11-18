"""Signed entitlements per machine-id + the home gate.

Most tests are pure crypto (no DB): issue with an ephemeral Ed25519
key, verify against the pinned public key. Revocation tests use the
Postgres test database (puspa_test). The team schema is applied by
the autouse fixture from tests.teams_helpers.
"""

from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from puspa.teams import (
    HomeGate,
    issue_entitlement,
    public_key_from_hex,
    public_key_from_raw,
    revoke_entitlement,
    verify_entitlement,
)
from puspa.teams.errors import EntitlementError
from tests.teams_helpers import _teams_schema, make_team

# The imported names are fixtures used by pytest injection (not
# by direct call); listing them keeps ruff from flagging the
# imports as unused or shadowed by the test parameters.
__all__ = ["_teams_schema", "make_team"]


@pytest.fixture
def signing_key():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def public_key(signing_key):
    return signing_key.public_key()


@pytest.fixture
def team_id(make_team):
    return make_team()["team"].id


def _issue(*, signing_key, team_id, machine_id="machine-1", tier="plus", **kwargs):
    return issue_entitlement(
        machine_id=machine_id,
        team_id=team_id,
        tier=tier,
        signing_key=signing_key,
        **kwargs,
    )


def test_issue_verify_round_trip(signing_key, public_key, team_id):
    entitlement = _issue(signing_key=signing_key, team_id=team_id)
    verdict = verify_entitlement(entitlement.token(), public_key)
    assert verdict.status == "valid"
    assert verdict.entitlement is not None
    assert verdict.entitlement.machine_id == "machine-1"
    assert verdict.entitlement.team_id == team_id
    assert verdict.entitlement.tier == "plus"


def test_token_shape_is_versioned_and_three_parts(signing_key, team_id):
    entitlement = _issue(signing_key=signing_key, team_id=team_id)
    parts = entitlement.token().split(".")
    assert len(parts) == 3
    assert parts[0] == "v1"


def _retier_token(token: str, tier: str) -> str:
    """Re-encode the payload with a different tier, keeping the old
    signature — a deterministic tamper (the signature must fail)."""
    import base64
    import json

    version, payload, signature = token.split(".")
    data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    data["tier"] = tier
    encoded = (
        base64.urlsafe_b64encode(json.dumps(data, sort_keys=True, separators=(",", ":")).encode())
        .rstrip(b"=")
        .decode()
    )
    return f"{version}.{encoded}.{signature}"


def test_tampered_payload_is_invalid(signing_key, public_key, team_id):
    entitlement = _issue(signing_key=signing_key, team_id=team_id)
    tampered = _retier_token(entitlement.token(), "pro")
    verdict = verify_entitlement(tampered, public_key)
    assert verdict.status == "invalid"
    assert "signature" in verdict.reason


def test_tampered_signature_is_invalid(signing_key, public_key, team_id):
    entitlement = _issue(signing_key=signing_key, team_id=team_id)
    token = entitlement.token()
    payload, signature = token.split(".")[1], token.split(".")[2]
    tampered_signature = ("A" if signature[0] != "A" else "B") + signature[1:]
    verdict = verify_entitlement(f"v1.{payload}.{tampered_signature}", public_key)
    assert verdict.status == "invalid"


def test_wrong_pinned_key_is_invalid(signing_key, team_id):
    entitlement = _issue(signing_key=signing_key, team_id=team_id)
    other_key = Ed25519PrivateKey.generate().public_key()
    verdict = verify_entitlement(entitlement.token(), other_key)
    assert verdict.status == "invalid"


def test_malformed_tokens_are_invalid(public_key):
    for bad in ("", "v1", "v1.aaa", "v2.aaa.bbb", "v1.!!!.bbb", "not-a-token"):
        verdict = verify_entitlement(bad, public_key)
        assert verdict.status == "invalid", bad


def test_expired_entitlement_is_expired(signing_key, public_key, team_id):
    entitlement = _issue(signing_key=signing_key, team_id=team_id, valid_days=1)
    after_expiry = entitlement.valid_until + timedelta(seconds=1)
    verdict = verify_entitlement(entitlement.token(), public_key, now=after_expiry)
    assert verdict.status == "expired"
    assert "expired on" in verdict.reason


def test_entitlement_valid_right_before_expiry(signing_key, public_key, team_id):
    entitlement = _issue(signing_key=signing_key, team_id=team_id, valid_days=1)
    just_before = entitlement.valid_until - timedelta(seconds=1)
    verdict = verify_entitlement(entitlement.token(), public_key, now=just_before)
    assert verdict.status == "valid"


def test_unknown_tier_is_rejected_at_issue(signing_key, team_id):
    with pytest.raises(EntitlementError):
        _issue(signing_key=signing_key, team_id=team_id, tier="enterprise")


def test_issue_rejects_empty_machine_id(signing_key, team_id):
    with pytest.raises(ValueError):
        _issue(signing_key=signing_key, team_id=team_id, machine_id="  ")


def test_issue_rejects_non_positive_validity(signing_key, team_id):
    with pytest.raises(ValueError):
        _issue(signing_key=signing_key, team_id=team_id, valid_days=0)


def test_pinned_key_round_trip_from_raw_and_hex(signing_key, public_key, team_id):
    raw = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    assert len(raw) == 32
    entitlement = _issue(signing_key=signing_key, team_id=team_id)
    assert verify_entitlement(entitlement.token(), public_key_from_raw(raw)).status == "valid"
    assert verify_entitlement(entitlement.token(), public_key_from_hex(raw.hex())).status == "valid"
    with pytest.raises(ValueError):
        public_key_from_raw(b"too short")
    with pytest.raises(ValueError):
        public_key_from_hex("not hex at all")


def test_revoked_entitlement_is_revoked_with_dsn(test_dsn, signing_key, public_key, team_id):
    entitlement = _issue(signing_key=signing_key, team_id=team_id, dsn=test_dsn)
    assert verify_entitlement(entitlement.token(), public_key, dsn=test_dsn).status == "valid"
    assert revoke_entitlement(entitlement.entitlement_id, dsn=test_dsn) is True
    verdict = verify_entitlement(entitlement.token(), public_key, dsn=test_dsn)
    assert verdict.status == "revoked"
    # Revoking an unknown id is a no-op (False), not an error.
    assert revoke_entitlement("00000000-0000-0000-0000-000000000000", dsn=test_dsn) is False


def test_revocation_invisible_without_dsn(test_dsn, signing_key, public_key, team_id):
    # A token-only check cannot see the revocation table: the token
    # still verifies. That is documented, not silent — the home gate
    # must be constructed with a DSN for revocation to apply.
    entitlement = _issue(signing_key=signing_key, team_id=team_id, dsn=test_dsn)
    revoke_entitlement(entitlement.entitlement_id, dsn=test_dsn)
    assert verify_entitlement(entitlement.token(), public_key).status == "valid"


# --- HomeGate ---------------------------------------------------------------


def test_home_gate_allows_valid_entitlement(signing_key, public_key, team_id):
    entitlement = _issue(signing_key=signing_key, team_id=team_id)
    gate = HomeGate(public_key)
    decision = gate.decide("machine-1", entitlement.token())
    assert decision.allowed is True
    assert decision.via == "entitlement"
    assert "plus" in decision.reason


def test_home_gate_allows_admin_entitlement(signing_key, public_key, team_id):
    entitlement = _issue(signing_key=signing_key, team_id=team_id, tier="admin")
    gate = HomeGate(public_key)
    decision = gate.decide("machine-1", entitlement.token())
    assert decision.allowed is True
    assert decision.via == "admin"


def test_home_gate_denies_without_any_token(public_key):
    decision = HomeGate(public_key).decide("machine-1", None)
    assert decision.allowed is False
    assert decision.via == "denied"
    assert "no entitlement" in decision.reason


def test_home_gate_denies_wrong_machine(signing_key, public_key, team_id):
    entitlement = _issue(signing_key=signing_key, team_id=team_id, machine_id="machine-1")
    decision = HomeGate(public_key).decide("machine-2", entitlement.token())
    assert decision.allowed is False
    assert "machine-2" in decision.reason


def test_home_gate_denies_expired_with_clear_reason(signing_key, public_key, team_id):
    entitlement = _issue(signing_key=signing_key, team_id=team_id, valid_days=1)
    gate = HomeGate(public_key)
    decision = gate.decide(
        "machine-1",
        entitlement.token(),
        now=entitlement.valid_until + timedelta(seconds=1),
    )
    assert decision.allowed is False
    assert decision.via == "denied"
    assert "expired on" in decision.reason


def test_home_gate_denies_tampered_token(signing_key, public_key, team_id):
    entitlement = _issue(signing_key=signing_key, team_id=team_id)
    tampered = _retier_token(entitlement.token(), "pro")
    decision = HomeGate(public_key).decide("machine-1", tampered)
    assert decision.allowed is False
    assert "signature" in decision.reason


def test_home_gate_denies_revoked_with_dsn(test_dsn, signing_key, public_key, team_id):
    entitlement = _issue(signing_key=signing_key, team_id=team_id, dsn=test_dsn)
    revoke_entitlement(entitlement.entitlement_id, dsn=test_dsn)
    decision = HomeGate(public_key, dsn=test_dsn).decide("machine-1", entitlement.token())
    assert decision.allowed is False
    assert "revoked" in decision.reason


def test_home_gate_denies_empty_machine_id(public_key):
    decision = HomeGate(public_key).decide("   ", "v1.aaa.bbb")
    assert decision.allowed is False
    assert "machine_id" in decision.reason


def test_verify_never_raises_on_garbage(public_key):
    # verify_entitlement must fail closed with a verdict, never an
    # exception — the home gate depends on it.
    for bad in (None, 123, "", "v1.e30.e30", "v1.aaaa.bbbb"):
        verdict = verify_entitlement(bad, public_key)
        assert verdict.status == "invalid"
    assert datetime.now(UTC) is not None  # sanity: the module imports cleanly
