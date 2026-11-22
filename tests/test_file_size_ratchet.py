"""Code-line size ratchet (V2-41).

Rules, implemented in ``scripts/code_size.py`` (mechanism studied from
nouverse/nouride ``scripts/check-size.ts``, rewritten as Puspa Python,
never verbatim):

- Code lines, not total lines: blank lines and comment / docstring
  lines do not count, so the best-documented files are not punished
  for explaining themselves.
- A source file may reach 400 code lines, a test file 1000, without a
  budget. A file over its cap without a budget fails; a new god file
  is refused outright.
- A RATCHET, not a threshold: each oversized file is recorded at its
  current size in ``scripts/size_budgets.py`` with one short ``why``
  (at most 200 characters, today's reason). It may shrink, it may not
  grow, and a budget more than 10 lines above its file is slack that
  must be lowered. A file back under its cap loses its entry.
- ``NEVER_BUDGETED`` lists files already split on their seams: a
  budget would let them grow back into one god file, so past the cap
  the new part goes in a file of its own.

This test uses only the standard library and touches no database, so
it also runs in CI's dependency-free ``checks`` lane with
``--noconftest``. See ``docs/code-size-ratchet.md`` for the rule in
full.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# --noconftest skips the repo conftest, so replicate the one path setup
# this test needs: the ratchet lives in scripts/, not in the package.
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from code_lines import count_code_lines
from code_size import (
    HEADROOM,
    MAX_CODE_LINES,
    MAX_TEST_CODE_LINES,
    budget_table_for,
    cap_for,
    check_tree,
    is_test,
    scoped_files,
    size_finding,
)
from size_budgets import BUDGET, MAX_WHY, NEVER_BUDGETED, TEST_BUDGET, Budget


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def test_ratchet_is_green() -> None:
    """The whole tree, as it stands, satisfies the ratchet."""
    findings = check_tree(_repo_root())
    assert not findings, "\n".join(f"{f.file}: {f.rule}: {f.detail}" for f in findings)


def test_new_god_file_is_refused() -> None:
    """A file over the cap without a budget fails, in a scratch tree."""
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp) / "puspa"
        tree.mkdir()
        god = tree / "god.py"
        god.write_text("\n".join(f"x_{i} = {i}" for i in range(MAX_CODE_LINES + 1)))
        findings = check_tree(Path(tmp))
        rules = {(f.file, f.rule) for f in findings}
        assert ("puspa/god.py", "file-too-large") in rules


def test_never_budgeted_file_over_cap_is_refused() -> None:
    """A split file may not be budgeted back: over the cap it fails."""
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp) / "puspa" / "world"
        tree.mkdir(parents=True)
        split = tree / "beliefs.py"
        split.write_text("\n".join(f"x_{i} = {i}" for i in range(MAX_CODE_LINES + 1)))
        findings = check_tree(Path(tmp))
        rules = {(f.file, f.rule) for f in findings}
        assert ("puspa/world/beliefs.py", "never-budgeted") in rules


def test_budget_tables_are_hygienic() -> None:
    """Every budget has one short why, sits above its cap, and names a real file."""
    root = _repo_root()
    for table_name, table, cap in (
        ("BUDGET", BUDGET, MAX_CODE_LINES),
        ("TEST_BUDGET", TEST_BUDGET, MAX_TEST_CODE_LINES),
    ):
        for rel, budget in table.items():
            assert budget.why.strip(), f"{rel}: budget in {table_name} carries no `why`"
            assert len(budget.why) <= MAX_WHY, (
                f"{rel}: `why` is {len(budget.why)} characters, over the {MAX_WHY} limit"
            )
            assert budget.lines > cap, (
                f"{rel}: budget of {budget.lines} sits under the {cap} cap — "
                "a file under the cap needs no budget"
            )
            assert (root / rel).is_file(), f"{rel}: budgeted in {table_name} but the file is gone"
    assert not (set(BUDGET) & set(NEVER_BUDGETED)), (
        "a file cannot be both budgeted and never-budgeted: "
        + ", ".join(sorted(set(BUDGET) & set(NEVER_BUDGETED)))
    )
    assert not (set(TEST_BUDGET) & set(NEVER_BUDGETED)), (
        "a file cannot be both budgeted and never-budgeted: "
        + ", ".join(sorted(set(TEST_BUDGET) & set(NEVER_BUDGETED)))
    )
    for rel, reason in NEVER_BUDGETED.items():
        assert reason.strip(), f"{rel}: NEVER_BUDGETED entry carries no reason"
        assert len(reason) <= MAX_WHY, f"{rel}: NEVER_BUDGETED reason is over {MAX_WHY} characters"
        assert (root / rel).is_file(), f"{rel}: listed in NEVER_BUDGETED but the file is gone"


def test_never_budgeted_files_are_under_cap() -> None:
    """Every split file is under its cap today (else the ratchet would already fail)."""
    root = _repo_root()
    for rel in NEVER_BUDGETED:
        code = count_code_lines(root / rel)
        assert code <= cap_for(rel), (
            f"{rel}: {code} code lines, over the {cap_for(rel)} cap — "
            "split it again, it may not be budgeted"
        )


def test_budgets_have_no_slack() -> None:
    """No budget sits more than HEADROOM above its file (else it hides regrowth)."""
    root = _repo_root()
    for table in (BUDGET, TEST_BUDGET):
        for rel, budget in table.items():
            code = count_code_lines(root / rel)
            assert budget.lines - code <= HEADROOM, (
                f"{rel}: {code} code lines against a budget of {budget.lines} — "
                f"lower it to {code}, or it may grow back unseen"
            )


def test_test_cap_applies_to_test_files_only() -> None:
    """The 1000-line cap is for tests; a source file over 400 is not a test file."""
    assert is_test("tests/test_router_oauth_translate.py")
    assert is_test("apps/pwa/src/api/client.test.ts")
    assert is_test("puspa/router/test_helper.py")
    assert not is_test("puspa/router/pool.py")
    assert not is_test("docs/openapi-pwa.yaml")
    assert cap_for("tests/test_router_oauth_translate.py") == MAX_TEST_CODE_LINES
    assert cap_for("puspa/router/pool.py") == MAX_CODE_LINES
    assert budget_table_for("tests/test_router_oauth_translate.py") is TEST_BUDGET
    assert budget_table_for("puspa/router/pool.py") is BUDGET


def test_size_finding_rules() -> None:
    """Each outcome of the ratchet, both directions, at the boundaries."""
    over = MAX_CODE_LINES + 1
    finding = size_finding(over, MAX_CODE_LINES, None)
    assert finding is not None and finding.rule == "file-too-large"
    assert size_finding(MAX_CODE_LINES, MAX_CODE_LINES, None) is None
    assert size_finding(over, MAX_CODE_LINES, Budget(over, "why")) is None
    finding = size_finding(over + 1, MAX_CODE_LINES, Budget(over, "why"))
    assert finding is not None and finding.rule == "file-grew"
    finding = size_finding(MAX_CODE_LINES, MAX_CODE_LINES, Budget(over, "why"))
    assert finding is not None and finding.rule == "budget-under-cap"
    assert size_finding(over, MAX_CODE_LINES, Budget(over + HEADROOM, "why")) is None
    finding = size_finding(over, MAX_CODE_LINES, Budget(over + HEADROOM + 1, "why"))
    assert finding is not None and finding.rule == "budget-slack"


def test_count_python_excludes_blanks_comments_and_docstrings(tmp_path: Path) -> None:
    """Python counting: only lines holding a real token count."""
    source = '''"""Module docstring, two lines.

    Second line.
    """


# A full-line comment.
import os  # trailing comment still counts: the import is code.

VALUE = 1


def f():
    """Function docstring."""
    return VALUE + len(
        "multi-"
        "line"
    )
'''
    path = tmp_path / "sample.py"
    path.write_text(source)
    # import os, VALUE = 1, def f():, return VALUE + len(, "multi-", "line", )
    assert count_code_lines(path) == 7


def test_count_python_multiline_string_counts(tmp_path: Path) -> None:
    """A multi-line string that is not a docstring is a value: its lines count."""
    path = tmp_path / "sample.py"
    path.write_text('TEXT = """one\ntwo\nthree"""\n')
    assert count_code_lines(path) == 3


def test_count_js_like_excludes_comments_and_strings(tmp_path: Path) -> None:
    """JS/TS counting: comments and string contents do not count, ${} code does."""
    source = """// A full-line comment.
import { x } from "./y"; /* trailing block comment */

/* A block comment,
   over two lines. */
const s = "not // a comment";
const t = `template ${x +
  1} end`;
"""
    path = tmp_path / "sample.ts"
    path.write_text(source)
    # import, const s, const t (two lines: the ${} expression spans two lines)
    assert count_code_lines(path) == 4


def test_count_yaml_sql_markdown_json(tmp_path: Path) -> None:
    """The other counters: full-line comments and blanks out, content in."""
    yaml_path = tmp_path / "sample.yaml"
    yaml_path.write_text("# comment\n\nkey: value  # trailing comment counts\nlist:\n  - one\n")
    assert count_code_lines(yaml_path) == 3
    sql_path = tmp_path / "sample.sql"
    sql_path.write_text(
        "-- comment\n\nCREATE TABLE t (\n  id INT -- trailing\n);\n/* block\ncomment */\nSELECT 1;\n"
    )
    assert count_code_lines(sql_path) == 4
    md_path = tmp_path / "sample.md"
    md_path.write_text(
        "# Title\n\nSome prose.\n\n<!-- a comment\nspanning lines -->\nMore prose.\n"
    )
    assert count_code_lines(md_path) == 3
    json_path = tmp_path / "sample.json"
    json_path.write_text('{\n  "a": 1,\n\n  "b": 2\n}\n')
    assert count_code_lines(json_path) == 4


def test_scoped_files_cover_scripts_and_skip_generated(tmp_path: Path) -> None:
    """Scope: scripts/ is counted, generated output and lockfiles are not."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "tool.py").write_text("x = 1\n")
    (tmp_path / "scripts" / "generated-tool.py").write_text("x = 1\n")
    (tmp_path / "package-lock.json").write_text("{}\n")
    rels = {str(p.relative_to(tmp_path)) for p in scoped_files(tmp_path)}
    assert "scripts/tool.py" in rels
    assert "scripts/generated-tool.py" not in rels
    assert "package-lock.json" not in rels
