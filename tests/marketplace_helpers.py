"""Shared helpers for marketplace tests (in-memory, no Postgres).

Not a test module itself (no ``test_`` prefix on the file name is
not enough — pytest collects ``test_*`` functions; this file has
none).
"""

from __future__ import annotations

import json
from pathlib import Path


def make_skill_dir(
    tmp_path,
    skill_id="price-compare",
    *,
    version="1.0.0",
    source="puspa-official",
    license="MIT",
    price=0,
    origin="library",
    trigger_terms=None,
    tools=None,
    body=None,
    frontmatter_extra=None,
    manifest_extra=None,
    sign_key=None,
):
    """Build a valid skill folder on disk; return the Path.

    ``sign_key`` (an Ed25519 private key) signs the manifest with
    ``puspa.policy.skill_signing.sign_skill``.
    """
    folder = Path(tmp_path) / skill_id
    folder.mkdir(parents=True, exist_ok=True)
    frontmatter = {
        "name": skill_id.replace("-", " ").title(),
        "description": "Compare prices across marketplaces.",
        "author": "Puspa Team",
    }
    if frontmatter_extra:
        frontmatter.update(frontmatter_extra)
    fm_lines = ["---"]
    for key, value in frontmatter.items():
        if isinstance(value, str):
            fm_lines.append(f'{key}: "{value}"')
        elif value is True:
            fm_lines.append(f"{key}: true")
        elif value is False:
            fm_lines.append(f"{key}: false")
        elif isinstance(value, list):
            fm_lines.append(f"{key}: {json.dumps(value)}")
        else:
            fm_lines.append(f"{key}: {value}")
    fm_lines.append("---")
    text = (
        "\n".join(fm_lines)
        + "\n\n"
        + (
            body
            if body is not None
            else (
                "Author: Puspa Team\n\n"
                "## Description\n\nCompares prices.\n\n"
                "## When to use\n\nWhen the user asks for a price.\n\n"
                "## Tools\n\nUses search_files and read_file.\n\n"
                "## Examples\n\nAsk for a price.\n"
            )
        )
    )
    (folder / "SKILL.md").write_text(text, encoding="utf-8")
    manifest = {
        "id": skill_id,
        "version": version,
        "name": skill_id.replace("-", " ").title(),
        "description": "Compare prices across marketplaces.",
        "triggerTerms": trigger_terms or ["price", "compare", "cheapest"],
        "requiredCapabilities": ["search"],
        "tools": tools or ["search_listings", "read_price"],
        "domains": ["tokopedia.com"],
        "connectors": {},
        "author": "puspa",
        "license": license,
        "source": source,
        "priceCredits": price,
        "origin": origin,
    }
    if manifest_extra:
        manifest.update(manifest_extra)
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if sign_key is not None:
        from puspa.policy.skill_signing import sign_skill

        sign_skill(folder, sign_key)
    return folder


def signed_keypair():
    """A fresh Ed25519 (signing key, public key) pair."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    signing_key = Ed25519PrivateKey.generate()
    return signing_key, signing_key.public_key()


def registry_with_skill(tmp_path, **kwargs):
    """A registry with one imported (disabled) skill; return both."""
    from puspa.marketplace import MarketplaceRegistry

    registry = MarketplaceRegistry()
    folder = make_skill_dir(tmp_path, **kwargs)
    entry = registry.import_skill(folder)
    return registry, entry
