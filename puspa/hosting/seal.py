"""Card seal: a per-start sealing key, signed by the identity key.

Pattern (DuskBot ``server/card-seal.ts`` HPKE card seal, rewritten as
Puspa code): the server generates a fresh X25519 sealing keypair on
every start. The private half lives only in process memory and is
NEVER written to disk, so a stolen disk or backup yields no sealing
key. Each seal is an HPKE encryption to that sealing public key, and
the seal is signed by the server's long-term identity key
(``identity.py``), which the app pinned at linking. A relay that
tried to swap in its own sealing key would fail the signature check
against the pinned identity key, so the relay can only forward
opaque sealed blobs, never read or re-seal them.

Cryptography, verified against the pinned dependency
(``cryptography==50.0.2`` in ``requirements.txt``):

- HPKE suite: ``DHKEM(X25519)`` + ``HKDF-SHA256`` + ``AES-256-GCM``,
  base mode. No deviation from the DuskBot pattern was needed: HPKE
  IS available in this version.
- API shape of this ``cryptography`` version, recorded because it
  differs from the RFC-shaped pseudocode: ``Suite.encrypt`` takes
  ``(plaintext, public_key, info=...)`` and returns ONE combined
  bytes blob (encapsulated key concatenated with the ciphertext);
  ``Suite.decrypt`` takes that same combined blob plus the private
  key. There is no separate AAD parameter in base mode; the context
  binding lives in the ``info`` string (``SEAL_INFO``). Keypairs are
  generated directly with ``X25519PrivateKey.generate()`` — the
  ``Suite`` object in this version exposes no ``kem`` attribute.
- The signature covers ``sealing_public_key || ciphertext`` (the
  exact sealed bytes), so neither half can be swapped independently.
- Plaintext is capped at 64 KiB: a seal is a small card (a link
  payload, a token), not a bulk channel; bulk data goes through the
  relay frames in ``relay.py``.

A ``SealedCard`` serialises to plain JSON (base64 fields) so it can
travel over any transport, including the relay, without the relay
needing to understand it.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.hpke import AEAD, KDF, KEM, Suite

from .errors import SealError
from .identity import IdentityKey

__all__ = [
    "MAX_SEAL_PLAINTEXT_BYTES",
    "SEAL_INFO",
    "SEAL_VERSION",
    "SealedCard",
    "SealingKey",
    "seal_card",
    "unseal_card",
]

#: HPKE ``info`` string binding every seal to this protocol version.
SEAL_INFO = b"puspa-card-seal/v1"

SEAL_VERSION = 1

MAX_SEAL_PLAINTEXT_BYTES = 65536  # 64 KiB

_HPKE_SUITE = Suite(KEM.X25519, KDF.HKDF_SHA256, AEAD.AES_256_GCM)


def _b64encode(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _b64decode(value: str) -> bytes:
    return base64.b64decode(value.encode(), validate=True)


class SealingKey:
    """A per-start X25519 sealing keypair. Memory-only by construction.

    Instances hold no path and offer no serialisation of the private
    half: there is deliberately no way to write this key to disk.
    ``__repr__`` never leaks key material.
    """

    def __init__(self, private_key: X25519PrivateKey | None = None) -> None:
        self._private_key = private_key or X25519PrivateKey.generate()

    @classmethod
    def generate(cls) -> SealingKey:
        """Generate a fresh sealing key (call once per server start)."""
        return cls(X25519PrivateKey.generate())

    def rotate(self) -> SealingKey:
        """Return a brand-new sealing key; the old one stays usable for
        unsealing cards sealed under it until the caller drops it."""
        return SealingKey.generate()

    def public_bytes(self) -> bytes:
        """Raw 32-byte sealing public key (published in each seal)."""
        return self._private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )

    def fingerprint(self) -> str:
        """SHA-256 hex of the raw sealing public key."""
        return hashlib.sha256(self.public_bytes()).hexdigest()

    def __repr__(self) -> str:  # never leak key material
        return f"SealingKey(fingerprint={self.fingerprint()[:16]}...)"


@dataclass(frozen=True)
class SealedCard:
    """One sealed card: HPKE ciphertext + identity signature.

    ``sealing_public_key`` is the per-start public key the ciphertext
    was sealed to; ``signature`` is the identity key's Ed25519
    signature over ``sealing_public_key || ciphertext``;
    ``identity_fingerprint`` names the identity key that must have
    signed it (the value the app pinned at linking).
    """

    version: int
    sealing_public_key: bytes
    ciphertext: bytes
    signature: bytes
    identity_fingerprint: str

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "sealing_public_key": _b64encode(self.sealing_public_key),
            "ciphertext": _b64encode(self.ciphertext),
            "signature": _b64encode(self.signature),
            "identity_fingerprint": self.identity_fingerprint,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SealedCard:
        try:
            version = int(data["version"])
            sealing_public_key = _b64decode(data["sealing_public_key"])
            ciphertext = _b64decode(data["ciphertext"])
            signature = _b64decode(data["signature"])
            fingerprint = str(data["identity_fingerprint"])
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            raise SealError("sealed card is malformed") from exc
        if version != SEAL_VERSION:
            raise SealError(f"unsupported seal version: {version}")
        if len(sealing_public_key) != 32:
            raise SealError("sealing public key must be 32 bytes")
        if not ciphertext or not signature or not fingerprint:
            raise SealError("sealed card is missing a field")
        return cls(
            version=version,
            sealing_public_key=sealing_public_key,
            ciphertext=ciphertext,
            signature=signature,
            identity_fingerprint=fingerprint,
        )


def seal_card(
    plaintext: bytes, *, sealing_key: SealingKey, identity_key: IdentityKey
) -> SealedCard:
    """Seal plaintext to the per-start sealing key; sign with identity.

    Raises ``SealError`` when the plaintext exceeds the size cap.
    The returned card carries everything the app needs to verify it
    against the pinned identity key — no server round trip.
    """
    if not isinstance(plaintext, (bytes, bytearray)):
        raise SealError("seal plaintext must be bytes")
    if len(plaintext) > MAX_SEAL_PLAINTEXT_BYTES:
        raise SealError(
            f"seal plaintext exceeds {MAX_SEAL_PLAINTEXT_BYTES} bytes "
            f"({len(plaintext)} given); bulk data uses relay frames"
        )
    public_key = X25519PublicKey.from_public_bytes(sealing_key.public_bytes())
    ciphertext = _HPKE_SUITE.encrypt(bytes(plaintext), public_key, info=SEAL_INFO)
    signature = identity_key.sign(sealing_key.public_bytes() + ciphertext)
    return SealedCard(
        version=SEAL_VERSION,
        sealing_public_key=sealing_key.public_bytes(),
        ciphertext=ciphertext,
        signature=signature,
        identity_fingerprint=identity_key.fingerprint(),
    )


def unseal_card(
    card: SealedCard,
    *,
    sealing_key: SealingKey,
    identity_public_key,
    pinned_fingerprint: str,
) -> bytes:
    """Verify a seal against the PINNED identity key, then open it.

    ``identity_public_key`` is the Ed25519 public key the app pinned
    at linking (an ``Ed25519PublicKey``); ``pinned_fingerprint`` is
    its SHA-256 fingerprint. The check order is deliberate:

    1. The card's claimed identity fingerprint must equal the pinned
       fingerprint — a seal from any other server is refused before
       any crypto runs.
    2. The identity signature over the sealing key + ciphertext must
       verify — a relay cannot substitute its own sealing key.
    3. The sealing public key inside the card must be THIS server's
       current sealing key — a card sealed for another start (or
       another server) is refused.
    4. Only then is the HPKE ciphertext opened.

    Raises ``SealError`` on any failure; never returns partial
    plaintext and never leaks which check failed beyond the message.
    """
    from cryptography.exceptions import InvalidSignature

    if card.identity_fingerprint != pinned_fingerprint:
        raise SealError("seal identity fingerprint does not match the pinned identity key")
    try:
        identity_public_key.verify(card.signature, card.sealing_public_key + card.ciphertext)
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise SealError("seal identity signature does not verify") from exc
    if card.sealing_public_key != sealing_key.public_bytes():
        raise SealError("seal was not made for this server's current sealing key")
    try:
        return _HPKE_SUITE.decrypt(card.ciphertext, sealing_key._private_key, info=SEAL_INFO)
    except Exception as exc:  # InvalidTag and friends: tampered ciphertext
        raise SealError("seal ciphertext does not open") from exc
