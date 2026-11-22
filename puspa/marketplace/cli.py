"""``puspa skill`` command group (COW-03): the hub on the command line.

- ``puspa skill list [--query ...]`` — the locally browsable
  skills (shipped + installed), one line each.
- ``puspa skill search <query>`` — same list, filtered.
- ``puspa skill install <source> [--yes]`` — install a skill
  from GitHub (``owner/repo``), ClawHub (``clawhub:owner/slug``)
  or a direct ``SKILL.md`` URL. Without ``--yes`` it only prints
  the preview (name, licence, content hash) and stops: the
  install itself needs the explicit ``--yes``, which is the
  approval for that exact previewed content.

Installed skills land under the user's Puspa home
(``<home>/skills``). The local user id for the registry's audit
trail is ``local``: the CLI is the single-user local operator,
not a logged-in PWA user.
"""

from __future__ import annotations

import argparse
import json
import sys

from puspa.marketplace.discovery import InstallApproval
from puspa.marketplace.errors import MarketplaceError
from puspa.marketplace.hub import (
    install_from_source,
    list_hub_skills,
    preview_source,
)
from puspa.marketplace.registry import MarketplaceRegistry

__all__ = [
    "LOCAL_USER_ID",
    "add_skill_parser",
    "run_skill",
]

#: User id recorded in the registry's audit trail for CLI installs.
LOCAL_USER_ID = "local"


def add_skill_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Add the ``skill`` group to the top-level parser."""
    skill = subparsers.add_parser(
        "skill",
        help="Skill marketplace hub: list, search, and install skills (COW-03).",
        description=(
            "Browse the skills on this machine and install new ones from "
            "GitHub (owner/repo), ClawHub (clawhub:owner/skill), or a direct "
            "SKILL.md URL. Every install previews first and only proceeds "
            "with --yes."
        ),
    )
    skill_sub = skill.add_subparsers(dest="skill_command", metavar="SKILL_COMMAND")
    list_cmd = skill_sub.add_parser(
        "list",
        help="List the locally browsable skills.",
        description="List the skills shipped with Puspa plus the ones installed here.",
    )
    list_cmd.add_argument(
        "--query",
        default=None,
        metavar="TEXT",
        help="Only skills matching this text (id, name, description).",
    )
    search_cmd = skill_sub.add_parser(
        "search",
        help="Search the locally browsable skills.",
        description="Search the skills shipped with Puspa plus the ones installed here.",
    )
    search_cmd.add_argument("query", help="Text to match (id, name, description).")
    install_cmd = skill_sub.add_parser(
        "install",
        help="Install a skill from GitHub, ClawHub, or a URL.",
        description=(
            "Install a skill from GitHub (owner/repo), ClawHub "
            "(clawhub:owner/skill), or a direct SKILL.md URL. Without --yes "
            "it only prints the preview and stops."
        ),
    )
    install_cmd.add_argument(
        "source",
        help="Skill source: owner/repo, clawhub:owner/skill, or a SKILL.md URL.",
    )
    install_cmd.add_argument(
        "--yes",
        action="store_true",
        help="Confirm the install of the exact previewed content.",
    )
    return skill


def _print_skills(skills, *, print_fn) -> int:
    if not skills:
        print_fn("No skills found.")
        return 0
    for skill in skills:
        print_fn(
            f"{skill.skill_id}  {skill.name}  v{skill.version}  "
            f"{skill.origin}  {skill.license}  {skill.source}"
        )
        if skill.description:
            print_fn(f"    {skill.description}")
    return 0


def _skills_root():
    from puspa.sessions.home import resolve_home

    return resolve_home() / "skills"


def run_skill(args: argparse.Namespace, *, print_fn=print) -> int:
    """Run one ``puspa skill`` command. Returns an exit code."""
    if args.skill_command in ("list", "search"):
        query = getattr(args, "query", None)
        try:
            skills = list_hub_skills(MarketplaceRegistry(), query=query)
        except MarketplaceError as exc:
            print(f"skill list failed: {exc}", file=sys.stderr)
            return 1
        return _print_skills(skills, print_fn=print_fn)
    if args.skill_command == "install":
        return _run_install(args, print_fn=print_fn)
    return 0


def _run_install(args: argparse.Namespace, *, print_fn) -> int:
    try:
        fetched = preview_source(args.source)
    except MarketplaceError as exc:
        print(f"install failed: {exc}", file=sys.stderr)
        return 1
    print_fn(f"Skill: {fetched.name} ({fetched.slug})")
    print_fn(f"Source: {fetched.source_label}")
    print_fn(f"Licence: {fetched.license or 'unknown (install will be refused)'}")
    print_fn(f"SHA256: {fetched.sha256}")
    if fetched.description:
        print_fn(f"Description: {fetched.description}")
    if not args.yes:
        print_fn("Preview only — nothing installed. Re-run with --yes to install.")
        return 2
    approval = InstallApproval.for_skill(fetched.slug, fetched.sha256)
    try:
        installed = install_from_source(
            MarketplaceRegistry(),
            args.source,
            user_id=LOCAL_USER_ID,
            approval=approval,
            expected_sha256=fetched.sha256,
            skills_root=_skills_root(),
        )
    except MarketplaceError as exc:
        print(f"install failed: {exc}", file=sys.stderr)
        return 1
    print_fn(f"Installed: {installed['skill_id']} v{installed['version']} ({installed['status']})")
    print_fn(json.dumps(installed, ensure_ascii=False))
    return 0
