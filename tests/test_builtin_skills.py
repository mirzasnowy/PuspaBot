"""Built-in capability skills: one SKILL.md per capability.

Every skill in ``skills/`` must be discoverable by the existing
loader (``PuspaSkillsLoader`` with the repo ``skills/`` directory
as its builtin skills dir), must parse under the loader's own
frontmatter parsing, must carry a valid marketplace manifest,
and must name the real code it teaches (module path + at least
one real public function).

In-memory: run with ``pytest tests/test_builtin_skills.py
--noconftest -q``. No database, no network.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Belt + braces (same as tests/conftest.py): the vendored nanobot clone
# lives in ./nanobot, so import from there when it exists. This keeps
# the test in-memory and runnable with --noconftest.
_NANOBOT_LOCAL = Path(__file__).resolve().parent.parent / "nanobot"
if _NANOBOT_LOCAL.exists():
    sys.path.insert(0, str(_NANOBOT_LOCAL))

import pytest
from nanobot.agent.skill_metadata import parse_skill_metadata

from puspa.chassis.skill_loader import PuspaSkillsLoader
from puspa.marketplace import MarketplaceRegistry
from puspa.marketplace.manifest import validate_manifest
from puspa.memory.curator import discover_skills

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_ROOT = REPO_ROOT / "skills"

# skill dir -> (real code the skill must name, frontmatter name)
EXPECTED_SKILLS = {
    "backup": ("puspa.backup", "Backup"),
    "bills": ("puspa.modules.bills", "Bills"),
    "billing": ("puspa.billing", "Billing"),
    "custom-modules": ("puspa.modules.manifest", "Custom Modules"),
    "drafting": ("puspa.drafting", "Drafting"),
    "meetings": ("puspa.meetings", "Meetings"),
    "memory": ("puspa.memory", "Memory"),
    "morning": ("puspa.morning", "Morning"),
    "notifications": ("puspa.modules.notifications", "Notifications"),
    "onboarding": ("puspa.onboarding", "Onboarding"),
    "persona": ("puspa.persona", "Persona"),
    "price-check": ("puspa.check_before_buy", "Price Check"),
    "receipts": ("puspa.receipts", "Receipts"),
    "rooms": ("puspa.rooms", "Rooms"),
    "scheduler": ("puspa.scheduler", "Scheduler"),
    "skill-creator": ("puspa.marketplace.skill_creator", "Skill Creator"),
    "tables": ("puspa.modules.tables", "Tables"),
    "views": ("puspa.modules.views", "Views"),
    "voice": ("puspa.voice", "Voice"),
    "workflows": ("puspa.workflow", "Workflows"),
}


@pytest.fixture()
def loader(tmp_path):
    return PuspaSkillsLoader(tmp_path, builtin_skills_dir=SKILLS_ROOT)


def _read_skill(name):
    return (SKILLS_ROOT / name / "SKILL.md").read_text(encoding="utf-8")


def _read_manifest(name):
    return json.loads((SKILLS_ROOT / name / "manifest.json").read_text(encoding="utf-8"))


# --- discovery ------------------------------------------------------------


def test_loader_discovers_all_builtin_skills(loader):
    names = {entry["name"] for entry in loader.list_skills(filter_unavailable=False)}
    assert "find-skills" in names  # the pre-existing skill still loads
    missing = sorted(set(EXPECTED_SKILLS) - names)
    assert not missing, f"loader does not discover: {missing}"
    for entry in loader.list_skills(filter_unavailable=False):
        if entry["name"] in EXPECTED_SKILLS:
            assert entry["source"] == "builtin"
            assert entry["path"] == str(SKILLS_ROOT / entry["name"] / "SKILL.md")


def test_loader_discovers_skills_with_default_filter(loader):
    # No skill declares a binary/env requirement, so the default
    # filter (requirements met) must not hide any of them.
    names = {entry["name"] for entry in loader.list_skills()}
    assert set(EXPECTED_SKILLS) <= names


def test_curator_discovers_all_builtin_skills():
    found = {path.name for path in discover_skills(SKILLS_ROOT)}
    assert set(EXPECTED_SKILLS) <= found


# --- per-skill validation -------------------------------------------------


@pytest.mark.parametrize("name", sorted(EXPECTED_SKILLS))
def test_skill_frontmatter_parses(name):
    content = _read_skill(name)
    metadata = parse_skill_metadata(content)
    assert metadata is not None, f"{name}: frontmatter does not parse"
    _, expected_title = EXPECTED_SKILLS[name]
    assert metadata.get("name") == expected_title
    assert isinstance(metadata.get("description"), str) and metadata["description"].strip()
    assert metadata.get("author") == "Puspa Team"


@pytest.mark.parametrize("name", sorted(EXPECTED_SKILLS))
def test_skill_manifest_validates(name):
    manifest = _read_manifest(name)
    _, problems, warnings = validate_manifest(manifest, folder_name=name)
    assert problems == [], f"{name}: {problems}"
    assert warnings == [], f"{name}: {warnings}"
    assert manifest["id"] == name
    assert manifest["origin"] == "builtin"
    assert manifest["license"] == "MIT"
    assert manifest["triggerTerms"], f"{name}: manifest needs trigger terms"


@pytest.mark.parametrize("name", sorted(EXPECTED_SKILLS))
def test_skill_imports_through_marketplace_disabled(name):
    registry = MarketplaceRegistry()
    entry = registry.import_skill(SKILLS_ROOT / name, origin="builtin")
    assert entry.skill_id == name
    assert entry.status == "disabled"  # import-disabled-default


@pytest.mark.parametrize("name", sorted(EXPECTED_SKILLS))
def test_skill_loads_through_loader(loader, name):
    content = loader.load_skill(name)
    assert content is not None and len(content.strip()) > 500
    # The context form strips the frontmatter but keeps the body.
    context = loader.load_skills_for_context([name])
    assert f"### Skill: {name}" in context
    assert "When to Use This Skill" in context
    body = context.split(f"### Skill: {name}", 1)[1]
    assert not body.lstrip().startswith("---"), f"{name}: frontmatter not stripped"
    metadata = loader.get_skill_metadata(name)
    assert metadata is not None
    assert metadata.get("name") == EXPECTED_SKILLS[name][1]


@pytest.mark.parametrize("name", sorted(EXPECTED_SKILLS))
def test_skill_names_real_code(name):
    content = _read_skill(name)
    code_path, _ = EXPECTED_SKILLS[name]
    assert code_path in content, f"{name}: SKILL.md never names {code_path}"
    # The named module must exist in the repo (file, package dir, or .py file).
    module_path = REPO_ROOT / code_path.replace(".", "/")
    assert (
        module_path.exists()
        or module_path.with_suffix(".py").exists()
        or (module_path / "__init__.py").exists()
    ), f"{name}: names missing code {code_path}"
    # The format model: when-to-use, how, limits, example.
    for section in (
        "## When to Use This Skill",
        "## Limits",
        "## Example",
    ):
        assert section in content, f"{name}: missing section {section!r}"
    # Every slash code path named in backticks must exist in the repo.
    import re

    for path in sorted(set(re.findall(r"`(puspa/[a-z0-9_/.-]+)`", content))):
        assert (REPO_ROOT / path).exists(), f"{name}: names missing code {path}"


def test_no_skill_covers_the_mcp_server():
    # The MCP server is owned by a parallel worker; no skill here
    # may teach it, and the loader must not discover one.
    for name in EXPECTED_SKILLS:
        content = _read_skill(name)
        assert "puspa mcp" not in content
    assert "mcp" not in {
        entry["name"]
        for entry in PuspaSkillsLoader(Path("/tmp"), builtin_skills_dir=SKILLS_ROOT).list_skills(
            filter_unavailable=False
        )
    }
