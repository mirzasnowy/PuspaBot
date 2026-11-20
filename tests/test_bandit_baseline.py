"""Bandit baseline ratchet (E-20, plan section 9.5).

Rules, following the file-size ratchet in ``tests/test_file_size_ratchet.py``:

- Bandit findings over production code may only shrink, per test id and
  in total. A new finding of any known id, or a new test id entirely,
  fails this test.
- A baseline entry that dropped to zero must be removed, so a later
  regrowth cannot hide behind a stale entry.

Scope (each exclusion has a reason, recorded in
``docs/e20-blind-except-audit.md``, section 3):

- Only ``puspa/`` is scanned. Scanning ``tests/`` adds 12,600 ``B101``
  (assert) findings — test asserts are the test mechanism, not a
  security signal — and ``demo/`` is throwaway provider code.
- The whole ``puspa/sandbox/`` directory is excluded. The sandbox
  executor intentionally runs untrusted code: subprocess calls and temp
  files are its purpose, not findings. It has its own containment tests
  instead.

Tool: ``bandit==1.8.6`` (pinned in ``requirements-dev.txt``), command
``python -m bandit -r puspa --exclude puspa/sandbox -f json``. Bandit
exits 1 when it finds anything, so the run result is parsed from its
JSON output, not from its exit code.

This test uses only the standard library plus the ``bandit`` module, and
touches no database, so it also runs in CI's dependency-free ``checks``
lane with ``--noconftest`` (the lane installs bandit for it). When
bandit is not installed the test skips instead of failing: the
authoritative run is the CI ``checks`` lane, and a local checkout
without dev tools installed must not report a false failure.
"""

from __future__ import annotations

import json
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

import pytest

#: Baseline recorded 20 Nov 2026 (E-20): per-test-id bandit findings
#: from ``python -m bandit -r puspa --exclude puspa/sandbox -f json``
#: (bandit 1.8.6). A count here may only ever go down. A test id may be
#: removed from this table only when its count reaches zero; a count may
#: only be lowered by fixing the finding (or proving it a false positive
#: and recording why in docs/e20-blind-except-audit.md), never by
#: editing this table to match a regrowth.
BASELINE_BANDIT: dict[str, int] = {
    # Asserts in production code: real debt (they vanish under
    # python -O), replaced with explicit checks where the assert guards
    # a security invariant.
    "B101": 36,
    # try/except: pass — same narrowing queue as the blind-except ratchet.
    "B110": 15,
    # Subprocess calls with fixed argv lists (no shell).
    "B603": 13,
    # Possible SQL injection: false positives in the sampled sites —
    # the interpolated values are table names from fixed internal
    # tuples, never user input.
    "B608": 10,
    # try/except: continue — same queue as B110.
    "B112": 10,
    # subprocess module imported (informational).
    "B404": 8,
    # Hardcoded password strings: test/dummy placeholders in
    # non-credential code paths — verify per site before removing.
    "B105": 6,
    # Standard random used where no token or key is involved.
    "B311": 5,
    # urllib URL open — check the scheme allowlist first
    # (puspa/plugins/catalog.py).
    "B310": 4,
    # Partial path in a subprocess call (fixed binary names).
    "B607": 4,
    # tempfile.mktemp — move to mkstemp.
    "B108": 3,
    # random module imported (informational).
    "B106": 3,
    # Permissive chmod 0o755 on a directory (puspa/backup/drill.py).
    "B103": 1,
    # requests call without a timeout (puspa/memory/learning_loop.py).
    "B113": 1,
}

#: Total baseline: the sum of BASELINE_BANDIT (119 on 20 Nov 2026).
BASELINE_TOTAL = 119


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


@lru_cache(maxsize=1)
def _bandit_counts(root: Path) -> dict[str, int]:
    """Per-test-id bandit findings over puspa/ excluding puspa/sandbox/.

    Cached for the session: one bandit scan takes ~35 seconds, and two
    tests read the same counts.
    """
    try:
        import bandit  # noqa: F401
    except ImportError:
        pytest.skip("bandit is not installed (pip install bandit==1.8.6)")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "bandit",
            "-r",
            "puspa",
            "--exclude",
            "puspa/sandbox",
            "-f",
            "json",
            "-q",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        report = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"bandit output was not JSON (exit {result.returncode}):\n"
            f"{result.stdout[:2000]}\n{result.stderr[:2000]}"
        ) from exc
    counts: dict[str, int] = {}
    for finding in report.get("results", []):
        test_id = finding["test_id"]
        counts[test_id] = counts.get(test_id, 0) + 1
    return counts


def test_bandit_findings_only_shrink() -> None:
    """Per-test-id bandit counts never exceed the baseline; no new id appears."""
    root = _repo_root()
    counts = _bandit_counts(root)
    violations = []
    for test_id, count in sorted(counts.items()):
        if test_id not in BASELINE_BANDIT:
            violations.append(
                f"{test_id}: {count} finding(s), not in BASELINE_BANDIT — "
                "fix it, or record it here with a reason, instead of growing silently"
            )
        elif count > BASELINE_BANDIT[test_id]:
            violations.append(
                f"{test_id}: {count} finding(s), above its baseline of "
                f"{BASELINE_BANDIT[test_id]} — bandit findings may only shrink"
            )
    total = sum(counts.values())
    if total > BASELINE_TOTAL:
        violations.append(
            f"total: {total} bandit finding(s), above the baseline total of {BASELINE_TOTAL}"
        )
    assert not violations, "\n".join(violations)


def test_bandit_baseline_entries_are_still_present() -> None:
    """Baseline entries that dropped to zero must be removed."""
    root = _repo_root()
    counts = _bandit_counts(root)
    stale = [
        f"{test_id}: now {counts.get(test_id, 0)} finding(s) (baseline {baseline}) — "
        "remove it from BASELINE_BANDIT"
        for test_id, baseline in sorted(BASELINE_BANDIT.items())
        if counts.get(test_id, 0) == 0
    ]
    assert not stale, "\n".join(stale)


def test_bandit_baseline_total_matches_entries() -> None:
    """BASELINE_TOTAL is the sum of the per-test-id entries (no silent drift)."""
    assert sum(BASELINE_BANDIT.values()) == BASELINE_TOTAL, (
        f"BASELINE_BANDIT sums to {sum(BASELINE_BANDIT.values())} "
        f"but BASELINE_TOTAL is {BASELINE_TOTAL}"
    )
