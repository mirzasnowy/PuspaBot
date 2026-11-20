"""Command line for the migration runner (E-08).

    python -m puspa.migrations up [--target 0006]
    python -m puspa.migrations down --to 0006
    python -m puspa.migrations status

Operator step: run as the database owner / superuser. ``down`` needs
an explicit ``--to`` (the version that stays applied; ``--to ""``
rolls back everything) so a rollback is never accidental.
"""

from __future__ import annotations

import argparse
import sys

from .runner import (
    MigrationError,
    apply_migrations,
    migration_status,
    rollback_migrations,
)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m puspa.migrations",
        description="Apply or roll back the numbered schema migrations.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    up = sub.add_parser("up", help="apply pending migrations")
    up.add_argument("--dsn", default=None, help="Postgres DSN")
    up.add_argument(
        "--target",
        default=None,
        help="stop after this version (default: apply all)",
    )

    down = sub.add_parser("down", help="roll back applied migrations")
    down.add_argument("--dsn", default=None, help="Postgres DSN")
    down.add_argument(
        "--to",
        required=True,
        help="version that stays applied; empty string rolls back all",
    )

    status = sub.add_parser("status", help="list migrations and their state")
    status.add_argument("--dsn", default=None, help="Postgres DSN")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.command == "up":
            applied = apply_migrations(dsn=args.dsn, target=args.target)
            if applied:
                print("applied: " + ", ".join(applied))
            else:
                print("up to date: no pending migrations")
        elif args.command == "down":
            rolled_back = rollback_migrations(dsn=args.dsn, target=args.to)
            if rolled_back:
                print("rolled back: " + ", ".join(rolled_back))
            else:
                print("nothing to roll back")
        elif args.command == "status":
            for row in migration_status(dsn=args.dsn):
                state = "applied" if row["applied"] else "pending"
                at = f" at {row['applied_at']}" if row["applied_at"] else ""
                print(f"{row['version']} {row['name']}: {state}{at}")
    except MigrationError as exc:
        print(f"migration error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
