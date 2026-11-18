"""Marketplace install / rollback / uninstall + audit (G3).

In-memory: run with ``pytest tests/test_marketplace_install.py
--noconftest -q``.
"""

import pytest

from puspa.marketplace import MarketplaceRegistry
from puspa.marketplace.errors import EntitlementError, MarketplaceError
from puspa.marketplace.trust import TrustScore
from tests.marketplace_helpers import make_skill_dir


def _trusted_registry():
    """Registry whose trust scorer always passes (isolates install
    flow from trust scoring, which has its own test file)."""
    return MarketplaceRegistry(
        trust_scorer=lambda manifest, skill_id: TrustScore(total=100, signals={}, reasons=[])
    )


def _import(registry, tmp_path, **kwargs):
    folder = make_skill_dir(tmp_path, **kwargs)
    return registry.import_skill(folder)


def test_install_enables_and_records_sha256_source_licence(tmp_path):
    registry = _trusted_registry()
    entry = _import(registry, tmp_path)
    installed = registry.install("price-compare", user_id="user-1")
    assert installed["status"] == "enabled"
    assert installed["version"] == "1.0.0"
    assert installed["sha256"] == entry.manifest["sha256"]
    assert installed["source"] == "puspa-official"
    assert installed["license"] == "MIT"
    assert installed["price_credits"] == 0
    assert registry.get_entry("price-compare").status == "enabled"


def test_install_records_audit_entry(tmp_path):
    registry = _trusted_registry()
    _import(registry, tmp_path)
    registry.install("price-compare", user_id="user-1")
    audit = registry.audit_log(skill_id="price-compare", user_id="user-1")
    assert [a["event"] for a in audit] == ["installed"]
    assert audit[0]["sha256"]
    assert audit[0]["source"] == "puspa-official"
    assert audit[0]["license"] == "MIT"


def test_install_twice_is_idempotent_version_record(tmp_path):
    registry = _trusted_registry()
    _import(registry, tmp_path)
    registry.install("price-compare", user_id="user-1")
    registry.install("price-compare", user_id="user-1")
    versions = registry.store.list_versions("price-compare", "user-1")
    assert len(versions) == 1  # same version recorded once (upsert)
    assert len(registry.audit_log(user_id="user-1")) == 2


def test_rollback_restores_previous_version(tmp_path):
    registry = _trusted_registry()
    entry_v1 = _import(registry, tmp_path, version="1.0.0")
    registry.install("price-compare", user_id="user-1")
    sha_v1 = entry_v1.manifest["sha256"]
    # A new version arrives and installs over v1 (different SKILL.md,
    # so a different sha256).
    entry_v2 = _import(
        registry,
        tmp_path,
        version="1.1.0",
        body="Author: Puspa Team\n\n## Description\n\nCompares prices, v2.\n",
    )
    registry.install("price-compare", user_id="user-1")
    assert registry.store.get_install("price-compare", "user-1")["version"] == "1.1.0"
    assert entry_v2.manifest["sha256"] != sha_v1
    # Roll back: the install row again carries v1's sha256 + source.
    rolled = registry.rollback("price-compare", user_id="user-1")
    assert rolled["version"] == "1.0.0"
    assert rolled["sha256"] == sha_v1
    assert rolled["source"] == "puspa-official"
    assert registry.store.get_install("price-compare", "user-1")["version"] == "1.0.0"
    events = [a["event"] for a in registry.audit_log(user_id="user-1")]
    assert "rolled_back" in events


def test_rollback_to_named_version(tmp_path):
    registry = _trusted_registry()
    _import(registry, tmp_path, version="1.0.0")
    registry.install("price-compare", user_id="user-1")
    _import(registry, tmp_path, version="1.1.0")
    registry.install("price-compare", user_id="user-1")
    _import(registry, tmp_path, version="1.2.0")
    registry.install("price-compare", user_id="user-1")
    rolled = registry.rollback("price-compare", user_id="user-1", to_version="1.0.0")
    assert rolled["version"] == "1.0.0"


def test_rollback_without_earlier_version_raises(tmp_path):
    registry = _trusted_registry()
    _import(registry, tmp_path)
    registry.install("price-compare", user_id="user-1")
    with pytest.raises(MarketplaceError, match="no earlier version"):
        registry.rollback("price-compare", user_id="user-1")


def test_rollback_unknown_version_raises(tmp_path):
    registry = _trusted_registry()
    _import(registry, tmp_path, version="1.0.0")
    registry.install("price-compare", user_id="user-1")
    _import(registry, tmp_path, version="1.1.0")
    registry.install("price-compare", user_id="user-1")
    with pytest.raises(MarketplaceError, match="never installed"):
        registry.rollback("price-compare", user_id="user-1", to_version="9.9.9")


def test_uninstall_removes_install_but_keeps_audit(tmp_path):
    registry = _trusted_registry()
    _import(registry, tmp_path)
    registry.install("price-compare", user_id="user-1")
    removed = registry.uninstall("price-compare", user_id="user-1")
    assert removed["status"] == "removed"
    assert registry.get_entry("price-compare").status == "disabled"
    events = [a["event"] for a in registry.audit_log(user_id="user-1")]
    assert events == ["installed", "uninstalled"]
    # History survives: versions + audit still list the install.
    assert registry.store.list_versions("price-compare", "user-1")


def test_uninstall_without_install_raises(tmp_path):
    registry = _trusted_registry()
    _import(registry, tmp_path)
    with pytest.raises(MarketplaceError, match="not installed"):
        registry.uninstall("price-compare", user_id="user-1")


def test_paid_module_without_entitlement_is_refused(tmp_path):
    registry = _trusted_registry()
    _import(registry, tmp_path, price=500)
    with pytest.raises(EntitlementError, match="500"):
        registry.install("price-compare", user_id="user-1")
    events = [a["event"] for a in registry.audit_log(user_id="user-1")]
    assert events == ["install_refused_entitlement"]


def test_paid_module_with_entitlement_installs(tmp_path):
    registry = MarketplaceRegistry(
        trust_scorer=lambda manifest, skill_id: TrustScore(total=100, signals={}, reasons=[]),
        entitlement=lambda user_id, manifest: True,
    )
    _import(registry, tmp_path, price=500)
    installed = registry.install("price-compare", user_id="user-1")
    assert installed["price_credits"] == 500
    assert installed["status"] == "enabled"


def test_install_is_scoped_per_user(tmp_path):
    registry = _trusted_registry()
    _import(registry, tmp_path)
    registry.install("price-compare", user_id="user-1")
    assert registry.store.get_install("price-compare", "user-2") is None
    assert registry.store.list_installs("user-1")
    assert registry.store.list_installs("user-2") == []
