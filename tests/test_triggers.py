"""Tes pemicu (§2.12 + §2.14): 4 pemicu MVP, tolak email/webhook."""

from datetime import date, datetime, timezone

import pytest

from puspa.workflow.triggers import (
    TriggerError, cocok_harga_turun, cocok_jatuh_tempo, pemicu_berikutnya,
    simpan_ke_penjadwal, validasi_pemicu,
)

SEKARANG = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)


def test_jadwal_tanggal_bulanan():
    p = {"jenis": "jadwal", "mode": "tanggal", "tanggal": 25, "jam_mulai": "07:00"}
    assert validasi_pemicu(p) == []
    berikut = pemicu_berikutnya(p, SEKARANG)
    assert (berikut.year, berikut.month, berikut.day) == (2026, 11, 25)
    assert (berikut.hour, berikut.minute) == (7, 0)


def test_jadwal_tanggal_lewat_bulan_ini_ke_bulan_depan():
    p = {"jenis": "jadwal", "mode": "tanggal", "tanggal": 10, "jam_mulai": "07:00"}
    berikut = pemicu_berikutnya(p, SEKARANG)
    assert (berikut.year, berikut.month, berikut.day) == (2026, 12, 10)


def test_jadwal_hari_mingguan():
    p = {"jenis": "jadwal", "mode": "hari", "hari": "senin", "jam_mulai": "07:00"}
    berikut = pemicu_berikutnya(p, SEKARANG)  # 17 Nov 2026 = Selasa
    assert berikut.weekday() == 0
    assert (berikut.year, berikut.month, berikut.day) == (2026, 11, 23)


def test_jadwal_interval_minimal_1_jam():
    p = {"jenis": "jadwal", "mode": "interval_jam", "jam": 0.5}
    assert any("minimal 1 jam" in m for m in validasi_pemicu(p))
    p2 = {"jenis": "jadwal", "mode": "interval_jam", "jam": 1}
    assert validasi_pemicu(p2) == []
    assert pemicu_berikutnya(p2, SEKARANG) == datetime(
        2026, 11, 17, 13, 0, tzinfo=timezone.utc)


def test_pemicu_email_dan_webhook_ditolak():
    for jenis in ("email_masuk", "webhook"):
        masalah = validasi_pemicu({"jenis": jenis})
        assert masalah and "sesudah MVP" in masalah[0]


def test_jatuh_tempo_n_hari_sebelum():
    p = {"jenis": "jatuh_tempo", "hari_sebelum": 3, "provider": "PLN"}
    assert validasi_pemicu(p) == []
    tagihan = [
        {"provider": "PLN", "jatuh_tempo": "2026-11-20", "status": "belum_bayar"},
        {"provider": "PLN", "jatuh_tempo": "2026-11-30", "status": "belum_bayar"},
        {"provider": "PLN", "jatuh_tempo": "2026-11-19", "status": "sudah_bayar"},
        {"provider": "PDAM", "jatuh_tempo": "2026-11-19", "status": "belum_bayar"},
    ]
    cocok = cocok_jatuh_tempo(p, tagihan, hari_ini=date(2026, 11, 17))
    assert len(cocok) == 1 and cocok[0]["jatuh_tempo"] == "2026-11-20"


def test_harga_turun_di_bawah_ambang():
    p = {"jenis": "harga_turun", "produk_id": "hp-x", "ambang_harga": 5000000}
    assert validasi_pemicu(p) == []
    assert cocok_harga_turun(p, 4999999) is True
    assert cocok_harga_turun(p, 5000000) is True
    assert cocok_harga_turun(p, 5000001) is False
    assert cocok_harga_turun(p, None) is False  # belum ada data: tidak memicu


def test_simpan_pemicu_jadwal_ke_penjadwal_fase2(make_user, test_dsn):
    user_id, _ = make_user()
    p = {"jenis": "jadwal", "mode": "tanggal", "tanggal": 25, "jam_mulai": "07:00"}
    pemicu = simpan_ke_penjadwal(user_id=user_id, alur_id="alur-uji",
                                 pemicu=p, sekarang=SEKARANG, dsn=test_dsn)
    assert pemicu is not None
    assert pemicu.jenis == "jadwal"
    assert pemicu.alur == "alur-uji"
    assert (pemicu.pemicu_berikutnya.year, pemicu.pemicu_berikutnya.month,
            pemicu.pemicu_berikutnya.day) == (2026, 11, 25)


def test_pemicu_non_jadwal_tidak_disimpan_ke_penjadwal():
    assert simpan_ke_penjadwal(user_id="u", alur_id="a",
                               pemicu={"jenis": "manual"}) is None
