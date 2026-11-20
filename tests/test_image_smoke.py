"""Image smoke: the deploy image must serve the React SPA (E-05).

Layers (see puspa/teams/image_smoke.py):

- Pure checks (run everywhere): bundle extraction + SPA-shell
  detection on synthetic HTML, and ``check_spa_at`` against a
  throwaway local HTTP server serving a fake SPA shell, a
  Jinja-style page, and a missing bundle.
- App wiring (run everywhere): ``GET /app`` serves the SPA shell
  from a fake ``dist`` (and the real built ``dist`` when present),
  and falls back to the Jinja template when ``dist`` is absent —
  which the shell check rejects as "not the SPA".
- Live check (Docker only): builds the real deploy image, runs
  one container, and checks ``/app`` serves the React bundle.
  Docker cannot run on the dev VM (no daemon socket), so this
  test skips honestly there; the CI sandbox lane (E-01) fails if
  it skips, so a broken image can never go green on skips.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from puspa.pwa.app import create_app
from puspa.teams.image_smoke import (
    ImageSmokeUnavailable,
    check_spa_at,
    docker_available,
    extract_bundle_src,
    is_spa_shell,
    run_image_smoke,
)

BUILD_ROOT = Path(__file__).resolve().parent.parent

SPA_HTML = """<!doctype html>
<html>
  <head>
    <script type="module" crossorigin src="/assets/index-AbC123.js"></script>
  </head>
  <body>
    <div id="root"></div>
    <!--PUSPA_BOOTSTRAP-->
  </body>
</html>
"""

JINJA_HTML = """<!doctype html>
<html>
  <head><title>Puspa</title></head>
  <body><h1>Chat</h1><p>No React here.</p></body>
</html>
"""

BUNDLE_JS = 'console.log("puspa bundle");\n'


# --- pure checks -----------------------------------------------------------


def test_extract_bundle_src_finds_hashed_bundle():
    assert extract_bundle_src(SPA_HTML) == "/assets/index-AbC123.js"


def test_extract_bundle_src_returns_none_for_jinja_page():
    assert extract_bundle_src(JINJA_HTML) is None
    assert extract_bundle_src("") is None


def test_is_spa_shell_requires_root_div_and_bundle():
    assert is_spa_shell(SPA_HTML)
    assert not is_spa_shell(JINJA_HTML)
    assert not is_spa_shell(SPA_HTML.replace('<div id="root">', "<div>"))
    assert not is_spa_shell(SPA_HTML.replace("/assets/index-AbC123.js", "/src/main.tsx"))


# --- check_spa_at against a throwaway local server -------------------------


def _serve(pages: dict[str, tuple[int, str]]):
    """Run a throwaway HTTP server on 127.0.0.1 serving ``pages``."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # stdlib handler name
            status, body = pages.get(self.path, (404, "not found"))
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):  # silence the server log
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_check_spa_at_ok_for_spa_shell_and_bundle():
    server = _serve({"/app": (200, SPA_HTML), "/assets/index-AbC123.js": (200, BUNDLE_JS)})
    try:
        result = check_spa_at(f"http://127.0.0.1:{server.server_port}")
    finally:
        server.shutdown()
    assert result.ok is True
    assert result.status == 200
    assert result.bundle_src == "/assets/index-AbC123.js"
    assert result.bundle_status == 200


def test_check_spa_at_rejects_jinja_page():
    server = _serve({"/app": (200, JINJA_HTML)})
    try:
        result = check_spa_at(f"http://127.0.0.1:{server.server_port}")
    finally:
        server.shutdown()
    assert result.ok is False
    assert "did not serve the React SPA shell" in result.detail


def test_check_spa_at_rejects_missing_bundle():
    server = _serve({"/app": (200, SPA_HTML), "/assets/index-AbC123.js": (404, "not found")})
    try:
        result = check_spa_at(f"http://127.0.0.1:{server.server_port}")
    finally:
        server.shutdown()
    assert result.ok is False
    assert result.bundle_src == "/assets/index-AbC123.js"
    assert result.bundle_status == 404


def test_check_spa_at_reports_unreachable_server():
    result = check_spa_at("http://127.0.0.1:9")
    assert result.ok is False
    assert "GET /app failed" in result.detail


# --- /app wiring in the FastAPI app ----------------------------------------


def _fake_dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(SPA_HTML, encoding="utf-8")
    (dist / "assets" / "index-AbC123.js").write_text(BUNDLE_JS, encoding="utf-8")
    return dist


def test_app_route_serves_spa_shell_from_dist(monkeypatch, tmp_path):
    monkeypatch.setattr("puspa.pwa.app.DIST_DIR", _fake_dist(tmp_path))
    app = create_app(test_user_id="user-smoke")
    with TestClient(app) as client:
        response = client.get("/app")
    assert response.status_code == 200
    assert is_spa_shell(response.text)
    assert "window.__PUSPA_BOOTSTRAP__" in response.text
    with TestClient(app) as client:
        bundle = client.get("/assets/index-AbC123.js")
    assert bundle.status_code == 200
    assert "puspa bundle" in bundle.text


def test_app_route_needs_no_login():
    app = create_app(test_user_id=None)
    with TestClient(app, follow_redirects=False) as client:
        response = client.get("/app")
    # Unauthenticated: the SPA shell itself (the SPA routes an
    # anonymous visitor to login), never a redirect to /masuk.
    assert response.status_code == 200


def test_app_route_falls_back_to_jinja_without_dist(monkeypatch, tmp_path):
    monkeypatch.setattr("puspa.pwa.app.DIST_DIR", tmp_path / "dist")
    app = create_app(test_user_id="user-smoke")
    with TestClient(app) as client:
        response = client.get("/app")
    assert response.status_code == 200
    assert not is_spa_shell(response.text)


def test_app_route_serves_real_built_dist():
    dist_index = BUILD_ROOT / "apps" / "pwa" / "dist" / "index.html"
    if not dist_index.exists():
        pytest.skip("PWA dist has not been built in this checkout (npm run build)")
    app = create_app(test_user_id="user-smoke")
    with TestClient(app) as client:
        response = client.get("/app")
    assert response.status_code == 200
    assert is_spa_shell(response.text)
    bundle_src = extract_bundle_src(response.text)
    assert bundle_src is not None
    assert (BUILD_ROOT / "apps" / "pwa" / "dist" / bundle_src.lstrip("/")).exists()
    with TestClient(app) as client:
        bundle = client.get(bundle_src)
    assert bundle.status_code == 200
    assert bundle.text.strip() != ""


# --- live image smoke (Docker only) ----------------------------------------


@pytest.mark.live
def test_image_smoke_live():
    if not docker_available():
        pytest.skip("no Docker daemon answers on this host; live image smoke runs in CI")
    try:
        result = run_image_smoke(BUILD_ROOT)
    except ImageSmokeUnavailable:
        pytest.skip("no Docker daemon answers on this host; live image smoke runs in CI")
    assert result.ok is True
    assert result.status == 200
    assert result.bundle_src is not None and result.bundle_src.endswith(".js")
    assert result.bundle_status == 200
