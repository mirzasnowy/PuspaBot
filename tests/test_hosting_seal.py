"""Tests: identity key file + HPKE card seal.

All in-memory except the identity key file (tmp_path); no database,
no network. HPKE runs against the pinned ``cryptography==50.0.2``.
"""

import os

import pytest

from puspa.hosting.errors import IdentityKeyError, SealError
from puspa.hosting.identity import load_or_create_identity_key
from puspa.hosting.seal import (
    MAX_SEAL_PLAINTEXT_BYTES,
    SealedCard,
    SealingKey,
    seal_card,
    unseal_card,
)


def _identity(tmp_path):
    return load_or_create_identity_key(tmp_path / "identity.key")


def _sealed_pair(tmp_path):
    identity = _identity(tmp_path)
    sealing = SealingKey.generate()
    card = seal_card(b"link payload", sealing_key=sealing, identity_key=identity)
    return identity, sealing, card


# --- identity key ---


def test_identity_key_created_mode_0600_and_reused(tmp_path):
    path = tmp_path / "identity.key"
    first = load_or_create_identity_key(path)
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    second = load_or_create_identity_key(path)
    assert second.fingerprint() == first.fingerprint()
    assert second.public_bytes() == first.public_bytes()


def test_identity_key_sign_and_verify(tmp_path):
    identity = _identity(tmp_path)
    signature = identity.sign(b"sealing-key-bytes")
    assert identity.verify(b"sealing-key-bytes", signature) is True
    assert identity.verify(b"other-bytes", signature) is False
    assert identity.verify(b"sealing-key-bytes", b"\x00" * 64) is False


def test_identity_key_file_too_open_is_refused(tmp_path):
    path = tmp_path / "identity.key"
    load_or_create_identity_key(path)
    os.chmod(path, 0o644)
    with pytest.raises(IdentityKeyError):
        load_or_create_identity_key(path)


def test_identity_key_file_garbage_is_refused(tmp_path):
    path = tmp_path / "identity.key"
    path.write_bytes(b"not a key")
    os.chmod(path, 0o600)
    with pytest.raises(IdentityKeyError):
        load_or_create_identity_key(path)


# --- seal round trip ---


def test_seal_and_unseal_roundtrip(tmp_path):
    identity, sealing, card = _sealed_pair(tmp_path)
    plaintext = unseal_card(
        card,
        sealing_key=sealing,
        identity_public_key=identity.public_key,
        pinned_fingerprint=identity.fingerprint(),
    )
    assert plaintext == b"link payload"


def test_seal_carries_the_identity_fingerprint(tmp_path):
    identity, _, card = _sealed_pair(tmp_path)
    assert card.identity_fingerprint == identity.fingerprint()
    assert card.version == 1
    assert len(card.sealing_public_key) == 32


def test_unseal_rejects_wrong_pinned_fingerprint(tmp_path):
    _, sealing, card = _sealed_pair(tmp_path)
    other = load_or_create_identity_key(tmp_path / "other.key")
    with pytest.raises(SealError):
        unseal_card(
            card,
            sealing_key=sealing,
            identity_public_key=other.public_key,
            pinned_fingerprint=other.fingerprint(),
        )


def test_unseal_rejects_signature_from_another_identity(tmp_path):
    identity, sealing, card = _sealed_pair(tmp_path)
    other = load_or_create_identity_key(tmp_path / "other.key")
    # Card claims identity's fingerprint but is checked against
    # another key's public half: the signature must fail.
    with pytest.raises(SealError):
        unseal_card(
            card,
            sealing_key=sealing,
            identity_public_key=other.public_key,
            pinned_fingerprint=identity.fingerprint(),
        )


def test_unseal_rejects_tampered_ciphertext(tmp_path):
    identity, sealing, card = _sealed_pair(tmp_path)
    tampered = SealedCard(
        version=card.version,
        sealing_public_key=card.sealing_public_key,
        ciphertext=bytes([card.ciphertext[0] ^ 1]) + card.ciphertext[1:],
        signature=card.signature,
        identity_fingerprint=card.identity_fingerprint,
    )
    # The signature covers the original ciphertext, so tampering is
    # caught at the signature check, before decryption is attempted.
    with pytest.raises(SealError):
        unseal_card(
            tampered,
            sealing_key=sealing,
            identity_public_key=identity.public_key,
            pinned_fingerprint=identity.fingerprint(),
        )


def test_unseal_rejects_swapped_sealing_key(tmp_path):
    identity, _, card = _sealed_pair(tmp_path)
    attacker_sealing = SealingKey.generate()
    # A relay that swaps in its own sealing key cannot forge the
    # identity signature; the card still names the real sealing key,
    # so opening under the attacker's key is refused.
    with pytest.raises(SealError):
        unseal_card(
            card,
            sealing_key=attacker_sealing,
            identity_public_key=identity.public_key,
            pinned_fingerprint=identity.fingerprint(),
        )


def test_unseal_rejects_card_from_a_previous_start(tmp_path):
    identity, sealing, card = _sealed_pair(tmp_path)
    next_start = sealing.rotate()
    assert next_start.fingerprint() != sealing.fingerprint()
    with pytest.raises(SealError):
        unseal_card(
            card,
            sealing_key=next_start,
            identity_public_key=identity.public_key,
            pinned_fingerprint=identity.fingerprint(),
        )


def test_sealing_key_rotation_gives_a_new_key():
    first = SealingKey.generate()
    second = first.rotate()
    assert second.public_bytes() != first.public_bytes()
    assert "fingerprint=" in repr(first)
    # The repr names the key by fingerprint only; raw key bytes
    # (public or private) never appear in it.
    assert first.public_bytes().hex() not in repr(first)


def test_sealed_card_json_roundtrip(tmp_path):
    _, _, card = _sealed_pair(tmp_path)
    restored = SealedCard.from_dict(card.to_dict())
    assert restored == card


def test_sealed_card_rejects_bad_version_and_malformed_dicts():
    with pytest.raises(SealError):
        SealedCard.from_dict({"version": 99})
    with pytest.raises(SealError):
        SealedCard.from_dict({})
    with pytest.raises(SealError):
        SealedCard.from_dict(
            {
                "version": 1,
                "sealing_public_key": "!!!not-base64!!!",
                "ciphertext": "AAAA",
                "signature": "AAAA",
                "identity_fingerprint": "abc",
            }
        )


def test_seal_rejects_oversized_plaintext(tmp_path):
    identity = _identity(tmp_path)
    sealing = SealingKey.generate()
    with pytest.raises(SealError):
        seal_card(
            b"x" * (MAX_SEAL_PLAINTEXT_BYTES + 1),
            sealing_key=sealing,
            identity_key=identity,
        )
    # Exactly at the cap still seals.
    card = seal_card(b"x" * MAX_SEAL_PLAINTEXT_BYTES, sealing_key=sealing, identity_key=identity)
    assert (
        unseal_card(
            card,
            sealing_key=sealing,
            identity_public_key=identity.public_key,
            pinned_fingerprint=identity.fingerprint(),
        )
        == b"x" * MAX_SEAL_PLAINTEXT_BYTES
    )
