"""Tes pustaka blok (§2.12): 15 blok tetap, skema, batas MVP."""

import pytest

from puspa.workflow.blocks import (
    BLOK, BlockError, daftar_blok, definisi_blok, kedalaman_cabang,
    periksa_alur, periksa_langkah, periksa_masukan,
)

BLOK_WAJIB = (
    "cek_tagihan", "bandingkan_harga", "pantau_harga", "cari_email",
    "baca_email", "buat_draf", "buat_event", "siapkan_bayar",
    "kirim_notif", "tanya_user", "tunggu_persetujuan", "kondisi",
    "untuk_tiap", "simpan_memori", "simpan_ke_tabel_modul",
)


def test_pustaka_tepat_15_blok():
    assert set(daftar_blok()) == set(BLOK_WAJIB)
    assert len(BLOK) == 15


def test_tiap_blok_punya_skema_kelas_dan_izin():
    for nama in BLOK_WAJIB:
        d = definisi_blok(nama)
        assert d["masukan"] and d["keluaran"], nama
        assert d["kelas_izin"] in ("Pembaca", "Pelaksana", "Pemeriksa"), nama
        assert set(d["izin"]) == {"konektor", "scope", "domain", "batas_belanja"}, nama


def test_blok_baca_email_lewat_pembaca():
    assert definisi_blok("baca_email")["kelas_izin"] == "Pembaca"
    assert definisi_blok("cari_email")["izin"]["konektor"] == ["gmail"]
    assert definisi_blok("baca_email")["izin"]["scope"] == ["gmail.readonly"]


def test_blok_aksi_keluar_hanya_buat_event_dan_siapkan_bayar():
    keluar = {n for n, d in BLOK.items() if d["aksi_keluar"]}
    assert keluar == {"buat_event", "siapkan_bayar"}


def test_blok_tidak_dikenal_ditolak():
    with pytest.raises(BlockError):
        definisi_blok("kirim_whatsapp")
    assert periksa_langkah({"blok": "kirim_whatsapp", "masukan": {}})


def test_periksa_masukan_kurang_field():
    assert periksa_masukan("cek_tagihan", {"provider": "PLN"}) == ["nomor_pelanggan"]
    assert periksa_masukan("cek_tagihan",
                            {"provider": "PLN", "nomor_pelanggan": "1"}) == []


def test_batas_maks_10_langkah():
    langkah = [{"blok": "kirim_notif", "masukan": {"pesan": "x"}}] * 11
    masalah = periksa_alur(langkah)
    assert any("maks 10" in m for m in masalah)


def test_batas_maks_2_tingkat_percabangan():
    dalam = {"blok": "kirim_notif", "masukan": {"pesan": "x"}}
    tiga = {"blok": "kondisi", "masukan": {"ungkapan": "a == b"},
            "maka": [{"blok": "kondisi", "masukan": {"ungkapan": "a == b"},
                      "maka": [{"blok": "kondisi", "masukan": {"ungkapan": "a == b"},
                                "maka": [dalam], "kalau_tidak": []}],
                      "kalau_tidak": []}],
            "kalau_tidak": []}
    assert kedalaman_cabang([tiga]) == 3
    assert any("maks 2" in m for m in periksa_alur([tiga]))


def test_cabang_2_tingkat_lolos():
    dalam = {"blok": "kirim_notif", "masukan": {"pesan": "x"}}
    dua = {"blok": "kondisi", "masukan": {"ungkapan": "a == b"},
           "maka": [{"blok": "kondisi", "masukan": {"ungkapan": "a == b"},
                     "maka": [dalam], "kalau_tidak": []}],
           "kalau_tidak": []}
    assert kedalaman_cabang([dua]) == 2
    assert periksa_alur([dua]) == []


def test_untuk_tiap_maks_20_item_dicek_di_runner():
    langkah = {"blok": "untuk_tiap", "masukan": {"daftar": list(range(21))},
               "langkah_dalam": [{"blok": "kirim_notif", "masukan": {"pesan": "x"}}]}
    # Skema langkah lolos (batas item dicek saat jalan, bukan saat susun).
    assert periksa_langkah(langkah) == []
