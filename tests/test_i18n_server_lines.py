"""Tests: Indonesian server-lines (G18).

Pure unit tests for :mod:`puspa.i18n` — rendering, placeholders,
fail-closed behaviour (unknown key / missing variable), locale
fallback recording, and locale-catalog validation. No database.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from puspa.i18n import (
    DEFAULT_LOCALE,
    LocaleValidationError,
    MissingVariable,
    UnknownMessageKey,
    all_keys,
    available_locales,
    catalog,
    check_locales,
    clear_fallback_records,
    fallback_records,
    load_locale_file,
    load_locales,
    merged_catalog,
    render,
    validate_locales,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCALES_DIR = REPO_ROOT / "puspa" / "i18n" / "locales"


def test_default_locale_is_indonesian_and_both_locales_ship():
    assert DEFAULT_LOCALE == "id"
    assert available_locales() == ("en", "id")


def test_catalog_has_every_key_the_code_renders():
    """The shipped catalog covers all keys the wired code asks for."""
    keys = all_keys()
    assert len(keys) == 63
    assert "error.not_logged_in" in keys
    assert "error.unknown_request" in keys
    assert "error.spending_limit_denied" in keys
    assert "cli.unknown_command" in keys
    for flag in (
        "first_modules_visit",
        "first_approval",
        "first_memory_use",
        "first_costly_task",
        "first_billing_row",
    ):
        for field in ("title", "body", "action_label"):
            assert f"hint.{flag}.{field}" in keys
    for screen in (
        "chat",
        "tasks",
        "modules",
        "connectors",
        "memory",
        "settings",
        "personas",
        "office",
        "live",
        "replay",
        "artifacts",
        "insights",
        "onboarding",
        "mockup",
        "rooms",
        "morning",
        "diagnostics",
        "hub",
        "wiki",
    ):
        assert f"ui.nav.{screen}" in keys
    assert "ui.chat.input_placeholder" in keys
    assert "ui.login.title" in keys
    assert "ui.settings.language.title" in keys


def test_render_plain_sentence():
    assert render("error.not_logged_in") == "belum masuk"
    assert render("error.unknown_request") == "permintaan tidak dikenal"


def test_render_named_placeholders():
    assert render("error.spending_limit_denied", nominal=5000) == (
        "batas belanja tidak mengizinkan nominal Rp5000"
    )
    assert (
        render("error.token_nominal_over_budget", nominal=1500000, budget=1000000)
        == "nominal Rp1500000 melebihi anggaran token Rp1000000"
    )
    assert render("hint.first_costly_task.body", ceiling="Rp2.000").startswith(
        "Tugas ini menghabiskan biaya sebesar batas satu tugas (Rp2.000)."
    )


def test_render_unknown_key_raises_never_silently_wrong():
    with pytest.raises(UnknownMessageKey, match="no_such_key"):
        render("no_such_key")
    with pytest.raises(UnknownMessageKey, match=re.escape("error.typo_key")):
        render("error.typo_key")


def test_render_missing_variable_raises_naming_it():
    with pytest.raises(MissingVariable, match="nominal"):
        render("error.spending_limit_denied")
    with pytest.raises(MissingVariable, match="budget"):
        render("error.token_nominal_over_budget", nominal=1500000)
    with pytest.raises(MissingVariable, match="ceiling"):
        render("hint.first_costly_task.body")


def test_render_extra_variables_are_ignored():
    assert render("error.not_logged_in", unused="whatever") == "belum masuk"


def test_render_english_catalog():
    assert render("error.not_logged_in", locale="en") == "not signed in"
    assert render("error.unknown_request", locale="en") == "unknown request"
    assert render("hint.first_modules_visit.title", locale="en") == "Modules add capabilities"
    assert render("ui.nav.tasks", locale="en") == "Tasks"
    assert render("ui.chat.input_placeholder", locale="en", bot="Puspa") == (
        "Write a message to Puspa..."
    )
    assert render("cli.unknown_command", locale="en", command="'frobnicate'") == (
        "unknown command: 'frobnicate'"
    )


def test_render_unknown_locale_falls_back_to_indonesian_and_records_it():
    clear_fallback_records()
    assert render("error.not_logged_in", locale="fr") == "belum masuk"
    assert render("error.not_logged_in", locale="xx") == "belum masuk"
    records = fallback_records()
    assert len(records) == 2
    assert records[0].requested_locale == "fr"
    assert records[0].key == "error.not_logged_in"
    assert records[0].used_locale == "id"
    assert records[1].requested_locale == "xx"
    clear_fallback_records()
    assert fallback_records() == ()


def test_render_unknown_locale_and_unknown_key_still_raises():
    clear_fallback_records()
    with pytest.raises(UnknownMessageKey):
        render("no_such_key", locale="xx")
    # The fallback was recorded even though the render then failed.
    assert fallback_records()[0].requested_locale == "xx"
    clear_fallback_records()


def test_render_missing_key_in_locale_falls_back_per_key(monkeypatch):
    """A key the requested locale is missing renders the Indonesian
    sentence for that key (recorded) — never a blank, never the
    wrong language for keys that *are* translated."""
    from puspa.i18n import server_lines

    monkeypatch.setattr(
        server_lines,
        "_catalogs",
        {
            "id": {"error.a": "A-id", "error.b": "B-id"},
            "en": {"error.a": "A-en"},
        },
    )
    clear_fallback_records()
    assert server_lines.render("error.a", locale="en") == "A-en"
    assert fallback_records() == ()
    assert server_lines.render("error.b", locale="en") == "B-id"
    records = fallback_records()
    assert len(records) == 1
    assert records[0].requested_locale == "en"
    assert records[0].key == "error.b"
    assert records[0].used_locale == "id"
    with pytest.raises(UnknownMessageKey):
        server_lines.render("error.c", locale="en")
    clear_fallback_records()


def test_merged_catalog_fills_missing_keys_never_blank():
    merged = merged_catalog("en", {"id": {"a": "A-id", "b": "B-id"}, "en": {"a": "A-en"}})
    assert merged == {"a": "A-en", "b": "B-id"}
    assert merged_catalog("xx", {"id": {"a": "A-id"}, "en": {"a": "A-en"}}) == {"a": "A-id"}
    full = catalog("en")
    assert len(full) == 63
    assert all(full.values())
    assert full["error.not_logged_in"] == "not signed in"
    assert catalog("id")["error.not_logged_in"] == "belum masuk"


def test_every_key_renders_with_example_variables():
    """No shipped template is broken (bad placeholder, empty string)."""
    examples = {
        "nominal": 5000,
        "budget": 1000000,
        "tool": "'gmail.kirim'",
        "action": "'send'",
        "domain": "'tokopedia.com'",
        "kind": "'send'",
        "request_id": "'req-1'",
        "status": "'approved'",
        "detail": "bad signature",
        "ceiling": "Rp2.000",
        "command": "'frobnicate'",
        "bot": "Puspa",
    }
    for key in sorted(all_keys()):
        assert render(key, **examples), key
        assert render(key, locale="en", **examples), key


# --- locale files + validation -------------------------------------------


def test_load_locale_file_rejects_bad_shape(tmp_path):
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not json", encoding="utf-8")
    with pytest.raises(LocaleValidationError):
        load_locale_file(bad_json)
    not_object = tmp_path / "list.json"
    not_object.write_text('["a", "b"]', encoding="utf-8")
    with pytest.raises(LocaleValidationError):
        load_locale_file(not_object)
    empty_value = tmp_path / "empty.json"
    empty_value.write_text('{"error.x": "  "}', encoding="utf-8")
    with pytest.raises(LocaleValidationError):
        load_locale_file(empty_value)
    non_string = tmp_path / "num.json"
    non_string.write_text('{"error.x": 42}', encoding="utf-8")
    with pytest.raises(LocaleValidationError):
        load_locale_file(non_string)


def test_load_locales_requires_the_default_locale(tmp_path):
    with pytest.raises(LocaleValidationError, match="default locale"):
        load_locales(tmp_path)
    (tmp_path / "en.json").write_text('{"error.x": "x"}', encoding="utf-8")
    with pytest.raises(LocaleValidationError, match="default locale"):
        load_locales(tmp_path)


def test_validate_locales_shipped_catalogs_pass():
    catalogs = load_locales(LOCALES_DIR)
    assert validate_locales(catalogs) == []
    check_locales(catalogs)


def test_validate_locales_catches_missing_and_orphan_keys(tmp_path):
    default = {"a": "A", "b": "B"}
    (tmp_path / "id.json").write_text(json.dumps(default), encoding="utf-8")
    (tmp_path / "en.json").write_text(json.dumps({"a": "A", "c": "C"}), encoding="utf-8")
    catalogs = load_locales(tmp_path)
    problems = validate_locales(catalogs)
    assert any("missing key 'b'" in p and "en" in p for p in problems)
    assert any("orphan key 'c'" in p and "en" in p for p in problems)
    with pytest.raises(LocaleValidationError):
        check_locales(catalogs)


def test_validate_locales_reports_missing_default():
    assert validate_locales({"en": {"a": "A"}}) == ["default locale 'id' is missing"]
    with pytest.raises(LocaleValidationError):
        check_locales({"en": {"a": "A"}})
