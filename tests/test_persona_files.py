"""Tests for user-editable persona files (V2-25, issue #133).

No database, no network: the persona files live on disk in the
Puspa home, and the turn loop reads them through a fake model
call that captures the wire messages. The suite proves the Done
criterion directly: an edit to a persona file changes what the
model sees on the next turn.
"""

from __future__ import annotations

from pathlib import Path

from puspa.persona.files import (
    DEFAULT_IDENTITY,
    DEFAULT_SOUL,
    DEFAULT_USER,
    MAX_FILE_BYTES,
    PersonaFiles,
    ensure_persona_files,
    load_persona_files,
    persona_file_paths,
    render_persona_block,
)
from puspa.runtime.prompt_assembly import PromptSession
from puspa.turns.loop import TurnLoop
from puspa.turns.messages import InternalMessage
from puspa.turns.turn_api import ModelReply


def _text_reply(text: str = "Hello from the turn.") -> ModelReply:
    return ModelReply(text=text, input_tokens=10, output_tokens=5)


def _system_text(wire_messages: list[dict]) -> str:
    """Text of the leading system message the model call received."""
    assert wire_messages and wire_messages[0]["role"] == "system"
    return wire_messages[0]["content"]


# ---------------------------------------------------------------------------
# Loader: defaults, edits, fallback
# ---------------------------------------------------------------------------


def test_missing_files_use_defaults(tmp_path):
    persona = load_persona_files(home=tmp_path)
    assert persona.soul == DEFAULT_SOUL
    assert persona.identity == DEFAULT_IDENTITY
    assert persona.user == DEFAULT_USER
    assert persona.name == "Puspa"
    assert persona.sources == {"SOUL.md": "default", "IDENTITY.md": "default", "USER.md": "default"}
    assert persona.warnings == []
    block = render_persona_block(persona)
    assert "Puspa" in block
    assert "# Agent persona" in block
    assert "# Agent identity" in block
    assert "# About the user" in block


def test_ensure_creates_defaults_without_overwriting(tmp_path):
    created = ensure_persona_files(home=tmp_path)
    assert {path.name for path in created} == {"SOUL.md", "IDENTITY.md", "USER.md"}
    assert (tmp_path / "SOUL.md").read_text(encoding="utf-8") == DEFAULT_SOUL
    # A user edit survives a second ensure: defaults never overwrite.
    (tmp_path / "SOUL.md").write_text("Always answer in rhymes.", encoding="utf-8")
    assert ensure_persona_files(home=tmp_path) == []
    assert (tmp_path / "SOUL.md").read_text(encoding="utf-8") == "Always answer in rhymes."


def test_persona_file_paths_live_under_the_home(tmp_path):
    paths = persona_file_paths(home=tmp_path)
    assert paths == {name: tmp_path / name for name in ("SOUL.md", "IDENTITY.md", "USER.md")}


def test_edit_changes_name_and_vibe_on_next_load(tmp_path):
    (tmp_path / "IDENTITY.md").write_text("# Identity\n\nName: Bimo\n", encoding="utf-8")
    (tmp_path / "SOUL.md").write_text("Always answer in rhymes.", encoding="utf-8")
    persona = load_persona_files(home=tmp_path)
    assert persona.name == "Bimo"
    assert persona.sources["IDENTITY.md"] == "file"
    assert persona.sources["SOUL.md"] == "file"
    block = render_persona_block(persona)
    assert "Bimo" in block
    assert "Always answer in rhymes." in block
    assert "Puspa" not in block


def test_second_edit_is_picked_up_by_the_following_load(tmp_path):
    (tmp_path / "IDENTITY.md").write_text("Name: Bimo\n", encoding="utf-8")
    assert load_persona_files(home=tmp_path).name == "Bimo"
    (tmp_path / "IDENTITY.md").write_text("Name: Citra\n", encoding="utf-8")
    persona = load_persona_files(home=tmp_path)
    assert persona.name == "Citra"
    assert "Bimo" not in render_persona_block(persona)


def test_name_line_wins_over_heading_and_quotes_are_stripped(tmp_path):
    (tmp_path / "IDENTITY.md").write_text('# Bimo\n\nName: "Citra"\n', encoding="utf-8")
    assert load_persona_files(home=tmp_path).name == "Citra"


def test_heading_names_the_agent_when_no_name_line(tmp_path):
    (tmp_path / "IDENTITY.md").write_text("# Bimo\n\nA helpful agent.\n", encoding="utf-8")
    assert load_persona_files(home=tmp_path).name == "Bimo"


def test_identity_heading_alone_keeps_the_default_name(tmp_path):
    (tmp_path / "IDENTITY.md").write_text("# Identity\n\nSome text.\n", encoding="utf-8")
    assert load_persona_files(home=tmp_path).name == "Puspa"


def test_invalid_utf8_file_falls_back_with_warning(tmp_path):
    (tmp_path / "SOUL.md").write_bytes(b"\xff\xfe not utf-8 \x80\x81")
    warnings: list[str] = []
    persona = load_persona_files(home=tmp_path, warn=warnings.append)
    assert persona.soul == DEFAULT_SOUL
    assert persona.sources["SOUL.md"] == "default"
    assert len(warnings) == 1 and "SOUL.md" in warnings[0]
    assert persona.warnings == warnings


def test_oversize_file_falls_back_with_warning(tmp_path):
    (tmp_path / "USER.md").write_text("x" * (MAX_FILE_BYTES + 1), encoding="utf-8")
    warnings: list[str] = []
    persona = load_persona_files(home=tmp_path, warn=warnings.append)
    assert persona.user == DEFAULT_USER
    assert len(warnings) == 1 and "USER.md" in warnings[0]


def test_directory_in_place_of_file_falls_back_with_warning(tmp_path):
    (tmp_path / "USER.md").mkdir()
    warnings: list[str] = []
    persona = load_persona_files(home=tmp_path, warn=warnings.append)
    assert persona.user == DEFAULT_USER
    assert len(warnings) == 1 and "USER.md" in warnings[0]
    # ensure() leaves the directory alone instead of crashing.
    assert ensure_persona_files(home=tmp_path) == [
        tmp_path / "SOUL.md",
        tmp_path / "IDENTITY.md",
    ]


def test_empty_file_renders_nothing_for_that_section(tmp_path):
    (tmp_path / "SOUL.md").write_text("   \n", encoding="utf-8")
    persona = load_persona_files(home=tmp_path)
    assert persona.warnings == []
    block = render_persona_block(persona)
    assert "# Agent persona" not in block
    assert "# Agent identity" in block


def test_all_empty_files_render_no_block():
    persona = PersonaFiles(soul="", identity="", user="", name="Puspa", home=Path("/tmp"))
    assert render_persona_block(persona) == ""


# ---------------------------------------------------------------------------
# Turn loop: an edit changes behaviour next turn (the Done criterion)
# ---------------------------------------------------------------------------


def _capturing_loop(tmp_path, sink=None):
    seen: list[list[dict]] = []

    def model_call(entry, wire_messages, tools):
        seen.append(list(wire_messages))
        return _text_reply("Hi, I am Puspa.")

    loop = TurnLoop(model_call=model_call, persona_home=tmp_path, sink=sink)
    return loop, seen


def test_turn_loop_picks_up_persona_edit_next_turn(tmp_path):
    loop, seen = _capturing_loop(tmp_path)
    history = [InternalMessage.user("Hello")]
    result = loop.run_turn(history)
    assert result.status == "completed"
    assert "Puspa" in _system_text(seen[0])

    # The user edits the files between turns.
    (tmp_path / "IDENTITY.md").write_text("Name: Bimo\n", encoding="utf-8")
    (tmp_path / "SOUL.md").write_text("Always answer in rhymes.", encoding="utf-8")
    result = loop.run_turn([*result.messages, InternalMessage.user("Hello again")])
    assert result.status == "completed"
    system = _system_text(seen[1])
    assert "Bimo" in system
    assert "Always answer in rhymes." in system
    assert "Puspa" not in system

    # A second edit is picked up by the following turn too.
    (tmp_path / "IDENTITY.md").write_text("Name: Citra\n", encoding="utf-8")
    result = loop.run_turn([*result.messages, InternalMessage.user("And again")])
    system = _system_text(seen[2])
    assert "Citra" in system
    assert "Bimo" not in system


def test_turn_loop_keeps_caller_system_text_exactly_once(tmp_path):
    loop, seen = _capturing_loop(tmp_path)
    history = [InternalMessage.system("Base instructions."), InternalMessage.user("Hi")]
    result = loop.run_turn(history)
    assert result.status == "completed"
    assert _system_text(seen[0]).count("Base instructions.") == 1
    assert "Puspa" in _system_text(seen[0])

    (tmp_path / "IDENTITY.md").write_text("Name: Bimo\n", encoding="utf-8")
    result = loop.run_turn([*result.messages, InternalMessage.user("Hi again")])
    system = _system_text(seen[1])
    assert system.count("Base instructions.") == 1
    assert "Bimo" in system
    # The persona block itself appears once, not once per turn.
    assert system.count("# Agent persona") == 1


def test_turn_loop_without_persona_home_injects_nothing():
    seen: list[list[dict]] = []

    def model_call(entry, wire_messages, tools):
        seen.append(list(wire_messages))
        return _text_reply()

    loop = TurnLoop(model_call=model_call)
    result = loop.run_turn([InternalMessage.user("Hi")])
    assert result.status == "completed"
    assert seen[0][0]["role"] == "user"


def test_turn_loop_malformed_file_warns_and_still_completes(tmp_path):
    events: list[dict] = []
    loop, seen = _capturing_loop(
        tmp_path, sink=lambda t, c: events.append({"type": t, "content": c})
    )
    (tmp_path / "SOUL.md").write_bytes(b"\xff\xfe not utf-8")
    result = loop.run_turn([InternalMessage.user("Hi")])
    assert result.status == "completed"
    # The turn ran on the default persona instead of crashing.
    assert "warm, direct, and plain" in _system_text(seen[0])
    warnings = [e for e in events if e["type"] == "persona_file_warning"]
    assert len(warnings) == 1
    assert "SOUL.md" in warnings[0]["content"]["warning"]


# ---------------------------------------------------------------------------
# Prompt assembly: persona lives in the context layer, never stable
# ---------------------------------------------------------------------------


def test_prompt_builder_persona_applies_next_turn_without_moving_stable():
    session = PromptSession(stable="You are an agent.")
    first = session.build([], persona="# Agent persona\n\nName: Puspa")
    assert "Name: Puspa" in first.system_prompt
    assert first.stable_prefix == "You are an agent."
    frozen = first.stable_prefix

    second = session.build([], persona="# Agent persona\n\nName: Bimo")
    assert "Name: Bimo" in second.system_prompt
    assert "Name: Puspa" not in second.system_prompt
    assert second.stable_prefix == frozen
    assert second.snapshot_hash != first.snapshot_hash
