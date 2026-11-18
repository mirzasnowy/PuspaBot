"""Skill discovery via skills.sh + install gates (G30).

In-memory: run with ``pytest tests/test_marketplace_discovery.py
--noconftest -q``. All HTTP goes through ``httpx.MockTransport``;
no test ever calls the live skills.sh API or GitHub.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from puspa.marketplace import MarketplaceRegistry
from puspa.marketplace.discovery import (
    DiscoveredSkill,
    DiscoveryCache,
    InstallApproval,
    SkillsShClient,
    assess_quality,
    detect_license,
    format_installs,
    format_options,
    install_discovered,
    rank_results,
    search_skills,
    search_with_cache,
)
from puspa.marketplace.errors import (
    ApprovalRequiredError,
    DiscoveryError,
    HashMismatchError,
    LicenseNotAllowedError,
    MarketplaceError,
)
from puspa.marketplace.manifest import sha256_bytes

REPO_ROOT = Path(__file__).resolve().parent.parent

SKILL_TEXT = """---
name: "React Best Practices"
description: "React and Next.js performance optimization guidelines."
author: "Vercel"
---

# React Best Practices

Optimize React rendering and bundle size.
"""

MIT_LICENSE_TEXT = """MIT License

Copyright (c) 2026 Example

Permission is hereby granted, free of charge, to any person obtaining
a copy of this software and associated documentation files.
"""

APACHE_LICENSE_TEXT = """Apache License
Version 2.0, January 2004
http://www.apache.org/licenses/

TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION
"""

GPL_LICENSE_TEXT = """GNU GENERAL PUBLIC LICENSE
Version 3, 29 June 2007

Copyright (C) 2007 Free Software Foundation, Inc.
"""

BSD3_LICENSE_TEXT = """Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice.
2. Redistributions in binary form must reproduce the above copyright notice.
3. Neither the name of the copyright holder nor the names of its
contributors may be used to endorse or promote products derived from
this software without specific prior written permission.
"""


def _skill_payload(slug, name, installs, source):
    return {"id": slug, "name": name, "installs": installs, "source": source}


def _search_handler(results_by_query, calls=None, status_by_query=None):
    """MockTransport handler for the skills.sh search API."""

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request.url.params.get("q", ""))
        query = request.url.params.get("q", "")
        status = (status_by_query or {}).get(query, 200)
        if status != 200:
            return httpx.Response(status, json={"error": "boom"})
        return httpx.Response(200, json={"skills": results_by_query.get(query, [])})

    return handler


def _search_client(results_by_query, calls=None, status_by_query=None):
    transport = httpx.MockTransport(_search_handler(results_by_query, calls, status_by_query))
    return SkillsShClient(client=httpx.Client(transport=transport))


def _skill(slug="react-best-practices", *, installs=185000, source="vercel-labs/agent-skills"):
    return DiscoveredSkill(
        slug=slug,
        name=slug.replace("-", " ").title(),
        source=source,
        installs=installs,
    )


def _install_kwargs(**overrides):
    kwargs = {
        "user_id": "user-1",
        "approval": InstallApproval.for_skill(
            "react-best-practices", sha256_bytes(SKILL_TEXT.encode("utf-8"))
        ),
        "skill_text": SKILL_TEXT,
        "license_text": MIT_LICENSE_TEXT,
    }
    kwargs.update(overrides)
    return kwargs


# -- parsing ------------------------------------------------------------


def test_search_parses_registry_payload():
    payload = {
        "react performance": [
            _skill_payload(
                "react-best-practices", "React Best Practices", 185000, "vercel-labs/agent-skills"
            ),
            _skill_payload("next-perf", "Next Perf", "42", "someone/next-perf"),
            _skill_payload("no-installs", "No Installs", None, "someone/no-installs"),
        ]
    }
    client = _search_client(payload)
    results = client.search("react performance")
    assert [r.slug for r in results] == ["react-best-practices", "next-perf", "no-installs"]
    assert results[0].installs == 185000
    assert results[0].source == "vercel-labs/agent-skills"
    assert results[0].detail_url == (
        "https://skills.sh/vercel-labs/agent-skills/react-best-practices"
    )
    assert results[1].installs == 42  # string installs coerced
    assert results[2].installs == 0  # missing installs default to 0
    assert all(r.exact for r in results)


def test_search_skips_items_without_id_or_name():
    payload = {
        "q": [
            {"name": "Nameless", "installs": 5, "source": "x/y"},
            {"id": "no-name", "installs": 5, "source": "x/y"},
            _skill_payload("kept", "Kept", 5, "x/y"),
        ]
    }
    assert [r.slug for r in _search_client(payload).search("q")] == ["kept"]


def test_search_strips_control_characters():
    payload = {"q": [_skill_payload("clean", "Bad\x01Name\x7f", 5, "x/y")]}
    assert _search_client(payload).search("q")[0].name == "BadName"


@pytest.mark.parametrize("status", [404, 500])
def test_search_non_200_returns_no_results(status):
    client = _search_client({}, status_by_query={"q": status})
    assert client.search("q") == []


def test_search_malformed_payload_returns_no_results():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="not json{"))
    assert SkillsShClient(client=httpx.Client(transport=transport)).search("q") == []
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=["not", "a", "dict"]))
    assert SkillsShClient(client=httpx.Client(transport=transport)).search("q") == []


def test_search_transport_error_returns_no_results():
    def boom(request: httpx.Request):
        raise httpx.ConnectError("down", request=request)

    assert (
        SkillsShClient(client=httpx.Client(transport=httpx.MockTransport(boom))).search("q") == []
    )


# -- fallback merge -----------------------------------------------------


def test_fallback_drops_one_word_and_merges():
    exact = [
        _skill_payload(
            "react-best-practices", "React Best Practices", 185000, "vercel-labs/agent-skills"
        ),
        _skill_payload("react-perf", "React Perf", 900, "someone/react-perf"),
    ]
    results_by_query = {
        "react performance test": exact,
        "performance test": [
            _skill_payload("react-perf", "React Perf", 900, "someone/react-perf"),
            _skill_payload("perf-test", "Perf Test", 300, "someone/perf-test"),
        ],
        "react test": [
            _skill_payload("perf-test", "Perf Test", 300, "someone/perf-test"),
            _skill_payload("react-test", "React Test", 1200, "someone/react-test"),
        ],
        "react performance": [
            _skill_payload(
                "react-best-practices", "React Best Practices", 185000, "vercel-labs/agent-skills"
            ),
        ],
    }
    calls: list[str] = []
    outcome = search_skills(_search_client(results_by_query, calls), "react performance test")
    assert outcome.exact_count == 2
    assert [s.slug for s in outcome.skills[:2]] == ["react-best-practices", "react-perf"]
    assert all(s.exact for s in outcome.skills[:2])
    partials = outcome.skills[2:]
    assert all(not s.exact for s in partials)
    # perf-test matched 2 subqueries, so it outranks react-test (1 hit).
    assert [s.slug for s in partials] == ["perf-test", "react-test"]
    # One exact call + one per dropped term.
    assert sorted(calls) == sorted(
        ["react performance test", "performance test", "react test", "react performance"]
    )


def test_fallback_merges_at_most_25_total():
    exact = [_skill_payload("exact-0", "Exact 0", 10, "x/y")]
    results_by_query = {"a b": exact}
    for dropped in ("b", "a"):
        results_by_query[dropped] = [
            _skill_payload(f"p-{dropped}-{i}", f"P {dropped} {i}", i, "x/y") for i in range(30)
        ]
    outcome = search_skills(_search_client(results_by_query), "a b")
    assert len(outcome.skills) == 25
    assert outcome.exact_count == 1


def test_no_fallback_when_exact_has_10_or_more():
    results = {"many words here": [_skill_payload(f"s-{i}", f"S {i}", i, "x/y") for i in range(12)]}
    calls: list[str] = []
    outcome = search_skills(_search_client(results, calls), "many words here")
    assert len(outcome.skills) == 12
    assert calls == ["many words here"]


def test_no_fallback_for_single_term_query():
    calls: list[str] = []
    outcome = search_skills(
        _search_client({"solo": [_skill_payload("s", "S", 1, "x/y")]}, calls), "solo"
    )
    assert len(outcome.skills) == 1
    assert calls == ["solo"]


def test_no_fallback_for_more_than_six_terms():
    calls: list[str] = []
    outcome = search_skills(_search_client({"a b c d e f g": []}, calls), "a b c d e f g")
    assert outcome.skills == []
    assert calls == ["a b c d e f g"]


# -- ranking rubric -----------------------------------------------------


def test_ranking_recommended_trusted_source_with_1k_installs():
    assessed = assess_quality(_skill(installs=185000))
    assert assessed.quality == "recommended"
    assert any("official source" in reason for reason in assessed.reasons)


def test_ranking_recommended_by_stars_when_source_unknown():
    assessed = assess_quality(_skill(installs=5000, source="someone/repo"), stars=500)
    assert assessed.quality == "recommended"


def test_ranking_suspect_under_100_installs():
    assessed = assess_quality(_skill(installs=50, source="someone/repo"), stars=5000)
    assert assessed.quality == "suspect"


def test_ranking_suspect_under_100_stars():
    assessed = assess_quality(_skill(installs=5000, source="someone/repo"), stars=12)
    assert assessed.quality == "suspect"


def test_ranking_caution_1k_installs_unknown_source_unknown_stars():
    assessed = assess_quality(_skill(installs=5000, source="someone/repo"))
    assert assessed.quality == "caution"
    assert any("stars unknown" in reason for reason in assessed.reasons)


def test_rank_results_orders_exact_then_quality_then_installs():
    skills = [
        DiscoveredSkill(slug="s-suspect", name="S", source="x/y", installs=9000, exact=False),
        DiscoveredSkill(slug="c-caution", name="C", source="x/y", installs=5000, exact=True),
        DiscoveredSkill(
            slug="r-rec", name="R", source="vercel-labs/agent-skills", installs=2000, exact=True
        ),
        DiscoveredSkill(
            slug="r-rec-2", name="R2", source="vercel-labs/agent-skills", installs=9000, exact=True
        ),
    ]
    ranked = rank_results(skills)
    assert [s.slug for s in ranked] == ["r-rec-2", "r-rec", "c-caution", "s-suspect"]


def test_format_installs():
    assert format_installs(0) == ""
    assert format_installs(1) == "1 install"
    assert format_installs(42) == "42 installs"
    assert format_installs(1000) == "1K installs"
    assert format_installs(1500) == "1.5K installs"
    assert format_installs(185000) == "185K installs"
    assert format_installs(2500000) == "2.5M installs"


def test_format_options_shows_quality_and_link():
    ranked = rank_results([_skill()])
    text = format_options(ranked)
    assert "React Best Practices" in text
    assert "185K installs" in text
    assert "[recommended]" in text
    assert "https://skills.sh/vercel-labs/agent-skills/react-best-practices" in text
    assert format_options([]) == "No matching skills found."


# -- license detection --------------------------------------------------


def test_detect_license():
    assert detect_license(MIT_LICENSE_TEXT) == "MIT"
    assert detect_license(APACHE_LICENSE_TEXT) == "Apache-2.0"
    assert detect_license(BSD3_LICENSE_TEXT) == "BSD-3-Clause"
    assert detect_license(GPL_LICENSE_TEXT) is None
    assert detect_license("") is None
    assert detect_license(None) is None


# -- cache --------------------------------------------------------------


def test_cache_round_trip_and_stale_rules(tmp_path):
    cache = DiscoveryCache(tmp_path / "cache.json")
    ranked = rank_results([_skill()])
    cache.store("react performance", ranked)
    fresh = cache.lookup("react performance")
    assert fresh is not None
    skills, _age = fresh
    assert [s.slug for s in skills] == ["react-best-practices"]
    assert skills[0].quality == "recommended"
    # A zero TTL makes the same entry stale immediately.
    stale_cache = DiscoveryCache(tmp_path / "cache.json", ttl_seconds=0)
    assert stale_cache.lookup("react performance") is None
    assert stale_cache.lookup("react performance", allow_stale=True) is not None
    assert cache.lookup("something else") is None


def test_search_with_cache_serves_cache_when_live_fails(tmp_path):
    cache = DiscoveryCache(tmp_path / "cache.json")
    live = _search_client(
        {
            "react performance": [
                _skill_payload(
                    "react-best-practices",
                    "React Best Practices",
                    185000,
                    "vercel-labs/agent-skills",
                )
            ]
        }
    )
    outcome = search_with_cache(live, cache, "react performance")
    assert outcome.from_cache is False
    assert len(outcome.skills) == 1
    assert (tmp_path / "cache.json").exists()
    failing = _search_client({}, status_by_query={"react performance": 500})
    cached = search_with_cache(failing, cache, "react performance")
    assert cached.from_cache is True
    assert [s.slug for s in cached.skills] == ["react-best-practices"]
    empty = search_with_cache(failing, cache, "nothing cached")
    assert empty.skills == [] and empty.from_cache is False


# -- install gates ------------------------------------------------------


def test_install_happy_path_records_sha256_source_license(tmp_path):
    registry = MarketplaceRegistry()
    skills_root = tmp_path / "skills"
    installed = install_discovered(registry, _skill(), skills_root=skills_root, **_install_kwargs())
    assert installed["status"] == "enabled"
    assert installed["version"] == "1.0.0"
    assert installed["sha256"] == sha256_bytes(SKILL_TEXT.encode("utf-8"))
    assert installed["source"] == "skills.sh:vercel-labs/agent-skills"
    assert installed["license"] == "MIT"
    assert installed["price_credits"] == 0
    assert registry.get_entry("react-best-practices").status == "enabled"
    folder = skills_root / "react-best-practices"
    assert (folder / "SKILL.md").read_text(encoding="utf-8") == SKILL_TEXT
    assert json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["id"] == (
        "react-best-practices"
    )
    # The G24 curator saw the install: usage telemetry exists.
    assert (folder / ".usage.json").exists()
    assert [a["event"] for a in registry.audit_log(skill_id="react-best-practices")] == [
        "installed"
    ]


def test_install_without_skills_root_uses_staging(tmp_path):
    registry = MarketplaceRegistry()
    installed = install_discovered(registry, _skill(), **_install_kwargs())
    assert installed["status"] == "enabled"
    assert registry.get_entry("react-best-practices").status == "enabled"


def test_install_slug_only_approval_also_works(tmp_path):
    registry = MarketplaceRegistry()
    installed = install_discovered(
        registry,
        _skill(),
        skills_root=tmp_path / "skills",
        **_install_kwargs(approval=InstallApproval.for_skill("react-best-practices")),
    )
    assert installed["status"] == "enabled"


def test_install_without_approval_is_refused(tmp_path):
    registry = MarketplaceRegistry()
    with pytest.raises(ApprovalRequiredError, match="explicit user approval"):
        install_discovered(
            registry, _skill(), skills_root=tmp_path / "skills", **_install_kwargs(approval=None)
        )
    assert registry.get_entry("react-best-practices") is None


def test_install_approval_for_another_skill_is_refused(tmp_path):
    registry = MarketplaceRegistry()
    with pytest.raises(ApprovalRequiredError):
        install_discovered(
            registry,
            _skill(),
            skills_root=tmp_path / "skills",
            **_install_kwargs(
                approval=InstallApproval.for_skill(
                    "other-skill", sha256_bytes(SKILL_TEXT.encode("utf-8"))
                )
            ),
        )
    assert registry.get_entry("react-best-practices") is None


def test_install_approval_for_other_content_is_refused(tmp_path):
    registry = MarketplaceRegistry()
    with pytest.raises(ApprovalRequiredError):
        install_discovered(
            registry,
            _skill(),
            skills_root=tmp_path / "skills",
            **_install_kwargs(approval=InstallApproval.for_skill("react-best-practices", "0" * 64)),
        )
    assert registry.get_entry("react-best-practices") is None


def test_install_wrong_expected_hash_is_refused(tmp_path):
    registry = MarketplaceRegistry()
    with pytest.raises(HashMismatchError, match="does not match"):
        install_discovered(
            registry,
            _skill(),
            skills_root=tmp_path / "skills",
            **_install_kwargs(expected_sha256="0" * 64),
        )
    assert registry.get_entry("react-best-practices") is None


def test_install_unknown_license_is_refused(tmp_path):
    registry = MarketplaceRegistry()
    with pytest.raises(LicenseNotAllowedError, match="no detectable licence"):
        install_discovered(
            registry,
            _skill(),
            skills_root=tmp_path / "skills",
            **_install_kwargs(license_text=None, fetch_license_text=lambda source, slug: None),
        )
    assert registry.get_entry("react-best-practices") is None


def test_install_gpl_license_is_refused(tmp_path):
    registry = MarketplaceRegistry()
    with pytest.raises(LicenseNotAllowedError):
        install_discovered(
            registry,
            _skill(),
            skills_root=tmp_path / "skills",
            **_install_kwargs(license_text=GPL_LICENSE_TEXT),
        )
    assert registry.get_entry("react-best-practices") is None


def test_install_apache_license_is_allowed(tmp_path):
    registry = MarketplaceRegistry()
    installed = install_discovered(
        registry,
        _skill(),
        skills_root=tmp_path / "skills",
        **_install_kwargs(license_text=APACHE_LICENSE_TEXT),
    )
    assert installed["license"] == "Apache-2.0"


def test_install_invalid_slug_is_refused(tmp_path):
    registry = MarketplaceRegistry()
    with pytest.raises(MarketplaceError, match="not a valid skill id"):
        install_discovered(registry, _skill(slug="Bad_Slug!"), **_install_kwargs())


def test_install_uses_default_fetchers_through_mock_transport(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/skills/react-best-practices/SKILL.md"):
            return httpx.Response(200, text=SKILL_TEXT)
        if url.endswith("/LICENSE"):
            return httpx.Response(200, text=MIT_LICENSE_TEXT)
        return httpx.Response(404, text="not found")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    registry = MarketplaceRegistry()
    installed = install_discovered(
        registry,
        _skill(),
        user_id="user-1",
        approval=InstallApproval.for_skill(
            "react-best-practices", sha256_bytes(SKILL_TEXT.encode("utf-8"))
        ),
        skills_root=tmp_path / "skills",
        http_client=client,
    )
    assert installed["status"] == "enabled"


def test_install_missing_skill_text_is_refused(tmp_path):
    def boom(source: str, slug: str):
        raise DiscoveryError("SKILL.md not found")

    registry = MarketplaceRegistry()
    with pytest.raises(DiscoveryError):
        install_discovered(
            registry,
            _skill(),
            skills_root=tmp_path / "skills",
            **_install_kwargs(skill_text=None, fetch_skill_text=boom),
        )


# -- built-in find-skills skill -----------------------------------------


def test_builtin_find_skills_imports_disabled():
    registry = MarketplaceRegistry()
    entry = registry.import_skill(REPO_ROOT / "skills" / "find-skills", origin="builtin")
    assert entry.skill_id == "find-skills"
    assert entry.status == "disabled"  # import-disabled-default
    assert entry.manifest.license == "MIT"
    assert entry.manifest.origin == "builtin"
    assert "how do i" in entry.manifest.to_dict()["triggerTerms"]


def test_builtin_find_skills_is_discoverable_by_curator():
    from puspa.memory.curator import discover_skills, run_curator

    found = discover_skills(REPO_ROOT / "skills")
    assert any(path.name == "find-skills" for path in found)
    report = run_curator(REPO_ROOT / "skills", dry_run=True)
    assert report.scanned >= 1
    assert report.errors == []
