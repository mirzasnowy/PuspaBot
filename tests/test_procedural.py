"""Tes Prosedural — lapis 3 memori (C55) + siklus hidup (3.3)."""

import uuid

import pytest

from puspa.memory import procedural

LANGKAH = ["buka situs pln", "masukkan nomor pelanggan", "baca jumlah tagihan"]


def test_task_successful_becomes_procedure_trial(make_user, test_dsn):
    u = make_user()
    proc = procedural.record_successful_task(
        u["user_id"], "check_pln", LANGKAH, agent_id=u["agent_id"], dsn=test_dsn
    )
    assert proc["status"] == "trial"
    assert proc["successful_runs"] == 1
    assert proc["step"] == LANGKAH


def test_same_task_does_not_duplicate_procedure(make_user, test_dsn):
    u = make_user()
    procedural.record_successful_task(u["user_id"], "check_pln", LANGKAH, agent_id=u["agent_id"], dsn=test_dsn)
    proc = procedural.record_successful_task(u["user_id"], "check_pln", LANGKAH, agent_id=u["agent_id"], dsn=test_dsn)
    assert proc["successful_runs"] == 2
    assert len(procedural.list_procedures(u["user_id"], dsn=test_dsn)) == 1


def test_procedure_verified_after_3_runs_by_2_agents(make_user, test_dsn):
    u = make_user()
    agen2 = str(uuid.uuid4())
    proc = procedural.record_successful_task(
        u["user_id"], "check_pln", LANGKAH, agent_id=u["agent_id"], dsn=test_dsn
    )
    proc = procedural.mark_run(proc["id"], u["user_id"], True, agent_id=agen2, dsn=test_dsn)
    assert proc["status"] == "trial"  # baru 2 jalan
    proc = procedural.mark_run(proc["id"], u["user_id"], True, agent_id=u["agent_id"], dsn=test_dsn)
    assert proc["status"] == "verified"
    assert proc["successful_runs"] == 3


def test_procedure_broken_after_2_consecutive_failures(make_user, test_dsn):
    u = make_user()
    proc = procedural.record_successful_task(u["user_id"], "check_pln", LANGKAH, dsn=test_dsn)
    proc = procedural.mark_run(proc["id"], u["user_id"], False, dsn=test_dsn)
    assert proc["status"] == "trial"
    proc = procedural.mark_run(proc["id"], u["user_id"], False, dsn=test_dsn)
    assert proc["status"] == "broken"
    assert proc["consecutive_failed_runs"] == 2


def test_procedure_without_private_data(make_user, test_dsn):
    """Prosedur adalah templat: nomor pelanggan tidak boleh masuk."""
    u = make_user()
    proc = procedural.record_successful_task(
        u["user_id"], "check_pln", LANGKAH, dsn=test_dsn
    )
    assert "1234567890" not in str(proc["step"])
    assert proc["user_id"] == u["user_id"]


def test_procedure_isolated_between_users(make_user, test_dsn):
    a, b = make_user(), make_user()
    proc = procedural.record_successful_task(a["user_id"], "check_pln", LANGKAH, dsn=test_dsn)
    assert procedural.list_procedures(b["user_id"], dsn=test_dsn) == []
    with pytest.raises(KeyError):
        procedural.get_procedure(proc["id"], b["user_id"], dsn=test_dsn)
