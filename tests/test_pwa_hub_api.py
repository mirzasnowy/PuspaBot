"""API + Jinja tests for the skill marketplace hub endpoints.

Covers the HTTP layer only (the parse/preview/install maths live
in ``tests/test_marketplace_hub.py``): a logged-in user lists
and searches the local skills, previews a skill source, and
installs it after confirming the previewed hash; a failed
install is reported honestly, an anonymous user gets 401s, and
the ``/hub`` page renders from its Jinja template when the
built SPA is absent (CI runs without ``dist/``).

These tests run against the real ``puspa.pwa.app.create_app``:
the routes are mounted there with the app's own ``need_user``
login resolver, so the paths, login gate, status codes, and
JSON shapes tested here are the contract the live PWA serves.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from puspa.marketplace.discovery import SKILL_INSTALL_ENV_VAR
from puspa.pwa.app import create_app

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

RAW_BASE = "https://raw.githubusercontent.com/acme/weather-watcher/main"


def _mock_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(RAW_BASE):
            rest = url[len(RAW_BASE) + 1 :]
            if rest == "LICENSE":
                return httpx.Response(200, text=MIT_LICENSE_TEXT)
            if rest in (
                "skills/weather-watcher/SKILL.md",
                "weather-watcher/SKILL.md",
                "SKILL.md",
            ):
                return httpx.Response(200, text=SKILL_TEXT)
        return httpx.Response(404, text="not found")

    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def _skill_install_enabled(monkeypatch):
    monkeypatch.setenv(SKILL_INSTALL_ENV_VAR, "1")


def _build_app(test_user_id, test_dsn, tmp_path):
    app = create_app(test_user_id=test_user_id, dsn=test_dsn)
    app.state.hub_http_client = httpx.Client(transport=_mock_transport())
    app.state.hub_skills_root = tmp_path / "skills"
    return app


def test_hub_routes_exist_on_create_app(make_user, test_dsn, tmp_path):
    user = make_user()
    app = _build_app(user["user_id"], test_dsn, tmp_path)
    paths = {route.path for route in app.routes}
    assert "/api/hub/skills" in paths
    assert "/api/hub/preview" in paths
    assert "/api/hub/install" in paths
    assert "/hub" in paths
    with TestClient(app) as client:
        assert client.get("/api/hub/skills").status_code == 200
        assert client.get("/hub").status_code == 200


def test_list_skills_returns_shipped_skills_and_searches(make_user, test_dsn, tmp_path):
    user = make_user()
    app = _build_app(user["user_id"], test_dsn, tmp_path)
    with TestClient(app) as client:
        body = client.get("/api/hub/skills").json()
        ids = {s["id"] for s in body["skills"]}
        assert "find-skills" in ids
        assert all(s["installed"] for s in body["skills"])
        found = client.get("/api/hub/skills", params={"q": "find skills"}).json()
        assert [s["id"] for s in found["skills"]] == ["find-skills"]
        assert client.get("/api/hub/skills", params={"q": "zzz-no-match"}).json()["skills"] == []


def test_preview_and_install_after_confirm(make_user, test_dsn, tmp_path):
    user = make_user()
    app = _build_app(user["user_id"], test_dsn, tmp_path)
    with TestClient(app) as client:
        preview = client.get("/api/hub/preview", params={"source": "acme/weather-watcher"})
        assert preview.status_code == 200
        assert preview.json()["slug"] == "weather-watcher"
        assert preview.json()["license"] == "MIT"
        sha = preview.json()["sha256"]
        # No hash, no install: the preview must be confirmed first.
        refused = client.post("/api/hub/install", json={"source": "acme/weather-watcher"})
        assert refused.status_code == 400
        # Wrong hash: content changed since the preview.
        stale = client.post(
            "/api/hub/install",
            json={"source": "acme/weather-watcher", "expected_sha256": "0" * 64},
        )
        assert stale.status_code == 409
        installed = client.post(
            "/api/hub/install",
            json={"source": "acme/weather-watcher", "expected_sha256": sha},
        )
        assert installed.status_code == 200
        assert installed.json()["ok"] is True
        assert installed.json()["installed"]["skill_id"] == "weather-watcher"
        listed = client.get("/api/hub/skills", params={"q": "weather"}).json()["skills"]
        assert [s["id"] for s in listed] == ["weather-watcher"]
        assert listed[0]["installed"] is True
        assert listed[0]["source"] == "github:acme/weather-watcher"


def test_preview_failure_is_reported_not_faked(make_user, test_dsn, tmp_path):
    user = make_user()
    app = _build_app(user["user_id"], test_dsn, tmp_path)
    with TestClient(app) as client:
        response = client.get("/api/hub/preview", params={"source": "acme/no-such-skill"})
        assert response.status_code == 502
        assert "SKILL.md not found" in response.json()["error"]
        failed = client.post(
            "/api/hub/install",
            json={"source": "acme/no-such-skill", "expected_sha256": "0" * 64},
        )
        assert failed.status_code == 502
        assert "error" in failed.json()


def test_anonymous_user_gets_401(test_dsn, tmp_path):
    app = _build_app(None, test_dsn, tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/hub/skills").status_code == 401
        assert client.get("/api/hub/preview", params={"source": "acme/x"}).status_code == 401
        assert (
            client.post(
                "/api/hub/install", json={"source": "acme/x", "expected_sha256": "0" * 64}
            ).status_code
            == 401
        )


def test_hub_page_renders_from_jinja_without_dist(make_user, test_dsn, tmp_path, monkeypatch):
    """The CI lesson: the Jinja template is what gets served when
    ``dist/`` was never built — hide it and the page must still
    render with the real skill list."""
    import puspa.pwa.app as app_module

    user = make_user()
    app = _build_app(user["user_id"], test_dsn, tmp_path)
    monkeypatch.setattr(app_module, "DIST_DIR", tmp_path / "no-dist-here")
    with TestClient(app) as client:
        page = client.get("/hub")
        assert page.status_code == 200
        assert "Hub Skill" in page.text
        assert "Find Skills" in page.text
        assert "/api/hub/preview" in page.text
