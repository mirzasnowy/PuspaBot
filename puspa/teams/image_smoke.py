"""Image smoke check for the Puspa deploy image (E-05, plan 4.5).

The deploy image must serve the React SPA, not the Jinja fallback:
the Dockerfile builds the PWA in a Node stage and copies
``apps/pwa/dist`` into the runtime stage. This module is the check
that proves it, in two layers:

- Pure checks (run anywhere, no Docker): ``extract_bundle_src`` /
  ``is_spa_shell`` judge served HTML, and ``check_spa_at`` fetches
  a live base URL and verifies the ``/app`` page serves the SPA
  shell plus its hashed JavaScript bundle.
- Live check (needs Docker): ``run_image_smoke`` builds the image,
  runs one container, waits for it to answer, and runs
  ``check_spa_at`` against it. Docker cannot run on the dev VM
  (no daemon socket there), so the live check raises
  ``ImageSmokeUnavailable`` there and the test skips honestly; it
  runs in the CI sandbox lane (E-01), which fails the lane if the
  live test skips.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "ImageSmokeError",
    "ImageSmokeResult",
    "ImageSmokeUnavailable",
    "check_spa_at",
    "docker_available",
    "extract_bundle_src",
    "is_spa_shell",
    "run_image_smoke",
]

#: The SPA entry point served by puspa/pwa/app.py (E-05).
APP_PATH = "/app"

#: The app is up once this stable, unauthenticated route answers.
READY_PATH = "/manifest.json"

#: How long the container gets to answer before the smoke fails.
READY_TIMEOUT_S = 120.0

#: How long the image build may take (torch is in requirements.txt).
BUILD_TIMEOUT_S = 1800

_BUNDLE_SCRIPT_RE = re.compile(r'<script[^>]+src="(/assets/[^"]+\.js)"')


class ImageSmokeError(RuntimeError):
    """The image smoke check ran and the image failed it."""


class ImageSmokeUnavailable(RuntimeError):
    """The image smoke check cannot run here (no Docker daemon)."""


@dataclass
class ImageSmokeResult:
    """Outcome of one ``/app`` check against a running app."""

    ok: bool
    app_url: str
    status: int | None = None
    bundle_src: str | None = None
    bundle_status: int | None = None
    detail: str = ""
    checks: list[str] = field(default_factory=list)


def extract_bundle_src(html: str) -> str | None:
    """Return the hashed React bundle path (``/assets/index-*.js``)
    from served HTML, or None when the page carries no bundle.

    The built SPA shell (``apps/pwa/dist/index.html``) references
    its bundle as a module script; the Jinja fallback templates
    carry no such script, so None means "not the SPA".
    """
    match = _BUNDLE_SCRIPT_RE.search(html)
    return match.group(1) if match else None


def is_spa_shell(html: str) -> bool:
    """True when the HTML is the built SPA shell: a React root div
    plus the hashed bundle script. A Jinja fallback page fails
    this (no root div, no bundle script)."""
    return '<div id="root">' in html and extract_bundle_src(html) is not None


def _http_get(url: str, timeout_s: float) -> tuple[int, str]:
    """GET a URL, following redirects; return (status, body text).

    HTTP error statuses (404, 500, ...) are returned, not raised,
    so the caller can report the exact status it got.
    """
    request = urllib.request.Request(url, headers={"User-Agent": "puspa-image-smoke/1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def check_spa_at(base_url: str, path: str = APP_PATH, timeout_s: float = 10.0) -> ImageSmokeResult:
    """Check that a running app serves the React SPA at ``path``.

    Fetches the page, requires the SPA shell (root div + bundle
    script), then fetches the bundle itself and requires HTTP 200
    with a non-empty body. Any failure returns ``ok=False`` with
    the reason in ``detail`` instead of raising, so the caller can
    report exactly which step failed.
    """
    app_url = base_url.rstrip("/") + path
    checks: list[str] = []
    try:
        status, html = _http_get(app_url, timeout_s)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        return ImageSmokeResult(
            ok=False, app_url=app_url, detail=f"GET {path} failed: {exc}", checks=checks
        )
    checks.append(f"GET {path} -> HTTP {status}")
    if status != 200:
        return ImageSmokeResult(
            ok=False,
            app_url=app_url,
            status=status,
            detail=f"GET {path} returned HTTP {status}, expected 200",
            checks=checks,
        )
    bundle_src = extract_bundle_src(html)
    if bundle_src is None or '<div id="root">' not in html:
        return ImageSmokeResult(
            ok=False,
            app_url=app_url,
            status=status,
            detail=f"GET {path} did not serve the React SPA shell (no root div + bundle script)",
            checks=checks,
        )
    checks.append(f"SPA shell served, bundle {bundle_src}")
    bundle_url = urllib.parse.urljoin(base_url.rstrip("/") + "/", bundle_src.lstrip("/"))
    try:
        bundle_status, bundle_body = _http_get(bundle_url, timeout_s)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        return ImageSmokeResult(
            ok=False,
            app_url=app_url,
            status=status,
            bundle_src=bundle_src,
            detail=f"GET {bundle_src} failed: {exc}",
            checks=checks,
        )
    checks.append(f"GET {bundle_src} -> HTTP {bundle_status}")
    if bundle_status != 200 or not bundle_body.strip():
        return ImageSmokeResult(
            ok=False,
            app_url=app_url,
            status=status,
            bundle_src=bundle_src,
            bundle_status=bundle_status,
            detail=f"GET {bundle_src} returned HTTP {bundle_status} with an empty body"
            if bundle_status == 200
            else f"GET {bundle_src} returned HTTP {bundle_status}, expected 200",
            checks=checks,
        )
    return ImageSmokeResult(
        ok=True,
        app_url=app_url,
        status=status,
        bundle_src=bundle_src,
        bundle_status=bundle_status,
        detail=f"React SPA served at {path}; bundle {bundle_src} fetched (HTTP 200)",
        checks=checks,
    )


def docker_available(timeout_s: float = 10.0) -> bool:
    """True when a Docker daemon answers here (binary present and
    ``docker info`` succeeds). False on the dev VM (no daemon
    socket) and on any host without Docker."""
    if shutil.which("docker") is None:
        return False
    try:
        proc = subprocess.run(
            ["docker", "info"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _free_loopback_port() -> int:
    """Pick a free port on 127.0.0.1 for the smoke container."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_ready(base_url: str, timeout_s: float = READY_TIMEOUT_S) -> None:
    """Poll READY_PATH until the container answers HTTP 200."""
    deadline = time.monotonic() + timeout_s
    last_error: str = "no attempt made"
    while time.monotonic() < deadline:
        try:
            status, _ = _http_get(base_url.rstrip("/") + READY_PATH, timeout_s=5.0)
            if status == 200:
                return
            last_error = f"HTTP {status}"
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            last_error = str(exc)
        time.sleep(2.0)
    raise ImageSmokeError(
        f"container did not answer {READY_PATH} within {timeout_s:.0f}s: {last_error}"
    )


def run_image_smoke(
    context: str | Path,
    *,
    tag: str = "puspa:local",
    container_name: str | None = None,
) -> ImageSmokeResult:
    """Build the deploy image, run it, and check /app serves React.

    Raises ``ImageSmokeUnavailable`` when no Docker daemon answers
    here, and ``ImageSmokeError`` when the build fails, the
    container never answers, or the SPA check fails. The container
    is always removed afterwards. The published port binds
    127.0.0.1 only, matching the compose file's exposure model.
    """
    if not docker_available():
        raise ImageSmokeUnavailable("no Docker daemon answers here (docker info failed)")
    context = str(context)
    name = container_name or f"puspa-image-smoke-{time.time_ns()}"
    try:
        build = subprocess.run(
            ["docker", "build", "-t", tag, context],
            check=False,
            capture_output=True,
            text=True,
            timeout=BUILD_TIMEOUT_S,
            env={"PATH": os.environ.get("PATH", ""), "DOCKER_BUILDKIT": "1"},
        )
    except subprocess.TimeoutExpired as exc:
        raise ImageSmokeError(f"docker build timed out after {BUILD_TIMEOUT_S}s") from exc
    if build.returncode != 0:
        tail = "\n".join(build.stderr.splitlines()[-20:] or build.stdout.splitlines()[-20:])
        raise ImageSmokeError(f"docker build failed (exit {build.returncode}):\n{tail}")
    port = _free_loopback_port()
    base_url = f"http://127.0.0.1:{port}"
    try:
        run = subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--rm",
                "--name",
                name,
                "-p",
                f"127.0.0.1:{port}:8000",
                tag,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if run.returncode != 0:
            raise ImageSmokeError(
                f"docker run failed (exit {run.returncode}): {run.stderr.strip()}"
            )
        _wait_ready(base_url)
        result = check_spa_at(base_url)
        if not result.ok:
            raise ImageSmokeError(result.detail)
        return result
    finally:
        subprocess.run(
            ["docker", "rm", "-f", name],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
