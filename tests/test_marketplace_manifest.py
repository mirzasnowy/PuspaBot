"""Marketplace manifest validation (G3).

In-memory: run with ``pytest tests/test_marketplace_manifest.py
--noconftest -q``.
"""

import json

import pytest

from puspa.marketplace import (
    MAX_INDEX_BYTES,
    MAX_SKILLS,
    MAX_STAGED,
    parse_frontmatter,
    validate_manifest,
    validate_skill_folder,
)
from puspa.marketplace.errors import ManifestError
from tests.marketplace_helpers import make_skill_dir


def _valid_manifest(**overrides):
    data = {
        "id": "price-compare",
        "version": "1.2.3",
        "name": "Price Compare",
        "description": "Compare prices across marketplaces.",
        "triggerTerms": ["price", "cheapest"],
        "requiredCapabilities": ["search"],
        "tools": ["search_listings"],
        "license": "MIT",
        "source": "puspa-official",
        "priceCredits": 0,
        "origin": "library",
    }
    data.update(overrides)
    return data


def test_valid_manifest_passes():
    manifest, problems, warnings = validate_manifest(_valid_manifest())
    assert problems == []
    assert warnings == []
    assert manifest.skill_id == "price-compare"
    assert manifest.version == "1.2.3"
    assert manifest.price_credits == 0


def test_id_pattern_rejects_uppercase_and_underscores():
    for bad in ("PriceCompare", "price_compare", "price compare", "-price", "price-"):
        _, problems, _ = validate_manifest(_valid_manifest(id=bad))
        assert problems, bad


def test_id_must_match_folder_name():
    _, problems, _ = validate_manifest(
        _valid_manifest(id="other-name"), folder_name="price-compare"
    )
    assert any("folder name" in p for p in problems)


def test_version_must_be_semver():
    for bad in ("1.0", "v1.2.3", "1.2.3.4", "latest"):
        _, problems, _ = validate_manifest(_valid_manifest(version=bad))
        assert problems, bad
    manifest, problems, _ = validate_manifest(
        _valid_manifest(version="2.0.0-beta.1+build.5")
    )
    assert problems == []
    assert manifest.version == "2.0.0-beta.1+build.5"


def test_empty_name_description_and_triggers_rejected():
    _, problems, _ = validate_manifest(_valid_manifest(name="  "))
    assert any("'name'" in p for p in problems)
    _, problems, _ = validate_manifest(_valid_manifest(description=""))
    assert any("'description'" in p for p in problems)
    _, problems, _ = validate_manifest(_valid_manifest(triggerTerms=[]))
    assert any("triggerTerms" in p for p in problems)
    _, problems, _ = validate_manifest(_valid_manifest(triggerTerms="price"))
    assert any("triggerTerms" in p for p in problems)


def test_capabilities_and_tools_must_be_string_lists():
    _, problems, _ = validate_manifest(
        _valid_manifest(requiredCapabilities=["search", 7])
    )
    assert any("requiredCapabilities" in p for p in problems)
    _, problems, _ = validate_manifest(_valid_manifest(tools="search_listings"))
    assert any("'tools'" in p for p in problems)


def test_negative_price_rejected():
    _, problems, _ = validate_manifest(_valid_manifest(priceCredits=-5))
    assert any("priceCredits" in p for p in problems)


def test_unknown_licence_is_warning_not_error():
    _, problems, warnings = validate_manifest(
        _valid_manifest(license="WeirdLicense-1.0")
    )
    assert problems == []
    assert any("unknown licence" in w for w in warnings)


def test_limits_are_the_documented_values():
    assert MAX_SKILLS == 30
    assert MAX_INDEX_BYTES == 4000
    assert MAX_STAGED == 20


def test_frontmatter_must_start_with_dashes():
    with pytest.raises(ManifestError):
        parse_frontmatter("# No frontmatter\n\nBody.\n")
    with pytest.raises(ManifestError):
        parse_frontmatter("---\nname: x\n")  # never closed


def test_frontmatter_parses_and_body_follows():
    frontmatter, body = parse_frontmatter(
        '---\nname: "Price Compare"\ndescription: "Compare prices."\n---\n\nBody text.\n'
    )
    assert frontmatter["name"] == "Price Compare"
    assert body.strip() == "Body text."


def test_valid_skill_folder_passes_and_hashes_skill_md(tmp_path):
    folder = make_skill_dir(tmp_path)
    manifest, problems, frontmatter, body = validate_skill_folder(folder)
    assert problems == []
    assert manifest.skill_id == "price-compare"
    assert len(manifest["sha256"]) == 64
    assert manifest["importedAt"]
    assert frontmatter["author"] == "Puspa Team"
    assert "Compares prices" in body


def test_skill_folder_without_skill_md_fails(tmp_path):
    folder = tmp_path / "empty-skill"
    folder.mkdir()
    manifest, problems, _, _ = validate_skill_folder(folder)
    assert manifest is None
    assert any("SKILL.md" in p for p in problems)


def test_skill_folder_without_frontmatter_fails(tmp_path):
    folder = make_skill_dir(tmp_path)
    (folder / "SKILL.md").write_text("# No frontmatter\n", encoding="utf-8")
    manifest, problems, _, _ = validate_skill_folder(folder)
    assert manifest is None
    assert problems


def test_skill_folder_id_must_match_folder(tmp_path):
    folder = make_skill_dir(tmp_path, skill_id="price-compare")
    manifest_path = folder / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["id"] = "other-name"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    manifest, problems, _, _ = validate_skill_folder(folder)
    assert manifest is None
    assert any("folder name" in p for p in problems)


def test_missing_referenced_file_is_a_problem(tmp_path):
    folder = make_skill_dir(
        tmp_path, frontmatter_extra={"references": ["scripts/run.py"]}
    )
    manifest, problems, _, _ = validate_skill_folder(folder)
    assert manifest is None
    assert any("missing" in p for p in problems)


def test_referenced_file_is_hashed(tmp_path):
    folder = make_skill_dir(
        tmp_path, frontmatter_extra={"references": ["scripts/run.py"]}
    )
    script = folder / "scripts" / "run.py"
    script.parent.mkdir()
    script.write_text("print('hi')\n", encoding="utf-8")
    manifest, problems, _, _ = validate_skill_folder(folder)
    assert problems == []
    assert manifest["referencesSha256"]["scripts/run.py"]
    assert len(manifest["referencesSha256"]["scripts/run.py"]) == 64
