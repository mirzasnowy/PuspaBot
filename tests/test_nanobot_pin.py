"""Tests for the nanobot pin process (E-19, plan section 9.4).

Unit tests only: no database, no network. The GitHub API is replaced
by fake fetchers, and the bump is applied to synthetic repo trees in
``tmp_path``. The one place that touches the real repo is the
consistency check between the real ``pin.json`` and the real
``requirements.txt`` — reading two files, not running anything.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

from puspa.chassis.contract import (
    CHASSIS_CONTRACT_TESTS,
    contract_test_files,
    missing_contract_files,
)
from puspa.chassis.pin import (
    FORK_LAG_THRESHOLD_DAYS,
    FORK_MIN_LAGGING_PATCHES,
    MONKEYPATCH_LIMIT,
    Advisory,
    PinRecord,
    evaluate_fork_rule,
    lagging_advisories,
    parse_pin_from_requirements,
    read_pin_record,
    record_matches_requirements,
    write_pin_record,
)

BUILD_ROOT = Path(__file__).resolve().parent.parent
BUMP_PATH = BUILD_ROOT / "scripts" / "nanobot_bump.py"
CONTRACT_RUNNER_PATH = BUILD_ROOT / "scripts" / "run_chassis_contract.py"

REQUIREMENTS_SAMPLE = """# test requirements
nanobot-ai @ git+https://github.com/HKUDS/nanobot@e767174
psycopg[binary]==3.3.9
"""

PIN_COMMIT = "e767174"
NEW_COMMIT = "a" * 40


def load_script(path: Path, name: str):
    """Load a script file as a module (scripts/ is no package)."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def bump():
    return load_script(BUMP_PATH, "nanobot_bump_under_test")


@pytest.fixture()
def contract_runner():
    return load_script(CONTRACT_RUNNER_PATH, "run_chassis_contract_under_test")


def _record(**overrides) -> PinRecord:
    values: dict = {
        "commit": PIN_COMMIT,
        "pinned_on": date(2026, 11, 17),
        "last_checked_on": date(2026, 11, 20),
        "last_upstream_commit": None,
        "advisories_open": [],
    }
    values.update(overrides)
    return PinRecord(**values)


def _advisory(advisory_id: str, published_on: date, **overrides) -> Advisory:
    values: dict = {
        "advisory_id": advisory_id,
        "summary": f"test advisory {advisory_id}",
        "published_on": published_on,
    }
    values.update(overrides)
    return Advisory(**values)


# ---------------------------------------------------------------------------
# Pin parsing
# ---------------------------------------------------------------------------


def test_parse_pin_from_requirements_returns_short_sha():
    assert parse_pin_from_requirements(REQUIREMENTS_SAMPLE) == PIN_COMMIT


def test_parse_pin_from_requirements_returns_full_sha():
    text = REQUIREMENTS_SAMPLE.replace(PIN_COMMIT, NEW_COMMIT)
    assert parse_pin_from_requirements(text) == NEW_COMMIT


def test_parse_pin_from_requirements_raises_when_missing():
    with pytest.raises(ValueError, match="no nanobot-ai git pin"):
        parse_pin_from_requirements("psycopg[binary]==3.3.9\n")


# ---------------------------------------------------------------------------
# Pin record
# ---------------------------------------------------------------------------


def test_pin_record_round_trip(tmp_path: Path):
    record = _record(
        advisories_open=[_advisory("GHSA-aaaa-bbbb-cccc", date(2026, 11, 1))],
    )
    path = tmp_path / "pin.json"
    write_pin_record(record, path)
    loaded = read_pin_record(path)
    assert loaded == record
    assert loaded.advisories_open[0].advisory_id == "GHSA-aaaa-bbbb-cccc"


def test_pin_record_json_is_stable_text(tmp_path: Path):
    record = _record()
    path = tmp_path / "pin.json"
    write_pin_record(record, path)
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert parsed["commit"] == PIN_COMMIT
    assert parsed["forked"] is False
    assert parsed["vendor_path"] is None


def test_record_matches_requirements():
    assert record_matches_requirements(_record(), REQUIREMENTS_SAMPLE) is True
    assert record_matches_requirements(_record(commit=NEW_COMMIT), REQUIREMENTS_SAMPLE) is False
    assert record_matches_requirements(_record(), "psycopg[binary]==3.3.9\n") is False


def test_real_pin_record_matches_real_requirements():
    record = read_pin_record(BUILD_ROOT / "puspa" / "chassis" / "pin.json")
    requirements = (BUILD_ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert record_matches_requirements(record, requirements) is True
    assert record.commit == PIN_COMMIT
    assert record.forked is False


# ---------------------------------------------------------------------------
# Advisory lag and the fork rule
# ---------------------------------------------------------------------------


def test_advisory_lag_boundary_is_exclusive():
    today = date(2026, 11, 20)
    exactly_threshold = _advisory("GHSA-old-exact", date(2026, 10, 21))
    one_day_over = _advisory("GHSA-old-over", date(2026, 10, 20))
    assert exactly_threshold.lag_days(today) == FORK_LAG_THRESHOLD_DAYS
    assert lagging_advisories([exactly_threshold], today) == []
    assert lagging_advisories([one_day_over], today) == [one_day_over]


def test_fixed_advisory_does_not_lag():
    today = date(2026, 11, 20)
    fixed = _advisory("GHSA-fixed", date(2026, 9, 1), fixed_in_commit="f" * 40)
    assert lagging_advisories([fixed], today) == []


def test_fork_rule_quiet_when_nothing_lags():
    today = date(2026, 11, 20)
    fresh = [_advisory("GHSA-fresh", date(2026, 11, 19))]
    triggered, reasons = evaluate_fork_rule(fresh, 0, today)
    assert triggered is False
    assert reasons == []


def test_fork_rule_needs_two_lagging_patches():
    today = date(2026, 11, 20)
    one = [_advisory("GHSA-one", date(2026, 10, 1))]
    triggered, _ = evaluate_fork_rule(one, 0, today)
    assert triggered is False
    assert len(one) < FORK_MIN_LAGGING_PATCHES
    two = [*one, _advisory("GHSA-two", date(2026, 10, 2))]
    triggered, reasons = evaluate_fork_rule(two, 0, today)
    assert triggered is True
    assert "GHSA-one" in reasons[0] and "GHSA-two" in reasons[0]


def test_fork_rule_fires_on_monkeypatch_overload():
    today = date(2026, 11, 20)
    triggered, reasons = evaluate_fork_rule([], MONKEYPATCH_LIMIT, today)
    assert triggered is False
    triggered, reasons = evaluate_fork_rule([], MONKEYPATCH_LIMIT + 1, today)
    assert triggered is True
    assert str(MONKEYPATCH_LIMIT + 1) in reasons[0]


def test_fork_rule_reports_both_reasons():
    today = date(2026, 11, 20)
    lagging = [
        _advisory("GHSA-one", date(2026, 10, 1)),
        _advisory("GHSA-two", date(2026, 10, 2)),
    ]
    triggered, reasons = evaluate_fork_rule(lagging, MONKEYPATCH_LIMIT + 1, today)
    assert triggered is True
    assert len(reasons) == 2


# ---------------------------------------------------------------------------
# Chassis contract list
# ---------------------------------------------------------------------------


def test_contract_files_all_exist():
    assert missing_contract_files(BUILD_ROOT) == []


def test_contract_files_each_cover_a_wrapper_surface():
    surfaces = (
        "PuspaTool",
        "PuspaRegistry",
        "PuspaHook",
        "PuspaSkillsLoader",
        "puspa.chassis",
        "assemble",
        "SandboxTool",
    )
    for relative in CHASSIS_CONTRACT_TESTS:
        text = (BUILD_ROOT / relative).read_text(encoding="utf-8")
        assert any(surface in text for surface in surfaces), relative


def test_missing_contract_files_reported_on_empty_tree(tmp_path: Path):
    missing = missing_contract_files(tmp_path)
    assert missing == list(CHASSIS_CONTRACT_TESTS)


def test_contract_test_files_are_absolute_paths():
    files = contract_test_files(BUILD_ROOT)
    assert all(path.is_absolute() for path in files)
    assert len(files) == len(CHASSIS_CONTRACT_TESTS)


# ---------------------------------------------------------------------------
# Bump script: fetching (fake network) and reporting
# ---------------------------------------------------------------------------


def test_latest_upstream_commit_uses_fake_fetcher(bump):
    def fake_fetcher(url: str):
        assert "HKUDS/nanobot" in url
        return {
            "sha": NEW_COMMIT,
            "commit": {"committer": {"date": "2026-11-19T10:00:00Z"}},
        }

    sha, committed = bump.latest_upstream_commit(fake_fetcher)
    assert sha == NEW_COMMIT
    assert committed == date(2026, 11, 19)


def test_list_advisories_uses_fake_fetcher(bump):
    def fake_fetcher(url: str):
        assert "advisories" in url
        return [
            {
                "ghsa_id": "GHSA-xxxx-yyyy-zzzz",
                "summary": "test advisory",
                "published_at": "2026-10-05T00:00:00Z",
                "severity": "high",
            },
            {"ghsa_id": "GHSA-no-date", "summary": "skipped, no date"},
        ]

    advisories = bump.list_advisories(fake_fetcher)
    assert len(advisories) == 1
    assert advisories[0].advisory_id == "GHSA-xxxx-yyyy-zzzz"
    assert advisories[0].severity == "high"
    assert advisories[0].published_on == date(2026, 10, 5)


def test_build_report_bump_available(bump):
    report = bump.build_report(_record(), NEW_COMMIT, [], 0, date(2026, 11, 20))
    assert report["bump_available"] is True
    assert report["current_pin"] == PIN_COMMIT
    assert report["latest_upstream_commit"] == NEW_COMMIT
    assert report["fork_rule_triggered"] is False


def test_build_report_current_pin(bump):
    report = bump.build_report(_record(), PIN_COMMIT, [], 0, date(2026, 11, 20))
    assert report["bump_available"] is False


def test_build_report_fork_rule_included(bump):
    lagging = [
        _advisory("GHSA-one", date(2026, 10, 1)),
        _advisory("GHSA-two", date(2026, 10, 2)),
    ]
    report = bump.build_report(_record(), PIN_COMMIT, lagging, 0, date(2026, 11, 20))
    assert report["fork_rule_triggered"] is True
    assert report["advisories_lagging"] == ["GHSA-one", "GHSA-two"]


# ---------------------------------------------------------------------------
# Bump script: applying the bump to a synthetic repo tree
# ---------------------------------------------------------------------------


def _synthetic_repo(tmp_path: Path) -> Path:
    (tmp_path / "puspa" / "chassis").mkdir(parents=True)
    (tmp_path / "requirements.txt").write_text(REQUIREMENTS_SAMPLE, encoding="utf-8")
    write_pin_record(_record(), tmp_path / "puspa" / "chassis" / "pin.json")
    return tmp_path


def test_apply_bump_rewrites_pin_and_record(bump, tmp_path: Path):
    root = _synthetic_repo(tmp_path)
    today = date(2026, 11, 20)
    updated = bump.apply_bump(NEW_COMMIT, [], today, root)
    assert updated.commit == NEW_COMMIT
    assert updated.pinned_on == today
    assert updated.last_upstream_commit == NEW_COMMIT
    assert (
        parse_pin_from_requirements((root / "requirements.txt").read_text(encoding="utf-8"))
        == NEW_COMMIT
    )
    assert read_pin_record(root / "puspa" / "chassis" / "pin.json").commit == NEW_COMMIT


def test_apply_bump_marks_old_advisories_fixed(bump, tmp_path: Path):
    root = _synthetic_repo(tmp_path)
    advisories = [_advisory("GHSA-old", date(2026, 10, 1))]
    updated = bump.apply_bump(NEW_COMMIT, advisories, date(2026, 11, 20), root)
    assert updated.advisories_open[0].fixed_in_commit == NEW_COMMIT


def test_apply_bump_without_pin_raises(bump, tmp_path: Path):
    (tmp_path / "puspa" / "chassis").mkdir(parents=True)
    (tmp_path / "requirements.txt").write_text("psycopg[binary]==3.3.9\n")
    write_pin_record(_record(), tmp_path / "puspa" / "chassis" / "pin.json")
    with pytest.raises(ValueError, match="no nanobot-ai git pin"):
        bump.apply_bump(NEW_COMMIT, [], date(2026, 11, 20), tmp_path)


def test_count_monkeypatch_sites(bump, tmp_path: Path):
    clean = tmp_path / "clean"
    (clean / "puspa").mkdir(parents=True)
    (clean / "puspa" / "plain.py").write_text("x = 1\n", encoding="utf-8")
    assert bump.count_monkeypatch_sites(clean) == 0
    patched = tmp_path / "patched"
    (patched / "puspa").mkdir(parents=True)
    (patched / "puspa" / "patch.py").write_text(
        "import nanobot.agent.loop\nnanobot.agent.loop.AgentLoop.run = patched_run\n",
        encoding="utf-8",
    )
    assert bump.count_monkeypatch_sites(patched) == 1


def test_count_monkeypatch_sites_on_real_repo_is_zero(bump):
    assert bump.count_monkeypatch_sites(BUILD_ROOT) == 0


# ---------------------------------------------------------------------------
# Bump script: main entry point with stubbed network and paths
# ---------------------------------------------------------------------------


def _stub_main_paths(bump, monkeypatch, root: Path):
    monkeypatch.setattr(bump, "REQUIREMENTS_PATH", root / "requirements.txt")
    monkeypatch.setattr(bump, "PIN_RECORD_PATH", root / "puspa" / "chassis" / "pin.json")
    monkeypatch.setattr(bump, "REPO_ROOT", root)


def test_main_check_current_pin_exits_zero(bump, tmp_path: Path, monkeypatch):
    root = _synthetic_repo(tmp_path)
    _stub_main_paths(bump, monkeypatch, root)
    monkeypatch.setattr(bump, "latest_upstream_commit", lambda: (PIN_COMMIT, date(2026, 11, 20)))
    monkeypatch.setattr(bump, "list_advisories", list)
    monkeypatch.setattr(bump, "count_monkeypatch_sites", lambda _root: 0)
    assert bump.main(["--check"]) == 0


def test_main_check_bump_available_exits_ten(bump, tmp_path: Path, monkeypatch):
    root = _synthetic_repo(tmp_path)
    _stub_main_paths(bump, monkeypatch, root)
    monkeypatch.setattr(bump, "latest_upstream_commit", lambda: (NEW_COMMIT, date(2026, 11, 19)))
    monkeypatch.setattr(bump, "list_advisories", list)
    monkeypatch.setattr(bump, "count_monkeypatch_sites", lambda _root: 0)
    assert bump.main(["--check"]) == bump.EXIT_BUMP_AVAILABLE


def test_main_apply_moves_pin_and_exits_zero(bump, tmp_path: Path, monkeypatch):
    root = _synthetic_repo(tmp_path)
    _stub_main_paths(bump, monkeypatch, root)
    monkeypatch.setattr(bump, "latest_upstream_commit", lambda: (NEW_COMMIT, date(2026, 11, 19)))
    monkeypatch.setattr(bump, "list_advisories", list)
    monkeypatch.setattr(bump, "count_monkeypatch_sites", lambda _root: 0)
    assert bump.main(["--apply"]) == 0
    assert read_pin_record(root / "puspa" / "chassis" / "pin.json").commit == NEW_COMMIT


def test_main_fork_rule_exits_twenty(bump, tmp_path: Path, monkeypatch):
    root = _synthetic_repo(tmp_path)
    _stub_main_paths(bump, monkeypatch, root)
    monkeypatch.setattr(bump, "latest_upstream_commit", lambda: (PIN_COMMIT, date(2026, 11, 20)))
    monkeypatch.setattr(
        bump,
        "list_advisories",
        lambda: [
            _advisory("GHSA-one", date(2026, 10, 1)),
            _advisory("GHSA-two", date(2026, 10, 2)),
        ],
    )
    monkeypatch.setattr(bump, "count_monkeypatch_sites", lambda _root: 0)
    assert bump.main(["--check"]) == bump.EXIT_FORK_TRIGGERED


def test_main_disagreeing_record_exits_error(bump, tmp_path: Path, monkeypatch):
    root = _synthetic_repo(tmp_path)
    (root / "requirements.txt").write_text(
        REQUIREMENTS_SAMPLE.replace(PIN_COMMIT, NEW_COMMIT), encoding="utf-8"
    )
    _stub_main_paths(bump, monkeypatch, root)
    monkeypatch.setattr(bump, "latest_upstream_commit", lambda: (PIN_COMMIT, date(2026, 11, 20)))
    monkeypatch.setattr(bump, "list_advisories", list)
    monkeypatch.setattr(bump, "count_monkeypatch_sites", lambda _root: 0)
    assert bump.main(["--check"]) == bump.EXIT_ERROR


# ---------------------------------------------------------------------------
# Contract lane runner
# ---------------------------------------------------------------------------


def test_contract_command_lists_every_contract_file(contract_runner):
    command = contract_runner.contract_command([])
    assert command[:3] == [sys.executable, "-m", "pytest"]
    for relative in CHASSIS_CONTRACT_TESTS:
        assert str(BUILD_ROOT / relative) in command


def test_contract_command_passes_pytest_args_through(contract_runner):
    command = contract_runner.contract_command(["-x", "-q"])
    assert command[-2:] == ["-x", "-q"]


def test_contract_command_fails_when_a_file_is_missing(
    contract_runner, monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(contract_runner, "REPO_ROOT", tmp_path)
    with pytest.raises(FileNotFoundError, match="chassis contract test files missing"):
        contract_runner.contract_command([])


# ---------------------------------------------------------------------------
# Workflows: structure
# ---------------------------------------------------------------------------


def _load_workflow(name: str) -> dict:
    path = BUILD_ROOT / ".github" / "workflows" / name
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    # PyYAML reads the YAML 1.1 key `on` as the boolean True; restore
    # the name the workflow file actually uses.
    if True in workflow:
        workflow["on"] = workflow.pop(True)
    return workflow


def test_chassis_contract_workflow_structure():
    workflow = _load_workflow("chassis-contract.yml")
    assert "pull_request" in workflow["on"]
    assert "workflow_dispatch" in workflow["on"]
    pin_input = workflow["on"]["workflow_dispatch"]["inputs"]["nanobot_pin"]
    assert pin_input["default"] == PIN_COMMIT
    job = workflow["jobs"]["contract"]
    steps_text = json.dumps(job["steps"])
    assert "run_chassis_contract.py" in steps_text
    assert "nanobot-ai @ git+https://github.com/HKUDS/nanobot@" in steps_text


def test_bump_workflow_is_monthly_and_opens_a_pr():
    workflow = _load_workflow("nanobot-bump.yml")
    schedules = [entry["cron"] for entry in workflow["on"]["schedule"]]
    assert "0 3 1 * *" in schedules
    job = workflow["jobs"]["bump"]
    steps_text = json.dumps(job["steps"])
    assert "nanobot_bump.py --check" in steps_text
    assert "nanobot_bump.py --apply" in steps_text
    assert "create-pull-request" in steps_text
    assert "run_chassis_contract.py" in steps_text


def test_advisory_watch_workflow_is_weekly():
    workflow = _load_workflow("nanobot-advisory-watch.yml")
    schedules = [entry["cron"] for entry in workflow["on"]["schedule"]]
    assert "0 3 * * 1" in schedules
    job = workflow["jobs"]["watch"]
    steps_text = json.dumps(job["steps"])
    assert "nanobot_bump.py --advisories-only" in steps_text


def test_all_workflows_parse_as_yaml():
    for name in (
        "chassis-contract.yml",
        "nanobot-bump.yml",
        "nanobot-advisory-watch.yml",
    ):
        workflow = _load_workflow(name)
        assert workflow["name"]
        assert workflow["jobs"]


# ---------------------------------------------------------------------------
# Hygiene: no placeholders in the new files
# ---------------------------------------------------------------------------


def test_new_files_have_no_placeholders():
    new_files = [
        BUILD_ROOT / "puspa" / "chassis" / "pin.py",
        BUILD_ROOT / "puspa" / "chassis" / "contract.py",
        BUILD_ROOT / "scripts" / "nanobot_bump.py",
        BUILD_ROOT / "scripts" / "run_chassis_contract.py",
        BUILD_ROOT / "tests" / "test_nanobot_pin.py",
        BUILD_ROOT / "docs" / "nanobot-pin-process.md",
        BUILD_ROOT / "docs" / "nanobot-changelog-checklist.md",
        BUILD_ROOT / "docs" / "nanobot-canary-rollback.md",
    ]
    # The markers are built from parts so this test file can name them
    # without tripping its own check.
    markers = ("TO" + "DO", "FIX" + "ME", "NotImplemented" + "Error")
    for path in new_files:
        text = path.read_text(encoding="utf-8")
        for marker in markers:
            assert marker not in text, (path, marker)
