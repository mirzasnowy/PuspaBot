#!/usr/bin/env python3
"""Monthly nanobot bump bot (E-19, plan section 9.4).

Fetches the newest upstream commit of HKUDS/nanobot and the public
security advisories that affect the ``nanobot-ai`` package, compares
both against the pin recorded in ``requirements.txt`` and
``puspa/chassis/pin.json``, and reports what to do next.

Usage (from the repo root)::

    # Report only: is a bump available, is anything lagging?
    python scripts/nanobot_bump.py --check

    # Apply the bump: rewrite the pin in requirements.txt and pin.json.
    python scripts/nanobot_bump.py --apply

    # Apply the bump and open the pull request with the gh CLI.
    python scripts/nanobot_bump.py --apply --open-pr

    # Advisory watch only (the weekly workflow uses this).
    python scripts/nanobot_bump.py --advisories-only

Exit codes: 0 = pin is current and the fork rule is quiet; 10 = a bump
is available (or was applied); 20 = the fork rule fired (see
``puspa/chassis/pin.py``); 1 = the check itself failed (network error,
missing pin, malformed record).

Only the standard library is used, so the script runs in the CI
bump workflow without installing the project. The live bump pull
request against real upstream is opened by the monthly workflow
(``.github/workflows/nanobot-bump.yml``) with ``--apply --open-pr``;
until that workflow has run on GitHub, no live bump PR exists, and
this script does not claim otherwise. A human merges the PR after the
chassis contract lane passes on it (see ``docs/nanobot-pin-process.md``).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    # Plain `python scripts/...` has no pytest.ini pythonpath, so make
    # the repo root importable for the `puspa` package below.
    sys.path.insert(0, str(REPO_ROOT))
_NANOBOT_LOCAL = REPO_ROOT / "nanobot"
if _NANOBOT_LOCAL.is_dir() and str(_NANOBOT_LOCAL) not in sys.path:
    # Same path setup as tests/conftest.py: the local clone is the
    # read reference for the nanobot package, and putting it first
    # makes `import nanobot.agent` resolve from there.
    sys.path.insert(0, str(_NANOBOT_LOCAL))

from puspa.chassis.pin import (
    UPSTREAM_OWNER,
    UPSTREAM_REPO,
    Advisory,
    PinRecord,
    evaluate_fork_rule,
    lagging_advisories,
    parse_pin_from_requirements,
    read_pin_record,
    record_matches_requirements,
    write_pin_record,
)

__all__ = [
    "apply_bump",
    "build_report",
    "count_monkeypatch_sites",
    "fetch_json",
    "latest_upstream_commit",
    "list_advisories",
]

COMMITS_URL = f"https://api.github.com/repos/{UPSTREAM_OWNER}/{UPSTREAM_REPO}/commits/main"
ADVISORIES_URL = "https://api.github.com/advisories?ecosystem=pip&affects=nanobot-ai&per_page=100"
REQUIREMENTS_PATH = REPO_ROOT / "requirements.txt"
PIN_RECORD_PATH = REPO_ROOT / "puspa" / "chassis" / "pin.json"

#: A monkeypatch site, for the fork-rule count: a line in Puspa code
#: that assigns an attribute on an imported nanobot object, or calls
#: ``setattr`` on one. This is a heuristic lower bound (it cannot see
#: patches built from computed names); the manual audit in
#: ``docs/nanobot-pin-process.md`` is the authority, and a reviewer
#: passes the audited count with ``--monkeypatch-sites``.
_MONKEYPATCH_PATTERNS = (
    re.compile(r"^\s*nanobot\.[\w.]*\s*="),
    re.compile(r"setattr\(\s*nanobot\b"),
    re.compile(r"monkeypatch\.setattr\([^)]*nanobot\b"),
)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_BUMP_AVAILABLE = 10
EXIT_FORK_TRIGGERED = 20


def fetch_json(url: str) -> Any:
    """GET a JSON document (GitHub API), with a plain-urllib client."""
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "puspa-nanobot-bump-bot",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"failed to fetch {url}: {exc}") from exc


def latest_upstream_commit(
    fetcher: Callable[[str], Any] = fetch_json,
) -> tuple[str, date]:
    """Newest commit on upstream ``main`` as ``(sha, commit_date)``."""
    payload = fetcher(COMMITS_URL)
    sha = payload["sha"]
    committed = payload["commit"]["committer"]["date"][:10]
    return sha, date.fromisoformat(committed)


def list_advisories(
    fetcher: Callable[[str], Any] = fetch_json,
) -> list[Advisory]:
    """Public advisories affecting the ``nanobot-ai`` package.

    Uses the unauthenticated GitHub Advisory Database endpoint, which
    lists published advisories for a package ecosystem. An advisory
    counts as open until the bump that picks up its fix lands; the
    fix commit is filled in by the bump itself (``--apply`` records
    the new pin as the fix for every advisory published before it).
    """
    payload = fetcher(ADVISORIES_URL)
    advisories = []
    for item in payload:
        published = item.get("published_at", "")[:10]
        if not published:
            continue
        advisories.append(
            Advisory(
                advisory_id=item.get("ghsa_id", "unknown"),
                summary=item.get("summary", ""),
                published_on=date.fromisoformat(published),
                fixed_in_commit=None,
                severity=item.get("severity", "unknown"),
            )
        )
    return advisories


def count_monkeypatch_sites(repo_root: str | Path = REPO_ROOT) -> int:
    """Heuristic count of monkeypatch sites in Puspa code (see above)."""
    root = Path(repo_root) / "puspa"
    sites = 0
    for path in root.rglob("*.py"):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if any(pattern.search(line) for pattern in _MONKEYPATCH_PATTERNS):
                sites += 1
    return sites


def build_report(
    record: PinRecord,
    latest_sha: str,
    advisories: list[Advisory],
    monkeypatch_sites: int,
    today: date,
) -> dict[str, Any]:
    """Decide what the fetched upstream facts mean for the pin."""
    bump_available = latest_sha != record.commit
    lagging = lagging_advisories(advisories, today)
    fork_triggered, fork_reasons = evaluate_fork_rule(advisories, monkeypatch_sites, today)
    return {
        "current_pin": record.commit,
        "latest_upstream_commit": latest_sha,
        "bump_available": bump_available,
        "advisories_open": len(advisories),
        "advisories_lagging": [advisory.advisory_id for advisory in lagging],
        "monkeypatch_sites": monkeypatch_sites,
        "fork_rule_triggered": fork_triggered,
        "fork_reasons": fork_reasons,
        "record_matches_requirements": None,
        "checked_on": today.isoformat(),
    }


def _rewrite_pin_line(text: str, new_sha: str) -> str:
    """Replace the nanobot pin in ``requirements.txt`` text."""
    pattern = re.compile(
        r"(nanobot-ai\s*@\s*git\+https://github\.com/HKUDS/nanobot@)[0-9a-f]{7,40}"
    )
    updated, count = pattern.subn(r"\g<1>" + new_sha, text, count=1)
    if count == 0:
        raise ValueError("no nanobot-ai git pin found in requirements.txt")
    return updated


def apply_bump(
    new_sha: str,
    advisories: list[Advisory],
    today: date,
    repo_root: str | Path = REPO_ROOT,
) -> PinRecord:
    """Move the pin to ``new_sha`` in requirements.txt and pin.json.

    The new pin is recorded as the fix for every advisory published
    before it (their fix is whatever the new upstream commit contains);
    advisories published after the new commit stay open. Returns the
    updated record. The caller opens the pull request; this function
    only changes files.
    """
    root = Path(repo_root)
    requirements_path = root / "requirements.txt"
    requirements_path.write_text(
        _rewrite_pin_line(requirements_path.read_text(encoding="utf-8"), new_sha),
        encoding="utf-8",
    )
    record = read_pin_record(root / "puspa" / "chassis" / "pin.json")
    record.commit = new_sha
    record.pinned_on = today
    record.last_checked_on = today
    record.last_upstream_commit = new_sha
    record.advisories_open = [
        Advisory(
            advisory_id=advisory.advisory_id,
            summary=advisory.summary,
            published_on=advisory.published_on,
            fixed_in_commit=new_sha if advisory.published_on <= today else advisory.fixed_in_commit,
            severity=advisory.severity,
        )
        for advisory in advisories
    ]
    write_pin_record(record, root / "puspa" / "chassis" / "pin.json")
    return record


def open_pull_request(branch: str, new_sha: str, report: dict[str, Any]) -> bool:
    """Open the bump PR with the ``gh`` CLI; False when unavailable.

    The branch must already exist with the bump committed. Returns
    False (instead of raising) when ``gh`` is missing or not
    authenticated, so the workflow can fall back to its PR action and
    a local run can print the manual commands instead.
    """
    body = (
        f"Bump nanobot pin to {new_sha}.\n\n"
        f"Advisories open at check time: {report['advisories_open']} "
        f"({len(report['advisories_lagging'])} lagging). "
        "Chassis contract lane must pass before merge; "
        "see docs/nanobot-pin-process.md."
    )
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "create",
                "--base",
                "main",
                "--head",
                branch,
                "--title",
                f"Bump nanobot pin to {new_sha[:12]}",
                "--body",
                body,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _exit_code(report: dict[str, Any]) -> int:
    if report["fork_rule_triggered"]:
        return EXIT_FORK_TRIGGERED
    if report["bump_available"]:
        return EXIT_BUMP_AVAILABLE
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """Entry point for the bump bot (see the module docstring)."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report only (default)")
    parser.add_argument("--apply", action="store_true", help="move the pin and record it")
    parser.add_argument(
        "--open-pr",
        action="store_true",
        help="open the bump PR with gh (needs --apply and a committed branch)",
    )
    parser.add_argument(
        "--advisories-only",
        action="store_true",
        help="check advisories only, skip the upstream commit fetch",
    )
    parser.add_argument(
        "--monkeypatch-sites",
        type=int,
        default=None,
        help="audited monkeypatch count (default: heuristic count from puspa/)",
    )
    parser.add_argument(
        "--branch",
        default="",
        help="branch holding the bump commit (for --open-pr)",
    )
    args = parser.parse_args(argv)

    try:
        record = read_pin_record(PIN_RECORD_PATH)
        requirements_text = REQUIREMENTS_PATH.read_text(encoding="utf-8")
        parse_pin_from_requirements(requirements_text)
    except (OSError, ValueError) as exc:
        print(f"error: cannot read the current pin: {exc}", file=sys.stderr)
        return EXIT_ERROR

    today = date.today()
    monkeypatch_sites = (
        args.monkeypatch_sites
        if args.monkeypatch_sites is not None
        else count_monkeypatch_sites(REPO_ROOT)
    )
    try:
        advisories = list_advisories()
        latest_sha = record.commit
        if not args.advisories_only:
            latest_sha, _ = latest_upstream_commit()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    report = build_report(record, latest_sha, advisories, monkeypatch_sites, today)
    report["record_matches_requirements"] = record_matches_requirements(record, requirements_text)
    if not report["record_matches_requirements"]:
        print(
            "error: pin.json and requirements.txt disagree on the pin",
            file=sys.stderr,
        )
        print(json.dumps(report, indent=2))
        return EXIT_ERROR

    if args.apply and report["bump_available"]:
        # Pass REPO_ROOT explicitly: apply_bump's default is bound at
        # definition time, so a caller that redirects the repo root
        # (tests, a checkout elsewhere) must say so here.
        record = apply_bump(latest_sha, advisories, today, REPO_ROOT)
        report["bump_available"] = False
        report["current_pin"] = record.commit
        if args.open_pr:
            if not args.branch:
                print("error: --open-pr needs --branch", file=sys.stderr)
                return EXIT_ERROR
            if open_pull_request(args.branch, latest_sha, report):
                print(f"opened bump PR from branch {args.branch}")
            else:
                print(
                    "warning: could not open the PR with gh; open it manually from the bump branch",
                    file=sys.stderr,
                )

    print(json.dumps(report, indent=2))
    return _exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
