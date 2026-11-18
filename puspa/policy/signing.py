"""Local keyless signing and transparency log for Puspa skills.

Design follows sigstore (keyless signing with an ephemeral key, DSSE
envelope, append-only transparency log with Merkle inclusion proofs),
reimplemented here as Puspa's own code from the public specifications.
This module is NOT the public sigstore service: there is no Fulcio
certificate authority, no Rekor server, and no network call anywhere.
Identity is a Puspa user identity string (for example a user id or
email), bound into the signed payload; trust in that binding comes
from Puspa's own authentication, not from an OIDC certificate.

References (read 18 Nov 2026):
- https://docs.sigstore.dev/ (overview: signed, associated with an
  identity through Fulcio, witnessed in the Rekor transparency log;
  ephemeral keys, verification = signature + expected identity +
  proof of inclusion in Rekor)
- https://docs.sigstore.dev/logging/overview/ (Rekor: immutable,
  tamper-resistant ledger; auditors check append-only consistency,
  verifiers check inclusion proofs)
- https://github.com/sigstore/sigstore (Apache-2.0; common library:
  signing interface with DSSE support, Fulcio OIDC client)
- https://github.com/dsse-lf/dsse/blob/master/protocol.md (DSSE v1.0.2:
  SIGNATURE = Sign(PAE(payloadType, body)) with
  PAE = "DSSEv1" SP LEN(type) SP type SP LEN(body) SP body)

Signing flow (sigstore-shaped, local):
1. ``sign_skill`` creates a fresh ephemeral Ed25519 key pair, builds a
   payload ``{identity, skill_sha256, issued_at}`` over the skill
   bytes, and signs the DSSE pre-authentication encoding (PAE) of it.
   The private key is discarded immediately after signing; only the
   public key (as ``keyid``) travels in the envelope.
2. The envelope is appended to a ``TransparencyLog``. The log is
   append-only and hash-chained; there is no delete or update path.
3. ``verify_skill_envelope`` checks the signature against the PAE,
   checks the payload identity against the expected identity, and
   checks the envelope is recorded in the log via a Merkle inclusion
   proof against the log root.

Persistence choice: file-backed JSONL log (one entry per line), NOT a
Postgres table. Reasons:
- The trust anchor must stay verifiable without a database: tests and
  offline verification prove the restart round trip from the file
  alone, and a dead Postgres must never make skill trust
  unverifiable.
- The log is a small, single-writer, append-only record; a JSONL file
  with a hash chain plus fsync on append gives restart durability
  with no extra operational coupling.
- Honest trade-off: this file log has a single writer and no witness
  or cosigning. A shared Postgres table (or a real Rekor instance)
  would be the right move once several processes append concurrently
  or once entries must be publicly auditable; the entry format keeps
  ``envelope_sha256`` so a future Postgres mirror can deduplicate
  against this file.
"""

import base64
import hashlib
import json
import os
import time
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .tokens import canonical_json

__all__ = [
    "PAYLOAD_TYPE",
    "SigningError",
    "TransparencyLog",
    "decode_envelope_payload",
    "envelope_leaf_hash",
    "pae",
    "sign_skill",
    "verify_inclusion_proof",
    "verify_skill_envelope",
]

#: DSSE payload type for a signed Puspa skill. Application-specific,
#: never a generic ``application/json`` (DSSE protocol guidance).
PAYLOAD_TYPE = "application/vnd.puspa.skill+json"

#: Hash of the empty string: prev_hash of the genesis entry.
_GENESIS_PREV_HASH = "00" * 32


class SigningError(Exception):
    """Any signing, envelope, or transparency-log failure."""


def pae(payload_type: str, body: bytes) -> bytes:
    """DSSE pre-authentication encoding (protocol v1.0.2).

    ``DSSEv1 SP LEN(type) SP type SP LEN(body) SP body`` where LEN is
    the ASCII decimal byte length with no leading zeros. Verified
    against the spec test vector: type
    ``http://example.com/HelloWorld`` and body ``hello world`` give
    ``DSSEv1 29 http://example.com/HelloWorld 11 hello world``.
    """
    encoded_type = payload_type.encode("utf-8")
    return (
        b"DSSEv1 "
        + str(len(encoded_type)).encode("ascii")
        + b" "
        + encoded_type
        + b" "
        + str(len(body)).encode("ascii")
        + b" "
        + body
    )


def _b64_encode(data: bytes) -> str:
    """Standard base64 (DSSE envelope encoding)."""
    return base64.b64encode(data).decode("ascii")


def _b64_decode(data: str) -> bytes:
    """Base64 decode accepting either the standard or the URL-safe
    alphabet (DSSE verifiers MUST accept either)."""
    if not isinstance(data, str):
        raise SigningError("base64 value must be a string")
    normalized = data.replace("-", "+").replace("_", "/")
    pad = "=" * (-len(normalized) % 4)
    try:
        return base64.b64decode(normalized + pad)
    except Exception as exc:
        raise SigningError("value is not valid base64") from exc


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def sign_skill(skill_bytes: bytes, identity: str) -> dict:
    """Sign skill bytes with a fresh ephemeral Ed25519 key.

    Returns a DSSE envelope ``{payloadType, payload, signatures}``.
    The payload is canonical JSON
    ``{identity, skill_sha256, issued_at}`` where ``skill_sha256`` is
    the sha256 hex of ``skill_bytes``. The ephemeral private key is
    discarded after this call; the public key travels as ``keyid``
    (base64url of the 32 raw public-key bytes). The envelope alone
    proves nothing until it is recorded in a ``TransparencyLog``.
    """
    if not isinstance(skill_bytes, (bytes, bytearray)):
        raise SigningError("sign_skill needs skill bytes")
    if not isinstance(identity, str) or not identity:
        raise SigningError("sign_skill needs a non-empty identity string")
    signing_key = Ed25519PrivateKey.generate()
    public_key = signing_key.public_key()
    payload = {
        "identity": identity,
        "issued_at": int(time.time()),
        "skill_sha256": hashlib.sha256(bytes(skill_bytes)).hexdigest(),
    }
    body = canonical_json(payload)
    signature = signing_key.sign(pae(PAYLOAD_TYPE, body))
    return {
        "payload": _b64_encode(body),
        "payloadType": PAYLOAD_TYPE,
        "signatures": [
            {
                "keyid": _b64url_encode(public_key.public_bytes_raw()),
                "sig": _b64_encode(signature),
            }
        ],
    }


def decode_envelope_payload(envelope: dict) -> dict:
    """Decode and parse an envelope payload. Raises SigningError on
    any malformed envelope, wrong payload type, or bad payload JSON.
    The returned dict is the payload as signed; callers MUST use this
    exact decoded body for any further check, never a re-parsed copy.
    """
    if not isinstance(envelope, dict):
        raise SigningError("envelope must be a JSON object")
    if envelope.get("payloadType") != PAYLOAD_TYPE:
        raise SigningError(f"unsupported payloadType: {envelope.get('payloadType')!r}")
    payload_b64 = envelope.get("payload")
    if not isinstance(payload_b64, str):
        raise SigningError("envelope payload must be a base64 string")
    body = _b64_decode(payload_b64)
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise SigningError("envelope payload is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise SigningError("envelope payload must be a JSON object")
    for field in ("identity", "issued_at", "skill_sha256"):
        if field not in payload:
            raise SigningError(f"envelope payload misses field: {field}")
    return payload


def envelope_leaf_hash(envelope: dict) -> str:
    """Leaf hash of an envelope: sha256 hex of its canonical JSON."""
    return hashlib.sha256(canonical_json(envelope)).hexdigest()


def _merkle_root(leaf_hashes: list[bytes]) -> bytes | None:
    """Merkle root over raw 32-byte leaf hashes.

    Interior node = sha256(left + right); an odd last node is
    duplicated at its level (the standard textbook rule). Returns
    None for an empty log.
    """
    if not leaf_hashes:
        return None
    level = list(leaf_hashes)
    while len(level) > 1:
        next_level = []
        for i in range(0, len(level), 2):
            left = level[i]
            right = level[i + 1] if i + 1 < len(level) else left
            next_level.append(hashlib.sha256(left + right).digest())
        level = next_level
    return level[0]


def _merkle_proof_path(leaf_hashes: list[bytes], index: int) -> list[dict]:
    """Audit path for ``index``: list of ``{hash, side}`` where side
    tells where the sibling sits relative to the current node."""
    if not 0 <= index < len(leaf_hashes):
        raise SigningError("inclusion proof index out of range")
    path = []
    level = list(leaf_hashes)
    cursor = index
    while len(level) > 1:
        if cursor % 2 == 0:
            sibling_index = cursor + 1
            if sibling_index >= len(level):
                sibling_index = cursor  # odd last node: sibling is itself
            path.append({"hash": level[sibling_index].hex(), "side": "right"})
        else:
            path.append({"hash": level[cursor - 1].hex(), "side": "left"})
        next_level = []
        for i in range(0, len(level), 2):
            left = level[i]
            right = level[i + 1] if i + 1 < len(level) else left
            next_level.append(hashlib.sha256(left + right).digest())
        level = next_level
        cursor //= 2
    return path


def verify_inclusion_proof(leaf_hash: str, proof: dict, expected_root: str) -> bool:
    """Verify a Merkle inclusion proof against an expected root.

    ``proof`` is ``{leaf_index, leaf_hash, path, root}`` as produced by
    ``TransparencyLog.inclusion_proof``. Never raises for a bad proof:
    returns False. ``expected_root`` is the root the verifier trusts
    (for example the root of the log it just loaded).
    """
    try:
        if not isinstance(proof, dict):
            return False
        if proof.get("leaf_hash") != leaf_hash:
            return False
        if proof.get("root") != expected_root:
            return False
        current = bytes.fromhex(leaf_hash)
        for step in proof.get("path", []):
            sibling = bytes.fromhex(step["hash"])
            if step["side"] == "left":
                current = hashlib.sha256(sibling + current).digest()
            elif step["side"] == "right":
                current = hashlib.sha256(current + sibling).digest()
            else:
                return False
        return current.hex() == expected_root
    except (ValueError, TypeError, AttributeError, KeyError):
        return False


def verify_skill_envelope(envelope, expected_identity, transparency_log) -> tuple[bool, str]:
    """Verify a signed skill envelope. Returns ``(ok, reason)``.

    Checks, in order: envelope shape and payload type; signature
    valid over the DSSE PAE under the public key in ``keyid``;
    payload identity equals ``expected_identity``; envelope recorded
    in ``transparency_log`` with a Merkle inclusion proof that
    verifies against the log root. Any failure returns
    ``(False, reason)``; nothing here raises for a bad envelope.
    """
    try:
        payload = decode_envelope_payload(envelope)
    except SigningError as exc:
        return False, f"malformed envelope: {exc}"
    if not isinstance(expected_identity, str) or not expected_identity:
        return False, "expected identity missing"
    if payload.get("identity") != expected_identity:
        return False, "identity mismatch"
    signatures = envelope.get("signatures")
    if not isinstance(signatures, list) or not signatures:
        return False, "envelope has no signatures"
    try:
        body = _b64_decode(envelope["payload"])
    except SigningError as exc:
        return False, f"malformed envelope: {exc}"
    signed = pae(PAYLOAD_TYPE, body)
    verified = False
    for entry in signatures:
        try:
            key_bytes = _b64_decode(entry["keyid"])
            signature = _b64_decode(entry["sig"])
            public_key = Ed25519PublicKey.from_public_bytes(key_bytes)
            public_key.verify(signature, signed)
            verified = True
            break
        except (InvalidSignature, ValueError, TypeError, AttributeError, KeyError):
            continue
        except SigningError:
            continue
    if not verified:
        return False, "signature invalid"
    if transparency_log is None:
        return False, "no transparency log provided"
    try:
        return transparency_log.verify_inclusion(envelope)
    except Exception as exc:  # log corruption must fail closed
        return False, f"transparency log error: {exc}"


class TransparencyLog:
    """Append-only transparency log (local Rekor-shaped ledger).

    Each entry records one signing event: the full DSSE envelope, its
    leaf hash, the hash of the previous entry (hash chain), and its
    own entry hash. The Merkle root over all leaf hashes is the log's
    compact commitment; ``inclusion_proof`` proves one entry belongs
    to the log at that root.

    Persistence is a JSONL file (one canonical-JSON entry per line).
    Appends flush and fsync. Loading re-verifies the whole hash chain
    and raises ``SigningError`` on any tampering, so a damaged log
    fails closed instead of silently trusting entries.

    There is deliberately NO delete or update method: entries are
    never mutated or removed, matching the append-only guarantee
    Rekor auditors check. ``path=None`` keeps the log in memory only
    (useful for tests); an in-memory log proves nothing across a
    restart, so production use MUST pass a path.
    """

    def __init__(self, path=None):
        self._path = Path(path) if path is not None else None
        self._entries: list[dict] = []
        if self._path is not None and self._path.exists():
            self._load()

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def path(self):
        """File backing this log, or None for an in-memory log."""
        return self._path

    def _leaf_hashes(self) -> list[bytes]:
        return [bytes.fromhex(e["leaf_hash"]) for e in self._entries]

    def root(self) -> str | None:
        """Merkle root hex over all entries, or None for an empty log."""
        digest = _merkle_root(self._leaf_hashes())
        return digest.hex() if digest is not None else None

    def entry(self, index: int) -> dict:
        """A copy of the entry at ``index``. Raises IndexError."""
        return dict(self._entries[index])

    def _load(self) -> None:
        """Load and verify the whole file. Raises SigningError on any
        corruption (bad JSON, broken hash chain, bad leaf hash)."""
        entries = []
        with open(self._path, encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise SigningError(f"log file corrupt at line {line_no}: not JSON") from exc
                if not isinstance(entry, dict):
                    raise SigningError(f"log file corrupt at line {line_no}")
                entries.append(entry)
        self._entries = entries
        ok, reason = self.verify_chain()
        if not ok:
            self._entries = []
            raise SigningError(f"log file failed verification: {reason}")

    def _entry_hash(self, entry: dict) -> str:
        """sha256 hex of the canonical entry JSON without entry_hash."""
        body = {k: v for k, v in entry.items() if k != "entry_hash"}
        return hashlib.sha256(canonical_json(body)).hexdigest()

    def verify_chain(self) -> tuple[bool, str]:
        """Verify the full hash chain and every leaf hash. Returns
        ``(ok, reason)``; never raises for a bad log."""
        prev_hash = _GENESIS_PREV_HASH
        for index, entry in enumerate(self._entries):
            if entry.get("index") != index:
                return False, f"entry {index} has wrong index"
            if entry.get("prev_hash") != prev_hash:
                return False, f"entry {index} breaks the hash chain"
            try:
                leaf = envelope_leaf_hash(entry["envelope"])
            except (SigningError, KeyError, TypeError) as exc:
                return False, f"entry {index} has a bad envelope: {exc}"
            if entry.get("leaf_hash") != leaf:
                return False, f"entry {index} has a wrong leaf hash"
            if entry.get("entry_hash") != self._entry_hash(entry):
                return False, f"entry {index} has a wrong entry hash"
            prev_hash = entry["entry_hash"]
        return True, "ok"

    def append(self, envelope: dict) -> dict:
        """Record one signing event. Returns a copy of the new entry.

        The entry stores the full envelope (so auditors can re-derive
        the leaf hash), the payload identity and skill hash, the
        envelope sha256, the leaf hash, the previous entry hash, and
        its own entry hash. Raises SigningError for a malformed
        envelope. File-backed logs flush and fsync on every append.
        """
        payload = decode_envelope_payload(envelope)
        leaf_hash = envelope_leaf_hash(envelope)
        prev_hash = self._entries[-1]["entry_hash"] if self._entries else _GENESIS_PREV_HASH
        entry = {
            "envelope": envelope,
            "envelope_sha256": hashlib.sha256(canonical_json(envelope)).hexdigest(),
            "identity": payload["identity"],
            "index": len(self._entries),
            "issued_at": payload["issued_at"],
            "leaf_hash": leaf_hash,
            "prev_hash": prev_hash,
            "skill_sha256": payload["skill_sha256"],
        }
        entry["entry_hash"] = self._entry_hash(entry)
        self._entries.append(entry)
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(canonical_json(entry).decode("utf-8") + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        return dict(entry)

    def find_index(self, envelope: dict) -> int | None:
        """Index of the entry holding ``envelope``, or None."""
        try:
            leaf = envelope_leaf_hash(envelope)
        except (SigningError, TypeError):
            return None
        for entry in self._entries:
            if entry["leaf_hash"] == leaf:
                return entry["index"]
        return None

    def inclusion_proof(self, index: int) -> dict:
        """Merkle inclusion proof for the entry at ``index``.

        Returns ``{leaf_index, leaf_hash, path, root}`` where path is
        the sibling-hash audit path. Raises SigningError for an empty
        log or an out-of-range index.
        """
        if not self._entries:
            raise SigningError("cannot prove inclusion in an empty log")
        if not 0 <= index < len(self._entries):
            raise SigningError("inclusion proof index out of range")
        return {
            "leaf_hash": self._entries[index]["leaf_hash"],
            "leaf_index": index,
            "path": _merkle_proof_path(self._leaf_hashes(), index),
            "root": self.root(),
        }

    def verify_inclusion(self, envelope: dict) -> tuple[bool, str]:
        """Check ``envelope`` is recorded here with a valid Merkle
        inclusion proof against the current root. Returns
        ``(ok, reason)``; never raises for an unrecorded envelope."""
        index = self.find_index(envelope)
        if index is None:
            return False, "envelope not recorded in transparency log"
        proof = self.inclusion_proof(index)
        root = self.root()
        if root is None:
            return False, "transparency log is empty"
        if verify_inclusion_proof(proof["leaf_hash"], proof, root):
            return True, "ok"
        return False, "inclusion proof failed"
