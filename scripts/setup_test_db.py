"""Set up the test databases (E-17, plan section 9.1).

Builds (or reuses) the migrated template database that the
pytest-xdist workers clone from. The pytest hooks in
``tests/conftest.py`` do this automatically on every ``-n`` run;
this script is for explicit setup (CI lanes, debugging) and for
cleaning up worker databases left behind by crashed runs.

Usage:

    python scripts/setup_test_db.py [--dsn DSN] [--prune-stale]

The base DSN comes from ``--dsn``, then ``PUSPA_TEST_DSN``,
then the peer-auth default.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from worker_db import ensure_template_dsn, prune_stale_worker_dbs, template_db_name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the per-worker test template database.")
    parser.add_argument("--dsn", default=None, help="base DSN (default: PUSPA_TEST_DSN)")
    parser.add_argument(
        "--prune-stale",
        action="store_true",
        help="drop worker databases from crashed runs (older than 2 hours and idle)",
    )
    args = parser.parse_args(argv)
    template_dsn = ensure_template_dsn(args.dsn)
    print(f"template ready: {template_db_name(args.dsn)} ({template_dsn})")
    if args.prune_stale:
        dropped = prune_stale_worker_dbs(args.dsn)
        for name in dropped:
            print(f"pruned stale worker database: {name}")
        if not dropped:
            print("no stale worker databases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
