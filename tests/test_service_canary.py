"""Tes Graf Alur Layanan + canary (§3.3 revisi 7)."""

import pytest

from puspa.billing.fixture import DATA_UJI, SitusUji
from puspa.graphs import service as graf

LANGKAH = [{"langkah": "buka_situs_resmi"}, {"langkah": "baca_jumlah"}]


def _buat_prosedur(user, agen_id, test_dsn, layanan="PLN"):
    return graf.tulis_prosedur(user_id=user["user_id"], layanan=layanan,
                               langkah=LANGKAH, agen_id=agen_id, dsn=test_dsn)


def test_tulis_prosedur_menolak_data_pribadi(make_user, test_dsn):
    user = make_user()
    with pytest.raises(graf.ServiceError):
        graf.tulis_prosedur(user_id=user["user_id"], layanan="PLN",
                            langkah=[{"langkah": "cek 123456789012"}],
                            agen_id=user["agen_id"], dsn=test_dsn)
    with pytest.raises(graf.ServiceError):
        graf.tulis_prosedur(user_id=user["user_id"], layanan="pulsa",
                            langkah=[{"langkah": "cek 081234567890"}],
                            agen_id=user["agen_id"], dsn=test_dsn)


def test_siklus_percobaan_ke_terverifikasi(make_user, agen_kedua, test_dsn):
    user = make_user()
    agen2 = agen_kedua(user["user_id"])
    prosedur = _buat_prosedur(user, user["agen_id"], test_dsn)
    assert prosedur["status"] == "percobaan"
    assert prosedur["jalan_sukses"] == 1  # tulis prosedur = jalan sukses pertama
    graf.catat_jalan(prosedur["id"], user["user_id"], True, agen_id=user["agen_id"], dsn=test_dsn)
    akhir = graf.catat_jalan(prosedur["id"], user["user_id"], True, agen_id=agen2["agen_id"], dsn=test_dsn)
    assert akhir["status"] == "terverifikasi"
    assert akhir["jalan_sukses"] == 3


def test_dua_gagal_berturut_menjadi_rusak_dan_tidak_dijalankan(make_user, test_dsn):
    user = make_user()
    prosedur = _buat_prosedur(user, user["agen_id"], test_dsn)
    graf.catat_jalan(prosedur["id"], user["user_id"], False, agen_id=user["agen_id"], dsn=test_dsn)
    akhir = graf.catat_jalan(prosedur["id"], user["user_id"], False,
                             agen_id=user["agen_id"], dsn=test_dsn)
    assert akhir["status"] == "rusak"
    hasil = graf.jalankan_prosedur(akhir, jalankan=lambda p: {"sukses": True},
                                   agen_id=user["agen_id"], user_id=user["user_id"],
                                   tugas_id="tugas-x", dsn=test_dsn)
    assert hasil["sukses"] is False and hasil["alasan"] == "prosedur_rusak"


def test_pakai_ulang_lintas_agen_dicatat(make_user, agen_kedua, test_dsn):
    user = make_user()
    agen2 = agen_kedua(user["user_id"])
    prosedur = _buat_prosedur(user, user["agen_id"], test_dsn)
    graf.jalankan_prosedur(prosedur, jalankan=lambda p: {"sukses": True},
                           agen_id=user["agen_id"], user_id=user["user_id"],
                           tugas_id="tugas-1", dsn=test_dsn)
    hasil = graf.catat_pakai_ulang(prosedur_id=prosedur["id"], user_id=user["user_id"],
                                   agen_id=agen2["agen_id"], tugas_id="tugas-2", dsn=test_dsn)
    assert hasil["agen_berbeda"] == 2


def test_canary_sukses_dan_canary_gagal_menjadi_rusak(make_user, agen_kedua, test_dsn):
    user = make_user()
    agen2 = agen_kedua(user["user_id"])
    prosedur = _buat_prosedur(user, user["agen_id"], test_dsn)
    for agen in (user["agen_id"], agen2["agen_id"], user["agen_id"]):
        graf.catat_jalan(prosedur["id"], user["user_id"], True, agen_id=agen, dsn=test_dsn)
    assert graf.ambil_prosedur(prosedur["id"], user["user_id"], dsn=test_dsn)["status"] == "terverifikasi"
    ok = graf.jalankan_canary(prosedur_id=prosedur["id"], user_id=user["user_id"],
                              agen_id=user["agen_id"],
                              jalankan=lambda p: {"sukses": True}, dsn=test_dsn)
    assert ok["sukses"] is True
    # Canary gagal 2x -> rusak.
    for _ in range(2):
        graf.jalankan_canary(prosedur_id=prosedur["id"], user_id=user["user_id"],
                             agen_id=user["agen_id"],
                             jalankan=lambda p: {"sukses": False, "alasan": "situs_berubah"},
                             dsn=test_dsn)
    assert graf.ambil_prosedur(prosedur["id"], user["user_id"], dsn=test_dsn)["status"] == "rusak"


def test_canary_hanya_untuk_terverifikasi(make_user, test_dsn):
    user = make_user()
    prosedur = _buat_prosedur(user, user["agen_id"], test_dsn)
    hasil = graf.jalankan_canary(prosedur_id=prosedur["id"], user_id=user["user_id"],
                                 agen_id=user["agen_id"],
                                 jalankan=lambda p: {"sukses": True}, dsn=test_dsn)
    assert hasil["sukses"] is False and hasil["alasan"] == "bukan_terverifikasi"


def test_canary_mendeteksi_perubahan_situs_uji(make_user, agen_kedua, test_dsn):
    """Uji suntik §3.6: canary membaca situs uji; nilai diubah ->
    canary gagal (terdeteksi sebelum dipakai user)."""
    user = make_user()
    agen2 = agen_kedua(user["user_id"])
    prosedur = _buat_prosedur(user, user["agen_id"], test_dsn)
    for agen in (user["agen_id"], agen2["agen_id"], user["agen_id"]):
        graf.catat_jalan(prosedur["id"], user["user_id"], True, agen_id=agen, dsn=test_dsn)

    def _canary_check(_prosedur):
        import httpx

        with SitusUji() as situs:
            teks = httpx.get(situs.url("pln"), timeout=5).text
        import re

        jumlah = float(re.search(r"Rp\s*([\d.]+)", teks).group(1).replace(".", ""))
        return {"sukses": jumlah == DATA_UJI["pln"]["jumlah"]}

    # 2x canary mendeteksi perubahan yang disuntikkan (2 dari 2).
    terdeteksi = 0
    try:
        SitusUji.ubah("pln", jumlah=999999)
        for _ in range(2):
            hasil = graf.jalankan_canary(prosedur_id=prosedur["id"],
                                         user_id=user["user_id"],
                                         agen_id=user["agen_id"],
                                         jalankan=_canary_check, dsn=test_dsn)
            if not hasil["sukses"]:
                terdeteksi += 1
    finally:
        SitusUji.pulihkan("pln")
    assert terdeteksi == 2


def test_pola_subagen_disimpan_di_graf_dan_dibaca_agen_lain(make_user, agen_kedua, test_dsn):
    user = make_user()
    agen2 = agen_kedua(user["user_id"])
    pola = graf.simpan_pola_subagen(user_id=user["user_id"], nama="bandingkan_harga",
                                    agen_id=user["agen_id"], dsn=test_dsn)
    assert pola["layanan"] == "pola:bandingkan_harga"
    assert [langkah["langkah"] for langkah in pola["langkah"]] == [
        "pembaca_tokopedia", "pembaca_shopee", "pembaca_blibli", "pemeriksa_baca_ulang"]
    # Agen kedua membaca pola dari graf tanpa diajari ulang.
    daftar = graf.daftar_pola(user["user_id"], dsn=test_dsn)
    assert len(daftar) == 1 and daftar[0]["id"] == pola["id"]
    with pytest.raises(graf.ServiceError):
        graf.simpan_pola_subagen(user_id=user["user_id"], nama="pola_tidak_ada",
                                 agen_id=agen2["agen_id"], dsn=test_dsn)


def test_perbaikan_butuh_3_uji_lalu_rollback(make_user, test_dsn):
    user = make_user()
    prosedur = _buat_prosedur(user, user["agen_id"], test_dsn)
    graf.catat_jalan(prosedur["id"], user["user_id"], False, agen_id=user["agen_id"], dsn=test_dsn)
    graf.catat_jalan(prosedur["id"], user["user_id"], False, agen_id=user["agen_id"], dsn=test_dsn)
    assert graf.ambil_prosedur(prosedur["id"], user["user_id"], dsn=test_dsn)["status"] == "rusak"
    langkah_baru = [{"langkah": "buka_situs_baru"}, {"langkah": "baca_jumlah"}]
    usul = graf.usulkan_perbaikan(prosedur_id=prosedur["id"], user_id=user["user_id"],
                                  langkah_baru=langkah_baru, dsn=test_dsn)
    assert usul["versi_baru"] == 2
    # Uji yang gagal 1 dari 3 -> belum menggantikan.
    hasil = graf.uji_perbaikan(prosedur_id=prosedur["id"], user_id=user["user_id"],
                               versi=2,
                               jalankan_uji=lambda v: {"sukses": False}, dsn=test_dsn)
    assert hasil["diganti"] is False
    hasil = graf.uji_perbaikan(prosedur_id=prosedur["id"], user_id=user["user_id"],
                               versi=2,
                               jalankan_uji=lambda v: {"sukses": True}, dsn=test_dsn)
    assert hasil["diganti"] is True
    prosedur_baru = graf.ambil_prosedur(prosedur["id"], user["user_id"], dsn=test_dsn)
    assert prosedur_baru["versi"] == 2 and prosedur_baru["langkah"] == langkah_baru
    # Rollback ke versi 1.
    lama = graf.rollback(prosedur_id=prosedur["id"], user_id=user["user_id"],
                         versi=1, dsn=test_dsn)
    assert lama["versi"] == 1 and lama["langkah"] == LANGKAH
