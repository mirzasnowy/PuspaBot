"""Tes Graf Alur Layanan + canary (§3.3 revisi 7)."""

import pytest

from puspa.billing.fixture import DATA_TEST, SitusTest
from puspa.graphs import service as graf

LANGKAH = [{"step": "open_situs_resmi"}, {"step": "read_amount"}]


@pytest.fixture
def agent_kedua(test_dsn):
    """Local override of the shared fixture: a factory that creates a
    second agent for a given user. (The shared conftest fixture calls
    create_agent("agen-kedua") with no user_id/name, but create_agent's
    signature is (user_id, name, ...), so it errors at setup. Tests
    here call agent_kedua(user_id) and use the returned agent_id.)"""
    from puspa.runtime.agents import create_agent

    return lambda user_id: create_agent(user_id, "agen-kedua", dsn=test_dsn)


def _create_prosedur(user, agent_id, test_dsn, service="PLN"):
    return graf.tulis_prosedur(user_id=user["user_id"], service=service,
                               step=LANGKAH, agent_id=agent_id, dsn=test_dsn)


def test_tulis_prosedur_menolak_data_pribadi(make_user, test_dsn):
    user = make_user()
    with pytest.raises(graf.ServiceError):
        graf.tulis_prosedur(user_id=user["user_id"], service="PLN",
                            step=[{"step": "cek 123456789012"}],
                            agent_id=user["agent_id"], dsn=test_dsn)
    with pytest.raises(graf.ServiceError):
        graf.tulis_prosedur(user_id=user["user_id"], service="airtime",
                            step=[{"step": "cek 081234567890"}],
                            agent_id=user["agent_id"], dsn=test_dsn)


def test_siklus_trial_to_verified(make_user, agent_kedua, test_dsn):
    user = make_user()
    agen2 = agent_kedua(user["user_id"])
    prosedur = _create_prosedur(user, user["agent_id"], test_dsn)
    assert prosedur["status"] == "trial"
    assert prosedur["successful_runs"] == 1  # tulis prosedur = jalan sukses pertama
    graf.record_jalan(prosedur["id"], user["user_id"], True, agent_id=user["agent_id"], dsn=test_dsn)
    end = graf.record_jalan(prosedur["id"], user["user_id"], True, agent_id=agen2["agent_id"], dsn=test_dsn)
    assert end["status"] == "verified"
    assert end["successful_runs"] == 3


def test_dua_failed_berturut_menjadi_broken_and_not_dijalankan(make_user, test_dsn):
    user = make_user()
    prosedur = _create_prosedur(user, user["agent_id"], test_dsn)
    graf.record_jalan(prosedur["id"], user["user_id"], False, agent_id=user["agent_id"], dsn=test_dsn)
    end = graf.record_jalan(prosedur["id"], user["user_id"], False,
                             agent_id=user["agent_id"], dsn=test_dsn)
    assert end["status"] == "broken"
    result = graf.jalankan_prosedur(end, jalankan=lambda p: {"success": True},
                                   agent_id=user["agent_id"], user_id=user["user_id"],
                                   task_id="tugas-x", dsn=test_dsn)
    assert result["success"] is False and result["reason"] == "prosedur_broken"


def test_use_ulang_lintas_agent_dicatat(make_user, agent_kedua, test_dsn):
    user = make_user()
    agen2 = agent_kedua(user["user_id"])
    prosedur = _create_prosedur(user, user["agent_id"], test_dsn)
    graf.jalankan_prosedur(prosedur, jalankan=lambda p: {"success": True},
                           agent_id=user["agent_id"], user_id=user["user_id"],
                           task_id="tugas-1", dsn=test_dsn)
    result = graf.record_use_ulang(prosedur_id=prosedur["id"], user_id=user["user_id"],
                                   agent_id=agen2["agent_id"], task_id="tugas-2", dsn=test_dsn)
    assert result["agent_berbeda"] == 2


def test_canary_success_and_canary_failed_menjadi_broken(make_user, agent_kedua, test_dsn):
    user = make_user()
    agen2 = agent_kedua(user["user_id"])
    prosedur = _create_prosedur(user, user["agent_id"], test_dsn)
    for agent in (user["agent_id"], agen2["agent_id"], user["agent_id"]):
        graf.record_jalan(prosedur["id"], user["user_id"], True, agent_id=agent, dsn=test_dsn)
    assert graf.take_prosedur(prosedur["id"], user["user_id"], dsn=test_dsn)["status"] == "verified"
    ok = graf.jalankan_canary(prosedur_id=prosedur["id"], user_id=user["user_id"],
                              agent_id=user["agent_id"],
                              jalankan=lambda p: {"success": True}, dsn=test_dsn)
    assert ok["success"] is True
    # Canary gagal 2x -> rusak.
    for _ in range(2):
        graf.jalankan_canary(prosedur_id=prosedur["id"], user_id=user["user_id"],
                             agent_id=user["agent_id"],
                             jalankan=lambda p: {"success": False, "reason": "situs_berubah"},
                             dsn=test_dsn)
    assert graf.take_prosedur(prosedur["id"], user["user_id"], dsn=test_dsn)["status"] == "broken"


def test_canary_hanya_for_verified(make_user, test_dsn):
    user = make_user()
    prosedur = _create_prosedur(user, user["agent_id"], test_dsn)
    result = graf.jalankan_canary(prosedur_id=prosedur["id"], user_id=user["user_id"],
                                 agent_id=user["agent_id"],
                                 jalankan=lambda p: {"success": True}, dsn=test_dsn)
    assert result["success"] is False and result["reason"] == "bukan_verified"


def test_canary_mendeteksi_perubahan_situs_test(make_user, agent_kedua, test_dsn):
    """Uji suntik §3.6: canary membaca situs uji; nilai diubah ->
    canary gagal (terdeteksi sebelum dipakai user)."""
    user = make_user()
    agen2 = agent_kedua(user["user_id"])
    prosedur = _create_prosedur(user, user["agent_id"], test_dsn)
    for agent in (user["agent_id"], agen2["agent_id"], user["agent_id"]):
        graf.record_jalan(prosedur["id"], user["user_id"], True, agent_id=agent, dsn=test_dsn)

    def _canary_check(_prosedur):
        import httpx

        with SitusTest() as situs:
            text = httpx.get(situs.url("pln"), timeout=5).text
        import re

        amount = float(re.search(r"Rp\s*([\d.]+)", text).group(1).replace(".", ""))
        return {"success": amount == DATA_TEST["pln"]["amount"]}

    # 2x canary mendeteksi perubahan yang disuntikkan (2 dari 2).
    terdeteksi = 0
    try:
        SitusTest.change("pln", amount=999999)
        for _ in range(2):
            result = graf.jalankan_canary(prosedur_id=prosedur["id"],
                                         user_id=user["user_id"],
                                         agent_id=user["agent_id"],
                                         jalankan=_canary_check, dsn=test_dsn)
            if not result["success"]:
                terdeteksi += 1
    finally:
        SitusTest.pulihkan("pln")
    assert terdeteksi == 2


def test_pattern_subagen_disimpan_di_graf_and_dibaca_agent_lain(make_user, agent_kedua, test_dsn):
    user = make_user()
    agen2 = agent_kedua(user["user_id"])
    pattern = graf.save_pattern_subagen(user_id=user["user_id"], name="compare_prices",
                                    agent_id=user["agent_id"], dsn=test_dsn)
    assert pattern["service"] == "pola:compare_prices"
    assert [step["step"] for step in pattern["step"]] == [
        "pembaca_tokopedia", "pembaca_shopee", "pembaca_blibli", "pemeriksa_read_ulang"]
    # Agen kedua membaca pola dari graf tanpa diajari ulang.
    items = graf.items_pattern(user["user_id"], dsn=test_dsn)
    assert len(items) == 1 and items[0]["id"] == pattern["id"]
    with pytest.raises(graf.ServiceError):
        graf.save_pattern_subagen(user_id=user["user_id"], name="pattern_not_ada",
                                 agent_id=agen2["agent_id"], dsn=test_dsn)


def test_perbaikan_need_3_test_lalu_rollback(make_user, test_dsn):
    user = make_user()
    prosedur = _create_prosedur(user, user["agent_id"], test_dsn)
    graf.record_jalan(prosedur["id"], user["user_id"], False, agent_id=user["agent_id"], dsn=test_dsn)
    graf.record_jalan(prosedur["id"], user["user_id"], False, agent_id=user["agent_id"], dsn=test_dsn)
    assert graf.take_prosedur(prosedur["id"], user["user_id"], dsn=test_dsn)["status"] == "broken"
    step_new = [{"step": "open_situs_new"}, {"step": "read_amount"}]
    usul = graf.usulkan_perbaikan(prosedur_id=prosedur["id"], user_id=user["user_id"],
                                  step_new=step_new, dsn=test_dsn)
    assert usul["versi_new"] == 2
    # Uji yang gagal 1 dari 3 -> belum menggantikan.
    result = graf.test_perbaikan(prosedur_id=prosedur["id"], user_id=user["user_id"],
                               versi=2,
                               jalankan_test=lambda v: {"success": False}, dsn=test_dsn)
    assert result["replaced"] is False
    result = graf.test_perbaikan(prosedur_id=prosedur["id"], user_id=user["user_id"],
                               versi=2,
                               jalankan_test=lambda v: {"success": True}, dsn=test_dsn)
    assert result["replaced"] is True
    prosedur_new = graf.take_prosedur(prosedur["id"], user["user_id"], dsn=test_dsn)
    assert prosedur_new["versi"] == 2 and prosedur_new["step"] == step_new
    # Rollback ke versi 1.
    old = graf.rollback(prosedur_id=prosedur["id"], user_id=user["user_id"],
                         versi=1, dsn=test_dsn)
    assert old["versi"] == 1 and old["step"] == LANGKAH
