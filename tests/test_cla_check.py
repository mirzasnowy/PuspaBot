"""Tests for the CLA sign-off checker (GitHub issue #28).

The checker (``scripts/cla_check.py``) decides, from a pull
request's comments in the GitHub REST shape, whether the PR author
signed the CLA by posting the agreement sentence as a comment. The
tests cover the decision (signed, not signed, invalid signature,
maintainer/bot exemption), the CLI exit codes, and the workflow
file that runs the checker. No database, no network: the PR
payloads are simulated here.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

BUILD_ROOT = Path(__file__).resolve().parent.parent
CHECKER_PATH = BUILD_ROOT / "scripts" / "cla_check.py"
WORKFLOW_PATH = BUILD_ROOT / ".github" / "workflows" / "cla.yml"
GUIDANCE_PATH = BUILD_ROOT / ".github" / "cla-guidance.md"
CLA_PATH = BUILD_ROOT / "CLA.md"
CONTRIBUTING_PATH = BUILD_ROOT / "CONTRIBUTING.md"

SENTENCE = "I have read the Puspa Contributor License Agreement (CLA.md) and I agree to its terms."


def load_checker():
    """Load scripts/cla_check.py as a module (scripts/ is no package).

    The module is registered in sys.modules before executing it:
    the @dataclass in the checker resolves its annotations through
    sys.modules, and an unregistered module breaks that lookup.
    """
    spec = importlib.util.spec_from_file_location("cla_check_under_test", CHECKER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["cla_check_under_test"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def checker():
    return load_checker()


def comment(login: str, body: str) -> dict:
    """One comment in the GitHub REST list-comments shape."""
    return {"user": {"login": login}, "body": body}


# --- The decision -------------------------------------------------


def test_signed_exact_sentence(checker) -> None:
    """A comment with exactly the agreement sentence signs the PR."""
    verdict = checker.check_pr("alice", [comment("alice", SENTENCE)])
    assert verdict.status == "signed"


def test_signed_sentence_with_surrounding_text(checker) -> None:
    """Extra text around the sentence is fine; the sentence is what counts."""
    verdict = checker.check_pr(
        "alice",
        [comment("alice", f"Thanks for the review!\n\n{SENTENCE}\n\nHappy to help.")],
    )
    assert verdict.status == "signed"


def test_signed_sentence_across_line_breaks(checker) -> None:
    """The sentence split over lines still counts (whitespace collapses)."""
    broken = SENTENCE.replace(" (CLA.md) and", " (CLA.md)\nand")
    verdict = checker.check_pr("alice", [comment("alice", broken)])
    assert verdict.status == "signed"


def test_not_signed_no_comments(checker) -> None:
    """No comments at all means not signed."""
    verdict = checker.check_pr("alice", [])
    assert verdict.status == "not_signed"
    assert "alice" in verdict.reason


def test_not_signed_other_person_signed(checker) -> None:
    """A signature from anyone but the PR author does not count."""
    verdict = checker.check_pr("alice", [comment("bob", SENTENCE)])
    assert verdict.status == "not_signed"


def test_not_signed_short_agreement(checker) -> None:
    """'I agree to the CLA' is not a signature: it names no agreement."""
    verdict = checker.check_pr("alice", [comment("alice", "I agree to the CLA.")])
    assert verdict.status == "not_signed"


def test_not_signed_i_signed(checker) -> None:
    """'I signed' is not a signature either."""
    verdict = checker.check_pr("alice", [comment("alice", "I signed the CLA, please merge.")])
    assert verdict.status == "not_signed"


def test_not_signed_sentence_missing_cla_md(checker) -> None:
    """The sentence without '(CLA.md)' is not the agreement sentence."""
    almost = SENTENCE.replace(" (CLA.md)", "")
    verdict = checker.check_pr("alice", [comment("alice", almost)])
    assert verdict.status == "not_signed"


def test_not_signed_sentence_word_changed(checker) -> None:
    """One changed word breaks the match: the sentence must be exact."""
    changed = SENTENCE.replace("I agree to its terms.", "I accept its terms.")
    verdict = checker.check_pr("alice", [comment("alice", changed)])
    assert verdict.status == "not_signed"


def test_not_signed_author_commented_but_never_signed(checker) -> None:
    """The author's own comments without the sentence do not sign."""
    verdict = checker.check_pr(
        "alice",
        [comment("alice", "Fixed the typo."), comment("bob", SENTENCE)],
    )
    assert verdict.status == "not_signed"


def test_login_match_is_case_insensitive(checker) -> None:
    """GitHub logins are case-insensitive; 'Alice' signs for 'alice'."""
    verdict = checker.check_pr("Alice", [comment("alice", SENTENCE)])
    assert verdict.status == "signed"


def test_maintainer_is_exempt(checker) -> None:
    """The maintainer never needs to sign."""
    verdict = checker.check_pr("mirzasnowy", [])
    assert verdict.status == "exempt"


def test_maintainer_exemption_is_case_insensitive(checker) -> None:
    verdict = checker.check_pr("MirzaSnowy", [])
    assert verdict.status == "exempt"


def test_bot_accounts_are_exempt(checker) -> None:
    """Dependabot, github-actions, and any [bot] login never sign."""
    for bot in ("dependabot[bot]", "github-actions[bot]", "some-tool[bot]"):
        verdict = checker.check_pr(bot, [])
        assert verdict.status == "exempt", bot


def test_malformed_comments_are_ignored_not_signatures(checker) -> None:
    """Comments without a usable author or body can never sign."""
    verdict = checker.check_pr(
        "alice",
        [{"user": None, "body": SENTENCE}, {"body": SENTENCE}, {"user": {"login": "alice"}}],
    )
    assert verdict.status == "not_signed"


def test_empty_author_is_an_input_error(checker) -> None:
    with pytest.raises(ValueError):
        checker.check_pr("", [])


def test_is_signing_comment_rejects_non_strings(checker) -> None:
    assert checker.is_signing_comment(None) is False
    assert checker.is_signing_comment(42) is False


# --- The CLI --------------------------------------------------------


def run_cli(
    tmp_path: Path, payload: object | str, author: str = "alice"
) -> subprocess.CompletedProcess:
    """Run the checker CLI against a comments file in tmp_path."""
    comments_file = tmp_path / "comments.json"
    if isinstance(payload, str):
        comments_file.write_text(payload, encoding="utf-8")
    else:
        comments_file.write_text(json.dumps(payload), encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            str(CHECKER_PATH),
            "--pr-author",
            author,
            "--comments",
            str(comments_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_signed_exits_zero(tmp_path) -> None:
    result = run_cli(tmp_path, [comment("alice", SENTENCE)])
    assert result.returncode == 0
    assert "signed" in result.stdout


def test_cli_not_signed_exits_one(tmp_path) -> None:
    result = run_cli(tmp_path, [comment("alice", "Looks good to me.")])
    assert result.returncode == 1
    assert "not_signed" in result.stdout


def test_cli_exempt_exits_zero(tmp_path) -> None:
    result = run_cli(tmp_path, [], author="mirzasnowy")
    assert result.returncode == 0
    assert "exempt" in result.stdout


def test_cli_malformed_json_exits_two(tmp_path) -> None:
    result = run_cli(tmp_path, "{not json")
    assert result.returncode == 2
    assert "input error" in result.stderr


def test_cli_non_array_json_exits_two(tmp_path) -> None:
    result = run_cli(tmp_path, {"user": {"login": "alice"}})
    assert result.returncode == 2


def test_cli_missing_file_exits_two(tmp_path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(CHECKER_PATH),
            "--pr-author",
            "alice",
            "--comments",
            str(tmp_path / "nope.json"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2


# --- The workflow, the guidance comment, and the docs -------------


def test_workflow_yaml_parses() -> None:
    """The workflow the bot runs is valid YAML (it has never run on GitHub)."""
    data = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert data["name"] == "CLA check"


def test_workflow_triggers_on_pr_and_comment() -> None:
    """The bot runs on PR events and on new comments (a late signature)."""
    data = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    # PyYAML reads the `on:` key as boolean True (YAML 1.1); that is
    # a parser quirk, not a workflow problem.
    triggers = data.get("on", data.get(True))
    assert "pull_request_target" in triggers
    assert "issue_comment" in triggers


def test_workflow_runs_the_checker_and_labels() -> None:
    """The workflow runs scripts/cla_check.py and uses the cla: missing label."""
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "scripts/cla_check.py" in text
    assert "cla: missing" in text
    assert ".github/cla-guidance.md" in text


def test_workflow_never_checks_out_the_pr_head() -> None:
    """pull_request_target must run base-branch code, never PR code."""
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "pull_request_target" in text
    assert "ref:" not in text or "github.event.pull_request.head" not in text


def test_guidance_comment_has_marker_and_sentence() -> None:
    """The guidance comment is idempotent (marker) and quotes the sentence."""
    text = GUIDANCE_PATH.read_text(encoding="utf-8")
    assert "<!-- puspa-cla-guidance -->" in text
    assert SENTENCE in text


def test_cla_text_states_no_copyright_assignment() -> None:
    """The locked decision: lightweight CLA, no copyright assignment."""
    text = " ".join(CLA_PATH.read_text(encoding="utf-8").split())
    assert "You keep your copyright" in text
    assert "not a transfer" in text
    assert "relicense" in text.lower()


def test_cla_text_states_the_license_gap() -> None:
    """D-01 is open: the CLA must say the project license is not chosen yet."""
    text = CLA_PATH.read_text(encoding="utf-8")
    assert "not yet have a `LICENSE` file" in text
    assert "D-01" in text


def test_cla_text_names_no_project_license() -> None:
    """The item must not choose a repo license: no license name in CLA.md."""
    text = CLA_PATH.read_text(encoding="utf-8")
    for name in ("Apache", "MIT", "AGPL", "GPL", "BSD", "Mozilla"):
        assert name not in text, f"CLA.md must not name a license ({name})"


def test_contributing_quotes_the_signing_sentence() -> None:
    """CONTRIBUTING.md gives the one signing mechanism, word for word."""
    text = CONTRIBUTING_PATH.read_text(encoding="utf-8")
    assert SENTENCE in text
    assert "CLA.md" in text
