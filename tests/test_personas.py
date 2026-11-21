"""Tests for multiple switchable personas (built on V2-25 files).

The default persona is the home's own SOUL.md / IDENTITY.md /
USER.md. Extra personas are the same three files in their own
folder under ``<home>/personas/<slug>/``, one active at a time.
The suite proves the Done criterion directly: create two
personas, switch the active one, and the turn loop answers from
the active persona's prompt and reply style on the next turn —
while the old persona stays stored untouched.

No database, no network: persona files live on disk in a
temporary home, and the turn loop reads them through a fake
model call that captures the wire messages.
"""

from __future__ import annotations

import json

import pytest

from puspa.persona.files import render_persona_block
from puspa.persona.switcher import (
    DEFAULT_SLUG,
    create_persona,
    delete_persona,
    get_active_slug,
    get_persona,
    list_personas,
    load_active_persona_files,
    set_active_persona,
    slugify_name,
    update_persona,
)
from puspa.turns.loop import TurnLoop
from puspa.turns.messages import InternalMessage
from puspa.turns.turn_api import ModelReply


def _text_reply(text: str = "Hello from the turn.") -> ModelReply:
    return ModelReply(text=text, input_tokens=10, output_tokens=5)


def _system_text(wire_messages: list[dict]) -> str:
    """Text of the leading system message the model call received."""
    assert wire_messages and wire_messages[0]["role"] == "system"
    return wire_messages[0]["content"]


def _capturing_loop(tmp_path, sink=None):
    seen: list[list[dict]] = []

    def model_call(entry, wire_messages, tools):
        seen.append(list(wire_messages))
        return _text_reply("Hi, I am Puspa.")

    loop = TurnLoop(model_call=model_call, persona_home=tmp_path, sink=sink)
    return loop, seen


# ---------------------------------------------------------------------------
# Slugs and validation
# ---------------------------------------------------------------------------


def test_slugify_name_lowercases_and_dashes(tmp_path):
    assert slugify_name("Kerja Harian") == "kerja-harian"
    assert slugify_name("  Personal! ") == "personal"
    assert slugify_name("Kantor_2") == "kantor-2"


def test_slugify_name_rejects_empty_and_default(tmp_path):
    with pytest.raises(ValueError):
        slugify_name("   ")
    with pytest.raises(ValueError):
        slugify_name("!!!")
    with pytest.raises(ValueError):
        slugify_name("Default")


def test_create_rejects_unknown_style(tmp_path):
    with pytest.raises(ValueError):
        create_persona(tmp_path, "Kerja", style="galak")


# ---------------------------------------------------------------------------
# Create / list / read: two personas beside the default
# ---------------------------------------------------------------------------


def test_create_two_personas_lists_three_with_default_active(tmp_path):
    kerja = create_persona(
        tmp_path,
        "Kerja",
        soul="Jawab singkat, langsung ke intinya.",
        style="rapi",
    )
    personal = create_persona(tmp_path, "Personal", soul="Santai aja, kayak ngobrol sama teman.")
    assert kerja["slug"] == "kerja"
    assert kerja["name"] == "Kerja"
    assert kerja["style"] == "rapi"
    assert kerja["soul"] == "Jawab singkat, langsung ke intinya."
    assert personal["slug"] == "personal"
    assert personal["style"] is None

    listed = list_personas(tmp_path)
    assert [item["slug"] for item in listed] == ["default", "kerja", "personal"]
    assert listed[0]["is_default"] is True
    assert listed[0]["is_active"] is True
    assert listed[1]["is_active"] is False
    assert get_active_slug(tmp_path) == DEFAULT_SLUG


def test_create_duplicate_slug_is_rejected(tmp_path):
    create_persona(tmp_path, "Kerja")
    with pytest.raises(ValueError):
        create_persona(tmp_path, "kerja")


def test_create_writes_the_same_three_files(tmp_path):
    create_persona(tmp_path, "Kerja", soul="Fokus kerja.")
    folder = tmp_path / "personas" / "kerja"
    assert (folder / "SOUL.md").read_text(encoding="utf-8") == "Fokus kerja."
    assert "Name: Kerja" in (folder / "IDENTITY.md").read_text(encoding="utf-8")
    assert (folder / "USER.md").exists()
    # No style given, so no meta file is written.
    assert not (folder / "meta.json").exists()


def test_create_with_style_stores_meta(tmp_path):
    create_persona(tmp_path, "Kerja", style="campur")
    folder = tmp_path / "personas" / "kerja"
    assert json.loads((folder / "meta.json").read_text(encoding="utf-8")) == {"style": "campur"}


def test_get_unknown_persona_raises_key_error(tmp_path):
    with pytest.raises(KeyError):
        get_persona(tmp_path, "tidak-ada")


# ---------------------------------------------------------------------------
# Switching: the active persona is what the next turn uses
# ---------------------------------------------------------------------------


def test_switch_active_changes_what_load_reads(tmp_path):
    create_persona(tmp_path, "Kerja", soul="Fokus kerja.", style="rapi")
    create_persona(tmp_path, "Personal", soul="Santai aja.")
    assert "warm, direct, and plain" in load_active_persona_files(tmp_path).soul

    set_active_persona(tmp_path, "kerja")
    assert get_active_slug(tmp_path) == "kerja"
    active = load_active_persona_files(tmp_path)
    assert active.soul == "Fokus kerja."
    assert active.name == "Kerja"
    assert active.style == "rapi"
    block = render_persona_block(active)
    assert "Fokus kerja." in block
    assert "# Reply style" in block
    assert "Rapi" in block

    set_active_persona(tmp_path, "personal")
    active = load_active_persona_files(tmp_path)
    assert active.soul == "Santai aja."
    assert active.style is None
    assert "# Reply style" not in render_persona_block(active)


def test_switch_back_restores_the_old_persona_untouched(tmp_path):
    create_persona(tmp_path, "Kerja", soul="Fokus kerja.", style="rapi")
    create_persona(tmp_path, "Personal", soul="Santai aja.")
    set_active_persona(tmp_path, "kerja")
    set_active_persona(tmp_path, "personal")
    set_active_persona(tmp_path, "kerja")
    assert load_active_persona_files(tmp_path).soul == "Fokus kerja."
    # The old persona is still stored exactly as created.
    assert get_persona(tmp_path, "personal")["soul"] == "Santai aja."


def test_set_active_unknown_slug_raises_key_error(tmp_path):
    with pytest.raises(KeyError):
        set_active_persona(tmp_path, "tidak-ada")


def test_broken_active_file_falls_back_to_default(tmp_path):
    create_persona(tmp_path, "Kerja", soul="Fokus kerja.")
    (tmp_path / "personas" / "active.json").write_text("{bukan json", encoding="utf-8")
    assert get_active_slug(tmp_path) == DEFAULT_SLUG
    assert "warm, direct, and plain" in load_active_persona_files(tmp_path).soul


def test_active_pointing_at_deleted_persona_falls_back(tmp_path):
    create_persona(tmp_path, "Kerja", soul="Fokus kerja.")
    set_active_persona(tmp_path, "kerja")
    delete_persona(tmp_path, "kerja")
    assert get_active_slug(tmp_path) == DEFAULT_SLUG


def test_turn_loop_uses_the_active_persona_next_turn(tmp_path):
    loop, seen = _capturing_loop(tmp_path)
    create_persona(tmp_path, "Kerja", soul="Fokus kerja.", style="rapi")
    create_persona(tmp_path, "Personal", soul="Santai aja, kayak teman.")
    history = [InternalMessage.user("Halo")]

    result = loop.run_turn(history)
    assert result.status == "completed"
    assert "warm, direct, and plain" in _system_text(seen[0])

    set_active_persona(tmp_path, "kerja")
    result = loop.run_turn([*result.messages, InternalMessage.user("Lanjut")])
    system = _system_text(seen[1])
    assert "Fokus kerja." in system
    assert "Rapi" in system
    assert "warm, direct, and plain" not in system

    set_active_persona(tmp_path, "personal")
    result = loop.run_turn([*result.messages, InternalMessage.user("Lagi")])
    system = _system_text(seen[2])
    assert "Santai aja, kayak teman." in system
    assert "Fokus kerja." not in system
    assert "Rapi" not in system


# ---------------------------------------------------------------------------
# Update / delete
# ---------------------------------------------------------------------------


def test_update_edits_only_given_fields(tmp_path):
    create_persona(tmp_path, "Kerja", soul="Fokus kerja.", style="rapi")
    updated = update_persona(tmp_path, "kerja", soul="Fokus kerja, tanpa basa-basi.")
    assert updated["soul"] == "Fokus kerja, tanpa basa-basi."
    assert updated["name"] == "Kerja"
    assert updated["style"] == "rapi"
    # The folder slug never moves when the name changes.
    renamed = update_persona(tmp_path, "kerja", name="Kantor")
    assert renamed["slug"] == "kerja"
    assert renamed["name"] == "Kantor"
    assert "Name: Kantor" in (tmp_path / "personas" / "kerja" / "IDENTITY.md").read_text(
        encoding="utf-8"
    )


def test_update_default_persona_edits_the_root_files(tmp_path):
    updated = update_persona(tmp_path, "default", soul="Selalu jawab singkat.")
    assert updated["slug"] == "default"
    assert (tmp_path / "SOUL.md").read_text(encoding="utf-8") == "Selalu jawab singkat."


def test_update_unknown_slug_raises_key_error(tmp_path):
    with pytest.raises(KeyError):
        update_persona(tmp_path, "tidak-ada", soul="x")


def test_delete_active_persona_falls_back_to_default(tmp_path):
    create_persona(tmp_path, "Kerja", soul="Fokus kerja.")
    create_persona(tmp_path, "Personal", soul="Santai aja.")
    set_active_persona(tmp_path, "kerja")
    assert delete_persona(tmp_path, "kerja") == "kerja"
    assert get_active_slug(tmp_path) == DEFAULT_SLUG
    assert [item["slug"] for item in list_personas(tmp_path)] == [
        "default",
        "personal",
    ]
    assert not (tmp_path / "personas" / "kerja").exists()


def test_delete_default_is_refused_and_unknown_raises(tmp_path):
    with pytest.raises(ValueError):
        delete_persona(tmp_path, "default")
    with pytest.raises(KeyError):
        delete_persona(tmp_path, "tidak-ada")
