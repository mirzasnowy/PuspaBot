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

CONTOH_ASLI = [f"pesan asli user nomor {i} tentang kopi dan tagihan" for i in range(12)]
BALASAN_DASAR = [f"gue cek dulu harga barang {i} ya" for i in range(10)]


def test_tiga_gaya_contoh_tersedia():
    assert set(STYLES) == {"santai", "rapi", "campur"}
    for gaya in STYLES.values():
        assert gaya["contoh"] and gaya["deskripsi"]


def test_pilih_gaya_tersimpan_sebagai_fakta_profil(make_user, test_dsn):
    u = make_user()
    choose_style(u["user_id"], "campur", dsn=test_dsn)
    persona = get_persona(u["user_id"], dsn=test_dsn)
    assert persona["gaya"] == "campur"
    with pytest.raises(ValueError):
        choose_style(u["user_id"], "galak", dsn=test_dsn)


def test_contoh_pesan_maks_20_tertua_dibuang(make_user, test_dsn):
    u = make_user()
    assert add_style_samples(u["user_id"], CONTOH_ASLI[:10], dsn=test_dsn) == 10
    assert add_style_samples(u["user_id"], [f"tambahan {i}" for i in range(12)], dsn=test_dsn) == 20
    contoh = list_style_samples(u["user_id"], dsn=test_dsn)
    assert len(contoh) == 20
    assert contoh[0] == "pesan asli user nomor 2 tentang kopi dan tagihan"
    with pytest.raises(ValueError):
        add_style_samples(u["user_id"], [f"x{i}" for i in range(21)], dsn=test_dsn)


def test_koreksi_langsung_berlaku_dan_tersimpan(make_user, test_dsn):
    u = make_user()
    choose_style(u["user_id"], "santai", dsn=test_dsn)
    fact = apply_correction(u["user_id"], "jangan pakai gue", dsn=test_dsn)
    assert fact["asal"] == "dikonfirmasi_user"
    assert fact["direktif"] == "tanpa_gue"
    persona = get_persona(u["user_id"], dsn=test_dsn)
    assert "tanpa_gue" in persona["direktif"]
    assert render_reply(persona, "gue cek dulu ya") == "aku cek dulu ya"


def test_koreksi_tidak_dikenali_tetap_disimpan_mentah(make_user, test_dsn):
    u = make_user()
    fact = apply_correction(u["user_id"], "pakai bahasa Betawi dikit", dsn=test_dsn)
    assert fact["direktif"] is None
    assert "Betawi" in get_persona(u["user_id"], dsn=test_dsn)["direktif"][0]


def test_harness_uji_buta_20_item_separuh_asli(make_user, test_dsn):
    u = make_user()
    choose_style(u["user_id"], "santai", dsn=test_dsn)
    add_style_samples(u["user_id"], CONTOH_ASLI, dsn=test_dsn)
    uji = build_blind_test(u["user_id"], BALASAN_DASAR, dsn=test_dsn, seed=7)
    assert len(uji["items"]) == 20
    asal = [i["asal"] for i in uji["items"]]
    assert asal.count("asli") == 10 and asal.count("persona") == 10
    assert [i["nomor"] for i in uji["items"]] == list(range(1, 21))
    # Deterministik: seed sama -> urutan sama.
    uji2 = build_blind_test(u["user_id"], BALASAN_DASAR, dsn=test_dsn, seed=7)
    assert [i["teks"] for i in uji["items"]] == [i["teks"] for i in uji2["items"]]
    sheet = render_sheet(uji)
    assert "UJI BUTA PERSONA" in sheet


def test_harness_butuh_10_contoh_dan_10_balasan(make_user, test_dsn):
    u = make_user()
    choose_style(u["user_id"], "santai", dsn=test_dsn)
    with pytest.raises(ValueError):
        build_blind_test(u["user_id"], BALASAN_DASAR, dsn=test_dsn)


def test_penilaian_uji_buta_dan_ambang_60_persen(make_user, test_dsn):
    u = make_user()
    choose_style(u["user_id"], "santai", dsn=test_dsn)
    add_style_samples(u["user_id"], CONTOH_ASLI, dsn=test_dsn)
    uji = build_blind_test(u["user_id"], BALASAN_DASAR, dsn=test_dsn, seed=7)
    # Juri yang menebak semua 'asli': benar 10/20 = 50% -> lolos.
    hasil = score_blind_test(uji, ["asli"] * 20)
    assert hasil["benar"] == 10 and hasil["akurasi"] == 0.5 and hasil["lolos"] is True
    # Juri sempurna: 100% -> tidak lolos (bisa membedakan).
    uji2 = build_blind_test(u["user_id"], BALASAN_DASAR, dsn=test_dsn, seed=7)
    jawaban_benar = [i["asal"] for i in uji2["items"]]
    hasil2 = score_blind_test(uji2, jawaban_benar)
    assert hasil2["akurasi"] == 1.0 and hasil2["lolos"] is False
    with pytest.raises(ValueError):
        score_blind_test(uji2, ["asli"] * 19)
