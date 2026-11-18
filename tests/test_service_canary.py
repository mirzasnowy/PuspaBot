"""Tes Graf Alur Layanan + canary (§3.3 revisi 7)."""

import pytest

from puspa.billing.fixture import DATA_TEST, SiteFixture
from puspa.graphs import service as graf

LANGKAH = [{"step": "open_official_site"}, {"step": "read_amount"}]


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
    return graf.write_procedure(
        user_id=user["user_id"], service=service, step=LANGKAH, agent_id=agent_id, dsn=test_dsn
    )


def test_write_procedure_rejects_private_data(make_user, test_dsn):
    user = make_user()
    with pytest.raises(graf.ServiceError):
        graf.write_procedure(
            user_id=user["user_id"],
            service="PLN",
            step=[{"step": "cek 123456789012"}],
            agent_id=user["agent_id"],
            dsn=test_dsn,
        )
    with pytest.raises(graf.ServiceError):
        graf.write_procedure(
            user_id=user["user_id"],
            service="airtime",
            step=[{"step": "cek 081234567890"}],
            agent_id=user["agent_id"],
            dsn=test_dsn,
        )


def test_cycle_trial_to_verified(make_user, agent_kedua, test_dsn):
    user = make_user()
    agen2 = agent_kedua(user["user_id"])
    procedure = _create_prosedur(user, user["agent_id"], test_dsn)
    assert procedure["status"] == "trial"
    assert procedure["successful_runs"] == 1  # tulis prosedur = jalan sukses pertama
    graf.record_run(procedure["id"], user["user_id"], True, agent_id=user["agent_id"], dsn=test_dsn)
    end = graf.record_run(
        procedure["id"], user["user_id"], True, agent_id=agen2["agent_id"], dsn=test_dsn
    )
    assert end["status"] == "verified"
    assert end["successful_runs"] == 3


def test_two_consecutive_failures_become_broken_and_do_not_run(make_user, test_dsn):
    user = make_user()
    procedure = _create_prosedur(user, user["agent_id"], test_dsn)
    graf.record_run(
        procedure["id"], user["user_id"], False, agent_id=user["agent_id"], dsn=test_dsn
    )
    end = graf.record_run(
        procedure["id"], user["user_id"], False, agent_id=user["agent_id"], dsn=test_dsn
    )
    assert end["status"] == "broken"
    result = graf.run_procedure(
        end,
        run=lambda p: {"success": True},
        agent_id=user["agent_id"],
        user_id=user["user_id"],
        task_id="tugas-x",
        dsn=test_dsn,
    )
    assert result["success"] is False and result["reason"] == "prosedur_broken"


def test_reuse_across_agents_recorded(make_user, agent_kedua, test_dsn):
    user = make_user()
    agen2 = agent_kedua(user["user_id"])
    procedure = _create_prosedur(user, user["agent_id"], test_dsn)
    graf.run_procedure(
        procedure,
        run=lambda p: {"success": True},
        agent_id=user["agent_id"],
        user_id=user["user_id"],
        task_id="tugas-1",
        dsn=test_dsn,
    )
    result = graf.record_reuse(
        procedure_id=procedure["id"],
        user_id=user["user_id"],
        agent_id=agen2["agent_id"],
        task_id="tugas-2",
        dsn=test_dsn,
    )
    assert result["different_agents"] == 2


def test_canary_success_and_failed_canary_becomes_broken(make_user, agent_kedua, test_dsn):
    user = make_user()
    agen2 = agent_kedua(user["user_id"])
    procedure = _create_prosedur(user, user["agent_id"], test_dsn)
    for agent in (user["agent_id"], agen2["agent_id"], user["agent_id"]):
        graf.record_run(procedure["id"], user["user_id"], True, agent_id=agent, dsn=test_dsn)
    assert (
        graf.get_procedure(procedure["id"], user["user_id"], dsn=test_dsn)["status"] == "verified"
    )
    ok = graf.run_canary(
        procedure_id=procedure["id"],
        user_id=user["user_id"],
        agent_id=user["agent_id"],
        run=lambda p: {"success": True},
        dsn=test_dsn,
    )
    assert ok["success"] is True
    # Canary gagal 2x -> rusak.
    for _ in range(2):
        graf.run_canary(
            procedure_id=procedure["id"],
            user_id=user["user_id"],
            agent_id=user["agent_id"],
            run=lambda p: {"success": False, "reason": "situs_berubah"},
            dsn=test_dsn,
        )
    assert graf.get_procedure(procedure["id"], user["user_id"], dsn=test_dsn)["status"] == "broken"


def test_canary_only_for_verified(make_user, test_dsn):
    user = make_user()
    procedure = _create_prosedur(user, user["agent_id"], test_dsn)
    result = graf.run_canary(
        procedure_id=procedure["id"],
        user_id=user["user_id"],
        agent_id=user["agent_id"],
        run=lambda p: {"success": True},
        dsn=test_dsn,
    )
    assert result["success"] is False and result["reason"] == "bukan_verified"


def test_canary_detects_test_site_change(make_user, agent_kedua, test_dsn):
    """Uji suntik §3.6: canary membaca situs uji; nilai diubah ->
    canary gagal (terdeteksi sebelum dipakai user)."""
    user = make_user()
    agen2 = agent_kedua(user["user_id"])
    procedure = _create_prosedur(user, user["agent_id"], test_dsn)
    for agent in (user["agent_id"], agen2["agent_id"], user["agent_id"]):
        graf.record_run(procedure["id"], user["user_id"], True, agent_id=agent, dsn=test_dsn)

    def _canary_check(_prosedur):
        import httpx

        with SiteFixture() as situs:
            text = httpx.get(situs.url("pln"), timeout=5).text
        import re

        amount = float(re.search(r"Rp\s*([\d.]+)", text).group(1).replace(".", ""))
        return {"success": amount == DATA_TEST["pln"]["amount"]}

    # 2x canary mendeteksi perubahan yang disuntikkan (2 dari 2).
    terdeteksi = 0
    try:
        SiteFixture.change("pln", amount=999999)
        for _ in range(2):
            result = graf.run_canary(
                procedure_id=procedure["id"],
                user_id=user["user_id"],
                agent_id=user["agent_id"],
                run=_canary_check,
                dsn=test_dsn,
            )
            if not result["success"]:
                terdeteksi += 1
    finally:
        SiteFixture.restore("pln")
    assert terdeteksi == 2


def test_subagent_pattern_stored_in_graph_and_read_by_other_agent(make_user, agent_kedua, test_dsn):
    user = make_user()
    agen2 = agent_kedua(user["user_id"])
    pattern = graf.save_subagent_pattern(
        user_id=user["user_id"], name="compare_prices", agent_id=user["agent_id"], dsn=test_dsn
    )
    assert pattern["service"] == "pola:compare_prices"
    assert [step["step"] for step in pattern["step"]] == [
        "reader_tokopedia",
        "reader_shopee",
        "reader_blibli",
        "verifier_reread",
    ]
    # Agen kedua membaca pola dari graf tanpa diajari ulang.
    items = graf.list_patterns(user["user_id"], dsn=test_dsn)
    assert len(items) == 1 and items[0]["id"] == pattern["id"]
    with pytest.raises(graf.ServiceError):
        graf.save_subagent_pattern(
            user_id=user["user_id"],
            name="pattern_not_ada",
            agent_id=agen2["agent_id"],
            dsn=test_dsn,
        )


def test_fix_needs_3_tests_then_rollback(make_user, test_dsn):
    user = make_user()
    procedure = _create_prosedur(user, user["agent_id"], test_dsn)
    graf.record_run(
        procedure["id"], user["user_id"], False, agent_id=user["agent_id"], dsn=test_dsn
    )
    graf.record_run(
        procedure["id"], user["user_id"], False, agent_id=user["agent_id"], dsn=test_dsn
    )
    assert graf.get_procedure(procedure["id"], user["user_id"], dsn=test_dsn)["status"] == "broken"
    step_new = [{"step": "open_situs_new"}, {"step": "read_amount"}]
    usul = graf.propose_fix(
        procedure_id=procedure["id"], user_id=user["user_id"], step_new=step_new, dsn=test_dsn
    )
    assert usul["new_version"] == 2
    # Uji yang gagal 1 dari 3 -> belum menggantikan.
    result = graf.test_fix(
        procedure_id=procedure["id"],
        user_id=user["user_id"],
        version=2,
        run_test=lambda v: {"success": False},
        dsn=test_dsn,
    )
    assert result["replaced"] is False
    result = graf.test_fix(
        procedure_id=procedure["id"],
        user_id=user["user_id"],
        version=2,
        run_test=lambda v: {"success": True},
        dsn=test_dsn,
    )
    assert result["replaced"] is True
    prosedur_new = graf.get_procedure(procedure["id"], user["user_id"], dsn=test_dsn)
    assert prosedur_new["version"] == 2 and prosedur_new["step"] == step_new
    # Rollback ke versi 1.
    old = graf.rollback(
        procedure_id=procedure["id"], user_id=user["user_id"], version=1, dsn=test_dsn
    )
    assert old["version"] == 1 and old["step"] == LANGKAH
