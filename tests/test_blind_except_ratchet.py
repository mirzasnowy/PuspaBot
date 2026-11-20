"""Blind-except ratchet (E-20, plan section 9.5).

Rules, following the file-size ratchet in ``tests/test_file_size_ratchet.py``:

- The count of blind-except handlers that swallow the exception may only
  shrink. A new swallowing handler, in any file, fails this test.
- Each file's count may only shrink against its recorded baseline below.
  A file with no baseline entry may not gain a handler at all: record it
  here, with a reason, instead of growing it silently.
- A baseline entry that dropped to zero must be removed, so a later
  regrowth cannot hide behind a stale entry.

What counts as "blind-except": ruff rule BLE001,
``ruff check --select BLE001 puspa tests demo`` (ruff 0.17.5, pinned in
``requirements-dev.txt``). Ruff flags only handlers that swallow the
exception (return a fallback, log and continue, or pass). A handler that
re-raises — ``raise``, ``raise ... from exc``, or a conditional raise —
is NOT flagged, which is exactly the error-translation pattern this
codebase uses (low-level failure translated to a domain error such as
``SigningError`` or ``TrustRootError``). An AST count on 20 Nov 2026
found 248 blind handlers in scope: 56 re-raising translators and 192
swallowing handlers. The baseline below is the 192.

Baseline history: 203 swallowing handlers before E-20. The "~98"
figure in the old ``pyproject.toml`` comment was never measured and was
wrong. E-20 narrowed 11 handlers with provably narrow failure modes
(see ``docs/e20-blind-except-audit.md``, section 2.2); the ratchet
resumes from 192. P-05 (20 Nov 2026) adds 1 recorded handler — the
room job runner, which must stay blind by design (audit section 2.1)
— so the ratchet resumes from 193. Every handler on a money,
credential, or approval path was audited; the audit notes which must
stay blind-except and why are in that document.

This test uses only the standard library plus the ``ruff`` binary, and
touches no database, so it also runs in CI's dependency-free ``checks``
lane with ``--noconftest`` (the lane installs ruff for it).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

#: Baseline recorded 20 Nov 2026 (E-20): per-file counts of ruff BLE001
#: findings from ``ruff check --select BLE001 puspa tests demo``.
#: A count here may only ever go down. A file may be removed from this
#: table only when its count reaches zero; a count may only be lowered
#: by narrowing a handler (with a neighbour-test run), never by editing
#: this table to match a regrowth.
BASELINE_BLE001: dict[str, int] = {
    "demo/byok_provider.py": 1,
    "demo/mock_provider_server.py": 1,
    "puspa/backup/drill.py": 2,
    "puspa/backup/local_state.py": 1,
    "puspa/billing/billing.py": 2,
    "puspa/billing/client.py": 1,
    "puspa/cassette/cassette.py": 1,
    "puspa/channels/delivery.py": 1,
    "puspa/chassis/agent_session.py": 1,
    "puspa/chassis/hooks.py": 1,
    "puspa/chassis/registry.py": 1,
    "puspa/chassis/tools.py": 4,
    "puspa/computer/sync.py": 1,
    "puspa/connectors/google.py": 1,
    "puspa/consistency/reconcile.py": 1,
    "puspa/consistency/runner.py": 1,
    "puspa/decider/backends.py": 2,
    "puspa/decider/decider.py": 4,
    "puspa/decider/keycheck.py": 1,
    "puspa/eval/fuzz.py": 3,
    "puspa/evidence/eval_harness.py": 1,
    "puspa/evidence/verifier.py": 1,
    "puspa/graphs/service.py": 3,
    "puspa/guardrails/rails.py": 1,
    "puspa/harness/repo_map.py": 3,
    "puspa/hosting/fallback.py": 1,
    "puspa/hosting/gateway.py": 2,
    "puspa/ledger/audit_daemon.py": 1,
    "puspa/marketplace/learning.py": 3,
    "puspa/marketplace/narrowing.py": 1,
    "puspa/memory/background_review.py": 3,
    "puspa/memory/curator.py": 2,
    "puspa/memory/learning_loop.py": 4,
    "puspa/memory/optimizer.py": 1,
    "puspa/memory/skill_repair.py": 1,
    "puspa/modules/manifest.py": 2,
    "puspa/modules/notifications.py": 1,
    "puspa/nightly/probes.py": 1,
    "puspa/observability/alarms.py": 1,
    "puspa/observability/tracing.py": 2,
    "puspa/plugins/adapters.py": 4,
    "puspa/plugins/catalog.py": 2,
    "puspa/plugins/loader.py": 2,
    "puspa/policy/revocation.py": 2,
    "puspa/policy/signing.py": 1,
    "puspa/policy/sigstore_verify.py": 4,
    "puspa/policy/skill_signing.py": 3,
    "puspa/policy/trust_root.py": 2,
    "puspa/pricing/readers.py": 1,
    "puspa/pricing/watchlist.py": 1,
    "puspa/pwa/app.py": 12,
    "puspa/pwa/graph_api.py": 3,
    "puspa/pwa/mockup_api.py": 6,
    "puspa/pwa/model_settings.py": 1,
    "puspa/pwa/onboarding_api.py": 1,
    "puspa/rooms/job_queue.py": 1,
    "puspa/router/compression.py": 3,
    "puspa/runtime/context_compression.py": 3,
    "puspa/runtime/hierarchy.py": 4,
    "puspa/runtime/tasks.py": 3,
    "puspa/sandbox/runtime.py": 1,
    "puspa/scheduler/cron.py": 5,
    "puspa/scheduler/curriculum.py": 4,
    "puspa/scheduler/recovery.py": 2,
    "puspa/scheduler/scheduler.py": 1,
    "puspa/sentinel/insights.py": 2,
    "puspa/sentinel/scanner.py": 2,
    "puspa/sentinel/verifier.py": 2,
    "puspa/subagents/hooks.py": 1,
    "puspa/subagents/host.py": 1,
    "puspa/subagents/lifecycle.py": 2,
    "puspa/teams/deploy.py": 1,
    "puspa/teams/visibility.py": 1,
    "puspa/teams/vps.py": 2,
    "puspa/tools/approval.py": 2,
    "puspa/tools/check.py": 7,
    "puspa/tools/narrowing.py": 2,
    "puspa/tools/registry.py": 4,
    "puspa/tools/toolsets.py": 1,
    "puspa/turns/conflict_wiring.py": 1,
    "puspa/turns/intercepts.py": 1,
    "puspa/turns/interrupt.py": 1,
    "puspa/turns/loop.py": 3,
    "puspa/workflow/runner.py": 6,
    "puspa/world/agents.py": 1,
    "puspa/world/causal.py": 1,
    "puspa/world/model.py": 1,
    "tests/test_agent_store.py": 1,
    "tests/test_anti_manipulation.py": 1,
    "tests/test_bitemporal.py": 1,
    "tests/test_causal_store.py": 1,
    "tests/test_loop_persistence_db.py": 1,
    "tests/test_readers.py": 1,
    "tests/test_router_oauth_translate.py": 1,
    "tests/test_session_gateway.py": 1,
    "tests/test_world_contact_habit.py": 1,
    "tests/test_world_store.py": 1,
}

#: Total baseline: the sum of BASELINE_BLE001 (193 on 20 Nov 2026;
#: P-05 adds 1 recorded handler — the room job runner, see
#: docs/e20-blind-except-audit.md section 2.1).
BASELINE_TOTAL = 193


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _ruff_binary() -> str:
    found = shutil.which("ruff")
    if found:
        return found
    sibling = Path(sys.executable).with_name("ruff")
    if sibling.is_file():
        return str(sibling)
    raise AssertionError(
        "ruff binary not found on PATH or next to the test interpreter; "
        "install ruff==0.17.5 (requirements-dev.txt)"
    )


def _ble001_counts(root: Path) -> dict[str, int]:
    """Per-file BLE001 counts from ruff, keyed by repo-relative path."""
    result = subprocess.run(
        [
            _ruff_binary(),
            "check",
            "--select",
            "BLE001",
            "--output-format",
            "json",
            "--no-cache",
            "puspa",
            "tests",
            "demo",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        raise AssertionError(
            f"ruff BLE001 run failed (exit {result.returncode}):\n{result.stdout}\n{result.stderr}"
        )
    findings = json.loads(result.stdout or "[]")
    counts: dict[str, int] = {}
    for finding in findings:
        rel = str(Path(finding["filename"]).relative_to(root))
        counts[rel] = counts.get(rel, 0) + 1
    return counts


def test_blind_except_counts_only_shrink() -> None:
    """Per-file BLE001 counts never exceed the baseline; no new file gains one."""
    root = _repo_root()
    counts = _ble001_counts(root)
    violations = []
    for rel, count in sorted(counts.items()):
        if rel not in BASELINE_BLE001:
            violations.append(
                f"{rel}: {count} blind-except handler(s), not in BASELINE_BLE001 — "
                "narrow it, or record it here with a reason, instead of growing silently"
            )
        elif count > BASELINE_BLE001[rel]:
            violations.append(
                f"{rel}: {count} blind-except handler(s), above its baseline of "
                f"{BASELINE_BLE001[rel]} — blind-except handlers may only shrink"
            )
    total = sum(counts.values())
    if total > BASELINE_TOTAL:
        violations.append(
            f"total: {total} blind-except handler(s), above the baseline total of {BASELINE_TOTAL}"
        )
    assert not violations, "\n".join(violations)


def test_blind_except_baseline_entries_are_still_present() -> None:
    """Baseline entries that dropped to zero must be removed.

    A stale baseline entry hides the fact that the file is clean again
    and lets a later regrowth pass unnoticed.
    """
    root = _repo_root()
    counts = _ble001_counts(root)
    stale = [
        f"{rel}: now {counts.get(rel, 0)} handler(s) (baseline {baseline}) — "
        "remove it from BASELINE_BLE001"
        for rel, baseline in sorted(BASELINE_BLE001.items())
        if counts.get(rel, 0) == 0
    ]
    assert not stale, "\n".join(stale)


def test_blind_except_baseline_total_matches_entries() -> None:
    """BASELINE_TOTAL is the sum of the per-file entries (no silent drift)."""
    assert sum(BASELINE_BLE001.values()) == BASELINE_TOTAL, (
        f"BASELINE_BLE001 sums to {sum(BASELINE_BLE001.values())} "
        f"but BASELINE_TOTAL is {BASELINE_TOTAL}"
    )
