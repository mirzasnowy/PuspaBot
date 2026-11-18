"""Plugin catalog tests (G27).

Manifest admission (required fields, license allowlist, semver,
repository shape, kind, API version, pricing) plus catalog
loading (seed entries valid, duplicate ids rejected). The
repository-liveness check never raises; the live-network check
of the seed repos is skipped unless explicitly enabled, because
the seed repositories are fictional examples and the check
needs the network.
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from puspa.plugins.catalog import (
    check_repository_alive,
    load_catalog,
    load_manifest,
    validate_manifest,
)
from puspa.plugins.errors import PluginStructureError

CATALOG_DIR = Path(__file__).resolve().parent.parent / "puspa" / "plugins" / "catalog"

VALID_MANIFEST = {
    "id": "acme-widget",
    "name": "Acme Widget",
    "version": "1.0.0",
    "description": "Does a widget thing.",
    "author": "Acme (hello@example.com)",
    "license": "MIT",
    "repository": "https://github.com/acme/widget-plugin",
    "entry_point": "acme_widget.plugin",
    "kind": "general",
    "api_version": 1,
    "dependencies": ["requests>=2.0"],
    "price_credits": 0,
}


def _with(**overrides):
    manifest = dict(VALID_MANIFEST)
    manifest.update(overrides)
    return manifest


def test_seed_catalog_loads():
    catalog = load_catalog(CATALOG_DIR)
    assert sorted(catalog) == [
        "acme-notes",
        "local-llm-context",
        "sqlite-memory",
    ]
    assert catalog["acme-notes"]["kind"] == "general"
    assert catalog["local-llm-context"]["kind"] == "context_engine"
    assert catalog["sqlite-memory"]["kind"] == "memory_provider"
    assert catalog["local-llm-context"]["price_credits"] == 10


def test_valid_manifest_passes():
    assert validate_manifest(_with()) == []


def test_manifest_missing_required_field():
    manifest = _with()
    del manifest["license"]
    assert any("license" in p for p in validate_manifest(manifest))


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "Bad ID!"),
        ("id", "ab"),
        ("version", "1.0"),
        ("version", "v1.0.0"),
        ("license", "GPL-3.0"),
        ("license", "Proprietary"),
        ("repository", "http://github.com/acme/widget"),
        ("repository", "https://example.com/acme/widget"),
        ("repository", "https://github.com/acme"),
        ("entry_point", "not a module"),
        ("entry_point", "puspa.plugins.builtin.echo"),
        ("kind", "super_plugin"),
        ("api_version", 2),
        ("price_credits", -1),
        ("price_credits", "free"),
        ("dependencies", "requests"),
        ("dependencies", ["not a requirement!!!"]),
    ],
)
def test_manifest_invalid_fields(field, value):
    problems = validate_manifest(_with(**{field: value}))
    assert problems, f"{field}={value!r} should fail admission"


def test_manifest_non_dict():
    assert validate_manifest(["not", "a", "dict"]) == ["manifest must be a YAML mapping"]


def test_load_manifest_rejects_invalid_yaml(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("id: [unclosed\n", encoding="utf-8")
    with pytest.raises(PluginStructureError, match="not valid YAML"):
        load_manifest(path)


def test_load_manifest_rejects_bad_admission(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text(
        textwrap.dedent(
            """\
            id: bad-one
            name: Bad
            version: 1.0
            description: x
            author: x
            license: MIT
            repository: https://github.com/acme/bad
            entry_point: bad.plugin
            kind: general
            api_version: 1
            """
        ),
        encoding="utf-8",
    )
    with pytest.raises(PluginStructureError, match="failed admission"):
        load_manifest(path)


def test_load_catalog_rejects_duplicate_id(tmp_path):
    first = tmp_path / "a.yaml"
    second = tmp_path / "b.yaml"
    body = textwrap.dedent(
        """\
        id: dup-plugin
        name: Dup
        version: 1.0.0
        description: x
        author: x
        license: MIT
        repository: https://github.com/acme/dup
        entry_point: dup.plugin
        kind: general
        api_version: 1
        """
    )
    first.write_text(body, encoding="utf-8")
    second.write_text(body, encoding="utf-8")
    with pytest.raises(PluginStructureError, match="duplicate plugin id"):
        load_catalog(tmp_path)


def test_load_catalog_missing_directory():
    with pytest.raises(PluginStructureError, match="not found"):
        load_catalog("/definitely/not/a/catalog")


def test_check_repository_alive_never_raises():
    # Unroutable / non-existent hosts fail closed to False.
    assert (
        check_repository_alive(
            "https://github.com/definitely-not-a-real-owner-xyz/definitely-not-a-repo-xyz",
            timeout_s=3,
        )
        is False
    )
    assert check_repository_alive("https://", timeout_s=3) is False


@pytest.mark.skipif(
    os.environ.get("PUSPA_CATALOG_NETWORK") != "1",
    reason=(
        "needs the network and the seed repositories are fictional "
        "examples (github.com/acme/*); set PUSPA_CATALOG_NETWORK=1 "
        "to run against real catalog repos"
    ),
)
def test_seed_repositories_respond():
    catalog = load_catalog(CATALOG_DIR)
    dead = [
        plugin_id
        for plugin_id, manifest in catalog.items()
        if not check_repository_alive(manifest["repository"])
    ]
    assert dead == [], f"catalog repos not responding: {dead}"
