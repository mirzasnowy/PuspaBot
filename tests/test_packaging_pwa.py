"""Packaging regression tests for the PWA templates/static (queue item 1.1).

The installed wheel once shipped no PWA templates or static files:
``puspa/pwa/app.py`` resolved them from the repo root
(``apps/pwa/templates|static``), which a wheel never carries, so an
installed ``puspa web`` could not render any screen. The fix ships
copies inside the package (``puspa/pwa/templates/``,
``puspa/pwa/static/``) and resolves them relative to the package.

The editable source of truth stays ``apps/pwa/templates|static``;
these tests fail if a shipped copy drifts from its source, if a
shipped file is not covered by the ``package-data`` globs, or if
the built React output (``apps/pwa/dist/``) is ever expected inside
the package: ``dist/`` is a gitignored ``npm run build`` artifact,
rebuilt at deploy time, and every screen falls back to its Jinja
template when it is absent.
"""

from __future__ import annotations

import fnmatch
import tomllib
from pathlib import Path

from fastapi.testclient import TestClient

from puspa.pwa.app import DIST_DIR, STATIC_DIR, TEMPLATE_DIR, create_app

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = REPO_ROOT / "puspa"
PYPROJECT = REPO_ROOT / "pyproject.toml"

# Source tree (editable) -> shipped tree (inside the package).
SYNCED_PAIRS = (
    (REPO_ROOT / "apps" / "pwa" / "templates", PACKAGE_DIR / "pwa" / "templates"),
    (REPO_ROOT / "apps" / "pwa" / "static", PACKAGE_DIR / "pwa" / "static"),
)


def _package_data_globs() -> list[str]:
    with PYPROJECT.open("rb") as handle:
        config = tomllib.load(handle)
    globs: list[str] = []
    for patterns in config["tool"]["setuptools"]["package-data"].values():
        globs.extend(patterns)
    assert globs, "no [tool.setuptools.package-data] globs declared"
    return globs


def test_shipped_pwa_copies_match_source():
    """Every shipped PWA file is byte-identical to its editable source."""
    drifted = []
    for source_dir, shipped_dir in SYNCED_PAIRS:
        sources = {p.relative_to(source_dir) for p in source_dir.rglob("*") if p.is_file()}
        shipped = {p.relative_to(shipped_dir) for p in shipped_dir.rglob("*") if p.is_file()}
        for rel in sorted(sources - shipped):
            drifted.append(f"missing shipped copy: {shipped_dir / rel}")
        for rel in sorted(shipped - sources):
            drifted.append(f"shipped file with no source: {shipped_dir / rel}")
        for rel in sorted(sources & shipped):
            if (source_dir / rel).read_bytes() != (shipped_dir / rel).read_bytes():
                drifted.append(f"content drift: {shipped_dir / rel}")
    assert not drifted, f"PWA shipped copies drifted from source: {drifted}"


def test_shipped_pwa_files_are_covered_by_package_data():
    globs = _package_data_globs()
    uncovered = []
    for _, shipped_dir in SYNCED_PAIRS:
        for path in sorted(shipped_dir.rglob("*")):
            if path.is_file() and not any(fnmatch.fnmatch(path.name, g) for g in globs):
                uncovered.append(str(path.relative_to(REPO_ROOT)))
    assert not uncovered, f"PWA files not covered by package-data: {uncovered}"


def test_pwa_dirs_resolve_inside_package():
    assert TEMPLATE_DIR.is_relative_to(PACKAGE_DIR)
    assert STATIC_DIR.is_relative_to(PACKAGE_DIR)
    assert (TEMPLATE_DIR / "chat.html").is_file()
    assert (TEMPLATE_DIR / "base.html").is_file()
    assert (STATIC_DIR / "sw.js").is_file()
    assert (STATIC_DIR / "puspa-logo.png").is_file()


def test_dist_is_not_shipped_in_package():
    """The React build stays a deploy-time artifact, never wheel content."""
    assert not DIST_DIR.is_relative_to(PACKAGE_DIR)
    assert not (PACKAGE_DIR / "pwa" / "dist").exists()


def test_manifest_lists_pwa_data():
    manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    for suffix in ("*.html", "*.js", "*.png"):
        assert suffix in manifest, f"MANIFEST.in misses {suffix}"


def test_chat_renders_from_package_templates_without_dist(
    make_user, test_dsn, tmp_path, monkeypatch
):
    """The installed-package layout: no dist, templates from the wheel.

    Hides the built SPA so the page must render from the shipped
    Jinja template, exactly as an installed ``puspa web`` serves it.
    """
    import puspa.pwa.app as app_module

    user = make_user()
    app = create_app(test_user_id=user["user_id"], dsn=test_dsn)
    monkeypatch.setattr(app_module, "DIST_DIR", tmp_path / "no-dist-here")
    with TestClient(app, raise_server_exceptions=False) as client:
        page = client.get("/chat")
        assert page.status_code == 200
        assert "<h1>Chat</h1>" in page.text
        worker = client.get("/sw.js")
        assert worker.status_code == 200
        assert worker.text.strip(), "service worker served empty"
