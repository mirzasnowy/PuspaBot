"""Marketplace registry: import limits + origin separation (G3).

In-memory: run with ``pytest tests/test_marketplace_registry.py
--noconftest -q``.
"""

import pytest

from puspa.marketplace import MarketplaceRegistry
from puspa.marketplace.errors import MarketplaceError
from tests.marketplace_helpers import make_skill_dir


def test_import_lands_disabled_and_staged(tmp_path):
    registry = MarketplaceRegistry()
    folder = make_skill_dir(tmp_path)
    entry = registry.import_skill(folder)
    assert entry.status == "disabled"
    assert entry.staged is True
    assert entry.origin == "library"
    assert registry.get_entry("price-compare") is entry


def test_import_only_takes_skill_md_text(tmp_path):
    registry = MarketplaceRegistry()
    folder = make_skill_dir(tmp_path)
    (folder / "run.sh").write_text("rm -rf /\n", encoding="utf-8")
    entry = registry.import_skill(folder)
    assert "rm -rf" not in entry.skill_text
    assert entry.manifest["sha256"]


def test_origins_are_kept_apart(tmp_path):
    registry = MarketplaceRegistry()
    builtin = make_skill_dir(
        tmp_path, skill_id="builtin-one", origin="builtin", source="puspa-builtin"
    )
    user = make_skill_dir(tmp_path, skill_id="user-one", origin="user", source="user")
    library = make_skill_dir(tmp_path, skill_id="library-one", origin="library")
    registry.import_skill(builtin)
    registry.import_skill(user)
    registry.import_skill(library)
    assert [e.skill_id for e in registry.list_entries(origin="builtin")] == ["builtin-one"]
    assert [e.skill_id for e in registry.list_entries(origin="user")] == ["user-one"]
    assert [e.skill_id for e in registry.list_entries(origin="library")] == ["library-one"]


def test_import_invalid_folder_raises(tmp_path):
    registry = MarketplaceRegistry()
    folder = tmp_path / "bad-skill"
    folder.mkdir()
    with pytest.raises(MarketplaceError):
        registry.import_skill(folder)


def test_index_byte_limit_rejects_huge_index(tmp_path):
    from puspa.marketplace.trust import TrustScore

    # Long ids inflate the index past 4,000 bytes. The 20-staged cap
    # binds first, so install some skills (freeing staged slots)
    # before importing more: installed entries still count in the
    # index, only staged ones count toward the staged cap.
    registry = MarketplaceRegistry(
        trust_scorer=lambda manifest, skill_id: TrustScore(total=100, signals={}, reasons=[])
    )
    ids = [f"{'k' * 55}-{i:02d}" for i in range(27)]
    for skill_id in ids[:20]:
        registry.import_skill(make_skill_dir(tmp_path, skill_id=skill_id))
    for skill_id in ids[:10]:
        registry.install(skill_id, user_id="user-1")
    for skill_id in ids[20:26]:
        registry.import_skill(make_skill_dir(tmp_path, skill_id=skill_id))
    assert registry.index_bytes() <= 4000
    assert len(registry.list_entries()) == 26
    with pytest.raises(MarketplaceError, match="4000 bytes"):
        registry.import_skill(make_skill_dir(tmp_path, skill_id=ids[26]))


def test_index_stays_under_4000_bytes_for_normal_skills(tmp_path):
    registry = MarketplaceRegistry()
    for i in range(5):
        registry.import_skill(make_skill_dir(tmp_path, skill_id=f"skill-{i}"))
    assert registry.index_bytes() <= 4000
    assert len(registry.list_entries()) == 5


def test_max_30_skills_enforced(tmp_path):
    from puspa.marketplace.trust import TrustScore

    # The 20-staged cap binds first, so install some skills (freeing
    # staged slots) before filling the index to 30.
    registry = MarketplaceRegistry(
        trust_scorer=lambda manifest, skill_id: TrustScore(total=100, signals={}, reasons=[])
    )
    for i in range(20):
        registry.import_skill(make_skill_dir(tmp_path, skill_id=f"skill-{i:02d}"))
    for i in range(10):
        registry.install(f"skill-{i:02d}", user_id="user-1")
    for i in range(20, 30):
        registry.import_skill(make_skill_dir(tmp_path, skill_id=f"skill-{i:02d}"))
    assert len(registry.list_entries()) == 30
    with pytest.raises(MarketplaceError, match="full"):
        registry.import_skill(make_skill_dir(tmp_path, skill_id="skill-30"))


def test_install_of_unknown_skill_raises(tmp_path):
    registry = MarketplaceRegistry()
    with pytest.raises(MarketplaceError, match="not in the registry"):
        registry.install("nope", user_id="user-1")


def test_trust_score_of_unknown_skill_raises(tmp_path):
    registry = MarketplaceRegistry()
    with pytest.raises(MarketplaceError, match="not in the registry"):
        registry.trust_score("nope")
