"""CLA sign-off checker (GitHub issue #28).

Reads the comments on a pull request — the JSON shape the GitHub
REST ``list issue comments`` endpoint returns — and decides whether
the PR author signed the Puspa CLA (``CLA.md``).

Signing mechanism (the only one; see ``CONTRIBUTING.md``): the PR
author posts a comment on their own pull request containing exactly
this sentence:

    I have read the Puspa Contributor License Agreement (CLA.md) and I agree to its terms.

The check is per pull request: one signing comment on that PR covers
that PR. Maintainers (``MAINTAINERS`` below) and bot accounts never
need to sign; the bot still records the exemption so the reason is
visible in the workflow log.

Usage (from the repo root; run by ``.github/workflows/cla.yml``):

    python scripts/cla_check.py --pr-author <login> --comments comments.json

Exit codes: 0 signed or exempt, 1 not signed, 2 input error (bad
JSON, wrong shape, missing author). The script uses only the standard
library, so the workflow needs no dependency install to run it.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

#: The exact sentence a contributor posts to sign the CLA. The check
#: matches this sentence as a substring of the comment (after
#: collapsing whitespace), so a comment may carry extra text around
#: it, but the sentence itself must appear word for word. Anything
#: shorter ("I agree to the CLA", "I signed") does NOT count: the
#: sentence names the agreement, so a vague "I agree" cannot be
#: mistaken for signing it.
AGREEMENT_SENTENCE = (
    "I have read the Puspa Contributor License Agreement (CLA.md) and I agree to its terms."
)

#: Maintainers never sign: they already act for the project, and
#: requiring their own signature on every PR would add friction with
#: no legal gain. Edit this set when maintainership changes; it is
#: the single list the bot trusts.
MAINTAINERS = frozenset({"mirzasnowy"})

#: Well-known bot accounts, exempt for the same reason as
#: maintainers. Any login ending in ``[bot]`` is also exempt (see
#: ``is_bot_login``), so this set only needs names that do not carry
#: the suffix.
KNOWN_BOTS = frozenset({"dependabot[bot]", "github-actions[bot]"})

EXIT_SIGNED = 0
EXIT_NOT_SIGNED = 1
EXIT_INPUT_ERROR = 2


@dataclass(frozen=True)
class Verdict:
    """The check outcome, plus the human-readable reason for it."""

    status: str  # "signed" | "exempt" | "not_signed"
    reason: str


def _collapse_whitespace(text: str) -> str:
    """Collapse every run of whitespace to one space, then strip."""
    return " ".join(text.split())


def is_signing_comment(body: object) -> bool:
    """True when a comment body contains the exact agreement sentence.

    The sentence is matched case-insensitively after collapsing
    whitespace, so line breaks or extra spaces around it do not
    matter, but no word of the sentence may be missing or changed.
    A non-string body (a malformed comment payload) is never a
    signature.
    """
    if not isinstance(body, str):
        return False
    return _collapse_whitespace(AGREEMENT_SENTENCE).lower() in (_collapse_whitespace(body).lower())


def comment_author(comment: object) -> str | None:
    """The login that posted a comment, or None when it is missing.

    GitHub returns ``{"user": {"login": "..."}, "body": "..."}``; a
    comment without a usable login cannot be attributed to the PR
    author, so it is ignored rather than treated as a signature.
    """
    if not isinstance(comment, dict):
        return None
    user = comment.get("user")
    if not isinstance(user, dict):
        return None
    login = user.get("login")
    return login if isinstance(login, str) and login else None


def is_bot_login(login: str) -> bool:
    """True for GitHub bot accounts: the ``[bot]`` suffix or a known name."""
    lowered = login.lower()
    return lowered.endswith("[bot]") or lowered in KNOWN_BOTS


def check_pr(
    pr_author: str,
    comments: list[object],
    maintainers: frozenset[str] = MAINTAINERS,
) -> Verdict:
    """Decide whether a PR author signed the CLA.

    ``comments`` is the list of comment objects from the GitHub REST
    ``list issue comments`` endpoint (``{"user": {"login": ...},
    "body": ...}`` per comment). Only a comment posted by the PR
    author themself, containing the exact agreement sentence,
    counts: a signature from anyone else is not the author's
    agreement.
    """
    if not isinstance(pr_author, str) or not pr_author:
        raise ValueError("pr_author must be a non-empty login string")
    author = pr_author.lower()
    if author in {name.lower() for name in maintainers}:
        return Verdict("exempt", f"@{pr_author} is a maintainer and never needs to sign.")
    if is_bot_login(pr_author):
        return Verdict("exempt", f"@{pr_author} is a bot account and never needs to sign.")
    own_comments: list[dict] = [
        comment
        for comment in comments
        if isinstance(comment, dict)
        and (login := comment_author(comment)) is not None
        and login.lower() == author
    ]
    if any(is_signing_comment(comment.get("body")) for comment in own_comments):
        return Verdict(
            "signed",
            f"@{pr_author} signed the CLA in a comment on this pull request.",
        )
    if not own_comments:
        return Verdict(
            "not_signed",
            f"@{pr_author} has not commented on this pull request yet; no signing comment found.",
        )
    return Verdict(
        "not_signed",
        f"@{pr_author} commented {len(own_comments)} time(s) on this pull request, "
        "but none of those comments contains the exact CLA agreement sentence.",
    )


def load_comments(path: Path) -> list[object]:
    """Load a comments JSON file; it must be a JSON array of objects."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        # The file is unreadable (missing, a directory, no permission):
        # an input error, not a "not signed" verdict.
        raise ValueError(f"cannot read comments file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"comments file {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError(
            f"comments file {path} must contain a JSON array, got {type(data).__name__}"
        )
    return data


def build_parser() -> argparse.ArgumentParser:
    """The command-line interface (see the module docstring)."""
    parser = argparse.ArgumentParser(
        description="Check whether a pull request author signed the Puspa CLA."
    )
    parser.add_argument("--pr-author", required=True, help="GitHub login of the PR author")
    parser.add_argument(
        "--comments",
        required=True,
        type=Path,
        help="Path to a JSON file with the PR comments (GitHub REST list-comments shape)",
    )
    parser.add_argument(
        "--maintainers",
        default=",".join(sorted(MAINTAINERS)),
        help="Comma-separated maintainer logins (default: %(default)s)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the check; see the module docstring for the exit codes."""
    args = build_parser().parse_args(argv)
    maintainers = frozenset(name.strip() for name in args.maintainers.split(",") if name.strip())
    try:
        comments = load_comments(args.comments)
        verdict = check_pr(args.pr_author, comments, maintainers)
    except ValueError as exc:
        print(f"CLA check input error: {exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    print(f"CLA check: {verdict.status} — {verdict.reason}")
    return EXIT_SIGNED if verdict.status in {"signed", "exempt"} else EXIT_NOT_SIGNED


if __name__ == "__main__":
    raise SystemExit(main())
