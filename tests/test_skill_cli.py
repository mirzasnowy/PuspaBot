"""Tests for the ``puspa skill`` command group (COW-03).

The list/search half reads the repo's own ``skills/`` dir (no
network, no database). The install half monkeypatches the
network fetch + registry install, so no test ever calls live
GitHub or writes outside a temporary ``PUSPA_HOME``.
"""

from __future__ import annotations

from puspa.cli import main
from puspa.marketplace.hub import FetchedSkill
from puspa.marketplace.manifest import sha256_bytes

SKILL_TEXT = """---
name: "Weather Watcher"
description: "Watches the weather for you."
author: "Acme"
---

# Weather Watcher

Check the forecast before you go out.
"""

MIT_LICENSE_TEXT = """MIT License

Copyright (c) 2026 Acme

Permission is hereby granted, free of charge, to any person obtaining
a copy of this software and associated documentation files.
"""


def _fake_fetched() -> FetchedSkill:
    return FetchedSkill(
        slug="weather-watcher",
        name="Weather Watcher",
        description="Watches the weather for you.",
        skill_text=SKILL_TEXT,
        license_text=MIT_LICENSE_TEXT,
        license="MIT",
        sha256=sha256_bytes(SKILL_TEXT.encode("utf-8")),
        source_label="github:acme/weather-watcher",
    )


def test_skill_list_lists_shipped_skills(capsys):
    assert main(["skill", "list"]) == 0
    out = capsys.readouterr().out
    assert "find-skills" in out
    assert "Find Skills" in out


def test_skill_search_filters(capsys):
    assert main(["skill", "search", "find skills"]) == 0
    out = capsys.readouterr().out
    assert "find-skills" in out
    assert main(["skill", "search", "zzz-no-match"]) == 0
    assert "No skills found." in capsys.readouterr().out


def test_skill_install_previews_without_yes(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("PUSPA_HOME", str(tmp_path))
    monkeypatch.setattr("puspa.marketplace.cli.preview_source", lambda source: _fake_fetched())
    assert main(["skill", "install", "acme/weather-watcher"]) == 2
    out = capsys.readouterr().out
    assert "Weather Watcher" in out
    assert "MIT" in out
    assert "Preview only" in out


def test_skill_install_with_yes_installs_previewed_hash(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("PUSPA_HOME", str(tmp_path))
    fetched = _fake_fetched()
    monkeypatch.setattr("puspa.marketplace.cli.preview_source", lambda source: fetched)
    seen: dict = {}

    def fake_install(registry, spec, **kwargs):
        seen.update(kwargs)
        seen["spec"] = spec
        return {
            "skill_id": "weather-watcher",
            "version": "1.0.0",
            "status": "enabled",
            "sha256": fetched.sha256,
            "source": "github:acme/weather-watcher",
        }

    monkeypatch.setattr("puspa.marketplace.cli.install_from_source", fake_install)
    assert main(["skill", "install", "acme/weather-watcher", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "Installed: weather-watcher v1.0.0" in out
    assert seen["spec"] == "acme/weather-watcher"
    assert seen["expected_sha256"] == fetched.sha256
    assert seen["user_id"] == "local"
    assert str(seen["skills_root"]).startswith(str(tmp_path))
