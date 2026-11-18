"""Tests for wiring the real Sigstore verifier into ``verify_skill``.

Covers: a skill whose manifest declares a Sigstore bundle verifies
when bundle + identity + issuer match (real staging fixture); a
tampered bundle, a tampered artifact, a wrong identity, a wrong
issuer, and a staging bundle under the production trust root are all
rejected; a skill with a failing bundle is rejected even when its
local Ed25519 signature is fine; a skill with no ``sigstore`` field
behaves exactly as before (local mechanism unchanged); the bundle
check adds to the provenance check instead of replacing it; a missing
bundle file, a malformed declaration, and a missing ``sigstore``
library all fail closed.

Fixture reuse. The bundle and artifact come from
``tests/test_sigstore_verify.py`` (the Sigstore project's public test
assets: a real staging bundle for ``william@yossarian.net`` via
``https://github.com/login/oauth``), imported — not copied — so the
blob lives in exactly one place. The import is function-local so
pytest does not collect that module's tests through this one.
"""

import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import puspa.policy.sigstore_verify as sigstore_verify
from puspa.policy.signing import TransparencyLog
from puspa.policy.skill_signing import (
    load_skill,
    sign_skill as sign_manifest,
)
from puspa.policy.skill_signing import (
    sign_skill_provenance,
    verify_skill,
)

IDENTITY = "william@yossarian.net"
ISSUER = "https://github.com/login/oauth"
PROVENANCE_IDENTITY = "user-123@example.id"


def _fixture_artifact_and_bundle():
    """(artifact bytes, bundle bytes) from test_sigstore_verify."""
    import test_sigstore_verify as fixture

    return (
        base64.b64decode(fixture._ARTIFACT_V3_B64),
        base64.b64decode(fixture._BUNDLE_V3_B64),
    )


def _keys():
    signing_key = Ed25519PrivateKey.generate()
    return signing_key, signing_key.public_key()


_SKILL_COUNTER = [0]


def _make_skill(tmp_path, name=None):
    """Unsigned skill directory: SKILL.md + manifest.json."""
    _SKILL_COUNTER[0] += 1
    if name is None:
        name = f"skill-sigstore-{_SKILL_COUNTER[0]}"
    skill_dir = tmp_path / name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Example Skill\n", encoding="utf-8")
    manifest = {
        "name": "example",
        "version": "0.1.0",
        "tools": ["read_price"],
        "domains": [],
        "connectors": {},
        "author": "community",
    }
    (skill_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return skill_dir


def _declare_sigstore(skill_dir, *, identity=IDENTITY, issuer=ISSUER,
                      trust_root="staging", bundle="bundle.sigstore",
                      artifact="artifact.txt", omit=()):
    """Add the ``sigstore`` declaration to the manifest (unsigned).

    The caller signs the manifest afterwards, so the local signature
    covers the declaration. ``omit`` drops named declaration fields.
    """
    path = skill_dir / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    declaration = {
        "bundle": bundle,
        "artifact": artifact,
        "identity": identity,
        "issuer": issuer,
        "trust_root": trust_root,
    }
    for field in omit:
        declaration.pop(field, None)
    manifest["sigstore"] = declaration
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _write_fixture_files(skill_dir, *, bundle_bytes=None, artifact_bytes=None):
    """Write the staging fixture bundle + artifact into the skill dir."""
    artifact, bundle = _fixture_artifact_and_bundle()
    (skill_dir / "artifact.txt").write_bytes(
        artifact if artifact_bytes is None else artifact_bytes
    )
    (skill_dir / "bundle.sigstore").write_bytes(
        bundle if bundle_bytes is None else bundle_bytes
    )


def _signed_skill_with_bundle(tmp_path, **declare_kwargs):
    """Signed skill with the fixture bundle declared and present."""
    sk, pk = _keys()
    skill_dir = _make_skill(tmp_path)
    _declare_sigstore(skill_dir, **declare_kwargs)
    sign_manifest(skill_dir, sk)
    _write_fixture_files(skill_dir)
    return skill_dir, sk, pk


def _tampered_bundle_bytes():
    """Fixture bundle with its message signature flipped."""
    _, bundle = _fixture_artifact_and_bundle()
    data = json.loads(bundle)
    signature = data["messageSignature"]["signature"]
    flipped = ("A" if signature[0] != "A" else "B") + signature[1:]
    data["messageSignature"]["signature"] = flipped
    return json.dumps(data).encode("utf-8")


# --- Happy path ------------------------------------------------------------

def test_valid_sigstore_bundle_verifies(tmp_path):
    skill_dir, _sk, pk = _signed_skill_with_bundle(tmp_path)
    assert verify_skill(skill_dir, pk) is True
    assert load_skill(skill_dir, pk)["name"] == "example"


def test_valid_bundle_with_provenance_verifies(tmp_path):
    skill_dir, sk, pk = _signed_skill_with_bundle(tmp_path)
    log = TransparencyLog()
    sign_skill_provenance(skill_dir, PROVENANCE_IDENTITY, log)
    assert verify_skill(skill_dir, pk, log, PROVENANCE_IDENTITY) is True


# --- Rejections -------------------------------------------------------------

def test_tampered_bundle_rejected_despite_valid_local_signature(tmp_path):
    skill_dir, _sk, pk = _signed_skill_with_bundle(tmp_path)
    # The local signature is fine: without the bundle it verifies.
    assert verify_skill(skill_dir, pk) is True
    _write_fixture_files(skill_dir, bundle_bytes=_tampered_bundle_bytes())
    assert verify_skill(skill_dir, pk) is False


def test_tampered_artifact_rejected(tmp_path):
    skill_dir, _sk, pk = _signed_skill_with_bundle(tmp_path)
    assert verify_skill(skill_dir, pk) is True
    _write_fixture_files(skill_dir, artifact_bytes=b"tampered artifact")
    assert verify_skill(skill_dir, pk) is False


def test_wrong_identity_rejected(tmp_path):
    skill_dir, _sk, pk = _signed_skill_with_bundle(
        tmp_path, identity="attacker@example.com"
    )
    assert verify_skill(skill_dir, pk) is False


def test_wrong_issuer_rejected(tmp_path):
    skill_dir, _sk, pk = _signed_skill_with_bundle(
        tmp_path, issuer="https://example.com"
    )
    assert verify_skill(skill_dir, pk) is False


def test_staging_bundle_rejected_under_production_root(tmp_path):
    skill_dir, _sk, pk = _signed_skill_with_bundle(tmp_path, trust_root="production")
    assert verify_skill(skill_dir, pk) is False


def test_missing_bundle_file_rejected(tmp_path):
    sk, pk = _keys()
    skill_dir = _make_skill(tmp_path)
    _declare_sigstore(skill_dir)
    sign_manifest(skill_dir, sk)
    # Declared, but no bundle.sigstore file in the directory.
    assert verify_skill(skill_dir, pk) is False


def test_missing_identity_or_issuer_rejected(tmp_path):
    skill_dir, _sk, pk = _signed_skill_with_bundle(tmp_path, omit=("identity",))
    assert verify_skill(skill_dir, pk) is False
    skill_dir, _sk, pk = _signed_skill_with_bundle(tmp_path, omit=("issuer",))
    assert verify_skill(skill_dir, pk) is False


def test_bundle_failure_rejects_despite_valid_provenance(tmp_path):
    skill_dir, _sk, pk = _signed_skill_with_bundle(tmp_path)
    log = TransparencyLog()
    sign_skill_provenance(skill_dir, PROVENANCE_IDENTITY, log)
    assert verify_skill(skill_dir, pk, log, PROVENANCE_IDENTITY) is True
    _write_fixture_files(skill_dir, bundle_bytes=_tampered_bundle_bytes())
    assert verify_skill(skill_dir, pk, log, PROVENANCE_IDENTITY) is False


def test_default_artifact_is_manifest(tmp_path):
    # Without an "artifact" field the bundle is expected to attest
    # manifest.json itself; the fixture bundle attests artifact.txt,
    # so the default path must reject it.
    skill_dir, _sk, pk = _signed_skill_with_bundle(tmp_path, omit=("artifact",))
    assert verify_skill(skill_dir, pk) is False


# --- Malformed declarations fail closed ------------------------------------

def test_malformed_sigstore_declaration_rejected(tmp_path):
    sk, pk = _keys()
    skill_dir = _make_skill(tmp_path)
    path = skill_dir / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["sigstore"] = "bundle.sigstore"  # not an object
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    sign_manifest(skill_dir, sk)
    assert verify_skill(skill_dir, pk) is False


def test_bundle_path_escaping_skill_dir_rejected(tmp_path):
    skill_dir, _sk, pk = _signed_skill_with_bundle(
        tmp_path, bundle="../outside.sigstore"
    )
    assert verify_skill(skill_dir, pk) is False


def test_missing_library_fails_closed(tmp_path, monkeypatch):
    skill_dir, _sk, pk = _signed_skill_with_bundle(tmp_path)

    def _no_library():
        raise ImportError("sigstore is not installed")

    monkeypatch.setattr(sigstore_verify, "_load_sigstore", _no_library)
    assert verify_skill(skill_dir, pk) is False


# --- No bundle declared: local mechanism unchanged --------------------------

def test_no_sigstore_declaration_unchanged(tmp_path):
    sk, pk = _keys()
    skill_dir = _make_skill(tmp_path)
    assert verify_skill(skill_dir, pk) is False  # unsigned
    sign_manifest(skill_dir, sk)
    assert verify_skill(skill_dir, pk) is True
    assert load_skill(skill_dir, pk)["name"] == "example"


def test_stray_bundle_file_without_declaration_is_ignored(tmp_path):
    # A bundle file next to the manifest does nothing on its own: the
    # declaration in the manifest is what opts a skill into Sigstore
    # verification. (The local signature still gates trust.)
    sk, pk = _keys()
    skill_dir = _make_skill(tmp_path)
    sign_manifest(skill_dir, sk)
    _write_fixture_files(skill_dir)
    assert verify_skill(skill_dir, pk) is True


# --- Caller parameters override the manifest -------------------------------

def test_parameter_identity_issuer_override(tmp_path):
    # Manifest declares the wrong issuer; the caller pins the right one.
    skill_dir, _sk, pk = _signed_skill_with_bundle(
        tmp_path, issuer="https://example.com"
    )
    assert verify_skill(skill_dir, pk) is False
    assert (
        verify_skill(
            skill_dir, pk, sigstore_identity=IDENTITY, sigstore_issuer=ISSUER
        )
        is True
    )
    # And a wrong override rejects a skill the manifest would pass.
    skill_dir, _sk, pk = _signed_skill_with_bundle(tmp_path)
    assert verify_skill(skill_dir, pk, sigstore_identity="attacker@example.com") is False


def test_parameter_supplies_missing_identity(tmp_path):
    skill_dir, _sk, pk = _signed_skill_with_bundle(tmp_path, omit=("identity",))
    assert verify_skill(skill_dir, pk) is False
    assert verify_skill(skill_dir, pk, sigstore_identity=IDENTITY) is True
