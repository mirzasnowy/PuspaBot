"""Tes Onboarding 4 langkah + ukur waktu (C122, C123, C125, I15 parsial)."""

import pytest

from puspa.onboarding import (
    StubNotifier,
    complete_step1,
    complete_step2,
    complete_step3,
    get_status,
    measure_onboarding_seconds,
    run_first_task,
    start_onboarding,
    within_target,
)


def test_onboarding_4_langkah_end_to_end(make_user, test_dsn, ledger):
    u = make_user()
    status = start_onboarding(u["user_id"], dsn=test_dsn)
    assert status["langkah_saat_ini"] == 0
    s1 = complete_step1(
        u["user_id"], "Nero", "santai",
        contoh_pesan=[f"pesan contoh {i}" for i in range(10)], dsn=test_dsn,
    )
    assert s1["langkah_saat_ini"] == 1 and s1["nama"] == "Nero"
    notifier = StubNotifier()
    s2 = complete_step2(u["user_id"], notifier=notifier, dsn=test_dsn)
    assert s2["langkah_saat_ini"] == 2
    # Web Push Fase 2 belum ada: uji kirim JUJUR melaporkan tidak terkirim.
    assert s2["notif_terkirim"] is False
    assert len(notifier.percobaan) == 1
    s3 = complete_step3(u["user_id"], konektor="nanti", dsn=test_dsn)
    assert s3["langkah_saat_ini"] == 3 and s3["konektor"] == "nanti"
    hasil = run_first_task(
        u["user_id"], "bandingkan_harga", "Asus Vivobook 14",
        dsn=test_dsn, ledger=ledger, agen_id=u["agen_id"],
    )
    assert hasil["terbukti"] is False  # stub jujur: wedge Fase 3
    assert "Fase 3" in hasil["alasan"]
    assert hasil["status"]["langkah_saat_ini"] == 4
    assert hasil["templat_alur_ditawarkan"] == [
        "Tagihan bulanan", "Pantau harga barang", "Rekap tagihan minggu ini",
    ]
    # Buku Kerja mencatat tugas stub; episodik mencatat tugas pertama.
    assert len(ledger.events(hasil["tugas_id"])) == 2
    durasi = measure_onboarding_seconds(hasil["status"])
    assert durasi is not None and durasi < 300
    assert within_target(hasil["status"]) is True


def test_tugas_pertama_tanpa_ledger_tetap_jalan(make_user, test_dsn):
    u = make_user()
    start_onboarding(u["user_id"], dsn=test_dsn)
    hasil = run_first_task(u["user_id"], "cek_tagihan", "PLN", dsn=test_dsn)
    assert hasil["terbukti"] is False and hasil["kejadian_ids"] == []


def test_validasi_langkah_onboarding(make_user, test_dsn):
    u = make_user()
    start_onboarding(u["user_id"], dsn=test_dsn)
    with pytest.raises(ValueError):
        complete_step1(u["user_id"], "Nero", "galak", dsn=test_dsn)
    with pytest.raises(ValueError):
        complete_step3(u["user_id"], konektor="whatsapp", dsn=test_dsn)
    with pytest.raises(ValueError):
        run_first_task(u["user_id"], "main_game", "x", dsn=test_dsn)
    assert get_status("00000000-0000-0000-0000-000000000000", dsn=test_dsn) is None


def test_onboarding_tidak_bisa_diulang_tanpa_mulai(make_user, test_dsn):
    u = make_user()
    with pytest.raises(KeyError):
        complete_step1(u["user_id"], "Nero", "santai", dsn=test_dsn)
