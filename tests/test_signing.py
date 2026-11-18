"""Tests for local keyless signing + transparency log (sigstore-shaped).

Covers: DSSE PAE test vector, sign/verify round trip, tampered
payload rejected, wrong identity rejected, envelope missing from the
log rejected, Merkle inclusion proofs (several log sizes, tampered
proof, wrong root), append-only guarantees (no delete/update API,
tampered file fails to load, broken chain detected), restart round
trip from the file-backed log, and verify_skill provenance wiring
(backward compatible without a log, enforced with one).
"""

import base64
import hashlib
import json

import pytest

from puspa.policy.signing import (
    PAYLOAD_TYPE,
    SigningError,
    TransparencyLog,
    decode_envelope_payload,
    envelope_leaf_hash,
    pae,
    sign_skill,
    verify_inclusion_proof,
    verify_skill_envelope,
)
from puspa.policy.skill_signing import (
    load_skill,
    sign_skill_provenance,
    verify_skill,
)
from puspa.policy.skill_signing import (
    sign_skill as sign_manifest,
)

IDENTITY = "user-123@example.id"


def _skill_bytes(name="example"):
    return json.dumps({"name": name, "tools": ["read_price"]}, sort_keys=True).encode("utf-8")


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _tamper_payload(envelope, **changes):
    """Return a copy of the envelope with payload fields changed
    (signature left as-is, so verification must fail)."""
    payload = decode_envelope_payload(envelope)
    payload.update(changes)
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    copy = dict(envelope)
    copy["payload"] = _b64(body)
    return copy


# --- DSSE pre-authentication encoding --------------------------------------


def test_pae_matches_spec_test_vector():
    assert pae("http://example.com/HelloWorld", b"hello world") == (
        b"DSSEv1 29 http://example.com/HelloWorld 11 hello world"
    )


# --- Round trip sign / verify ----------------------------------------------


def test_sign_verify_round_trip():
    log = TransparencyLog()
    envelope = sign_skill(_skill_bytes(), IDENTITY)
    assert envelope["payloadType"] == PAYLOAD_TYPE
    assert len(envelope["signatures"]) == 1
    entry = log.append(envelope)
    assert entry["index"] == 0
    assert entry["identity"] == IDENTITY
    ok, reason = verify_skill_envelope(envelope, IDENTITY, log)
    assert ok is True
    assert reason == "ok"


def test_sign_skill_rejects_bad_inputs():
    with pytest.raises(SigningError):
        sign_skill("not-bytes", IDENTITY)
    with pytest.raises(SigningError):
        sign_skill(_skill_bytes(), "")
    with pytest.raises(SigningError):
        sign_skill(_skill_bytes(), None)


def test_decode_envelope_payload_rejects_wrong_type():
    envelope = sign_skill(_skill_bytes(), IDENTITY)
    envelope["payloadType"] = "application/json"
    with pytest.raises(SigningError):
        decode_envelope_payload(envelope)


# --- Rejections -------------------------------------------------------------


def test_tampered_payload_rejected():
    log = TransparencyLog()
    envelope = sign_skill(_skill_bytes(), IDENTITY)
    log.append(envelope)
    tampered = _tamper_payload(envelope, skill_sha256="0" * 64)
    ok, reason = verify_skill_envelope(tampered, IDENTITY, log)
    assert ok is False
    assert "signature invalid" in reason


def test_tampered_signature_rejected():
    log = TransparencyLog()
    envelope = sign_skill(_skill_bytes(), IDENTITY)
    log.append(envelope)
    tampered = json.loads(json.dumps(envelope))
    sig = tampered["signatures"][0]["sig"]
    tampered["signatures"][0]["sig"] = (
        _b64(b"\x00" * 64) if sig != _b64(b"\x00" * 64) else _b64(b"\x01" * 64)
    )
    ok, reason = verify_skill_envelope(tampered, IDENTITY, log)
    assert ok is False
    assert "signature invalid" in reason


def test_wrong_identity_rejected():
    log = TransparencyLog()
    envelope = sign_skill(_skill_bytes(), IDENTITY)
    log.append(envelope)
    ok, reason = verify_skill_envelope(envelope, "someone-else@example.id", log)
    assert ok is False
    assert "identity mismatch" in reason


def test_envelope_not_in_log_rejected():
    log = TransparencyLog()
    envelope = sign_skill(_skill_bytes(), IDENTITY)
    ok, reason = verify_skill_envelope(envelope, IDENTITY, log)
    assert ok is False
    assert "not recorded" in reason


def test_verify_without_log_rejected():
    envelope = sign_skill(_skill_bytes(), IDENTITY)
    ok, reason = verify_skill_envelope(envelope, IDENTITY, None)
    assert ok is False
    assert "no transparency log" in reason


def test_malformed_envelope_never_raises():
    log = TransparencyLog()
    for bad in (
        None,
        {},
        {"payloadType": PAYLOAD_TYPE},
        "junk",
        {"payloadType": PAYLOAD_TYPE, "payload": "!!!", "signatures": []},
    ):
        ok, reason = verify_skill_envelope(bad, IDENTITY, log)
        assert ok is False
        assert reason


# --- Merkle inclusion proofs -----------------------------------------------


@pytest.mark.parametrize("size", [1, 2, 3, 5, 8])
def test_inclusion_proof_verifies_for_many_sizes(size):
    log = TransparencyLog()
    envelopes = [sign_skill(_skill_bytes(f"skill-{i}"), IDENTITY) for i in range(size)]
    for envelope in envelopes:
        log.append(envelope)
    root = log.root()
    assert root is not None
    for i, envelope in enumerate(envelopes):
        proof = log.inclusion_proof(i)
        assert proof["leaf_index"] == i
        assert proof["root"] == root
        assert verify_inclusion_proof(envelope_leaf_hash(envelope), proof, root) is True
        ok, _ = log.verify_inclusion(envelope)
        assert ok is True


def test_inclusion_proof_tampered_sibling_fails():
    log = TransparencyLog()
    envelopes = [sign_skill(_skill_bytes(f"skill-{i}"), IDENTITY) for i in range(4)]
    for envelope in envelopes:
        log.append(envelope)
    proof = log.inclusion_proof(1)
    proof["path"][0]["hash"] = hashlib.sha256(b"forged").hexdigest()
    assert verify_inclusion_proof(envelope_leaf_hash(envelopes[1]), proof, log.root()) is False


def test_inclusion_proof_wrong_root_fails():
    log = TransparencyLog()
    envelope = sign_skill(_skill_bytes(), IDENTITY)
    log.append(envelope)
    proof = log.inclusion_proof(0)
    assert (
        verify_inclusion_proof(
            envelope_leaf_hash(envelope), proof, hashlib.sha256(b"other").hexdigest()
        )
        is False
    )


def test_inclusion_proof_empty_log_raises():
    with pytest.raises(SigningError):
        TransparencyLog().inclusion_proof(0)


# --- Append-only guarantees ------------------------------------------------


def test_log_has_no_delete_or_update_api():
    log = TransparencyLog()
    for name in ("delete", "remove", "update", "mutate", "rewrite", "pop", "clear"):
        assert not hasattr(log, name), f"log must not expose {name}"


def test_hash_chain_verifies_and_detects_tampering():
    log = TransparencyLog()
    for i in range(3):
        log.append(sign_skill(_skill_bytes(f"skill-{i}"), IDENTITY))
    ok, reason = log.verify_chain()
    assert ok is True
    assert reason == "ok"
    log._entries[1]["identity"] = "attacker@example.id"
    ok, reason = log.verify_chain()
    assert ok is False
    assert "entry hash" in reason or "leaf hash" in reason


def test_tampered_file_fails_to_load(tmp_path):
    path = tmp_path / "log.jsonl"
    log = TransparencyLog(path)
    log.append(sign_skill(_skill_bytes("a"), IDENTITY))
    log.append(sign_skill(_skill_bytes("b"), IDENTITY))
    lines = path.read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[1])
    entry["identity"] = "attacker@example.id"
    lines[1] = json.dumps(entry, sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(SigningError):
        TransparencyLog(path)


def test_roots_differ_after_append():
    log = TransparencyLog()
    log.append(sign_skill(_skill_bytes("a"), IDENTITY))
    root_one = log.root()
    log.append(sign_skill(_skill_bytes("b"), IDENTITY))
    assert log.root() != root_one
    assert len(log) == 2


# --- Restart round trip (file-backed persistence) --------------------------


def test_restart_round_trip(tmp_path):
    path = tmp_path / "transparency.jsonl"
    log = TransparencyLog(path)
    envelopes = [sign_skill(_skill_bytes(f"skill-{i}"), IDENTITY) for i in range(3)]
    for envelope in envelopes:
        log.append(envelope)
    root_before = log.root()
    assert path.exists()

    reloaded = TransparencyLog(path)  # fresh process shape: new object
    assert len(reloaded) == 3
    assert reloaded.root() == root_before
    ok, _ = reloaded.verify_chain()
    assert ok is True
    for envelope in envelopes:
        ok, reason = verify_skill_envelope(envelope, IDENTITY, reloaded)
        assert ok is True, reason
        ok, _ = reloaded.verify_inclusion(envelope)
        assert ok is True

    # Appends after restart continue the chain (index 3, same root path).
    extra = sign_skill(_skill_bytes("skill-3"), IDENTITY)
    reloaded.append(extra)
    assert len(reloaded) == 4
    assert reloaded.entry(3)["prev_hash"] == reloaded.entry(2)["entry_hash"]
    assert TransparencyLog(path).root() == reloaded.root()


def test_in_memory_log_does_not_persist(tmp_path):
    log = TransparencyLog()
    log.append(sign_skill(_skill_bytes(), IDENTITY))
    assert log.path is None
    assert len(TransparencyLog()) == 0


# --- verify_skill provenance wiring ----------------------------------------


def test_verify_skill_backward_compatible_without_log(ephemeral_keys, tmp_skill_dir):
    sk, pk = ephemeral_keys
    assert verify_skill(tmp_skill_dir, pk) is False
    sign_manifest(tmp_skill_dir, sk)
    assert verify_skill(tmp_skill_dir, pk) is True
    assert load_skill(tmp_skill_dir, pk)["name"] == "example"


def test_verify_skill_with_log_requires_envelope(ephemeral_keys, tmp_skill_dir):
    sk, pk = ephemeral_keys
    sign_manifest(tmp_skill_dir, sk)
    log = TransparencyLog()
    # Valid manifest signature, but no envelope and nothing in the log.
    assert verify_skill(tmp_skill_dir, pk, log, IDENTITY) is False
    envelope = sign_skill_provenance(tmp_skill_dir, IDENTITY, log)
    assert (tmp_skill_dir / "envelope.json").is_file()
    assert verify_skill(tmp_skill_dir, pk, log, IDENTITY) is True
    assert envelope["payloadType"] == PAYLOAD_TYPE


def test_verify_skill_with_log_wrong_identity(ephemeral_keys, tmp_skill_dir):
    sk, pk = ephemeral_keys
    sign_manifest(tmp_skill_dir, sk)
    log = TransparencyLog()
    sign_skill_provenance(tmp_skill_dir, IDENTITY, log)
    assert verify_skill(tmp_skill_dir, pk, log, "someone-else@example.id") is False
    assert verify_skill(tmp_skill_dir, pk, log, None) is False


def test_verify_skill_tampered_manifest_breaks_provenance(ephemeral_keys, tmp_skill_dir):
    sk, pk = ephemeral_keys
    sign_manifest(tmp_skill_dir, sk)
    log = TransparencyLog()
    sign_skill_provenance(tmp_skill_dir, IDENTITY, log)
    assert verify_skill(tmp_skill_dir, pk, log, IDENTITY) is True
    manifest_path = tmp_skill_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["tools"].append("delete_all")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    assert verify_skill(tmp_skill_dir, pk, log, IDENTITY) is False


def test_verify_skill_envelope_replay_to_other_skill_rejected(
    ephemeral_keys, tmp_skill_dir, tmp_path
):
    sk, pk = ephemeral_keys
    sign_manifest(tmp_skill_dir, sk)
    log = TransparencyLog()
    envelope = sign_skill_provenance(tmp_skill_dir, IDENTITY, log)

    other_dir = tmp_path / "skill-other"
    other_dir.mkdir()
    (other_dir / "SKILL.md").write_text("# Other\n", encoding="utf-8")
    (other_dir / "manifest.json").write_text(
        json.dumps(
            {
                "name": "other",
                "version": "0.1.0",
                "tools": ["read_price"],
                "domains": [],
                "connectors": {},
                "author": "puspa",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    sign_manifest(other_dir, sk)
    # Replay skill A's envelope into skill B's directory: the envelope
    # is recorded in the log, but it binds A's manifest bytes, not B's.
    (other_dir / "envelope.json").write_text(json.dumps(envelope, indent=2), encoding="utf-8")
    assert verify_skill(other_dir, pk, log, IDENTITY) is False
