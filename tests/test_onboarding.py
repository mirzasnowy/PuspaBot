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


def test_onboarding_4_step_end_to_end(make_user, test_dsn, ledger):
    u = make_user()
    status = start_onboarding(u["user_id"], dsn=test_dsn)
    assert status["current_step"] == 0
    s1 = complete_step1(
        u["user_id"], "Nero", "santai",
        example_message=[f"pesan contoh {i}" for i in range(10)], dsn=test_dsn,
    )
    assert s1["current_step"] == 1 and s1["name"] == "Nero"
    notifier = StubNotifier()
    s2 = complete_step2(u["user_id"], notifier=notifier, dsn=test_dsn)
    assert s2["current_step"] == 2
    # Web Push Fase 2 belum ada: uji kirim JUJUR melaporkan tidak terkirim.
    assert s2["notif_terkirim"] is False
    assert len(notifier.trial) == 1
    s3 = complete_step3(u["user_id"], connector="nanti", dsn=test_dsn)
    assert s3["current_step"] == 3 and s3["connector"] == "nanti"
    result = run_first_task(
        u["user_id"], "compare_prices", "Asus Vivobook 14",
        dsn=test_dsn, ledger=ledger, agent_id=u["agent_id"],
    )
    assert result["terbukti"] is False  # stub jujur: wedge Fase 3
    assert "Fase 3" in result["reason"]
    assert result["status"]["current_step"] == 4
    assert result["templat_workflow_ditawarkan"] == [
        "Tagihan bulanan", "Pantau harga barang", "Rekap tagihan minggu ini",
    ]
    # Buku Kerja mencatat tugas stub; episodik mencatat tugas pertama.
    assert len(ledger.events(result["task_id"])) == 2
    durasi = measure_onboarding_seconds(result["status"])
    assert durasi is not None and durasi < 300
    assert within_target(result["status"]) is True


def test_task_pertama_without_ledger_tetap_jalan(make_user, test_dsn):
    u = make_user()
    start_onboarding(u["user_id"], dsn=test_dsn)
    result = run_first_task(u["user_id"], "check_bill", "PLN", dsn=test_dsn)
    assert result["terbukti"] is False and result["event_ids"] == []


def test_validasi_step_onboarding(make_user, test_dsn):
    u = make_user()
    start_onboarding(u["user_id"], dsn=test_dsn)
    with pytest.raises(ValueError):
        complete_step1(u["user_id"], "Nero", "galak", dsn=test_dsn)
    with pytest.raises(ValueError):
        complete_step3(u["user_id"], connector="whatsapp", dsn=test_dsn)
    with pytest.raises(ValueError):
        run_first_task(u["user_id"], "main_game", "x", dsn=test_dsn)
    assert get_status("00000000-0000-0000-0000-000000000000", dsn=test_dsn) is None


def test_onboarding_not_can_diulang_without_mulai(make_user, test_dsn):
    u = make_user()
    with pytest.raises(KeyError):
        complete_step1(u["user_id"], "Nero", "santai", dsn=test_dsn)
