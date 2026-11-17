"""Tes templat bawaan (§2.12) + tugas acuan 8 dan 9 sebagai alur."""

import pytest

from puspa.workflow.blocks import periksa_alur
from puspa.workflow.runner import (
    aktifkan_alur, jalankan_alur, uji_kering,
)
from puspa.workflow.templates import (
    TUGAS_ACUAN_ALUR, ambil_templat, buat_dari_templat, daftar_templat,
)
from puspa.workflow.triggers import validasi_pemicu


def test_tepat_3_templat_bawaan():
    templat = daftar_templat()
    assert len(templat) == 3
    assert {t["kunci"] for t in templat} == {
        "tagihan_bulanan", "pantau_harga", "rekap_tagihan_mingguan"}


def test_semua_templat_lolos_validasi():
    for t in daftar_templat():
        assert validasi_pemicu(t["pemicu"]) == [], t["kunci"]
        assert periksa_alur(t["langkah"]) == [], t["kunci"]


def test_tugas_acuan_8_dan_9_memetakan_ke_templat():
    assert TUGAS_ACUAN_ALUR[8] == "tagihan_bulanan"
    assert TUGAS_ACUAN_ALUR[9] == "rekap_tagihan_mingguan"


def _aktifkan(make_user, test_dsn, izin_user_penuh, kunci, pemicu=None):
    user_id, agen_id = make_user()
    alur = buat_dari_templat(kunci, user_id=user_id, agen_id=agen_id,
                             pemicu=pemicu, dsn=test_dsn)
    kering = uji_kering(alur["id"], user_id=user_id, dsn=test_dsn)
    assert kering["sukses"] is True, kering
    aktifkan_alur(alur["id"], user_id=user_id, izin_user=izin_user_penuh,
                  hasil_kering=kering, dsn=test_dsn)
    return user_id, alur


def test_tugas_acuan_8_pengingat_tagihan_sebagai_alur(make_user, test_dsn,
                                                     izin_user_penuh):
    # Tugas 8: "Buatkan pengingat untuk tagihan." — pemicu manual.
    user_id, alur = _aktifkan(make_user, test_dsn, izin_user_penuh,
                              "tagihan_bulanan", pemicu={"jenis": "manual"})
    hasil = jalankan_alur(alur["id"], user_id=user_id, pemicu_jenis="manual",
                          dsn=test_dsn)
    # Berhenti di siapkan bayar (aksi keluar -> antrean persetujuan).
    assert hasil["status"] == "menunggu_persetujuan"
    cek = [l for l in hasil["langkah_hasil"] if l["blok"] == "cek_tagihan"]
    assert len(cek) == 2
    assert cek[0]["hasil"]["jumlah"] == 132500  # PLN fixture
    assert cek[1]["hasil"]["jumlah"] == 87500   # PDAM fixture
    assert any(l["blok"] == "kirim_notif" and l["sukses"]
               for l in hasil["langkah_hasil"])


def test_tugas_acuan_9_rekap_mingguan_sebagai_alur(make_user, test_dsn,
                                                  izin_user_penuh):
    # Tugas 9: "Ingatkan semua tagihan yang jatuh tempo minggu ini."
    user_id, alur = _aktifkan(make_user, test_dsn, izin_user_penuh,
                              "rekap_tagihan_mingguan",
                              pemicu={"jenis": "manual"})
    hasil = jalankan_alur(alur["id"], user_id=user_id, pemicu_jenis="manual",
                          dsn=test_dsn)
    assert hasil["sukses"] is True, hasil
    cek = [l for l in hasil["langkah_hasil"] if l["blok"] == "cek_tagihan"]
    assert len(cek) == 4  # PLN, PDAM, pulsa, paket data
    jumlah = {l["hasil"]["jumlah"] for l in cek}
    assert jumlah == {132500, 87500, 50000, 75000}
    assert hasil["langkah_hasil"][-1]["blok"] == "kirim_notif"


def test_templat_pantau_harga_tertahan_gerbang_tos(make_user, test_dsn):
    # Jujur: pantau harga live ditahan keputusan 15 (ToS). Uji kering
    # templat ini GAGAL TERLIHAT dengan alasan syarat layanan — bukan
    # sukses palsu, bukan upaya HTTP.
    user_id, agen_id = make_user()
    alur = buat_dari_templat("pantau_harga", user_id=user_id, agen_id=agen_id,
                             dsn=test_dsn)
    hasil = uji_kering(alur["id"], user_id=user_id, dsn=test_dsn)
    assert hasil["sukses"] is False
    assert "keputusan 15" in hasil["alasan"]
