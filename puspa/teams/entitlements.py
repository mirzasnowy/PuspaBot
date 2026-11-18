"""Signed entitlements per machine-id: the gate for "may this home run".

Pattern (DuskBot ``server/cloud-entitlement.ts``, rewritten as Puspa
code): a cloud home only runs with a signed entitlement for one
machine-id + team + tier + validity window. Without a valid
entitlement, only a signed admin entitlement and the liveness probe
are answered — everything else is refused, loudly, never silently.

Why Ed25519, not the HMAC of G2's billing entitlements
(``puspa/billing/entitlements.py``): a billing entitlement is
verified by the server that holds the HMAC secret. A home
entitlement is verified by the home itself, which must NOT hold the
server secret — anyone who can read the home's disk would then be
able to mint entitlements. So the entitlement is signed with the
server's Ed25519 private key and verified against the public key
pinned in the release — the same pinning pattern as the G11
identity key (``puspa/hosting/identity.py``): the fingerprint is
SHA-256 of the raw 32-byte public key, and the home refuses to run
when the pinned key cannot be loaded.

Token format (``v1.<payload>.<signature>``, both parts base64url
without padding): the payload is canonical JSON (``sort_keys``,
compact separators) with keys ``v``, ``entitlement_id``,
``machine_id``, ``team_id``, ``tier``, ``issued_at``,
``valid_until`` (ISO-8601 UTC). The signature covers the exact
payload bytes. Any change to any field breaks the signature.

Tiers are the G2 billing tier ids (``free`` / ``plus`` / ``pro`` /
``team``) plus ``admin``: an ``admin`` entitlement is the signed
admin override — it answers the liveness probe and the admin path,
but it is still an entitlement (it expires, it can be revoked).
An unknown tier is invalid, fail-closed.

Verification (``verify_entitlement``) checks, in order: shape →
signature against the pinned key → tier known → not expired →
not revoked (when a store/DSN is given, the ``entitlements`` table
is consulted). The result is a ``EntitlementVerdict`` with a plain
``reason`` string — the home prints that reason instead of dying
silently, so an expired or tampered entitlement is diagnosable.

``HomeGate.decide`` is the one call a home makes at startup:
a valid entitlement for THIS machine-id allows the home
(``via="entitlement"``); a valid admin entitlement allows it as
admin (``via="admin"``); anything else denies with the exact
reason (``via="denied"``). An entitlement for another machine-id
never allows this home, even when the signature is valid.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from psycopg.rows import dict_row

from puspa.accounts.users import resolve_dsn

from .errors import EntitlementError
from .teams import _validate_uuid

__all__ = [
    "ENTITLEMENT_VERSION",
    "KNOWN_TIERS",
    "Entitlement",
    "EntitlementVerdict",
    "HomeDecision",
    "HomeGate",
    "issue_entitlement",
    "public_key_from_hex",
    "public_key_from_raw",
    "revoke_entitlement",
    "verify_entitlement",
]

ENTITLEMENT_VERSION = 1

#: Billing tier ids (G2) plus the signed-admin override tier.
KNOWN_TIERS = ("free", "plus", "pro", "team", "admin")

ADMIN_TIER = "admin"


@dataclass(frozen=True)
class Entitlement:
    """A signed grant of a tier to one machine-id in one team."""

    entitlement_id: str
    machine_id: str
    team_id: str
    tier: str
    issued_at: datetime
    valid_until: datetime
    signature: str  # base64url (no padding) over the canonical payload

    def token(self) -> str:
        """Serialise to the ``v1.<payload>.<signature>`` token."""
        payload = _canonical_payload_bytes(self)
        return f"v{ENTITLEMENT_VERSION}.{_b64url(payload)}.{self.signature}"

    def to_dict(self) -> dict:
        return {
            "entitlement_id": self.entitlement_id,
            "machine_id": self.machine_id,
            "team_id": self.team_id,
            "tier": self.tier,
            "issued_at": self.issued_at.isoformat(),
            "valid_until": self.valid_until.isoformat(),
            "signature": self.signature,
        }


@dataclass(frozen=True)
class EntitlementVerdict:
    """One verification result: status + plain reason + entitlement
    when the token parsed (even when expired or revoked)."""

    status: str  # "valid" | "expired" | "invalid" | "revoked"
    reason: str
    entitlement: Entitlement | None = None


@dataclass(frozen=True)
class HomeDecision:
    """One home gate decision: allowed or not, why, and via what."""

    allowed: bool
    reason: str
    via: str  # "entitlement" | "admin" | "denied"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _canonical_payload_bytes(entitlement: Entitlement) -> bytes:
    """Canonical bytes that get signed (signature excluded)."""
    return json.dumps(
        {
            "v": ENTITLEMENT_VERSION,
            "entitlement_id": entitlement.entitlement_id,
            "machine_id": entitlement.machine_id,
            "team_id": entitlement.team_id,
            "tier": entitlement.tier,
            "issued_at": entitlement.issued_at.isoformat(),
            "valid_until": entitlement.valid_until.isoformat(),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def public_key_from_raw(raw: bytes) -> Ed25519PublicKey:
    """Build a public key from 32 raw bytes (what the app pins)."""
    if not isinstance(raw, bytes) or len(raw) != 32:
        raise ValueError("pinned public key must be 32 raw bytes")
    return Ed25519PublicKey.from_public_bytes(raw)


def public_key_from_hex(hex_value: str) -> Ed25519PublicKey:
    """Build a public key from 64 hex characters."""
    try:
        return public_key_from_raw(bytes.fromhex(hex_value))
    except (ValueError, binascii.Error) as exc:
        raise ValueError("pinned public key must be 64 hex characters") from exc


def _parse_token(token: str) -> tuple[Entitlement, bytes, bytes] | None:
    """Parse a token into (entitlement, payload bytes, signature).
    Returns None when the shape is wrong (never raises)."""
    if not isinstance(token, str):
        return None
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != f"v{ENTITLEMENT_VERSION}":
        return None
    try:
        payload = _b64url_decode(parts[1])
        signature = _b64url_decode(parts[2])
        data = json.loads(payload.decode("utf-8"))
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or data.get("v") != ENTITLEMENT_VERSION:
        return None
    try:
        issued_at = datetime.fromisoformat(str(data["issued_at"]))
        valid_until = datetime.fromisoformat(str(data["valid_until"]))
        if issued_at.tzinfo is None or valid_until.tzinfo is None:
            return None  # naive datetimes would break expiry comparison
        entitlement = Entitlement(
            entitlement_id=str(data["entitlement_id"]),
            machine_id=str(data["machine_id"]),
            team_id=str(data["team_id"]),
            tier=str(data["tier"]),
            issued_at=issued_at,
            valid_until=valid_until,
            signature=parts[2],
        )
    except (KeyError, ValueError, TypeError):
        return None
    return entitlement, payload, signature


def issue_entitlement(
    *,
    machine_id: str,
    team_id: str,
    tier: str,
    signing_key: Ed25519PrivateKey,
    valid_days: int = 30,
    now: datetime | None = None,
    dsn=None,
) -> Entitlement:
    """Issue a signed entitlement. When ``dsn`` is given, the
    entitlement is also recorded in the ``entitlements`` table
    (so it can be revoked later); without a DSN it is token-only.

    Raises ``EntitlementError`` for an unknown tier, ``ValueError``
    for empty ids or a non-positive validity window.
    """
    if tier not in KNOWN_TIERS:
        raise EntitlementError(f"unknown tier: {tier!r}")
    if not isinstance(machine_id, str) or not machine_id.strip():
        raise ValueError("machine_id must be a non-empty string")
    if valid_days <= 0:
        raise ValueError("valid_days must be positive")
    team_id = _validate_uuid(team_id, "team_id")
    now = now or datetime.now(UTC)
    unsigned = Entitlement(
        entitlement_id=str(uuid.uuid4()),
        machine_id=machine_id.strip(),
        team_id=team_id,
        tier=tier,
        issued_at=now,
        valid_until=now + timedelta(days=valid_days),
        signature="",
    )
    signature = _b64url(signing_key.sign(_canonical_payload_bytes(unsigned)))
    entitlement = Entitlement(
        entitlement_id=unsigned.entitlement_id,
        machine_id=unsigned.machine_id,
        team_id=unsigned.team_id,
        tier=unsigned.tier,
        issued_at=unsigned.issued_at,
        valid_until=unsigned.valid_until,
        signature=signature,
    )
    if dsn is not None:
        with psycopg.connect(resolve_dsn(dsn)) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO entitlements (id, machine_id, team_id, tier, valid_until) "
                "VALUES (%s, %s, %s, %s, %s)",
                (
                    entitlement.entitlement_id,
                    entitlement.machine_id,
                    entitlement.team_id,
                    entitlement.tier,
                    entitlement.valid_until,
                ),
            )
            conn.commit()
    return entitlement


def revoke_entitlement(entitlement_id: str, *, dsn=None) -> bool:
    """Mark an entitlement revoked. Returns True when a row was
    marked, False when the id does not exist (idempotent).
    """
    entitlement_id = _validate_uuid(entitlement_id, "entitlement_id")
    with psycopg.connect(resolve_dsn(dsn)) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE entitlements SET revoked = true WHERE id = %s",
            (entitlement_id,),
        )
        marked = cur.rowcount > 0
        conn.commit()
    return marked


def _is_revoked(entitlement_id: str, dsn) -> bool:
    with psycopg.connect(resolve_dsn(dsn), row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT revoked FROM entitlements WHERE id = %s", (entitlement_id,))
        row = cur.fetchone()
    return bool(row["revoked"]) if row is not None else False


def verify_entitlement(
    token: str,
    public_key: Ed25519PublicKey,
    *,
    now: datetime | None = None,
    dsn=None,
) -> EntitlementVerdict:
    """Verify a token against the pinned public key. Never raises:
    every failure is a verdict with a plain reason.

    Checks in order: shape → signature → tier known → not expired
    → not revoked (only when ``dsn`` is given; a token-only check
    cannot see revocations and says so in its reason when the
    token is otherwise valid).
    """
    now = now or datetime.now(UTC)
    parsed = _parse_token(token)
    if parsed is None:
        return EntitlementVerdict(status="invalid", reason="entitlement token is malformed")
    entitlement, payload, signature = parsed
    try:
        public_key.verify(signature, payload)
    except (InvalidSignature, ValueError, TypeError):
        return EntitlementVerdict(
            status="invalid",
            reason="entitlement signature does not verify against the pinned key",
            entitlement=entitlement,
        )
    if entitlement.tier not in KNOWN_TIERS:
        return EntitlementVerdict(
            status="invalid",
            reason=f"entitlement tier is unknown: {entitlement.tier!r}",
            entitlement=entitlement,
        )
    if entitlement.valid_until <= now:
        return EntitlementVerdict(
            status="expired",
            reason=f"entitlement expired on {entitlement.valid_until.isoformat()}",
            entitlement=entitlement,
        )
    if dsn is not None and _is_revoked(entitlement.entitlement_id, dsn):
        return EntitlementVerdict(
            status="revoked",
            reason="entitlement was revoked",
            entitlement=entitlement,
        )
    return EntitlementVerdict(
        status="valid", reason="entitlement is valid", entitlement=entitlement
    )


class HomeGate:
    """The "may this home run" gate. Constructed once per home with
    the pinned public key (and a DSN when revocation must be
    checked); ``decide`` is then the single startup check.
    """

    def __init__(self, public_key: Ed25519PublicKey, *, dsn=None):
        self.public_key = public_key
        self.dsn = resolve_dsn(dsn) if dsn is not None else None

    def decide(
        self, machine_id: str, token: str | None, *, now: datetime | None = None
    ) -> HomeDecision:
        """Decide whether this home may run.

        - No token at all → denied: only a signed admin entitlement
          and the liveness probe are answered, and this call is
          neither (the probe is the ``/health`` endpoint on the G11
          service allowlist, not this gate).
        - Valid entitlement for THIS machine-id → allowed.
        - Valid ``admin`` entitlement → allowed as admin.
        - Anything else → denied, with the exact reason (expired,
          tampered, wrong machine, revoked) so the operator can
          diagnose it instead of guessing.
        """
        if not isinstance(machine_id, str) or not machine_id.strip():
            return HomeDecision(allowed=False, reason="machine_id is empty", via="denied")
        if not token:
            return HomeDecision(
                allowed=False,
                reason=(
                    "no entitlement presented: this home cannot run without a "
                    "signed entitlement for its machine-id; only a signed admin "
                    "entitlement and the liveness probe are answered"
                ),
                via="denied",
            )
        verdict = verify_entitlement(token, self.public_key, now=now, dsn=self.dsn)
        if verdict.status != "valid" or verdict.entitlement is None:
            return HomeDecision(allowed=False, reason=verdict.reason, via="denied")
        entitlement = verdict.entitlement
        if entitlement.machine_id != machine_id.strip():
            return HomeDecision(
                allowed=False,
                reason=(
                    f"entitlement is for machine {entitlement.machine_id!r}, "
                    f"not this home ({machine_id.strip()!r})"
                ),
                via="denied",
            )
        if entitlement.tier == ADMIN_TIER:
            return HomeDecision(
                allowed=True,
                reason="signed admin entitlement for this machine",
                via="admin",
            )
        return HomeDecision(
            allowed=True,
            reason=(
                f"signed {entitlement.tier} entitlement for this machine, "
                f"valid until {entitlement.valid_until.isoformat()}"
            ),
            via="entitlement",
        )
