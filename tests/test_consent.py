"""Tes Persetujuan data + ekspor/hapus akun (C127, C129)."""

import pytest

from puspa.consent import (
    TEKS_PERSETUJUAN,
    TEKS_VERSI,
    delete_account,
    export_account,
    has_consented,
    list_consents,
    record_consent,
)
from puspa.memory import episodic, procedural, profile


def test_teks_persetujuan_memuat_6_hal_wajib():
    for frasa in (
        "Data yang disimpan",
        "Di mana",
        "Berapa lama",
        "Siapa bisa melihat",
        "Ekspor dan hapus",
        "Graf bersama",
    ):
        assert frasa in TEKS_PERSETUJUAN


def test_persetujuan_dicatat_tabel_dan_buku_kerja(make_user, test_dsn, ledger):
    u = make_user()
    assert has_consented(u["user_id"], dsn=test_dsn) is False
    tugas_id = "22222222-2222-2222-2222-222222222222"
    hasil = record_consent(
        u["user_id"], dsn=test_dsn, ledger=ledger,
        agen_id=u["agen_id"], tugas_id=tugas_id,
    )
    assert hasil["versi_teks"] == TEKS_VERSI
    assert hasil["kejadian_id"] is not None
    assert has_consented(u["user_id"], dsn=test_dsn) is True
    assert len(list_consents(u["user_id"], dsn=test_dsn)) == 1
    events = ledger.events(tugas_id, jenis="persetujuan_data")
    assert len(events) == 1
    assert events[0]["isi"]["versi_teks"] == TEKS_VERSI


def test_persetujuan_tanpa_ledger_tetap_tercatat_di_tabel(make_user, test_dsn):
    u = make_user()
    hasil = record_consent(u["user_id"], dsn=test_dsn)
    assert hasil["kejadian_id"] is None
    assert has_consented(u["user_id"], dsn=test_dsn) is True


def test_ekspor_memuat_data_tanpa_rahasia(make_user, test_dsn, vault_master_key):
    from puspa.accounts.vault import put as vault_put

    u = make_user()
    profile.add_fact(u["user_id"], "nama", "Nero", "diucapkan_user", dsn=test_dsn)
    episodic.record_episode(u["user_id"], "suka kopi", "pesan_user", dsn=test_dsn)
    procedural.record_successful_task(u["user_id"], "cek_pln", ["buka situs"], dsn=test_dsn)
    record_consent(u["user_id"], dsn=test_dsn)
    vault_put(u["user_id"], "google_oauth", '{"access_token": "RAHASIA"}', dsn=test_dsn)
    ekspor = export_account(u["user_id"], dsn=test_dsn)
    assert ekspor["profil"] == {"nama": "Nero"}
    assert len(ekspor["episodik"]) == 1
    assert len(ekspor["prosedur"]) == 1
    assert len(ekspor["persetujuan"]) == 1
    assert "RAHASIA" not in str(ekspor)  # token vault tidak ikut ekspor


def test_hapus_akun_menghapus_data_di_luar_buku_kerja(make_user, test_dsn, ledger):
    u = make_user()
    profile.add_fact(u["user_id"], "nama", "Nero", "diucapkan_user", dsn=test_dsn)
    episodic.record_episode(u["user_id"], "suka kopi", "pesan_user", dsn=test_dsn)
    record_consent(u["user_id"], dsn=test_dsn)
    counts = delete_account(u["user_id"], dsn=test_dsn)
    assert counts["fakta_profil"] == 1
    assert counts["episodik"] == 1
    assert counts["persetujuan_data"] == 1
    assert counts["pengguna"] == 1
    assert profile.get_profile(u["user_id"], dsn=test_dsn) == {}
    assert episodic.list_episodes(u["user_id"], dsn=test_dsn) == []
    assert has_consented(u["user_id"], dsn=test_dsn) is False
