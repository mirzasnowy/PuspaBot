"""One-command rollback for a Puspa release (E-21, plan 9.6).

A release is one pinned image tag (``puspa:<git sha>`` or
``puspa:v<semver>``). Rolling back means pointing the compose file
at the previous pinned tag and restarting the containers — no new
code, no data migration (migrations are expand-then-contract, so
the old image still runs against the current schema; see
``docs/feature-flags-canary.md``).

Usage (from the repo root):

    # Dry run (default): validate the tag, show what would change,
    # and print the exact command that would run. Changes nothing.
    python scripts/rollback.py --to-tag puspa:81016e3a

    # Apply: rewrite the image lines in docker-compose.yml to the
    # pinned tag and print the restart command.
    python scripts/rollback.py --to-tag puspa:81016e3a --apply

    # Apply and restart the containers right away (needs Docker).
    python scripts/rollback.py --to-tag puspa:81016e3a --apply --run

The tag is validated before anything is touched: ``latest``,
``local``, and anything that is not a full git sha or a ``v``
release tag is refused, because rolling back to an unpinned tag
is not a rollback — it is a guess about what used to run. Only
``image:`` lines that already name a ``puspa:`` image are
rewritten; other images (Postgres in the dev compose file, for
example) are left alone.

This script has never run against a real container host: Docker
cannot run on the dev VM, so ``--run`` is untested live and the
dry-run path is the one the tests cover. The first real rollback
must be done with the doc open (``docs/feature-flags-canary.md``),
not from memory.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

#: A pinned Puspa image tag: a git sha (7-40 hex chars) or a
#: version tag (``v0.1.0``, ``v0.1.0-beta``). ``local`` and
#: ``latest`` are deliberately not in this shape.
PINNED_TAG_RE = re.compile(r"^puspa:(?:[0-9a-f]{7,40}|v\d+\.\d+\.\d+(?:[-.][A-Za-z0-9]+)*)$")

#: One ``image:`` line in a compose file. Only ``puspa:`` images
#: are ever rewritten by this script.
IMAGE_LINE_RE = re.compile(r"^(\s*image:\s*)(\S+)\s*$")


class RollbackError(ValueError):
    """The requested rollback is not safe to perform."""


def validate_tag(tag: str) -> str:
    """Return ``tag`` when it is a pinned Puspa image tag, else raise.

    Refuses ``puspa:latest``, ``puspa:local``, unprefixed tags, and
    anything that is not a git sha or a ``v`` release tag.
    """
    if not PINNED_TAG_RE.match(tag or ""):
        raise RollbackError(
            f"refusing unpinned image tag {tag!r}: a rollback target must be "
            "puspa:<git sha> or puspa:v<semver> (never 'latest' or 'local')"
        )
    return tag


def rewrite_images(text: str, tag: str) -> tuple[str, list[tuple[int, str, str]]]:
    """Rewrite every ``puspa:`` image line to ``tag``.

    Returns the new text and the list of ``(line, old, new)``
    changes. Raises :class:`RollbackError` when the file has no
    ``puspa:`` image line at all — there is nothing to roll back.
    """
    changes: list[tuple[int, str, str]] = []
    out: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        match = IMAGE_LINE_RE.match(line)
        if match and match.group(2).startswith("puspa:"):
            out.append(f"{match.group(1)}{tag}")
            changes.append((number, match.group(2), tag))
        else:
            out.append(line)
    if not changes:
        raise RollbackError("no 'image: puspa:...' line found in the compose file")
    return "\n".join(out) + "\n", changes


def restart_command(compose_file: Path) -> list[str]:
    """The exact command that restarts the stack from the file."""
    return ["docker", "compose", "-f", str(compose_file), "up", "-d"]


def main(argv: list[str] | None = None) -> int:
    """Entry point: validate, then dry-run or apply. Returns an exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--to-tag",
        required=True,
        help="pinned image tag to roll back to, e.g. puspa:81016e3a",
    )
    parser.add_argument(
        "--compose-file",
        type=Path,
        default=Path("docker-compose.yml"),
        help="compose file whose puspa: image lines are rewritten",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="rewrite the compose file (default is a dry run that changes nothing)",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="also run 'docker compose up -d' after applying (needs --apply and Docker)",
    )
    args = parser.parse_args(argv)

    try:
        tag = validate_tag(args.to_tag)
    except RollbackError as exc:
        print(f"rollback refused: {exc}", file=sys.stderr)
        return 2
    if args.run and not args.apply:
        print("rollback refused: --run needs --apply", file=sys.stderr)
        return 2
    if not args.compose_file.is_file():
        print(f"rollback refused: compose file not found: {args.compose_file}", file=sys.stderr)
        return 2
    text = args.compose_file.read_text(encoding="utf-8")
    try:
        new_text, changes = rewrite_images(text, tag)
    except RollbackError as exc:
        print(f"rollback refused: {exc}", file=sys.stderr)
        return 2

    for number, old, new in changes:
        print(f"  line {number}: {old} -> {new}")
    command = restart_command(args.compose_file)
    if not args.apply:
        print("dry run: nothing changed. To apply:")
        print(f"  python scripts/rollback.py --to-tag {tag} --apply")
        print("then restart with:")
        print(f"  {' '.join(command)}")
        return 0
    args.compose_file.write_text(new_text, encoding="utf-8")
    print(f"rewrote {args.compose_file}: {len(changes)} image line(s) now {tag}")
    if not args.run:
        print("restart with:")
        print(f"  {' '.join(command)}")
        return 0
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        print(
            "docker compose failed — the compose file already points at "
            f"{tag}, so re-run the command above by hand: {' '.join(command)}",
            file=sys.stderr,
        )
        return result.returncode or 1
    print("rolled back: containers are running the previous image")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
