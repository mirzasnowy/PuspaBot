"""Skill marketplace hub: parse, preview, install, browse (COW-03).

All HTTP goes through ``httpx.MockTransport``; no test ever calls
the live GitHub, ClawHub, or skills.sh APIs. Run with ``pytest
tests/test_marketplace_hub.py --noconftest -q`` for the in-memory
half (the CLI list test reads the repo's own ``skills/`` dir).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from puspa.marketplace import MarketplaceRegistry
from puspa.marketplace.discovery import SKILL_INSTALL_ENV_VAR, InstallApproval
from puspa.marketplace.errors import (
    ApprovalRequiredError,
    DiscoveryError,
    HashMismatchError,
    LicenseNotAllowedError,
)
from puspa.marketplace.hub import (
    FetchedSkill,
    install_from_source,
    list_builtin_skills,
    list_hub_skills,
    parse_skill_source,
    preview_source,
    search_local,
    slugify_skill_name,
)
from puspa.marketplace.manifest import sha256_bytes

REPO_ROOT = Path(__file__).resolve().parent.parent

SKILL_TEXT = """---
name: "Weather Watcher"
description: "Watches the weather for you."
author: "Acme"
---

# Weather Watcher

Check the forecast before you go out.
"""

GPL_SKILL_TEXT = """---
name: "GPL Skill"
description: "A skill under the GPL."
author: "Acme"
---

# GPL Skill

Copyleft content.
"""

MIT_LICENSE_TEXT = """MIT License

Copyright (c) 2026 Acme

Permission is hereby granted, free of charge, to any person obtaining
a copy of this software and associated documentation files.
"""

GPL_LICENSE_TEXT = """GNU GENERAL PUBLIC LICENSE
Version 3, 29 June 2007

Copyright (C) 2007 Free Software Foundation, Inc.
"""

CLAWHUB_PAGE = """<html><head><title>weather-watcher — ClawHub</title></head>
<body><a href="https://github.com/acme/weather-watcher">acme/weather-watcher</a></body></html>
"""

RAW_BASE = "https://raw.githubusercontent.com/acme/weather-watcher/main"


def _mock_transport(*, license_text: str | None = MIT_LICENSE_TEXT, skill_text: str = SKILL_TEXT):
    """Fake GitHub raw + ClawHub page + direct-URL hosting."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://clawhub.ai/acme/weather-watcher":
            return httpx.Response(200, text=CLAWHUB_PAGE)
        if url == "https://example.com/skills/weather-watcher/SKILL.md":
            return httpx.Response(200, text=skill_text)
        if url in (
            "https://example.com/skills/weather-watcher/LICENSE",
            "https://example.com/skills/weather-watcher/LICENSE.md",
            "https://example.com/skills/weather-watcher/LICENSE.txt",
        ):
            if license_text is None:
                return httpx.Response(404, text="not found")
            return httpx.Response(200, text=license_text)
        if url.startswith(RAW_BASE):
            rest = url[len(RAW_BASE) + 1 :]
            if rest == "LICENSE":
                if license_text is None:
                    return httpx.Response(404, text="not found")
                return httpx.Response(200, text=license_text)
            if rest in (
                "skills/weather-watcher/SKILL.md",
                "weather-watcher/SKILL.md",
                "SKILL.md",
            ):
                return httpx.Response(200, text=skill_text)
        return httpx.Response(404, text="not found")

    return httpx.MockTransport(handler)


@pytest.fixture
def http_client():
    return httpx.Client(transport=_mock_transport())


@pytest.fixture(autouse=True)
def _skill_install_enabled(monkeypatch):
    """Third-party skill installs are off by default; opt in here."""
    monkeypatch.setenv(SKILL_INSTALL_ENV_VAR, "1")


def _approval(fetched: FetchedSkill) -> InstallApproval:
    return InstallApproval.for_skill(fetched.slug, fetched.sha256)


def _install_kwargs(fetched: FetchedSkill) -> dict:
    return {
        "user_id": "user-1",
        "approval": _approval(fetched),
        "expected_sha256": fetched.sha256,
    }


# -- source parsing -------------------------------------------------------


def test_parse_github_bare_and_prefixed():
    source = parse_skill_source("acme/weather-watcher")
    assert (source.kind, source.owner, source.repo, source.path) == (
        "github",
        "acme",
        "weather-watcher",
        "",
    )
    assert source.slug_hint == "weather-watcher"
    assert parse_skill_source("github:acme/weather-watcher").kind == "github"
    assert parse_skill_source("acme/weather-watcher.git").repo == "weather-watcher"


def test_parse_github_folder_and_tree_url():
    source = parse_skill_source("acme/skills-repo/skills/weather-watcher")
    assert source.path == "skills/weather-watcher"
    assert source.slug_hint == "weather-watcher"
    source = parse_skill_source(
        "https://github.com/acme/skills-repo/tree/main/skills/weather-watcher"
    )
    assert (source.owner, source.repo, source.path) == (
        "acme",
        "skills-repo",
        "skills/weather-watcher",
    )
    source = parse_skill_source(
        "https://raw.githubusercontent.com/acme/weather-watcher/main/SKILL.md"
    )
    assert (source.owner, source.repo, source.path) == ("acme", "weather-watcher", "")


def test_parse_clawhub_and_direct_url():
    source = parse_skill_source("clawhub:acme/weather-watcher")
    assert (source.kind, source.owner, source.slug_hint) == (
        "clawhub",
        "acme",
        "weather-watcher",
    )
    source = parse_skill_source("https://clawhub.ai/acme/weather-watcher")
    assert (source.kind, source.owner, source.slug_hint) == (
        "clawhub",
        "acme",
        "weather-watcher",
    )
    source = parse_skill_source("https://example.com/skills/weather-watcher/SKILL.md")
    assert source.kind == "url"
    assert source.url == "https://example.com/skills/weather-watcher/SKILL.md"


def test_parse_rejects_gibberish():
    for bad in ("", "   ", "justaword", "https://example.com/not-a-skill", "/etc/passwd"):
        with pytest.raises(DiscoveryError):
            parse_skill_source(bad)


def test_slugify_skill_name():
    assert slugify_skill_name("Weather Watcher") == "weather-watcher"
    assert slugify_skill_name("  My  Cool_Skill! ") == "my-cool-skill"
    assert slugify_skill_name("!!!") == ""


# -- preview --------------------------------------------------------------


def test_preview_github_happy_path(http_client):
    fetched = preview_source("acme/weather-watcher", http_client=http_client)
    assert fetched.slug == "weather-watcher"
    assert fetched.name == "Weather Watcher"
    assert fetched.description == "Watches the weather for you."
    assert fetched.license == "MIT"
    assert fetched.sha256 == sha256_bytes(SKILL_TEXT.encode("utf-8"))
    assert fetched.skill_text == SKILL_TEXT


def test_preview_clawhub_resolves_repo_via_page(http_client):
    fetched = preview_source("clawhub:acme/weather-watcher", http_client=http_client)
    assert fetched.slug == "weather-watcher"
    assert fetched.license == "MIT"


def test_preview_direct_url(http_client):
    fetched = preview_source(
        "https://example.com/skills/weather-watcher/SKILL.md", http_client=http_client
    )
    assert fetched.slug == "weather-watcher"
    assert fetched.license == "MIT"


def test_preview_fetch_failure_is_honest(http_client):
    with pytest.raises(DiscoveryError, match=r"SKILL\.md not found"):
        preview_source("acme/no-such-skill", http_client=http_client)


# -- install gates ----------------------------------------------------------


def test_install_happy_path_records_sha256_source_license(tmp_path, http_client):
    registry = MarketplaceRegistry()
    skills_root = tmp_path / "skills"
    fetched = preview_source("acme/weather-watcher", http_client=http_client)
    installed = install_from_source(
        registry,
        "acme/weather-watcher",
        skills_root=skills_root,
        http_client=http_client,
        **_install_kwargs(fetched),
    )
    assert installed["status"] == "enabled"
    assert installed["version"] == "1.0.0"
    assert installed["sha256"] == sha256_bytes(SKILL_TEXT.encode("utf-8"))
    assert installed["source"] == "github:acme/weather-watcher"
    assert installed["license"] == "MIT"
    assert installed["price_credits"] == 0
    assert registry.get_entry("weather-watcher").status == "enabled"
    folder = skills_root / "weather-watcher"
    assert (folder / "SKILL.md").read_text(encoding="utf-8") == SKILL_TEXT
    assert json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["id"] == (
        "weather-watcher"
    )
    assert [a["event"] for a in registry.audit_log(skill_id="weather-watcher")] == ["installed"]


def test_install_refused_when_flag_off(tmp_path, monkeypatch, http_client):
    monkeypatch.delenv(SKILL_INSTALL_ENV_VAR, raising=False)
    registry = MarketplaceRegistry()
    fetched = preview_source("acme/weather-watcher", http_client=http_client)
    with pytest.raises(DiscoveryError, match="feature flag"):
        install_from_source(
            registry,
            "acme/weather-watcher",
            skills_root=tmp_path / "skills",
            http_client=http_client,
            **_install_kwargs(fetched),
        )


def test_install_refused_without_approval(tmp_path, http_client):
    registry = MarketplaceRegistry()
    fetched = preview_source("acme/weather-watcher", http_client=http_client)
    with pytest.raises(ApprovalRequiredError, match="explicit user approval"):
        install_from_source(
            registry,
            "acme/weather-watcher",
            user_id="user-1",
            approval=None,
            expected_sha256=fetched.sha256,
            skills_root=tmp_path / "skills",
            http_client=http_client,
        )


def test_install_refused_on_hash_mismatch(tmp_path, http_client):
    registry = MarketplaceRegistry()
    fetched = preview_source("acme/weather-watcher", http_client=http_client)
    with pytest.raises(HashMismatchError, match="does not match"):
        install_from_source(
            registry,
            "acme/weather-watcher",
            user_id="user-1",
            approval=_approval(fetched),
            expected_sha256="0" * 64,
            skills_root=tmp_path / "skills",
            http_client=http_client,
        )
    assert registry.get_entry("weather-watcher") is None


def test_install_refused_on_gpl_license(tmp_path):
    client = httpx.Client(
        transport=_mock_transport(license_text=GPL_LICENSE_TEXT, skill_text=GPL_SKILL_TEXT)
    )
    registry = MarketplaceRegistry()
    fetched = preview_source("acme/weather-watcher", http_client=client)
    assert fetched.license is None  # GPL is not in the allowlist detector
    with pytest.raises(LicenseNotAllowedError, match="licence"):
        install_from_source(
            registry,
            "acme/weather-watcher",
            skills_root=tmp_path / "skills",
            http_client=client,
            **_install_kwargs(fetched),
        )
    assert registry.get_entry("gpl-skill") is None


def test_install_refused_without_detectable_license(tmp_path):
    client = httpx.Client(transport=_mock_transport(license_text=None))
    registry = MarketplaceRegistry()
    fetched = preview_source("acme/weather-watcher", http_client=client)
    assert fetched.license is None
    with pytest.raises(LicenseNotAllowedError, match="no detectable licence"):
        install_from_source(
            registry,
            "acme/weather-watcher",
            skills_root=tmp_path / "skills",
            http_client=client,
            **_install_kwargs(fetched),
        )


# -- browse / search --------------------------------------------------------


def test_list_builtin_skills_reads_shipped_skills():
    skills = list_builtin_skills(REPO_ROOT / "skills")
    ids = {s.skill_id for s in skills}
    assert "find-skills" in ids
    assert len(skills) == len(list((REPO_ROOT / "skills").iterdir()))
    assert all(s.origin == "builtin" and s.installed for s in skills)


def test_list_hub_skills_merges_registry_and_searches(tmp_path, http_client):
    registry = MarketplaceRegistry()
    skills_root = tmp_path / "skills"
    fetched = preview_source("acme/weather-watcher", http_client=http_client)
    install_from_source(
        registry,
        "acme/weather-watcher",
        skills_root=skills_root,
        http_client=http_client,
        **_install_kwargs(fetched),
    )
    all_skills = list_hub_skills(registry, skills_dir=REPO_ROOT / "skills")
    assert any(s.skill_id == "weather-watcher" for s in all_skills)
    assert any(s.skill_id == "find-skills" for s in all_skills)
    found = list_hub_skills(registry, skills_dir=REPO_ROOT / "skills", query="weather")
    assert [s.skill_id for s in found] == ["weather-watcher"]
    assert list_hub_skills(registry, skills_dir=REPO_ROOT / "skills", query="nope") == []


def test_search_local_matches_all_terms():
    skills = list_builtin_skills(REPO_ROOT / "skills")
    found = search_local(skills, "find skills")
    assert [s.skill_id for s in found] == ["find-skills"]
    assert search_local(skills, "") == skills
    assert search_local(skills, "zzz-no-match") == []
