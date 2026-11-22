"""Tests for conversational skill authoring (COW-04).

Four layers, matching the wiring:

- Parsing (in-memory, deterministic): a described workflow yields
  steps with the named tools; an API description yields its base
  URL, endpoints and auth; an incomplete description yields
  nothing to guess from.
- Interview (in-memory): the session asks a follow-up question
  instead of guessing — for missing steps, for a step with no
  tool, for a missing base URL or auth — and re-asks a question
  whose answer does not resolve.
- Draft + validation (in-memory): a described workflow becomes a
  valid SKILL.md with the right steps; an API description becomes
  a skill with the endpoints and auth declared; an invalid draft
  is rejected, never registered.
- Trial registration (in-memory store, plus the durable store):
  the created skill runs through the compiler's trial path —
  versioned, replayable, status trial — and the CLI shows the
  draft first and registers nothing without --approve.
"""

from __future__ import annotations

import argparse

from puspa.governor import InMemorySkillStore, ProceduralSkillStore, SkillCompiler
from puspa.marketplace.skill_creator import (
    ApiEndpoint,
    SkillCreatorSession,
    SkillDraft,
    known_tool_names,
    parse_api_description,
    parse_workflow_steps,
    register_trial_skill,
    render_skill_md,
    run_skill,
    slugify,
    validate_draft,
)
from puspa.memory import procedural as procedural_store

#: Tools the tests pretend Puspa has (small, deterministic).
KNOWN = {"price_check", "memory_search", "ledger_recent", "fetch_url"}

WORKFLOW_DESCRIPTION = (
    "Every morning:\n"
    "1. check the price of beras with price_check\n"
    "2. tell me the result with memory_search"
)

API_DESCRIPTION = (
    "Use the weather API at https://api.weather.example/v1 with an api key.\n"
    "GET /current - current weather\n"
    "GET /forecast - 7 day forecast"
)


def _workflow_draft() -> SkillDraft:
    """A valid workflow draft, as the interview would produce it."""
    session = SkillCreatorSession(author="user", known_tools=KNOWN)
    session.answer(WORKFLOW_DESCRIPTION)
    session.answer("Morning beras check")
    draft = session.draft()
    assert draft is not None
    return draft


def _api_draft() -> SkillDraft:
    """A valid API draft, as the interview would produce it."""
    session = SkillCreatorSession(author="user", known_tools=KNOWN)
    session.answer(API_DESCRIPTION)
    session.answer("Weather lookup")
    draft = session.draft()
    assert draft is not None
    return draft


def _compiler(store):
    return SkillCompiler(store)


# --- parsing ---------------------------------------------------------------


def test_workflow_steps_name_the_tools_they_mention():
    steps = parse_workflow_steps(WORKFLOW_DESCRIPTION, KNOWN)
    assert [step["tool"] for step in steps] == ["price_check", "memory_search"]
    assert steps[0]["arguments"]["step"] == "check the price of beras with price_check"
    assert steps[1]["arguments"]["step"] == "tell me the result with memory_search"


def test_workflow_step_without_a_tool_is_left_open_not_guessed():
    steps = parse_workflow_steps("Daily check:\n1. check the price of beras\n", KNOWN)
    assert len(steps) == 1
    assert steps[0]["tool"] is None


def test_text_without_step_lines_yields_no_steps():
    assert parse_workflow_steps("help me with my morning routine", KNOWN) == []


def test_api_description_yields_base_url_endpoints_and_auth():
    parsed = parse_api_description(API_DESCRIPTION)
    assert parsed["base_url"] == "https://api.weather.example/v1"
    assert parsed["auth"] == "api_key"
    assert parsed["endpoints"] == [
        ApiEndpoint("GET", "/current", "current weather"),
        ApiEndpoint("GET", "/forecast", "7 day forecast"),
    ]


def test_api_description_without_facts_leaves_them_empty():
    parsed = parse_api_description("use the weather API")
    assert parsed["base_url"] == ""
    assert parsed["endpoints"] == []
    assert parsed["auth"] == ""


def test_slugify_makes_a_skill_id():
    assert slugify("Morning Beras Check") == "morning-beras-check"
    assert slugify("  Weather  lookup! ") == "weather-lookup"


def test_known_tool_names_include_core_and_native_tools():
    names = known_tool_names()
    assert {"price_check", "memory_search", "fetch_url", "read_file"} <= names


# --- interview --------------------------------------------------------------


def test_workflow_interview_finishes_with_a_draft():
    session = SkillCreatorSession(author="user", known_tools=KNOWN)
    assert session.next_question() is not None
    assert session.answer(WORKFLOW_DESCRIPTION) is not None  # asks the name
    assert session.answer("Morning beras check") is None  # complete
    assert session.is_complete
    draft = session.draft()
    assert draft is not None
    assert draft.skill_id == "morning-beras-check"
    assert draft.kind == "workflow"
    assert [step["tool"] for step in draft.steps] == ["price_check", "memory_search"]


def test_api_interview_finishes_with_a_draft():
    session = SkillCreatorSession(author="user", known_tools=KNOWN)
    session.answer(API_DESCRIPTION)
    assert session.answer("Weather lookup") is None
    draft = session.draft()
    assert draft is not None
    assert draft.skill_id == "weather-lookup"
    assert draft.kind == "api"
    assert draft.base_url == "https://api.weather.example/v1"
    assert draft.auth == "api_key"
    assert [endpoint.path for endpoint in draft.endpoints] == ["/current", "/forecast"]


def test_incomplete_description_asks_what_kind_instead_of_guessing():
    session = SkillCreatorSession(author="user", known_tools=KNOWN)
    question = session.answer("help me with my morning routine")
    assert question is not None
    assert "workflow" in question and "api" in question
    assert session.draft() is None
    assert session.answer("workflow") is not None  # now asks the name
    assert session.answer("Morning routine") is not None  # now asks the steps
    assert session.answer("1. check the price of beras with price_check") is None
    draft = session.draft()
    assert draft is not None and draft.kind == "workflow"


def test_step_without_a_tool_asks_which_tool_runs_it():
    session = SkillCreatorSession(author="user", known_tools=KNOWN)
    session.answer("Daily check:\n1. check the price of beras\n2. send me the result")
    session.answer("Daily check")
    question = session.next_question()
    assert question is not None
    assert "Step 1" in question and "check the price of beras" in question
    # An unknown tool re-asks the same question instead of accepting it.
    again = session.answer("nonsense_tool")
    assert again is not None and "Step 1" in again
    assert "not a known tool" in again
    assert session.answer("price_check") is not None  # now asks about step 2
    assert session.answer("ledger_recent") is None
    draft = session.draft()
    assert draft is not None
    assert [step["tool"] for step in draft.steps] == ["price_check", "ledger_recent"]


def test_api_interview_asks_for_a_missing_base_url_and_auth():
    session = SkillCreatorSession(author="user", known_tools=KNOWN)
    session.answer("Use the weather API.\nGET /current - current weather")
    session.answer("Weather lookup")
    question = session.next_question()
    assert question is not None and "base URL" in question
    assert session.answer("not a url") is not None  # re-asks: bad URL
    assert session.answer("https://api.weather.example/v1") is not None  # now asks auth
    assert session.answer("api key") is None
    draft = session.draft()
    assert draft is not None and draft.auth == "api_key"


def test_empty_reply_re_asks_the_same_question():
    session = SkillCreatorSession(author="user", known_tools=KNOWN)
    question = session.answer("   ")
    assert question is not None and "need an answer" in question
    assert session.current_key == "description"


# --- draft + validation ------------------------------------------------------


def test_workflow_draft_renders_a_valid_skill_md_with_the_right_steps():
    draft = _workflow_draft()
    assert validate_draft(draft, known_tools=KNOWN) == []
    text = render_skill_md(draft)
    assert "# Morning beras check" in text
    assert "1. `price_check` — check the price of beras with price_check" in text
    assert "2. `memory_search` — tell me the result with memory_search" in text
    assert "## When to Use This Skill" in text


def test_api_draft_declares_its_endpoints_and_auth():
    draft = _api_draft()
    assert validate_draft(draft, known_tools=KNOWN) == []
    text = render_skill_md(draft)
    assert "`GET /current` — current weather" in text
    assert "`GET /forecast` — 7 day forecast" in text
    assert "{{config.base_url}}" in text
    assert "api_key" in text


def test_draft_with_an_unknown_tool_is_rejected():
    draft = SkillDraft(
        skill_id="bad-tool",
        name="Bad Tool",
        description="Do a thing.",
        kind="workflow",
        steps=[{"tool": "nonsense_tool", "arguments": {"step": "do a thing"}}],
    )
    problems = validate_draft(draft, known_tools=KNOWN)
    assert any("nonsense_tool" in problem for problem in problems)


def test_api_draft_with_a_bad_base_url_is_rejected():
    draft = SkillDraft(
        skill_id="bad-url",
        name="Bad Url",
        description="Call an API.",
        kind="api",
        endpoints=[ApiEndpoint("GET", "/current", "current weather")],
        base_url="not a url",
    )
    problems = validate_draft(draft, known_tools=KNOWN)
    assert any("base URL" in problem for problem in problems)


def test_api_draft_without_endpoints_is_rejected():
    draft = SkillDraft(
        skill_id="no-endpoints",
        name="No Endpoints",
        description="Call an API.",
        kind="api",
        base_url="https://api.weather.example/v1",
    )
    problems = validate_draft(draft, known_tools=KNOWN)
    assert any("at least one endpoint" in problem for problem in problems)


# --- trial registration -------------------------------------------------------


def test_created_workflow_skill_runs_through_the_compiler_trial_path():
    draft = _workflow_draft()
    store = InMemorySkillStore()
    result = register_trial_skill(draft, _compiler(store))
    assert result.skill is not None, result.detail
    assert result.skill.status == "trial"
    assert result.skill.version == 1
    found = store.find("authored:morning-beras-check")
    assert found is not None
    assert found.status == "trial"
    assert found.run_regression(lambda step: {"ok": True, "tool": step["tool"]}).passed


def test_created_api_skill_registers_with_fetch_url_steps():
    draft = _api_draft()
    store = InMemorySkillStore()
    result = register_trial_skill(draft, _compiler(store))
    assert result.skill is not None, result.detail
    assert result.skill.status == "trial"
    assert [step["tool"] for step in result.skill.steps] == ["fetch_url", "fetch_url"]
    assert result.skill.steps[0]["arguments"]["url"] == "https://api.weather.example/v1/current"


def test_invalid_draft_is_rejected_not_registered():
    draft = SkillDraft(
        skill_id="bad-tool",
        name="Bad Tool",
        description="Do a thing.",
        kind="workflow",
        steps=[{"tool": "nonsense_tool", "arguments": {"step": "do a thing"}}],
    )
    store = InMemorySkillStore()
    result = register_trial_skill(draft, _compiler(store))
    assert result.skill is None
    assert "nonsense_tool" in result.detail
    assert store.find("authored:bad-tool") is None


def test_created_skill_lands_in_the_procedural_store_as_trial(test_dsn, make_user):
    user = make_user()
    store = ProceduralSkillStore(user_id=user["user_id"], dsn=test_dsn)
    result = register_trial_skill(_workflow_draft(), _compiler(store), agent_id=user["agent_id"])
    assert result.skill is not None, result.detail
    rows = [
        row
        for row in procedural_store.list_procedures(user["user_id"], dsn=test_dsn)
        if row["service"] == "compiled:authored:morning-beras-check"
    ]
    assert len(rows) == 1
    assert rows[0]["version"] == 1
    assert rows[0]["status"] == "trial"


# --- CLI -----------------------------------------------------------------------


def _cli_args(**overrides):
    args = {
        "skill_command": "create",
        "describe": None,
        "name": None,
        "user": None,
        "author": "user",
        "approve": False,
    }
    args.update(overrides)
    return argparse.Namespace(**args)


def _run_cli(args, answers):
    """Run the CLI with canned stdin answers; return (exit code, printed lines)."""
    printed: list[str] = []
    queue = list(answers)

    def input_fn(prompt=""):
        if not queue:
            raise EOFError
        return queue.pop(0)

    code = run_skill(args, input_fn=input_fn, print_fn=printed.append)
    return code, printed


def test_cli_shows_the_draft_and_registers_nothing_without_approve():
    code, printed = _run_cli(
        _cli_args(describe=WORKFLOW_DESCRIPTION, name="Morning beras check"),
        answers=[],
    )
    assert code == 0
    joined = "\n".join(printed)
    assert "Draft SKILL.md" in joined
    assert "Morning beras check" in joined
    assert "Nothing was registered" in joined


def test_cli_registers_the_trial_skill_with_approve():
    code, printed = _run_cli(
        _cli_args(describe=WORKFLOW_DESCRIPTION, name="Morning beras check", approve=True),
        answers=[],
    )
    assert code == 0
    joined = "\n".join(printed)
    assert "Registered trial skill 'morning-beras-check' v1" in joined


def test_cli_interview_asks_follow_up_questions():
    code, printed = _run_cli(
        _cli_args(),
        answers=[
            "Daily check:\n1. check the price of beras",
            "Daily check",
            "price_check",
        ],
    )
    assert code == 0
    joined = "\n".join(printed)
    assert "Step 1" in joined
    assert "Registered trial skill 'daily-check' v1" not in joined  # no --approve
    assert "Nothing was registered" in joined


def test_cli_registers_durably_with_user_and_approve(test_dsn, make_user):
    user = make_user()
    code, printed = _run_cli(
        _cli_args(
            describe=WORKFLOW_DESCRIPTION,
            name="Morning beras check",
            user=user["user_id"],
            approve=True,
        ),
        answers=[],
    )
    assert code == 0
    assert any("Registered trial skill 'morning-beras-check' v1" in line for line in printed)
    rows = [
        row
        for row in procedural_store.list_procedures(user["user_id"], dsn=test_dsn)
        if row["service"] == "compiled:authored:morning-beras-check"
    ]
    assert len(rows) == 1
    assert rows[0]["status"] == "trial"
