"""Marketplace trust scoring (G3 flagship).

In-memory: run with ``pytest tests/test_marketplace_trust.py
--noconftest -q``. Signature checks use fresh ephemeral Ed25519
keys and real ``sign_skill`` / ``verify_skill`` — no live Sigstore
call is made (the Sigstore bundle path is exercised only when a
manifest declares one; these tests cover the local signature
signal, which is what ``verify_skill`` checks by default).
"""

import pytest

from puspa.marketplace import (
    score_module,
    score_skill_dir,
)
from puspa.marketplace.errors import TrustError
from puspa.marketplace.trust import MIN_TRUST_SCORE, require_trust
from tests.marketplace_helpers import (
    make_skill_dir,
    registry_with_skill,
    signed_keypair,
)


def _manifest(**overrides):
    data = {
        "id": "price-compare",
        "version": "1.0.0",
        "name": "Price Compare",
        "description": "Compare prices across marketplaces.",
        "triggerTerms": ["price"],
        "requiredCapabilities": ["search"],
        "tools": ["search_listings"],
        "license": "MIT",
        "source": "puspa-official",
        "priceCredits": 0,
        "origin": "library",
    }
    data.update(overrides)
    return data


def test_minimum_is_60():
    assert MIN_TRUST_SCORE == 60


def test_perfect_signals_score_100():
    score = score_module(manifest=_manifest(), signature_ok=True, clean_installs=2)
    assert score.total == 100
    assert score.passed is True
    assert score.signals == {
        "signature": 30,
        "manifest": 20,
        "license": 15,
        "source": 20,
        "history": 15,
    }


def test_unsigned_unknown_source_scores_below_floor():
    # No signature check, unknown source, no history, valid manifest
    # with a known licence: 0 + 20 + 15 + 0 + 0 = 35.
    score = score_module(manifest=_manifest(source="random-site"))
    assert score.total == 35
    assert score.passed is False


def test_failed_signature_scores_zero_on_that_signal():
    score = score_module(manifest=_manifest(), signature_ok=False, clean_installs=1)
    assert score.signals["signature"] == 0
    assert "FAILED" in " ".join(score.reasons)


def test_unknown_licence_scores_zero_on_that_signal():
    score = score_module(
        manifest=_manifest(license="WeirdLicense-1.0"),
        signature_ok=True,
        clean_installs=1,
    )
    assert score.signals["license"] == 0
    assert score.total == 30 + 10 + 0 + 20 + 15  # manifest half: warning


def test_denied_source_scores_zero_and_is_flagged():
    score = score_module(
        manifest=_manifest(source="clawhub-unverified"),
        signature_ok=True,
        clean_installs=1,
    )
    assert score.signals["source"] == 0
    assert any("denied list" in r for r in score.reasons)


def test_incidents_wipe_the_history_signal():
    score = score_module(
        manifest=_manifest(),
        signature_ok=True,
        clean_installs=5,
        incidents=1,
    )
    assert score.signals["history"] == 0


def test_require_trust_raises_below_floor_without_approval():
    score = score_module(manifest=_manifest(source="random-site"))
    with pytest.raises(TrustError, match="below the minimum"):
        require_trust(score, skill_id="price-compare")


def test_require_trust_passes_with_explicit_approval():
    score = score_module(manifest=_manifest(source="random-site"))
    require_trust(score, explicit_approval=True, skill_id="price-compare")


def test_registry_refuses_untrusted_install_without_approval(tmp_path):
    # Default scorer: no signature check, library skill from an
    # unknown source -> below the floor.
    registry, _ = registry_with_skill(tmp_path, source="random-site")
    with pytest.raises(TrustError):
        registry.install("price-compare", user_id="user-1")
    events = [a["event"] for a in registry.audit_log(user_id="user-1")]
    assert events == ["install_refused_trust"]


def test_registry_installs_untrusted_skill_with_explicit_approval(tmp_path):
    registry, _ = registry_with_skill(tmp_path, source="random-site")
    installed = registry.install("price-compare", user_id="user-1", explicit_approval=True)
    assert installed["status"] == "enabled"


def test_score_skill_dir_uses_real_signature_check(tmp_path):
    signing_key, public_key = signed_keypair()
    folder = make_skill_dir(tmp_path, sign_key=signing_key)
    score = score_skill_dir(folder, public_key)
    # Signed (30) + complete manifest (20) + MIT (15) +
    # trusted source (20) + no history (0) = 85.
    assert score.total == 85
    assert score.passed is True


def test_score_skill_dir_unsigned_scores_signature_zero(tmp_path):
    _signing_key, public_key = signed_keypair()
    folder = make_skill_dir(tmp_path)  # unsigned
    score = score_skill_dir(folder, public_key)
    assert score.signals["signature"] == 0
    # 0 + 20 + 15 + 20 + 0 = 55: an unsigned skill stays below the
    # floor even from a trusted source — the signature matters.
    assert score.total == 55
    assert score.passed is False


def test_score_skill_dir_tampered_manifest_fails(tmp_path):
    import json

    signing_key, public_key = signed_keypair()
    folder = make_skill_dir(tmp_path, sign_key=signing_key)
    manifest_path = folder / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["tools"] = ["search_listings", "evil_tool"]
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    score = score_skill_dir(folder, public_key)
    assert score.signals["signature"] == 0
