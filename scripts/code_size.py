"""Code-line size ratchet (V2-41).

Counts CODE lines, not total lines, and refuses growth past a
recorded budget. Mechanism studied from nouverse/nouride
``scripts/check-size.ts`` (FSL-1.1-ALv2, verified 22 Nov 2026) and
rewritten as Puspa Python, never verbatim: the TypeScript code itself
was not taken (the FSL forbids competing use, and Puspa's hosted side
is competing use). What was taken is the shape of the rule:

- Code lines, not total lines. This repo's house style is dense
  documentation, so a total-line rule punishes exactly the files
  that explain themselves best. Blank lines and comment / docstring
  lines do not count.
- A cap of 400 code lines for source files (their median file is 84,
  p90 287) and 1000 for test files. A file over its cap without a
  budget fails; a new god file is refused outright.
- A RATCHET, not a threshold. Each oversized file is recorded at the
  size it is now in ``size_budgets.py``: it may shrink, it may not
  grow, and a budget more than ``HEADROOM`` lines above its file is
  slack that must be lowered. A file back under its cap loses its
  entry. Raising a budget is allowed, with its ``why`` rewritten.
- Every budget carries a short ``why`` (at most ``MAX_WHY``
  characters, one reason, today's). ``NEVER_BUDGETED`` lists files
  already split on their seams: a budget would let them grow back
  into one god file, so past the cap the new part goes in a file of
  its own.

This replaces the old total-line ratchet (2000-line hard cap /
1000-line soft cap), which was failing on main: it counted the
documentation along with the code, so the best-documented files
looked the largest, and it still let ``provider_login.py``,
``openapi-pwa.yaml`` and ``hands.py`` grow past it unrecorded.

Usage, from the repo root (also runnable as a plain script, standard
library only, so the CI checks lane can run it without dependencies)::

    python scripts/code_size.py            # exit 1 with findings
    python scripts/code_size.py --report   # every file over a cap

The same check runs in CI as ``tests/test_file_size_ratchet.py``.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from code_lines import count_code_lines
from size_budgets import (
    BUDGET,
    HEADROOM,
    MAX_WHY,
    NEVER_BUDGETED,
    TEST_BUDGET,
    Budget,
)

#: Code lines a source file may reach without a budget in ``BUDGET``.
MAX_CODE_LINES = 400

#: Code lines a test file may reach without a budget in ``TEST_BUDGET``.
#: Tests used to be exempt, and six grew past a thousand lines; a test
#: file that size is one nobody reads before adding to it.
MAX_TEST_CODE_LINES = 1000

#: Puspa-owned top-level directories covered by the ratchet.
SCOPED_DIRS = ("puspa", "tests", "apps", "cli", "web", "docs", "infra", "demo", "scripts")

#: File extensions counted (text source and docs only; binaries excluded).
COUNTED_SUFFIXES = {".py", ".md", ".sql", ".yml", ".yaml", ".json", ".ts", ".tsx", ".js"}

#: Directory names never descended into.
SKIPPED_DIRS = {
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".git",
    "nanobot",
    "web-replica",
    "backups",
    "cassettes",
    "node_modules",
    "dist",
}

#: Generated lockfiles: committed with the code they lock, but not
#: hand-written source, so the ratchet does not count their lines.
SKIPPED_FILES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    # Committed schema snapshot (E-08): `pg_dump -s` output, regenerated
    # from the migrations with one command (see its header). Generated
    # output, not hand-written source — it is 6000+ lines by nature.
    "schema_snapshot.sql",
}

#: Generated output (e.g. TypeScript types from the OpenAPI spec):
#: reproducible from a counted source file, so not counted itself.
GENERATED_PREFIX = "generated-"


@dataclass(frozen=True)
class Finding:
    """One ratchet violation: which file, which rule, and the detail."""

    file: str
    rule: str
    detail: str


def is_test(rel: str) -> bool:
    """Whether a repo-relative path is a test file (own cap, own budget table)."""
    parts = rel.split("/")
    name = parts[-1]
    return (
        "tests" in parts
        or name.startswith("test_")
        or name.endswith((".test.ts", ".test.tsx", ".test.js"))
    )


def cap_for(rel: str) -> int:
    """The code-line cap for one file: the test cap for tests, else the source cap."""
    return MAX_TEST_CODE_LINES if is_test(rel) else MAX_CODE_LINES


def budget_table_for(rel: str) -> dict[str, Budget]:
    """The budget table for one file: ``TEST_BUDGET`` for tests, else ``BUDGET``."""
    return TEST_BUDGET if is_test(rel) else BUDGET


def scoped_files(root: Path) -> list[Path]:
    """Every Puspa-owned file the ratchet counts, sorted."""
    files: list[Path] = []
    for top in SCOPED_DIRS:
        top_dir = root / top
        if not top_dir.is_dir():
            continue
        for path in sorted(top_dir.rglob("*")):
            if not path.is_file():
                continue
            if any(part in SKIPPED_DIRS for part in path.relative_to(root).parts):
                continue
            if path.name in SKIPPED_FILES:
                continue
            if path.name.startswith(GENERATED_PREFIX):
                continue
            if path.suffix not in COUNTED_SUFFIXES:
                continue
            files.append(path)
    # Root-level text files (README, NOTICE, helper scripts).
    for path in sorted(root.iterdir()):
        if (
            path.is_file()
            and path.name not in SKIPPED_FILES
            and not path.name.startswith(GENERATED_PREFIX)
            and path.suffix in COUNTED_SUFFIXES
        ):
            files.append(path)
    return files


def size_finding(
    code: int,
    cap: int,
    budget: Budget | None,
    table: str = "BUDGET",
) -> Finding | None:
    """What a file's size says against its cap and its budget, if anything.

    Both directions of the ratchet: a file may not grow past its
    budget, a budget more than ``HEADROOM`` above its file is slack
    that would let the file grow back unseen, and a file back under
    its cap loses its entry.
    """
    if budget is None:
        if code <= cap:
            return None
        return Finding(
            file="",
            rule="file-too-large",
            detail=(
                f"{code} lines of code, over the {cap} cap. Split it on a seam, or add it "
                f"to `{table}` in scripts/size_budgets.py with the reason it stays whole"
            ),
        )
    if code > budget.lines:
        return Finding(
            file="",
            rule="file-grew",
            detail=(
                f"{code} lines of code, over its budget of {budget.lines}. Split it, or raise "
                f"the budget in `{table}` and say in its `why` what the lines are and why "
                f"they belong here, in at most {MAX_WHY} characters"
            ),
        )
    if code <= cap:
        return Finding(
            file="",
            rule="budget-under-cap",
            detail=(
                f"{code} lines of code, under the {cap} cap — remove its entry from "
                f"`{table}`, or it may grow back to {budget.lines}"
            ),
        )
    if budget.lines - code > HEADROOM:
        return Finding(
            file="",
            rule="budget-slack",
            detail=(
                f"{code} lines of code against a budget of {budget.lines} — lower it to "
                f"{code}, or it may grow back unseen"
            ),
        )
    return None


def check_tree(root: Path) -> list[Finding]:
    """Every ratchet finding in the tree at ``root`` (empty means green)."""
    findings: list[Finding] = []
    seen: set[str] = set()
    for path in scoped_files(root):
        rel = str(path.relative_to(root))
        seen.add(rel)
        code = count_code_lines(path)
        cap = cap_for(rel)
        if rel in NEVER_BUDGETED:
            if code > cap:
                findings.append(
                    Finding(
                        file=rel,
                        rule="never-budgeted",
                        detail=(
                            f"{code} lines of code, over the {cap} cap. This file was split "
                            f"on its seam ({NEVER_BUDGETED[rel]}) and may not be budgeted "
                            "again: split the new part into a file of its own"
                        ),
                    )
                )
            continue
        table = budget_table_for(rel)
        table_name = "TEST_BUDGET" if is_test(rel) else "BUDGET"
        finding = size_finding(code, cap, table.get(rel), table_name)
        if finding is not None:
            findings.append(Finding(file=rel, rule=finding.rule, detail=finding.detail))
    for table_name, table in (("BUDGET", BUDGET), ("TEST_BUDGET", TEST_BUDGET)):
        for rel, budget in table.items():
            if rel not in seen:
                findings.append(
                    Finding(
                        file=rel,
                        rule="budget-stale",
                        detail=f"budgeted in `{table_name}` but the file is gone — remove the entry",
                    )
                )
            if not budget.why.strip():
                findings.append(
                    Finding(
                        file=rel,
                        rule="budget-why-missing",
                        detail=f"budget in `{table_name}` carries no `why` — one reason, today's",
                    )
                )
            elif len(budget.why) > MAX_WHY:
                findings.append(
                    Finding(
                        file=rel,
                        rule="budget-why-too-long",
                        detail=(
                            f"`why` is {len(budget.why)} characters, over the {MAX_WHY} limit — "
                            "one reason, today's, not a changelog"
                        ),
                    )
                )
    for rel in NEVER_BUDGETED:
        if rel not in seen:
            findings.append(
                Finding(
                    file=rel,
                    rule="never-budgeted-stale",
                    detail="listed in `NEVER_BUDGETED` but the file is gone — remove the entry",
                )
            )
    return findings


def _report(root: Path) -> str:
    """Every file over a cap, with its code lines and budget (for re-baselining)."""
    lines = ["file: code lines (cap / budget)"]
    for path in scoped_files(root):
        rel = str(path.relative_to(root))
        code = count_code_lines(path)
        cap = cap_for(rel)
        budget = budget_table_for(rel).get(rel)
        if code > cap or budget is not None or rel in NEVER_BUDGETED:
            budget_s = f" / {budget.lines}" if budget is not None else ""
            never = " (never-budgeted)" if rel in NEVER_BUDGETED else ""
            lines.append(f"{rel}: {code} ({cap}{budget_s}){never}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Run the ratchet from the command line: 0 green, 1 violations."""
    parser = argparse.ArgumentParser(description="Code-line size ratchet (V2-41).")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repo root (default: cwd)")
    parser.add_argument(
        "--report", action="store_true", help="list every file over a cap instead of checking"
    )
    args = parser.parse_args(argv)
    if args.report:
        print(_report(args.root))
        return 0
    findings = check_tree(args.root)
    if not findings:
        print("code-size ratchet: green")
        return 0
    for finding in findings:
        print(f"{finding.file}: {finding.rule}: {finding.detail}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
