"""VPS validation + health checks (E-02, plan section 4.2).

One VPS with full virtualization, then in order: compose up, health
check, sandbox tests, network allowlist test, backup restore. Plus a
"machine replaced" drill: rebuild from scratch with one command and
time it (see ``scripts/vps-rebuild.sh``).

The live steps need a real VPS, which has NOT been bought yet, so
nothing here claims a live check that never ran. What this module
does provide, runnable on any machine today:

- ``check_postgres`` — connect to a Postgres DSN, prove the core
  tables exist and RLS is enabled on the tables that carry it.
- ``check_http`` — prove an HTTP endpoint answers (the dashboard
  answers ``/manifest.json``; the PWA app has no ``/health`` route
  yet, so the compose HEALTHCHECK uses that route too).
- ``check_docker_available`` — prove ``docker`` + ``docker compose``
  exist. On this dev VM Docker is not installed and containers
  cannot run (the kernel rejects setns), so this check FAILS here
  and must pass on the VPS.
- ``validate_deploy_files`` — the offline Dockerfile / compose
  validation from ``puspa.teams.deploy``, wrapped as check results.
- ``classify_allowlist_probe`` — judge one network-probe outcome
  against the sandbox allowlist: an allowed URL must fetch, a
  blocked URL must not. The live probing itself runs on the VPS
  (see ``docs/vps-validation.md``); this function only judges the
  outcome, so it is fully testable here.
- ``run_health_checks`` — all of the above in one call, for the
  health-check step of the VPS validation procedure.

Every function returns plain ``CheckResult`` values (name, passed,
detail) so a drill log can record them verbatim; ``format_report``
renders them as text. No secrets are read or stored here: Postgres
uses peer auth and HTTP checks need no credentials.
"""

from __future__ import annotations

import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import psycopg

from ..memory.schema import resolve_dsn
from ..sandbox.allowlist import is_allowed
from .deploy import validate_compose, validate_dockerfile

__all__ = [
    "CheckResult",
    "all_passed",
    "check_docker_available",
    "check_http",
    "check_postgres",
    "classify_allowlist_probe",
    "format_report",
    "run_health_checks",
    "validate_deploy_files",
]

#: Tables the core schema must create (ledger + accounts).
EXPECTED_TABLES = ("users", "sessions", "vault", "events", "kill_switch")

#: Tables that must have row-level security enabled.
RLS_TABLES = ("users", "sessions", "vault")

#: Default dashboard probe: the PWA manifest route (see module docstring).
DEFAULT_DASHBOARD_URL = "http://127.0.0.1:8000/manifest.json"

#: How long one HTTP probe may take before it counts as failed.
HTTP_TIMEOUT_SECONDS = 10.0


@dataclass
class CheckResult:
    """One health-check outcome: a name, pass/fail, and a detail line."""

    name: str
    passed: bool
    detail: str


def check_postgres(dsn: str | None = None) -> list[CheckResult]:
    """Check a Postgres database: connect, core tables, RLS flags.

    ``dsn`` defaults to ``resolve_dsn`` (explicit arg, then
    ``PUSPA_DSN`` / ``PUSPA_TEST_DSN``, then the local peer-auth
    default). Returns one result per check so a drill log can show
    exactly which part failed.
    """
    target = resolve_dsn(dsn)
    results: list[CheckResult] = []
    try:
        conn = psycopg.connect(target)
    except Exception as exc:  # connection refused, missing role, ...
        return [CheckResult("postgres.connect", False, f"cannot connect: {exc}")]
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
            results.append(CheckResult("postgres.connect", True, f"connected ({target})"))
            cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            present = {row[0] for row in cur.fetchall()}
            missing = [name for name in EXPECTED_TABLES if name not in present]
            if missing:
                results.append(
                    CheckResult(
                        "postgres.tables",
                        False,
                        f"missing tables: {', '.join(missing)}",
                    )
                )
            else:
                results.append(
                    CheckResult(
                        "postgres.tables",
                        True,
                        f"all {len(EXPECTED_TABLES)} core tables present",
                    )
                )
            cur.execute(
                "SELECT relname, relrowsecurity FROM pg_class WHERE relname = ANY(%s)",
                (list(RLS_TABLES),),
            )
            flags = {row[0]: row[1] for row in cur.fetchall()}
            disabled = [name for name in RLS_TABLES if not flags.get(name, False)]
            if disabled:
                results.append(
                    CheckResult(
                        "postgres.rls",
                        False,
                        f"RLS disabled on: {', '.join(disabled)}",
                    )
                )
            else:
                results.append(
                    CheckResult(
                        "postgres.rls",
                        True,
                        "RLS enabled on users, sessions, vault",
                    )
                )
    except Exception as exc:  # query failed mid-check: report, never crash
        results.append(CheckResult("postgres.query", False, f"query failed: {exc}"))
    return results


def check_http(url: str, timeout: float = HTTP_TIMEOUT_SECONDS) -> CheckResult:
    """GET ``url``; pass only on HTTP 200.

    Any transport error (refused, DNS, timeout) or non-200 status is
    a failure with the reason in the detail, so a drill log shows
    what the dashboard actually did instead of a bare red/green.
    """
    request = urllib.request.Request(url, method="GET")
    request.add_header("User-Agent", "puspa-vps-validation/1.0")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        return CheckResult("http.dashboard", False, f"{url} answered HTTP {exc.code}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return CheckResult("http.dashboard", False, f"{url} unreachable: {exc}")
    if status == 200:
        return CheckResult("http.dashboard", True, f"{url} answered HTTP 200")
    return CheckResult("http.dashboard", False, f"{url} answered HTTP {status}")


def check_docker_available() -> CheckResult:
    """Prove ``docker`` + ``docker compose`` exist on this machine.

    This is a precondition for the compose-up step, not a proof that
    a container runs: the sandbox tests on the VPS prove that.
    """
    docker = shutil.which("docker")
    if docker is None:
        return CheckResult(
            "docker.available",
            False,
            "docker is not installed (no `docker` binary on PATH)",
        )
    try:
        proc = subprocess.run(
            [docker, "compose", "version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CheckResult("docker.available", False, f"`docker compose version` failed: {exc}")
    if proc.returncode != 0:
        return CheckResult(
            "docker.available",
            False,
            f"`docker compose version` exited {proc.returncode}: {proc.stderr.strip()[:200]}",
        )
    return CheckResult(
        "docker.available",
        True,
        f"`docker compose version` ok: {proc.stdout.strip()[:200]}",
    )


def validate_deploy_files(root: str | Path | None = None) -> list[CheckResult]:
    """Offline validation of the shipped Dockerfile + compose file.

    Wraps ``puspa.teams.deploy`` as check results so the same
    report format covers the files that ``docker build`` would read
    on the VPS. ``root`` defaults to the repo root (two levels above
    this file's package).
    """
    base = Path(root) if root is not None else Path(__file__).resolve().parent.parent.parent
    results: list[CheckResult] = []
    docker_issues = validate_dockerfile(base / "Dockerfile")
    results.append(
        CheckResult(
            "deploy.dockerfile",
            not docker_issues,
            "clean" if not docker_issues else "; ".join(docker_issues),
        )
    )
    compose_issues = validate_compose(base / "docker-compose.yml")
    results.append(
        CheckResult(
            "deploy.compose",
            not compose_issues,
            "clean" if not compose_issues else "; ".join(compose_issues),
        )
    )
    return results


def classify_allowlist_probe(url: str, fetched_ok: bool, allowlist=None) -> CheckResult:
    """Judge one network-probe outcome against the sandbox allowlist.

    An allowed URL must fetch (the egress path lets it through); a
    blocked URL must NOT fetch (the egress path refuses it). A probe
    whose outcome disagrees with the allowlist is a failure either
    way: an allowed URL that does not fetch means the egress path is
    broken, a blocked URL that fetches means the allowlist is not
    enforced. ``fetched_ok`` is the observed outcome (True when the
    fetch succeeded); this function never fetches anything itself.
    """
    allowed = is_allowed(url, allowlist)
    if allowed and fetched_ok:
        return CheckResult(
            "allowlist.probe",
            True,
            f"allowed URL fetched as expected: {url}",
        )
    if not allowed and not fetched_ok:
        return CheckResult(
            "allowlist.probe",
            True,
            f"blocked URL refused as expected: {url}",
        )
    if allowed:
        return CheckResult(
            "allowlist.probe",
            False,
            f"allowed URL did NOT fetch (egress path broken?): {url}",
        )
    return CheckResult(
        "allowlist.probe",
        False,
        f"blocked URL FETCHED (allowlist not enforced!): {url}",
    )


def run_health_checks(
    dsn: str | None = None,
    dashboard_url: str = DEFAULT_DASHBOARD_URL,
    root: str | Path | None = None,
) -> list[CheckResult]:
    """Run every health check that can run on the current machine.

    Order matches the VPS validation procedure (see
    ``docs/vps-validation.md``): Postgres first, then the dashboard
    HTTP probe, then the Docker precondition, then the offline
    deploy-file validation. Callers that need a live compose-up
    result run that step separately on the VPS — it is not faked
    here.
    """
    results: list[CheckResult] = []
    results.extend(check_postgres(dsn))
    results.append(check_http(dashboard_url))
    results.append(check_docker_available())
    results.extend(validate_deploy_files(root))
    return results


def all_passed(results: list[CheckResult]) -> bool:
    """True when every check in ``results`` passed."""
    return all(result.passed for result in results)


def format_report(results: list[CheckResult]) -> str:
    """Render check results as plain text, one line per check plus a
    summary line, for pasting into a drill log."""
    lines = [
        f"{'PASS' if result.passed else 'FAIL'} {result.name} - {result.detail}"
        for result in results
    ]
    passed = sum(1 for result in results if result.passed)
    lines.append(f"SUMMARY {passed}/{len(results)} checks passed")
    return "\n".join(lines)
