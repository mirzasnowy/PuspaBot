"""PR lane runner (E-17, plan section 9.1).

Runs only the tests affected by a change, on 4 xdist workers with
one database each (see tests/worker_db.py), excluding the live
tests (those run in the nightly lane). The affected set comes
from ``scripts/affected.py``: the reverse import closure of the
changed files, computed from Puspa's own import graph.

Usage:

    python scripts/pr_lane.py --base <sha> --head <sha>
    python scripts/pr_lane.py --changed-files <file>
    python scripts/pr_lane.py --full        # full suite, 4 workers
    python scripts/pr_lane.py --dry-run     # print the selection only

Target (plan section 9.1): the lane finishes in under 8 minutes.
The full suite target is under 10 minutes with 4 workers; measure
with ``--full``. Both targets are measured on the machine that
runs them — a 1.5-CPU dev VM is not CI, so record the machine
with the number.

Exit code: pytest's exit code (0 when the lane passes, or when
no test is affected).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import affected as affected_mod

DEFAULT_WORKERS = 4


def _sanitize_proxy_env() -> None:
    """The dirty default NO_PROXY breaks httpx (see TOOLS.md)."""
    os.environ["NO_PROXY"] = "localhost,127.0.0.1"
    os.environ["no_proxy"] = "localhost,127.0.0.1"


def _run_pytest(root: Path, workers: int, paths: list[Path]) -> int:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-n",
        str(workers),
        "-m",
        "not live",
        "--dist",
        "loadfile",
        "-q",
    ]
    command.extend(path.relative_to(root).as_posix() for path in paths)
    started = time.monotonic()
    result = subprocess.run(command, cwd=root, check=False)
    elapsed = time.monotonic() - started
    print(f"pr-lane: pytest exit {result.returncode} in {elapsed:.1f}s", file=sys.stderr)
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the PR lane: affected tests only.")
    parser.add_argument("--root", default=None, help="repo root (default: parent of scripts/)")
    parser.add_argument("--base", default=None, help="diff base ref")
    parser.add_argument("--head", default="HEAD", help="diff head ref")
    parser.add_argument("--changed-files", default=None, help="file with changed paths")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="xdist workers")
    parser.add_argument("--full", action="store_true", help="run the full suite instead")
    parser.add_argument("--dry-run", action="store_true", help="print the selection, run nothing")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent
    _sanitize_proxy_env()

    graph = affected_mod.ImportGraph(root)
    graph.build()
    if args.full:
        selected = affected_mod._all_test_files(root)
        reason = "full-suite:explicit"
        packages: list[str] = []
    else:
        if args.changed_files:
            changed = [
                line.strip()
                for line in Path(args.changed_files).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        else:
            changed = affected_mod._changed_from_git(root, args.base, args.head)
        selected, reason = affected_mod.affected_tests(graph, changed)
        packages = affected_mod.affected_packages(graph, changed)

    print(f"pr-lane: selection {reason}", file=sys.stderr)
    print(
        f"pr-lane: affected packages: {', '.join(packages) if packages else '(none)'}",
        file=sys.stderr,
    )
    print(f"pr-lane: {len(selected)} test file(s)", file=sys.stderr)
    for path in selected:
        print(path.relative_to(root).as_posix())
    if args.dry_run:
        return 0
    if not selected:
        print("pr-lane: no tests affected; nothing to run", file=sys.stderr)
        return 0
    return _run_pytest(root, args.workers, selected)


if __name__ == "__main__":
    raise SystemExit(main())
