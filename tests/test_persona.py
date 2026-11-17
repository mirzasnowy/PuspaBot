"""Tes Persona + harness uji buta gerbang 3 (C124, I3)."""

import pytest

from puspa.persona import (
    STYLES,
    add_style_samples,
    apply_correction,
    build_blind_test,
    choose_style,
    get_persona,
    list_style_samples,
    render_reply,
    render_sheet,
    score_blind_test,
)

EXAMPLE_ASLI = [f"pesan asli user nomor {i} tentang kopi dan tagihan" for i in range(12)]
BALASAN_DASAR = [f"gue cek dulu harga barang {i} ya" for i in range(10)]


def test_three_style_examples_available():
    assert set(STYLES) == {"santai", "rapi", "campur"}
    for style in STYLES.values():
        assert style["example"] and style["description"]


def test_selected_style_stored_as_profile_fact(make_user, test_dsn):
    u = make_user()
    choose_style(u["user_id"], "campur", dsn=test_dsn)
    persona = get_persona(u["user_id"], dsn=test_dsn)
    assert persona["style"] == "campur"
    with pytest.raises(ValueError):
        choose_style(u["user_id"], "galak", dsn=test_dsn)


def test_example_message_max_20_oldest_discarded(make_user, test_dsn):
    u = make_user()
    assert add_style_samples(u["user_id"], EXAMPLE_ASLI[:10], dsn=test_dsn) == 10
    assert add_style_samples(u["user_id"], [f"tambahan {i}" for i in range(12)], dsn=test_dsn) == 20
    example = list_style_samples(u["user_id"], dsn=test_dsn)
    assert len(example) == 20
    assert example[0] == "pesan asli user nomor 2 tentang kopi dan tagihan"
    with pytest.raises(ValueError):
        add_style_samples(u["user_id"], [f"x{i}" for i in range(21)], dsn=test_dsn)


def test_direct_correction_active_and_stored(make_user, test_dsn):
    u = make_user()
    choose_style(u["user_id"], "santai", dsn=test_dsn)
    fact = apply_correction(u["user_id"], "jangan pakai gue", dsn=test_dsn)
    assert fact["source"] == "user_confirmed"
    assert fact["directive"] == "without_gue"
    persona = get_persona(u["user_id"], dsn=test_dsn)
    assert "without_gue" in persona["directive"]
    assert render_reply(persona, "gue cek dulu ya") == "aku cek dulu ya"


def test_unrecognized_correction_still_stored_raw(make_user, test_dsn):
    u = make_user()
    fact = apply_correction(u["user_id"], "pakai bahasa Betawi dikit", dsn=test_dsn)
    assert fact["directive"] is None
    assert "Betawi" in get_persona(u["user_id"], dsn=test_dsn)["directive"][0]


def test_blind_test_harness_20_items_half_original(make_user, test_dsn):
    u = make_user()
    choose_style(u["user_id"], "santai", dsn=test_dsn)
    add_style_samples(u["user_id"], EXAMPLE_ASLI, dsn=test_dsn)
    test = build_blind_test(u["user_id"], BALASAN_DASAR, dsn=test_dsn, seed=7)
    assert len(test["items"]) == 20
    source = [i["source"] for i in test["items"]]
    assert source.count("asli") == 10 and source.count("persona") == 10
    assert [i["number"] for i in test["items"]] == list(range(1, 21))
    # Deterministik: seed sama -> urutan sama.
    uji2 = build_blind_test(u["user_id"], BALASAN_DASAR, dsn=test_dsn, seed=7)
    assert [i["text"] for i in test["items"]] == [i["text"] for i in uji2["items"]]
    sheet = render_sheet(test)
    assert "UJI BUTA PERSONA" in sheet


def test_harness_need_10_example_and_10_reply(make_user, test_dsn):
    u = make_user()
    choose_style(u["user_id"], "santai", dsn=test_dsn)
    with pytest.raises(ValueError):
        build_blind_test(u["user_id"], BALASAN_DASAR, dsn=test_dsn)


def test_blind_test_assessment_and_60_percent_threshold(make_user, test_dsn):
    u = make_user()
    choose_style(u["user_id"], "santai", dsn=test_dsn)
    add_style_samples(u["user_id"], EXAMPLE_ASLI, dsn=test_dsn)
    test = build_blind_test(u["user_id"], BALASAN_DASAR, dsn=test_dsn, seed=7)
    # Juri yang menebak semua 'asli': benar 10/20 = 50% -> lolos.
    result = score_blind_test(test, ["asli"] * 20)
    assert result["correct"] == 10 and result["akurasi"] == 0.5 and result["passed"] is True
    # Juri sempurna: 100% -> tidak lolos (bisa membedakan).
    uji2 = build_blind_test(u["user_id"], BALASAN_DASAR, dsn=test_dsn, seed=7)
    jawaban_correct = [i["source"] for i in uji2["items"]]
    hasil2 = score_blind_test(uji2, jawaban_correct)
    assert hasil2["akurasi"] == 1.0 and hasil2["passed"] is False
    with pytest.raises(ValueError):
        score_blind_test(uji2, ["asli"] * 19)
